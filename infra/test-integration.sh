#!/bin/sh
# Targeted local loop: runs exactly the integration modules named on the command line, starting
# only the services classification.toml says they need (never all of test-postgres/test-redis/
# test-elasticsearch/fixture unconditionally, unlike infra/test-docker.sh, the mandatory full
# gate this never replaces). Resolves the selection before starting anything: unknown/empty
# selections and unit-module ids are rejected without touching Docker.
# Usage: infra/test-integration.sh <pytest-node-id>...
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"

[ "$#" -ge 1 ] || { echo "usage: infra/test-integration.sh <pytest-node-id>..." >&2; exit 2; }

# Normalize every id (strip a leading "backend/") so both a repo-root-relative id (as typed from
# $root) and a backend/-relative one work identically -- pytest itself runs with cwd=/app (==
# backend/) inside the container, so a literal "backend/" prefix would otherwise fail collection
# there. Rebuilt via append-then-shift, not `set -- $(...)`: a bare unquoted command substitution
# word-splits on every space, which would mangle a parametrized node id containing one (pytest
# produces ids like "test_foo.py::test[a b]" for string/tuple parametrize values) -- appending
# each already-quoted stripped id one at a time keeps it intact as a single argument.
ni_orig_n=$#
for ni_arg do
  set -- "$@" "${ni_arg#backend/}"
done
shift "$ni_orig_n"

project=$(ni_project integration)
compose="docker compose -p $project -f docker/compose.test.yaml"
# Run-scoped tags, same convention as test-docker.sh/test-quick.sh: never fall back to the
# shared :local tag, so a concurrent run can't clobber this run's images (or vice versa).
export NEWSINTEL_IMAGE_BACKEND_TEST="newsintel-backend-test:$project"
export NEWSINTEL_IMAGE_FRONTEND_TEST="newsintel-frontend-test:$project"
ni_report_init "${NEWSINTEL_TEST_ARTIFACTS:-}" "$root" integration

cleanup() {
  status=$?
  ni_report_finalize "$status"
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > "$artifacts/compose.log" 2>&1 || true
    echo "Test artifacts: $artifacts" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$root"
ni_env_report "$root" "${NEWSINTEL_CACHE_STATE:-warm}"
ni_mem_start "$project"

ni_stage build.backend-test
$compose build backend-test

# Resolve before starting anything: unknown ids or an empty selection fail pytest's own
# collection (nonzero exit); a unit-kind module in the selection is rejected explicitly (this
# script is integration-only, test-quick.sh covers unit); classify.py --services then prints the
# union of services the selection actually needs.
ni_stage resolve
$compose run --rm --no-deps -v "$artifacts:/artifacts" backend-test bash -o pipefail -c '
  python -m pytest --collect-only -q "$@" | tee /artifacts/collect.log
  collect_status=${PIPESTATUS[0]}
  [ "$collect_status" -eq 0 ] || exit "$collect_status"
  grep -Eq "^[0-9]+ tests? collected" /artifacts/collect.log || {
    echo "Empty selection: nothing collected" >&2
    exit 1
  }
  python tests/classify.py --kinds "$@" > /artifacts/kinds.tsv
  if cut -f2 /artifacts/kinds.tsv | grep -qx unit; then
    echo "Selection includes a unit-kind module; test-integration.sh is for integration" >&2
    echo "modules only (postgres-gated) -- run unit modules through test-quick.sh instead." >&2
    exit 1
  fi
  python tests/classify.py --services "$@" > /artifacts/services.txt
' _ "$@"

services=$(cat "$artifacts/services.txt")
need_es=0
need_fixture=0
case " $services " in *" elasticsearch "*) need_es=1 ;; esac
case " $services " in *" fixture_server "*) need_fixture=1 ;; esac

deps="test-postgres test-redis"
[ "$need_es" -eq 1 ] && deps="$deps test-elasticsearch"

ni_stage deps.up
$compose up -d --wait --wait-timeout 180 $deps

ni_stage alembic.upgrade
$compose run --rm --no-deps backend-test alembic upgrade head
ni_stage alembic.single-head
ni_single_head "$compose" backend-test "Migrations must have exactly one head" --no-deps

# ES url: the live cluster only if the selection actually declared elasticsearch=true, else the
# closed-port guard -- so a manifest entry wrongly left at elasticsearch=false fails loudly
# against a selection that needed a real cluster, instead of silently passing against nothing.
es_env=
[ "$need_es" -eq 0 ] && es_env="-e NEWSINTEL_ELASTICSEARCH_URL=http://127.0.0.1:1"

fixture_prelude=
if [ "$need_fixture" -eq 1 ]; then
  fixture_prelude='
    python tests/fixtures/server.py 18080 > /artifacts/fixture-server.log 2>&1 &
    fixture_pid=$!
    trap "kill $fixture_pid >/dev/null 2>&1 || true" EXIT
    for attempt in {1..30}; do
      python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:18080/feed.xml\")" >/dev/null 2>&1 && break
      [ "$attempt" -lt 30 ] || { echo "Fixture server did not start" >&2; exit 1; }
      sleep 1
    done
'
fi
run_script="$fixture_prelude"'
  python -m pytest -q -rs "$@" | tee /artifacts/pytest.log
  pytest_status=${PIPESTATUS[0]}
  [ "$pytest_status" -eq 0 ] || exit "$pytest_status"
  ! grep -Eq "[0-9]+ skipped" /artifacts/pytest.log
'

ni_stage pytest
$compose run --rm --no-deps -v "$artifacts:/artifacts" $es_env backend-test \
  bash -o pipefail -c "$run_script" _ "$@"

echo "Integration selection passed: project=$project artifacts=$artifacts services=[$services]"
