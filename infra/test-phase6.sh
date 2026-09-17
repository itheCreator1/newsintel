#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase6-$run_id"
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
psql_app() { $compose exec -T postgres psql -U newsintel -d newsintel -Atc "$1"; }

cd "$root"
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml config --quiet
$compose build api worker nlp-worker scheduler frontend

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests
test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# tests/test_phase7_postgres.py (added on the Phase 7 branch, collected here too since this runs
# the whole tests/ directory) needs a real Elasticsearch reachable from this host-side pytest
# process, the same way the line above overrides the database URL for the host-mapped Postgres.
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen pytest -q -rs tests) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
tests_psql() { $compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "$1"; }
archive_before=$(tests_psql "select count(*) from articles")
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic downgrade 0006
[ "$(tests_psql "select to_regclass('saved_searches') is null")" = t ] || { echo "Phase 6 downgrade kept saved_searches" >&2; exit 1; }
[ "$archive_before" = "$(tests_psql "select count(*) from articles")" ] || { echo "Phase 6 downgrade changed canonical article count" >&2; exit 1; }
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head

(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen ruff check .)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen mypy app)
(cd frontend && npm test)
(cd frontend && npm run typecheck)
(cd frontend && npm run build)

$compose run --rm api alembic upgrade head
printf 'phase6-password\nphase6-password\n' | $compose run --rm -T api python -m app.cli create-user phase6
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

(cd frontend && npm run e2e -- --grep "investigation seed")
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

(cd frontend && npm run e2e -- --grep "investigation workflow")
# Simulate a filter retired by a later release so the stored state no longer validates.
[ "$(psql_app "with stale as (update saved_searches set state = state || '{\"retired_filter\": true}' where name = 'Stale investigation' returning id) select count(*) from stale")" = 1 ] || { echo "Stale saved search missing" >&2; exit 1; }
(cd frontend && npm run e2e -- --grep "invalid saved search")
[ "$(psql_app "select count(*) from saved_searches")" = 0 ] || { echo "Saved searches were not deleted" >&2; exit 1; }
echo "Phase 6 acceptance passed: project=$project artifacts=$artifacts"
