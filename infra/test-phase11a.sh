#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase11a-$run_id"
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

# Migration round trip: 0010 adds only monitors, and its downgrade leaves the rest untouched.
migrate upgrade head
[ "$(tests_psql "select to_regclass('monitors') is not null")" = t ] || { echo "0010 did not create monitors" >&2; exit 1; }
saved_before=$(tests_psql "select count(*) from saved_searches")
migrate downgrade 0009
[ "$(tests_psql "select to_regclass('monitors') is null")" = t ] || { echo "Phase 11A downgrade kept monitors" >&2; exit 1; }
[ "$(tests_psql "select to_regclass('saved_searches') is not null")" = t ] || { echo "Phase 11A downgrade removed saved_searches" >&2; exit 1; }
[ "$saved_before" = "$(tests_psql "select count(*) from saved_searches")" ] || { echo "Phase 11A downgrade changed saved searches" >&2; exit 1; }
migrate upgrade head
[ "$(tests_psql "select to_regclass('monitors') is not null")" = t ] || { echo "0010 did not re-apply" >&2; exit 1; }

host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# Monitors are PostgreSQL-only, so point Elasticsearch at an unreachable port to prove it.
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL=http://127.0.0.1:1 NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q tests/test_monitors.py tests/test_saved_searches.py tests/test_phase11a_postgres.py) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m ruff check app tests/test_monitors.py tests/test_phase11a_postgres.py migrations)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# No routes yet, so the API contract and generated frontend types must not change.
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > frontend/openapi.json
(cd frontend && npm run generate:api && npm run typecheck)
git diff --exit-code -- frontend/openapi.json frontend/src/lib/types.generated.ts

echo "Phase 11A acceptance passed: project=$project artifacts=$artifacts"
