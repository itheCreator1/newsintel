#!/bin/sh
# Maintained browser suite: runs every Playwright workflow entirely inside Docker.
# Usage: infra/test-e2e.sh [--reuse-images <manifest>] <group>, group is one of search,
# investigations, monitors, graph.
# --reuse-images <manifest> skips this script's own frontend-test build and runs the app with
# --no-build, trusting the images test-e2e.sh's own project's caller (test-docker.sh) already
# built and recorded in <manifest> (a shell env file, sourced in). Standalone invocations without
# the flag are unaffected: they always build from current source using Docker's cache, as before.
# Each group gets a fresh Compose project because the specs share fixture feeds and assert exact counts.
# The phase scripts stay as historical records; this one tracks the current migration head.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"

usage="usage: infra/test-e2e.sh [--reuse-images <manifest>] search|investigations|monitors|graph"
reuse_manifest=
if [ "${1:-}" = "--reuse-images" ]; then
  reuse_manifest=${2:?$usage}
  shift 2
fi
group=${1:?$usage}

case $group in
  search | investigations | monitors | graph) ;;
  *) echo "Unknown group: $group" >&2; exit 2 ;;
esac

if [ -n "$reuse_manifest" ]; then
  [ -r "$reuse_manifest" ] || { echo "Reuse manifest not readable: $reuse_manifest" >&2; exit 2; }
  . "$reuse_manifest"
  ni_head=$(git -C "$root" rev-parse HEAD)
  [ "${NEWSINTEL_MANIFEST_REVISION:-}" = "$ni_head" ] || {
    echo "Reuse manifest is for a different revision: manifest=${NEWSINTEL_MANIFEST_REVISION:-<unset>} HEAD=$ni_head" >&2
    exit 2
  }
  ni_wanthash=$(ni_tree_hash "$root")
  [ "${NEWSINTEL_MANIFEST_TREE_HASH:-}" = "$ni_wanthash" ] || {
    echo "Reuse manifest is stale: the working tree changed since the images were built" >&2
    exit 2
  }
  # ni_check_image <label> <tag-var-name> <id-var-name>: resolves the two named manifest
  # variables indirectly (POSIX eval, not a bashism) and fails with a message naming exactly
  # which image and which check (missing/unbuilt/stale) tripped -- never a generic error.
  ni_check_image() {
    eval "ni_ci_tag=\${$2:-}"
    eval "ni_ci_want=\${$3:-}"
    [ -n "$ni_ci_tag" ] && [ -n "$ni_ci_want" ] || {
      echo "Reuse manifest is missing the $1 image tag/id" >&2
      exit 2
    }
    ni_ci_got=$(docker image inspect --format '{{.Id}}' "$ni_ci_tag" 2>/dev/null) || {
      echo "Reuse manifest image not found for $1: $ni_ci_tag" >&2
      exit 2
    }
    [ "$ni_ci_got" = "$ni_ci_want" ] || {
      echo "Reuse manifest image is stale for $1: $ni_ci_tag is $ni_ci_got, manifest recorded $ni_ci_want" >&2
      exit 2
    }
  }
  ni_check_image backend NEWSINTEL_IMAGE_BACKEND NEWSINTEL_IMAGE_BACKEND_ID
  ni_check_image frontend NEWSINTEL_IMAGE_FRONTEND NEWSINTEL_IMAGE_FRONTEND_ID
  ni_check_image frontend-test NEWSINTEL_IMAGE_FRONTEND_TEST NEWSINTEL_IMAGE_FRONTEND_TEST_ID
  [ "$group" = graph ] && ni_check_image backend-ner NEWSINTEL_IMAGE_BACKEND_NER NEWSINTEL_IMAGE_BACKEND_NER_ID
  export NEWSINTEL_IMAGE_BACKEND NEWSINTEL_IMAGE_FRONTEND NEWSINTEL_IMAGE_FRONTEND_TEST NEWSINTEL_IMAGE_BACKEND_NER
fi

project=$(ni_project "e2e-$group")
ni_report_init "${NEWSINTEL_E2E_ARTIFACTS:-}" "$root" "e2e-$group"

files="-f docker/compose.yaml -f docker/compose.e2e.yaml -f docker/compose.test.yaml -f docker/compose.e2e-container.yaml"
# Entities, events, sources, comparison and the map need real NER; the other groups skip its heavy build.
[ "$group" = graph ] && files="$files -f docker/compose.ner.yaml"
compose="docker compose -p $project $files"
cleanup() {
  status=$?
  ni_report_finalize "$status"
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "E2E artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
ni_env_report "$root" "${NEWSINTEL_CACHE_STATE:-warm}"
ni_mem_start "$project"
psql_app() { $compose exec -T postgres psql -U newsintel -d newsintel -Atc "$1"; }
e2e() {
  $compose run --rm --no-deps -v "$artifacts:/artifacts" \
    -e NEWSINTEL_E2E_BASE_URL=http://frontend:8080 \
    -e NEWSINTEL_E2E_OUTPUT_DIR=/artifacts/playwright \
    frontend-test npm run e2e -- --grep "$1"
}
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

ni_stage build
if [ -z "$reuse_manifest" ]; then
  $compose build frontend-test
fi

deps_extra=
app_build_flag=--build
[ -n "$reuse_manifest" ] && { deps_extra=--no-build; app_build_flag=--no-build; }

ni_stage deps.up
$compose up -d --wait --wait-timeout 180 $deps_extra postgres redis elasticsearch fixture
ni_stage alembic.upgrade
$compose run --rm api alembic upgrade head
ni_stage alembic.single-head
ni_single_head "$compose" api "Migrations must have a single head"
ni_stage users
case $group in
  search) users phase3 phase4 phase5 ;;
  investigations) users phase6 ;;
  monitors) users phase11d ;;
  # `relationships seed` signs in as phase7; each later spec has its own user.
  graph) users phase7 phase10b phase10c phase12d phase13a phase13b phase13c phase13d ;;
esac
ni_stage app.up
$compose up -d $app_build_flag api worker nlp-worker scheduler frontend
wait_until "Application readiness" '$compose exec -T frontend wget -qO- http://127.0.0.1:8080/api/v1/health/live >/dev/null 2>&1'

ni_stage scenarios

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
    # A second rebuild over the live alias (the first one above created it): the cutover path a V3
    # re-rebuild or a V4 takes. Every article must land in the new index, the old target becomes
    # retained and its index stays for rollback. Search reads the current target's index (from
    # PostgreSQL, not the alias), so the re-run spec proves reads survive the switch; the checks
    # before it prove the alias moved.
    es() { $compose exec -T elasticsearch curl -fsS "http://127.0.0.1:9200/$1"; }
    alias_index() { es "_cat/aliases/articles-current?h=index" | tr -d '[:space:]'; }
    old_index=$(alias_index)
    [ -n "$old_index" ] || { echo "Search alias missing before the second rebuild" >&2; exit 1; }
    rebuild_search
    new_index=$(alias_index)
    [ -n "$new_index" ] && [ "$new_index" != "$old_index" ] || { echo "Alias did not move: $old_index -> $new_index" >&2; exit 1; }
    [ "$(es "_cat/count/articles-current?h=count" | tr -d '[:space:]')" = "$(psql_app "select count(*) from articles")" ] || { echo "The new index does not hold every article" >&2; exit 1; }
    [ "$(psql_app "select role from search_index_targets where index_name = '$new_index'")" = current ] || { echo "New target is not current" >&2; exit 1; }
    [ "$(psql_app "select role from search_index_targets where index_name = '$old_index'")" = retained ] || { echo "Old target is not retained" >&2; exit 1; }
    es "$old_index/_count" >/dev/null || { echo "The old index was not kept for rollback" >&2; exit 1; }
    e2e "search restores URL state"
    ;;
  investigations)
    e2e "investigation seed"
    rebuild_search
    e2e "investigation workflow"
    # A saved search whose state no longer validates (as after a retired filter) must explain itself.
    [ "$(psql_app "with stale as (update saved_searches set state = state || '{\"retired_filter\": true}' where name = 'Stale investigation' returning id) select count(*) from stale")" = 1 ] || { echo "Stale saved search missing" >&2; exit 1; }
    e2e "invalid saved search"
    [ "$(psql_app "select count(*) from saved_searches")" = 0 ] || { echo "Saved searches were not deleted" >&2; exit 1; }
    e2e "related coverage workflow"
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
