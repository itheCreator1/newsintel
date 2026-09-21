#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase13d-$run_id"
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
# Sources link entities and stories, so real NER runs in the one stack (as in Phases 10B and 12D).
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml -f docker/compose.ner.yaml"
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
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml -f docker/compose.ner.yaml config --quiet
$compose build api worker nlp-worker scheduler frontend
$compose up -d --wait --wait-timeout 120 postgres fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests

test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
tests_psql() { $compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "$1"; }
migrate() { $compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic "$@"; }

# 13D adds migration 0013 (event association runs and four indexes on finish times); prove it round-trips.
migrate upgrade head
[ "$(tests_psql "select version_num from alembic_version")" = 0014 ] || { echo "The migration head must be 0014 (0014 follows 13D)" >&2; exit 1; }
idx() { tests_psql "select count(*) from pg_indexes where indexname in ('ix_event_association_runs_started','ix_article_jobs_completed','ix_cluster_jobs_completed','ix_nlp_runs_completed','ix_feed_fetches_started')"; }
[ "$(idx)" = 5 ] || { echo "0013 did not create its indexes" >&2; exit 1; }
migrate downgrade 0012
[ "$(idx)" = 0 ] || { echo "0013 downgrade left indexes behind" >&2; exit 1; }
[ "$(tests_psql "select count(*) from pg_tables where tablename = 'event_association_runs'")" = 0 ] || { echo "0013 downgrade left its table" >&2; exit 1; }
migrate upgrade head
[ "$(idx)" = 5 ] || { echo "0013 did not re-apply" >&2; exit 1; }

host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# The operations API must answer with Elasticsearch unreachable (its probe reports Down), so point it at a closed port.
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL=http://127.0.0.1:1 NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q tests/test_operations_api.py tests/test_phase13d_postgres.py tests/test_event_engine.py tests/test_events.py tests/test_event_api.py tests/test_phase12a_postgres.py tests/test_phase12b_postgres.py tests/test_phase12c_postgres.py tests/test_geo_api.py tests/test_phase13c_postgres.py tests/test_phase13b_postgres.py) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m ruff check app tests/test_operations_api.py tests/test_phase13d_postgres.py tests/event_fixtures.py migrations)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# The operations routes change the API contract, so regenerate it and the frontend types, then prove the
# committed copies match (a stale contract would fail here).
cp frontend/openapi.json "$artifacts/openapi.before.json"
cp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts"
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > frontend/openapi.json
(cd frontend && npm run generate:api)
cmp frontend/openapi.json "$artifacts/openapi.before.json" || { echo "frontend/openapi.json was stale" >&2; exit 1; }
cmp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts" || { echo "generated types were stale" >&2; exit 1; }

# The frontend suite is flaky-sensitive (Phase 11E removed an unmounted-component race), so run it repeatedly.
for attempt in 1 2 3; do (cd frontend && npm test) > "$artifacts/vitest-$attempt.log" 2>&1 || { cat "$artifacts/vitest-$attempt.log"; echo "npm test failed on attempt $attempt" >&2; exit 1; }; done
tail -n 8 "$artifacts/vitest-3.log"
(cd frontend && npm run typecheck)
(cd frontend && npm run build)

# The rest of the backend suite still passes with the new routes.
(cd backend && NEWSINTEL_DATABASE_URL="$host_test_database" UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q) > "$artifacts/pytest-full.log"
tail -3 "$artifacts/pytest-full.log"

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head
$compose run --rm api alembic heads | tr -d '\r' | grep -q '^0014 (head)' || { echo "The migration head must be 0014 (0014 follows 13D)" >&2; exit 1; }
# `relationships seed` signs in as phase7; the entity spec uses phase10b, the event spec phase12d, the source spec phase13a, the compare spec phase13b, the map spec phase13d.
printf 'phase7-password\nphase7-password\n' | $compose run --rm -T api python -m app.cli create-user phase7
printf 'phase10b-password\nphase10b-password\n' | $compose run --rm -T api python -m app.cli create-user phase10b
printf 'phase12d-password\nphase12d-password\n' | $compose run --rm -T api python -m app.cli create-user phase12d
printf 'phase13a-password\nphase13a-password\n' | $compose run --rm -T api python -m app.cli create-user phase13a
printf 'phase13b-password\nphase13b-password\n' | $compose run --rm -T api python -m app.cli create-user phase13b
printf 'phase13c-password\nphase13c-password\n' | $compose run --rm -T api python -m app.cli create-user phase13c
printf 'phase13d-password\nphase13d-password\n' | $compose run --rm -T api python -m app.cli create-user phase13d
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

# The map spec follows a country into search, which needs the current index; build it while the archive is empty
# (as in 11E), so the fixture articles are indexed as they arrive.
rebuild_output=$($compose run --rm worker python -m app.cli rebuild-search)
echo "$rebuild_output"
echo "$rebuild_output" | grep -q "status=completed" || { echo "Search rebuild did not complete" >&2; exit 1; }

(cd frontend && npm run e2e -- --grep "relationships seed")
(cd frontend && npm run e2e -- --grep "operations workflow")
(cd frontend && npm run e2e -- --grep "map workflow")
# The compare, source, event and entity specs prove the shared pages and the new navigation still work.
(cd frontend && npm run e2e -- --grep "compare workflow")
(cd frontend && npm run e2e -- --grep "source dossier workflow")
(cd frontend && npm run e2e -- --grep "event workflow")
(cd frontend && npm run e2e -- --grep "entity dossier workflow")
# The grouped navigation, feedback states and filter chips (UI polish), while search still has Elasticsearch.
(cd frontend && npm run e2e -- --grep "ui polish workflow")
# Last: stop Elasticsearch, which the page must report as Down while every other panel still renders.
$compose stop elasticsearch
(cd frontend && npm run e2e -- --grep "stopped Elasticsearch")

echo "Phase 13D acceptance passed: project=$project artifacts=$artifacts"
