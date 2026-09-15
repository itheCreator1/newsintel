#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase5-$run_id"
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
compose="docker compose -p $project -f compose.yaml -f compose.e2e.yaml"
compose_ner="docker compose -p $project-ner -f compose.yaml -f compose.e2e.yaml -f compose.ner.yaml"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "Acceptance artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  $compose_ner down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
docker compose -f compose.yaml -f compose.e2e.yaml config --quiet
docker compose -f compose.yaml -f compose.e2e.yaml -f compose.ner.yaml config --quiet
$compose build api worker nlp-worker scheduler frontend
$compose run --rm --no-deps nlp-worker python -c "import importlib.util; assert importlib.util.find_spec('spacy') is None"
$compose_ner build nlp-worker
$compose_ner run --rm --no-deps nlp-worker python -c "import spacy; nlp=spacy.load('en_core_web_sm'); doc=nlp('Barack Obama met Microsoft executives.'); entities={(e.text,e.label_) for e in doc.ents}; assert ('Barack Obama','PERSON') in entities and ('Microsoft','ORG') in entities; print('spaCy',spacy.__version__,nlp.meta['version'],sorted(entities))" > "$artifacts/ner-model.log"
cat "$artifacts/ner-model.log"
$compose_ner down -v --remove-orphans

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose exec -T postgres createdb -U newsintel newsintel_tests
test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head
host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
# tests/test_phase7_postgres.py (added on the Phase 7 branch, collected here too since this runs
# the whole tests/ directory) needs a real Elasticsearch reachable from this host-side pytest
# process, the same way NEWSINTEL_DATABASE_URL above overrides the host-mapped Postgres.
host_elasticsearch_url="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 NEWSINTEL_DATABASE_URL="$host_test_database" NEWSINTEL_ELASTICSEARCH_URL="$host_elasticsearch_url" NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen pytest -q -rs tests) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then echo "Required backend tests were skipped" >&2; exit 1; fi
archive_before=$($compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "select count(*) from articles")
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic downgrade 0004
archive_after=$($compose exec -T postgres psql -U newsintel -d newsintel_tests -Atc "select count(*) from articles")
[ "$archive_before" = "$archive_after" ] || { echo "Phase 5 downgrade changed canonical article count" >&2; exit 1; }
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head

(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen ruff check .)
(cd backend && UV_CACHE_DIR="$root/backend/.uv-cache" uv run --frozen mypy app)
(cd frontend && npm test)
(cd frontend && npm run typecheck)
(cd frontend && npm run build)

$compose run --rm api alembic upgrade head
printf 'phase5-password\nphase5-password\n' | $compose run --rm -T api python -m app.cli create-user phase5
printf 'phase4-password\nphase4-password\n' | $compose run --rm -T api python -m app.cli create-user phase4
printf 'phase3-password\nphase3-password\n' | $compose run --rm -T api python -m app.cli create-user phase3
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

$compose stop elasticsearch
(cd frontend && npm run e2e -- --grep "failure and retry workflow")
deadline=$(( $(date +%s) + 120 ))
while :; do
  outstanding=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from nlp_jobs where status in ('queued','running','retrying')")
  succeeded=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from nlp_jobs where status='succeeded'")
  [ "$outstanding" = 0 ] && [ "$succeeded" -gt 0 ] && break
  [ "$(date +%s)" -lt "$deadline" ] || { echo "NLP backlog timed out during Elasticsearch outage" >&2; exit 1; }
  sleep 1
done
$compose start elasticsearch
deadline=$(( $(date +%s) + 120 ))
until $compose exec -T elasticsearch curl -fsS http://127.0.0.1:9200/_cluster/health >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Elasticsearch recovery timed out" >&2; exit 1; }; sleep 1; done
rebuild_output=$($compose run --rm worker python -m app.cli rebuild-search)
echo "$rebuild_output"
rebuild_id=$(echo "$rebuild_output" | sed -n 's/.*rebuild_id=\([^ ]*\).*/\1/p')
[ -n "$rebuild_id" ] || { echo "Rebuild id missing" >&2; exit 1; }
deadline=$(( $(date +%s) + 120 ))
while :; do
  outstanding=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from search_deliveries where status in ('queued','running','retrying')")
  failed=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc "select count(*) from search_deliveries where status='failed'")
  [ "$failed" = 0 ] || { echo "Indexing has $failed permanent failures" >&2; exit 1; }
  [ "$outstanding" = 0 ] && break
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Indexing backlog timed out" >&2; exit 1; }
  sleep 1
done
$compose run --rm worker python -m app.cli resume-search-rebuild "$rebuild_id"

dry_run=$($compose run --rm nlp-worker python -m app.cli reprocess-nlp --processors keywords --all)
echo "$dry_run"
echo "$dry_run" | grep -q '^dry-run:'
reprocess_output=$($compose run --rm nlp-worker python -m app.cli reprocess-nlp --processors keywords --all --apply)
echo "$reprocess_output"
reprocess_id=$(echo "$reprocess_output" | sed -n 's/.*run_id=\([^ ]*\).*/\1/p')
[ -n "$reprocess_id" ] || { echo "NLP reprocessing run id missing" >&2; exit 1; }
$compose run --rm nlp-worker python -m app.cli resume-nlp-reprocessing "$reprocess_id"
$compose run --rm nlp-worker python -m app.cli nlp-status

(cd frontend && npm run e2e -- --grep "search restores URL state|annotations refine search")
$compose exec -T postgres psql -U newsintel -d newsintel -c \
  "EXPLAIN SELECT id FROM nlp_jobs WHERE status IN ('queued','retrying','running') AND next_attempt_at <= now() ORDER BY next_attempt_at, id LIMIT 100" \
  > "$artifacts/query-plan.txt"
$compose exec -T postgres psql -U newsintel -d newsintel -c \
  "EXPLAIN SELECT entity_id FROM article_nlp_entities WHERE article_id = (SELECT id FROM articles ORDER BY id LIMIT 1) AND is_current IS true ORDER BY relevance DESC LIMIT 50" \
  >> "$artifacts/query-plan.txt"
cat "$artifacts/query-plan.txt"
$compose exec -T nlp-worker python -c "import resource,time; from app.nlp.input import InputSection; from app.nlp.processors import ProcessorContext,extract_keywords; text=('Energy policy markets and renewable investment reporting. '*1000); start=time.perf_counter(); result=extract_keywords(ProcessorContext(text=text,input_fingerprint='a'*64,sections=(InputSection('body',None,0,len(text)),),language='en',stop_words=frozenset(),ner_enabled=False,ner_model='')); print('characters',len(text),'keywords',len(result.keywords),'seconds',round(time.perf_counter()-start,3),'max_rss_kib',resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)" > "$artifacts/nlp-measurement.txt"
$compose stats --no-stream nlp-worker >> "$artifacts/nlp-measurement.txt"
cat "$artifacts/nlp-measurement.txt"
echo "Phase 5 acceptance passed: project=$project artifacts=$artifacts"
