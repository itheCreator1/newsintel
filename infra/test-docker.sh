#!/bin/sh
# Full repository validation using Docker only.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"
project=$(ni_project test)
compose="docker compose -p $project -f docker/compose.test.yaml"
ni_report_init "${NEWSINTEL_TEST_ARTIFACTS:-}" "$root" test

cleanup() {
  status=$?
  ni_report_finalize "$status"
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
ni_env_report "$root" "${NEWSINTEL_CACHE_STATE:-warm}"
ni_mem_start "$project"

ni_stage build.all
$compose build backend-test frontend-test
{
  printf 'service_image\timage_id\n'
  $compose config --images | while read -r ni_img; do
    printf '%s\t%s\n' "$ni_img" "$(docker image inspect --format '{{.Id}}' "$ni_img" 2>/dev/null || echo unknown)"
  done
} > "$artifacts/images.tsv"

ni_stage deps.up
$compose up -d --wait --wait-timeout 180 test-postgres test-redis test-elasticsearch test-fixture

ni_stage ruff
$compose run --rm backend-test ruff check .
ni_stage mypy
$compose run --rm backend-test mypy app
ni_stage alembic.upgrade
$compose run --rm backend-test alembic upgrade head
ni_stage alembic.single-head
ni_single_head "$compose" backend-test "Migrations must have exactly one head"

ni_stage pytest
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

ni_stage restore
infra/test-restore.sh "$($compose ps -q test-postgres)" newsintel_tests

ni_stage contract.openapi
$compose run --rm -v "$artifacts:/artifacts" backend-test \
  sh -c "python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > /artifacts/openapi.json"
cmp frontend/openapi.json "$artifacts/openapi.json" || {
  echo "frontend/openapi.json is stale" >&2
  exit 1
}

ni_stage contract.types
$compose run --rm --no-deps -v "$artifacts:/artifacts" frontend-test sh -c \
  'cp src/lib/types.generated.ts /tmp/types.generated.ts && cp /artifacts/openapi.json openapi.json && npm run generate:api && cmp /tmp/types.generated.ts src/lib/types.generated.ts' || {
  echo "frontend/src/lib/types.generated.ts is stale" >&2
  exit 1
}

ni_stage frontend.unit
$compose run --rm --no-deps frontend-test npm test
ni_stage frontend.typecheck
$compose run --rm --no-deps frontend-test npm run typecheck
ni_stage frontend.build
$compose run --rm --no-deps frontend-test npm run build

for group in search investigations monitors graph; do
  ni_stage "e2e.$group"
  NEWSINTEL_E2E_ARTIFACTS="$artifacts/e2e-$group" infra/test-e2e.sh "$group"
done

echo "Docker test gate passed; diagnostics directory: $artifacts"
