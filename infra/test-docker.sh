#!/bin/sh
# Full repository validation using Docker only.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
project="newsintel-test-$$-$(date +%s)"
artifacts=${NEWSINTEL_TEST_ARTIFACTS:-/tmp/$project}
compose="docker compose -p $project -f docker/compose.test.yaml"
mkdir -p "$artifacts"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "Test artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$root"

echo "==> Building test images"
$compose build backend-test frontend-test

echo "==> Starting integration dependencies"
$compose up -d --wait --wait-timeout 180 test-postgres test-redis test-elasticsearch test-fixture

echo "==> Backend static checks and migrations"
$compose run --rm backend-test ruff check .
$compose run --rm backend-test mypy app
$compose run --rm backend-test alembic upgrade head
[ "$($compose run --rm backend-test alembic heads | grep -c '(head)')" = 1 ] || {
  echo "Migrations must have exactly one head" >&2
  exit 1
}

echo "==> Backend suite (skips are failures)"
$compose run --rm -v "$artifacts:/artifacts" backend-test \
  bash -o pipefail -c '
    python tests/fixtures/server.py 18080 > /artifacts/fixture-server.log 2>&1 &
    fixture_pid=$!
    trap "kill $fixture_pid >/dev/null 2>&1 || true" EXIT
    for attempt in {1..30}; do
      python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:18080/feed.xml\")" >/dev/null 2>&1 && break
      [ "$attempt" -lt 30 ] || { echo "Fixture server did not start" >&2; exit 1; }
      sleep 1
    done
    python -m pytest -q -rs tests | tee /artifacts/pytest.log
    pytest_status=${PIPESTATUS[0]}
    [ "$pytest_status" -eq 0 ] || exit "$pytest_status"
    ! grep -Eq "[0-9]+ skipped" /artifacts/pytest.log
  '

echo "==> PostgreSQL backup and restore rehearsal"
infra/test-restore.sh "$($compose ps -q test-postgres)" newsintel_tests

echo "==> API contract"
$compose run --rm -v "$artifacts:/artifacts" backend-test \
  sh -c "python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > /artifacts/openapi.json"
cmp frontend/openapi.json "$artifacts/openapi.json" || {
  echo "frontend/openapi.json is stale" >&2
  exit 1
}
$compose run --rm --no-deps -v "$artifacts:/artifacts" frontend-test sh -c \
  'cp src/lib/types.generated.ts /tmp/types.generated.ts && cp /artifacts/openapi.json openapi.json && npm run generate:api && cmp /tmp/types.generated.ts src/lib/types.generated.ts' || {
  echo "frontend/src/lib/types.generated.ts is stale" >&2
  exit 1
}

echo "==> Frontend unit, type, and production-build checks"
$compose run --rm --no-deps frontend-test npm test
$compose run --rm --no-deps frontend-test npm run typecheck
$compose run --rm --no-deps frontend-test npm run build

for group in search investigations monitors graph; do
  echo "==> Browser workflows: $group"
  NEWSINTEL_E2E_ARTIFACTS="$artifacts/e2e-$group" infra/test-e2e.sh "$group"
done

echo "Docker test gate passed; diagnostics directory: $artifacts"
