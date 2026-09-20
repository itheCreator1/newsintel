#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase11c-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_FIXTURE_PORT=${NEWSINTEL_TEST_FIXTURE_PORT:-$(pick_port)}
NEWSINTEL_TEST_ELASTICSEARCH_PORT=${NEWSINTEL_TEST_ELASTICSEARCH_PORT:-$(pick_port)}
export NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT
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
$compose build api
$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests

test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
tests_psql() { $compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "$1"; }
migrate() { $compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic "$@"; }

# 11C adds no schema: the head must still be 0010 and the monitors table must exist.
migrate upgrade head
migrate heads | tr -d '\r' | grep -q '^0010 (head)' || { echo "Phase 11C must not add a migration" >&2; exit 1; }
[ "$(tests_psql "select to_regclass('monitors') is not null")" = t ] || { echo "monitors table missing" >&2; exit 1; }

host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
# The evaluation tests use a real index, so Elasticsearch must be reachable from the host.
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q -rs tests/test_monitors.py tests/test_monitor_api.py tests/test_monitor_evaluation.py tests/test_saved_searches.py tests/test_search_results.py tests/test_phase11a_postgres.py tests/test_phase11b_postgres.py tests/test_phase11b_elasticsearch.py tests/test_phase11c_postgres.py tests/test_phase11c_elasticsearch.py) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m ruff check app tests/test_monitors.py tests/test_monitor_api.py tests/test_monitor_evaluation.py tests/test_search_results.py tests/test_phase11a_postgres.py tests/test_phase11b_postgres.py tests/test_phase11b_elasticsearch.py tests/test_phase11c_postgres.py tests/test_phase11c_elasticsearch.py migrations)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# The checked-in OpenAPI document and generated types must already match the backend.
cp frontend/openapi.json "$artifacts/openapi.before"
cp frontend/src/lib/types.generated.ts "$artifacts/types.before"
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > frontend/openapi.json
(cd frontend && npm run generate:api && npm test && npm run typecheck)
cmp frontend/openapi.json "$artifacts/openapi.before" || { echo "frontend/openapi.json is stale" >&2; exit 1; }
cmp frontend/src/lib/types.generated.ts "$artifacts/types.before" || { echo "generated types are stale" >&2; exit 1; }

echo "Phase 11C acceptance passed: project=$project artifacts=$artifacts"
