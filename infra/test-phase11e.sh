#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase11e-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_PORT=${NEWSINTEL_PORT:-$(pick_port)}
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_FIXTURE_PORT=${NEWSINTEL_TEST_FIXTURE_PORT:-$(pick_port)}
NEWSINTEL_TEST_ELASTICSEARCH_PORT=${NEWSINTEL_TEST_ELASTICSEARCH_PORT:-$(pick_port)}
export NEWSINTEL_PORT NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT
export NEWSINTEL_E2E_BASE_URL="http://127.0.0.1:$NEWSINTEL_PORT"
export NEWSINTEL_E2E_OUTPUT_DIR="$artifacts/playwright"
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "Acceptance artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml config --quiet
$compose build api worker scheduler frontend

(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q tests/test_monitors.py tests/test_monitor_api.py tests/test_monitor_evaluation.py tests/test_monitor_changes.py)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen ruff check .)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# The frontend suite is flaky-sensitive (Phase 11E removed an unmounted-component race), so run it repeatedly.
for attempt in 1 2 3; do (cd frontend && npm test) || { echo "npm test failed on attempt $attempt" >&2; exit 1; }; done
(cd frontend && npm run typecheck)
(cd frontend && npm run build)
# The checked-in contract must match the application (Phase 11E adds GET /monitors/{id}/changes).
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > "$artifacts/openapi.json"
cmp "$artifacts/openapi.json" frontend/openapi.json || { echo "frontend/openapi.json is stale" >&2; exit 1; }

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head
# 11E adds no schema: the head must still be 0010.
$compose run --rm api alembic heads | tr -d '\r' | grep -q '^0010 (head)' || { echo "Phase 11E must not add a migration" >&2; exit 1; }
# The change summaries read real Elasticsearch aggregations and PostgreSQL rows, so unit tests alone cannot prove them.
$compose exec -T postgres createdb -U newsintel newsintel_tests
$compose run --rm -e NEWSINTEL_DATABASE_URL="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q -rs tests/test_monitor_changes_elasticsearch.py) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
# The monitor workflow spec (extended in 11E) keeps the user it was written with.
printf 'phase11d-password\nphase11d-password\n' | $compose run --rm -T api python -m app.cli create-user phase11d
$compose up -d --build api worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

# Monitors count by `first_discovered_at`, which needs the current (v3) index; build it while the archive is empty.
rebuild_output=$($compose run --rm worker python -m app.cli rebuild-search)
echo "$rebuild_output"
echo "$rebuild_output" | grep -q "status=completed" || { echo "Search rebuild did not complete" >&2; exit 1; }

(cd frontend && npm run e2e -- --grep "monitor workflow")

echo "Phase 11E acceptance passed: project=$project artifacts=$artifacts"
