#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase12c-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_FIXTURE_PORT=${NEWSINTEL_TEST_FIXTURE_PORT:-$(pick_port)}
export NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT
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
$compose up -d --wait --wait-timeout 120 postgres fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests

test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
tests_psql() { $compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "$1"; }
migrate() { $compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic "$@"; }

# 12C adds no migration: the schema stays at 0012.
migrate upgrade head
[ "$(tests_psql "select version_num from alembic_version")" = 0012 ] || { echo "Phase 12C changed the migration head" >&2; exit 1; }

host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# The event API is PostgreSQL-only, so point Elasticsearch at an unreachable port to prove it.
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL=http://127.0.0.1:1 NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q tests/test_events.py tests/test_event_engine.py tests/test_event_api.py tests/test_phase12a_postgres.py tests/test_phase12b_postgres.py tests/test_phase12c_postgres.py) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m ruff check app tests/test_events.py tests/test_event_engine.py tests/test_event_api.py tests/test_phase12a_postgres.py tests/test_phase12b_postgres.py tests/test_phase12c_postgres.py tests/event_fixtures.py migrations)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# The event routes change the API contract, so regenerate it and the frontend types, then prove the
# committed copies match (a stale contract would fail here).
cp frontend/openapi.json "$artifacts/openapi.before.json"
cp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts"
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > frontend/openapi.json
(cd frontend && npm run generate:api && npm test && npm run typecheck)
cmp frontend/openapi.json "$artifacts/openapi.before.json" || { echo "frontend/openapi.json was stale" >&2; exit 1; }
cmp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts" || { echo "generated types were stale" >&2; exit 1; }

# The rest of the backend suite still passes with the new routes and the shared helpers.
(cd backend && NEWSINTEL_DATABASE_URL="$host_test_database" UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q) > "$artifacts/pytest-full.log"
tail -3 "$artifacts/pytest-full.log"

echo "Phase 12C acceptance passed: project=$project artifacts=$artifacts"
