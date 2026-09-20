#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase10c-$run_id"
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
# The edge-evidence workflow needs real extracted entities, so NER is merged into the one stack (as in Phase 7).
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml -f docker/compose.ner.yaml"
psql_app() { $compose exec -T postgres psql -U newsintel -d newsintel -Atc "$1"; }
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

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head

# Backend: focused evidence tests plus the graph suite, PostgreSQL hydration against a scratch database.
$compose exec -T postgres createdb -U newsintel newsintel_tests
$compose run --rm -e NEWSINTEL_DATABASE_URL="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
backend_tests="tests/test_graph_edge_evidence.py tests/test_graph_entities.py tests/test_phase10c_postgres.py"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL=http://127.0.0.1:1 NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m pytest -q $backend_tests) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m ruff check app tests/test_graph_edge_evidence.py tests/test_phase10c_postgres.py)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen python -m mypy app)

# The checked-in contract must match the API; regenerate into scratch files and compare.
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > "$artifacts/openapi.json"
diff "$artifacts/openapi.json" frontend/openapi.json
(cd frontend && npx openapi-typescript "$artifacts/openapi.json" -o "$artifacts/types.generated.ts")
diff "$artifacts/types.generated.ts" frontend/src/lib/types.generated.ts

(cd frontend && npm test)
(cd frontend && npm run typecheck)
(cd frontend && npm run build)
# `relationships seed` (reused fixture collection) signs in as phase7; the evidence spec uses phase10c.
printf 'phase7-password\nphase7-password\n' | $compose run --rm -T api python -m app.cli create-user phase7
printf 'phase10c-password\nphase10c-password\n' | $compose run --rm -T api python -m app.cli create-user phase10c
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

(cd frontend && npm run e2e -- --grep "relationships seed")
# The graph and its evidence read the schema-3 search index, which is built by a rebuild after seeding.
rebuild_output=$($compose run --rm worker python -m app.cli rebuild-search)
echo "$rebuild_output"
rebuild_id=$(echo "$rebuild_output" | sed -n 's/.*rebuild_id=\([^ ]*\).*/\1/p')
[ -n "$rebuild_id" ] || { echo "Rebuild id missing" >&2; exit 1; }
deadline=$(( $(date +%s) + 120 ))
while :; do
  outstanding=$(psql_app "select count(*) from search_deliveries where status in ('queued','running','retrying')")
  failed=$(psql_app "select count(*) from search_deliveries where status='failed'")
  [ "$failed" = 0 ] || { echo "Indexing has $failed permanent failures" >&2; exit 1; }
  [ "$outstanding" = 0 ] && break
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Indexing backlog timed out" >&2; exit 1; }
  sleep 1
done
$compose run --rm worker python -m app.cli resume-search-rebuild "$rebuild_id"
[ "$(psql_app "select count(*) from search_index_targets where role='current' and schema_version=3")" = 1 ] || { echo "Search cutover did not reach schema version 3" >&2; exit 1; }

(cd frontend && npm run e2e -- --grep "relationships workflow")
(cd frontend && npm run e2e -- --grep "graph edge evidence workflow")

echo "Phase 10C acceptance passed: project=$project artifacts=$artifacts"
