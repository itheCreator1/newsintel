#!/bin/sh
# Fast local loop: unit-only backend tests plus the static/contract/frontend checks, no service
# containers. Not a substitute for infra/test-docker.sh (the mandatory full-regression gate) --
# excludes npm run build, migrations, all integration tests, restore rehearsal and every browser
# group. Every command runs --no-deps; nothing but backend-test/frontend-test is ever started.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"
project=$(ni_project quick)
compose="docker compose -p $project -f docker/compose.test.yaml -f docker/compose.quick.yaml"
# Run-scoped tags, same convention as test-docker.sh: never fall back to the shared :local tag,
# so a concurrent full-gate or integration run can't clobber this run's images (or vice versa).
export NEWSINTEL_IMAGE_BACKEND_TEST="newsintel-backend-test:$project"
export NEWSINTEL_IMAGE_FRONTEND_TEST="newsintel-frontend-test:$project"
ni_report_init "${NEWSINTEL_TEST_ARTIFACTS:-}" "$root" quick

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

ni_stage build.backend-test
$compose build backend-test
ni_stage build.frontend-test
$compose build frontend-test

ni_stage ruff
$compose run --rm --no-deps backend-test ruff check .
ni_stage mypy
$compose run --rm --no-deps backend-test mypy app

ni_stage pytest
$compose run --rm --no-deps -v "$artifacts:/artifacts" backend-test \
  bash -o pipefail -c '
    python -m pytest -q -rs $(python tests/classify.py --paths unit) | tee /artifacts/pytest.log
    pytest_status=${PIPESTATUS[0]}
    [ "$pytest_status" -eq 0 ] || exit "$pytest_status"
    ! grep -Eq "[0-9]+ skipped" /artifacts/pytest.log
  '

ni_stage contract.openapi
$compose run --rm --no-deps -v "$artifacts:/artifacts" backend-test \
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

echo "Quick test loop passed: project=$project artifacts=$artifacts"
