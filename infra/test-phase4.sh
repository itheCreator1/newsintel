#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase4-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_PORT=$(pick_port)
NEWSINTEL_TEST_POSTGRES_PORT=$(pick_port)
NEWSINTEL_TEST_FIXTURE_PORT=$(pick_port)
NEWSINTEL_TEST_ELASTICSEARCH_PORT=$(pick_port)
export NEWSINTEL_PORT NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT
export NEWSINTEL_E2E_BASE_URL="http://127.0.0.1:$NEWSINTEL_PORT"
export NEWSINTEL_E2E_OUTPUT_DIR="$artifacts/playwright"
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml"
cleanup() { status=$?; if [ "$status" -ne 0 ]; then $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true; echo "Acceptance artifacts: $artifacts" >&2; fi; $compose down -v --remove-orphans >/dev/null 2>&1 || true; exit "$status"; }
trap cleanup EXIT INT TERM

cd "$root"
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml config --quiet
$compose build api worker scheduler frontend
$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests
test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic downgrade 0003
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# tests/test_phase7_postgres.py (added on the Phase 7 branch, collected here too since this runs
# the whole tests/ directory) needs a real Elasticsearch reachable from this host-side pytest
# process, the same way NEWSINTEL_DATABASE_URL above overrides the host-mapped Postgres.
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen pytest -q -rs tests) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
$compose run --rm api alembic upgrade head
printf 'phase4-password\nphase4-password\n' | $compose run --rm -T api python -m app.cli create-user phase4
printf 'phase3-password\nphase3-password\n' | $compose run --rm -T api python -m app.cli create-user phase3
$compose up -d --build api worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done
$compose stop elasticsearch
(cd frontend && npm run e2e -- --grep "failure and retry workflow")
$compose start elasticsearch
deadline=$(( $(date +%s) + 120 ))
until $compose exec -T elasticsearch curl -fsS http://127.0.0.1:9200/_cluster/health >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Elasticsearch recovery timed out" >&2; exit 1; }; sleep 1; done
rebuild_output=$($compose run --rm worker python -m app.cli rebuild-search)
echo "$rebuild_output"
rebuild_id=$(echo "$rebuild_output" | sed -n 's/.*rebuild_id=\([^ ]*\).*/\1/p')
[ -n "$rebuild_id" ] || { echo "Rebuild id missing" >&2; exit 1; }
deadline=$(( $(date +%s) + 90 ))
while :; do
  outstanding=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from search_deliveries where status in ('queued','running','retrying')")
  failed=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from search_deliveries where status='failed'")
  [ "$failed" = 0 ] || { echo "Indexing has $failed permanent failures" >&2; exit 1; }
  [ "$outstanding" = 0 ] && break
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Indexing backlog timed out" >&2; exit 1; }
  sleep 1
done
$compose run --rm worker python -m app.cli resume-search-rebuild "$rebuild_id"
(cd frontend && npm run e2e -- --grep "search restores URL state")
$compose exec -T postgres psql -U newsintel -d newsintel -c \
  "EXPLAIN SELECT id FROM search_deliveries WHERE status IN ('queued','retrying','running') AND next_attempt_at <= now() ORDER BY next_attempt_at, id LIMIT 100" \
  > "$artifacts/query-plan.txt"
cat "$artifacts/query-plan.txt"
echo "Phase 4 acceptance passed: project=$project artifacts=$artifacts"
