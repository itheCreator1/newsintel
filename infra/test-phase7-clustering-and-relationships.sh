#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase7-$run_id"
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
# Ruling R5: unlike Phase 5/6 (NER smoke-tested in isolation, then every real workflow run with
# NER disabled), the graph endpoint needs real entities in every document, so compose.ner.yaml
# is merged into the ONE compose stack used for the entire script, not a separate short-lived
# stack that gets torn down after a smoke test.
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
psql_app() { $compose exec -T postgres psql -U newsintel -d newsintel -Atc "$1"; }

cd "$root"
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml config --quiet
docker compose -f docker/compose.yaml -f docker/compose.e2e.yaml -f docker/compose.ner.yaml config --quiet
$compose build api worker nlp-worker scheduler frontend

# Fail fast (in ~1 minute, not 10) if the NER-enabled image can't actually extract the two
# entity kinds every fixture and assertion in this script depends on. This mirrors the positive
# half of test-phase5-versioned-nlp.sh's smoke assertion; unlike Phase 5, there is no negative "NER absent in
# the default image" half here, because this script never builds a non-NER nlp-worker.
$compose run --rm --no-deps nlp-worker python -c "
import spacy
nlp = spacy.load('en_core_web_sm')
doc = nlp('Barack Obama met Microsoft executives.')
entities = {(e.text, e.label_) for e in doc.ents}
assert ('Barack Obama', 'PERSON') in entities and ('Microsoft', 'ORG') in entities
print('spaCy', spacy.__version__, nlp.meta['version'], sorted(entities))
" > "$artifacts/ner-model.log"
cat "$artifacts/ner-model.log"

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests
test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# tests/test_clustering_postgres.py's real-Elasticsearch tests run in the host-side pytest process
# against a real adapter (see the Task 3 carried-forward note in the brief), so
# NEWSINTEL_ELASTICSEARCH_URL needs a host-reachable override, the same way the host database URL
# already overrides NEWSINTEL_DATABASE_URL above. test-phase6-investigations.sh gets the identical three lines
# below since the same shared test file is collected there too; the Phase 3, 4 and 5 scripts do not and are
# reported as a follow-up in this task's report instead.
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen pytest -q -rs tests) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
tests_psql() { $compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "$1"; }
archive_before=$(tests_psql "select count(*) from articles")
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic downgrade 0007
[ "$(tests_psql "select to_regclass('story_clusters') is null")" = t ] || { echo "Phase 7 downgrade kept story_clusters" >&2; exit 1; }
[ "$(tests_psql "select to_regclass('story_cluster_members') is null")" = t ] || { echo "Phase 7 downgrade kept story_cluster_members" >&2; exit 1; }
[ "$(tests_psql "select to_regclass('cluster_jobs') is null")" = t ] || { echo "Phase 7 downgrade kept cluster_jobs" >&2; exit 1; }
[ "$(tests_psql "select to_regclass('article_cluster_state') is null")" = t ] || { echo "Phase 7 downgrade kept article_cluster_state" >&2; exit 1; }
[ "$archive_before" = "$(tests_psql "select count(*) from articles")" ] || { echo "Phase 7 downgrade changed canonical article count" >&2; exit 1; }
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head

(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen ruff check .)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen mypy app)
(cd frontend && npm test)
(cd frontend && npm run typecheck)
(cd frontend && npm run build)

$compose run --rm api alembic upgrade head
printf 'phase7-password\nphase7-password\n' | $compose run --rm -T api python -m app.cli create-user phase7
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

(cd frontend && npm run e2e -- --grep "relationships seed")

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

# psql assertions: exactly two clusters, each with exactly two members from distinct feeds, and
# the one unrelated fixture article carries no cluster membership at all.
[ "$(psql_app "select count(*) from story_clusters")" = 2 ] || { echo "Expected exactly 2 story clusters" >&2; exit 1; }
[ "$(psql_app "select count(*) from story_cluster_members")" = 4 ] || { echo "Expected exactly 4 clustered articles" >&2; exit 1; }
malformed_clusters=$(psql_app "
  select count(*) from (
    select scm.cluster_id
    from story_cluster_members scm
    join feed_articles fa on fa.article_id = scm.article_id
    group by scm.cluster_id
    having count(*) <> 2 or count(distinct fa.feed_id) <> 2
  ) t
")
[ "$malformed_clusters" = 0 ] || { echo "A story cluster did not have exactly 2 members from 2 distinct feeds" >&2; exit 1; }
unrelated_article_id=$(psql_app "select article_id from feed_articles where guid = 'relationships-wire-3'")
[ -n "$unrelated_article_id" ] || { echo "Unrelated fixture article not found" >&2; exit 1; }
[ "$(psql_app "select count(*) from story_cluster_members where article_id = '$unrelated_article_id'")" = 0 ] || { echo "Unrelated fixture article unexpectedly joined a cluster" >&2; exit 1; }

# An authenticated API assertion that nodes=50 stays within the backend's absolute bounds
# (<=50 nodes, <=150 edges). There is no existing precedent for an authenticated API check
# outside the browser in prior phase gates; a curl session (csrf token + cookie jar, mirroring
# the frontend's own login flow) is the simplest way to call a real endpoint without adding a
# throwaway Python one-liner that reimplements FastAPI's dependency wiring.
cookiejar="$artifacts/cookies.txt"
csrf_token=$(curl -sS -c "$cookiejar" "$NEWSINTEL_E2E_BASE_URL/api/v1/auth/csrf" | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])')
curl -sS -b "$cookiejar" -c "$cookiejar" -H "Content-Type: application/json" -H "X-CSRF-Token: $csrf_token" \
  -d '{"username":"phase7","password":"phase7-password"}' "$NEWSINTEL_E2E_BASE_URL/api/v1/auth/login" > /dev/null
curl -sS -b "$cookiejar" "$NEWSINTEL_E2E_BASE_URL/api/v1/graph/entities?nodes=50" > "$artifacts/graph.json"
python3 -c "
import json
with open('$artifacts/graph.json') as handle:
    data = json.load(handle)
nodes, edges = data['nodes'], data['edges']
assert 1 <= len(nodes) <= 50, f'expected 1..50 nodes, got {len(nodes)}'
assert len(edges) <= 150, f'expected at most 150 edges, got {len(edges)}'
print(f'graph bounded check ok: nodes={len(nodes)} edges={len(edges)}')
"

echo "Phase 7 acceptance passed: project=$project artifacts=$artifacts"
