#!/bin/sh
# Maintained browser suite (Phase 14A.3): runs every Playwright workflow against the current stack.
# Usage: infra/test-e2e.sh <group>, one of search, investigations, monitors, graph.
# Each group gets a fresh Compose project because the specs share fixture feeds and assert exact counts.
# The phase scripts stay as historical records; this one tracks the current migration head.
set -eu

group=${1:?usage: infra/test-e2e.sh search|investigations|monitors|graph}
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
project="newsintel-e2e-$group-$$-$(date +%s)"
artifacts=${NEWSINTEL_E2E_ARTIFACTS:-/tmp/$project}
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_PORT=${NEWSINTEL_PORT:-$(pick_port)}
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_FIXTURE_PORT=${NEWSINTEL_TEST_FIXTURE_PORT:-$(pick_port)}
NEWSINTEL_TEST_ELASTICSEARCH_PORT=${NEWSINTEL_TEST_ELASTICSEARCH_PORT:-$(pick_port)}
export NEWSINTEL_PORT NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT
export NEWSINTEL_E2E_BASE_URL="http://127.0.0.1:$NEWSINTEL_PORT"
export NEWSINTEL_E2E_OUTPUT_DIR="$artifacts/playwright"

files="-f docker/compose.yaml -f docker/compose.e2e.yaml"
# Entities, events, sources, comparison and the map need real NER; the other groups skip its heavy build.
[ "$group" = graph ] && files="$files -f docker/compose.ner.yaml"
compose="docker compose -p $project $files"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "E2E artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
psql_app() { $compose exec -T postgres psql -U newsintel -d newsintel -Atc "$1"; }
e2e() { (cd frontend && npm run e2e -- --grep "$1"); }
users() { for user in "$@"; do printf '%s-password\n%s-password\n' "$user" "$user" | $compose run --rm -T api python -m app.cli create-user "$user"; done; }
wait_until() {  # wait_until <what> <shell condition>
  deadline=$(( $(date +%s) + 120 ))
  until eval "$2"; do [ "$(date +%s)" -lt "$deadline" ] || { echo "$1 timed out" >&2; exit 1; }; sleep 1; done
}
indexing_drained() {
  failed=$(psql_app "select count(*) from search_deliveries where status='failed'")
  [ "$failed" = 0 ] || { echo "Indexing has $failed permanent failures" >&2; exit 1; }
  [ "$(psql_app "select count(*) from search_deliveries where status in ('queued','running','retrying')")" = 0 ]
}
# Rebuild the index from the archive and wait for every delivery (a rebuild on an empty archive completes at once).
rebuild_search() {
  output=$($compose run --rm worker python -m app.cli rebuild-search)
  echo "$output"
  rebuild_id=$(echo "$output" | sed -n 's/.*rebuild_id=\([^ ]*\).*/\1/p')
  [ -n "$rebuild_id" ] || { echo "Rebuild id missing" >&2; exit 1; }
  wait_until "Indexing backlog" indexing_drained
  echo "$output" | grep -q "status=completed" || $compose run --rm worker python -m app.cli resume-search-rebuild "$rebuild_id"
}

$compose up -d --wait --wait-timeout 180 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head
$compose run --rm api alembic heads | tr -d '\r' | grep -q '^0014 (head)' || { echo "The migration head must be 0014" >&2; exit 1; }
case $group in
  search) users phase3 phase4 phase5 ;;
  investigations) users phase6 ;;
  monitors) users phase11d ;;
  # `relationships seed` signs in as phase7; each later spec has its own user.
  graph) users phase7 phase10b phase10c phase12d phase13a phase13b phase13c phase13d ;;
  *) echo "Unknown group: $group" >&2; exit 2 ;;
esac
$compose up -d --build api worker nlp-worker scheduler frontend
wait_until "Application readiness" 'curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1'

case $group in
  search)
    # Processing must not depend on search (Phases 3 and 5): ingest and enrich with Elasticsearch stopped.
    $compose stop elasticsearch
    e2e "failure and retry workflow"
    fixture_content() { psql_app "select c.html_object_key || ':' || c.content_hash || ':' || length(c.text) from article_contents c join articles a on a.id=c.article_id where a.title='Fixture story'"; }
    before=$(fixture_content)
    [ -n "$before" ] || { echo "Retained article content is missing" >&2; exit 1; }
    $compose up -d --force-recreate worker
    $compose exec -T worker test -f "/var/lib/newsintel/articles/${before%%:*}"
    [ "$before" = "$(fixture_content)" ] || { echo "Article content changed after worker recreation" >&2; exit 1; }
    e2e "retained detail survives worker recreation"
    nlp_drained() {
      [ "$(psql_app "select count(*) from nlp_jobs where status in ('queued','running','retrying')")" = 0 ] &&
        [ "$(psql_app "select count(*) from nlp_jobs where status='succeeded'")" -gt 0 ]
    }
    wait_until "NLP backlog" nlp_drained
    $compose start elasticsearch
    wait_until "Elasticsearch recovery" '$compose exec -T elasticsearch curl -fsS http://127.0.0.1:9200/_cluster/health >/dev/null 2>&1'
    rebuild_search
    output=$($compose run --rm nlp-worker python -m app.cli reprocess-nlp --processors keywords --all --apply)
    echo "$output"
    run_id=$(echo "$output" | sed -n 's/.*run_id=\([^ ]*\).*/\1/p')
    [ -n "$run_id" ] || { echo "NLP reprocessing run id missing" >&2; exit 1; }
    $compose run --rm nlp-worker python -m app.cli resume-nlp-reprocessing "$run_id"
    # The spec filters by a keyword read from PostgreSQL, then searches Elasticsearch once; wait until
    # the new keywords are both annotated and indexed, or the search can answer before they arrive.
    wait_until "NLP backlog" nlp_drained
    wait_until "Indexing" indexing_drained
    e2e "search restores URL state|annotations refine search"
    ;;
  investigations)
    e2e "investigation seed"
    rebuild_search
    e2e "investigation workflow"
    # A saved search whose state no longer validates (as after a retired filter) must explain itself.
    [ "$(psql_app "with stale as (update saved_searches set state = state || '{\"retired_filter\": true}' where name = 'Stale investigation' returning id) select count(*) from stale")" = 1 ] || { echo "Stale saved search missing" >&2; exit 1; }
    e2e "invalid saved search"
    [ "$(psql_app "select count(*) from saved_searches")" = 0 ] || { echo "Saved searches were not deleted" >&2; exit 1; }
    ;;
  monitors)
    rebuild_search
    e2e "monitor workflow"
    ;;
  graph)
    e2e "relationships seed"
    # Rebuild after seeding (as Phases 7 and 10C do): search results carry the story cluster, which the
    # seed only settles after the articles were first indexed.
    rebuild_search
    e2e "relationships workflow"
    e2e "graph edge evidence workflow"
    e2e "operations workflow"
    e2e "map workflow"
    e2e "compare workflow"
    e2e "source dossier workflow"
    e2e "event workflow"
    e2e "entity dossier workflow"
    e2e "ui polish workflow"
    # Last: the operations page must report a stopped Elasticsearch while every other panel renders.
    $compose stop elasticsearch
    e2e "stopped Elasticsearch"
    ;;
esac

echo "E2E group $group passed: project=$project artifacts=$artifacts"
