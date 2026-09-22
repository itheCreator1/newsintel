#!/bin/sh
# Full repository validation using Docker only.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"
project=$(ni_project test)
compose="docker compose -p $project -f docker/compose.test.yaml"
rt_compose="docker compose -p $project -f docker/compose.yaml"
ner_compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.ner.yaml"
# Run-scoped tags: every build below names its image explicitly so up/run in this script and in
# the e2e groups it dispatches (via --reuse-images) resolve to the same already-built image
# instead of rebuilding it once per service/group.
export NEWSINTEL_IMAGE_BACKEND_TEST="newsintel-backend-test:$project"
export NEWSINTEL_IMAGE_FRONTEND_TEST="newsintel-frontend-test:$project"
export NEWSINTEL_IMAGE_BACKEND="newsintel-backend:$project"
export NEWSINTEL_IMAGE_BACKEND_NER="newsintel-backend-ner:$project"
export NEWSINTEL_IMAGE_FRONTEND="newsintel-frontend:$project"
ni_report_init "${NEWSINTEL_TEST_ARTIFACTS:-}" "$root" test

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

# Compose 5.5.1 does not dedupe a multi-service build that shares one `image:` name (verified:
# `docker compose build --dry-run api worker nlp-worker scheduler` runs all four separately).
# So each shared-image group is built through exactly one representative service; the sibling
# services (worker/nlp-worker/scheduler, and worker/scheduler under the NER overlay) resolve to
# the same tag at up/run time without ever being built themselves.
# ni_stage's own aggregate would only cover the gap between two stage markers (near-zero), so
# build.all is instead written by hand below as the wall-clock span of the five sub-builds --
# required so phase-5's stage-name diff against the phase-1 baseline (which only has build.all)
# has a common key. It duplicates the 5 rows' time; ni_report_finalize's TOTAL excludes it.
ni_bt0=$(date +%s)
ni_stage build.backend-test
$compose build backend-test
ni_stage build.frontend-test
$compose build frontend-test
ni_stage build.backend
$rt_compose build api
ni_stage build.backend-ner
$ner_compose build api
ni_stage build.frontend
$rt_compose build frontend
ni_bt1=$(date +%s)
printf 'build.all\t%s\t%s\t0\n' "$ni_bt0" "$((ni_bt1 - ni_bt0))" >> "$artifacts/timings.tsv"

{
  printf 'service_image\timage_id\n'
  for ni_c in "$compose" "$rt_compose" "$ner_compose"; do
    $ni_c config --images | while read -r ni_img; do
      printf '%s\t%s\n' "$ni_img" "$(docker image inspect --format '{{.Id}}' "$ni_img" 2>/dev/null || echo unknown)"
    done
  done
} > "$artifacts/images.tsv"

# Manifest test-e2e.sh's --reuse-images validates against: revision + tree hash prove the images
# below were built from exactly this working tree, and each recorded image ID must still match
# what `docker image inspect` reports at group-start time.
ni_manifest="$artifacts/image-manifest.env"
{
  echo "NEWSINTEL_MANIFEST_REVISION=$(git -C "$root" rev-parse HEAD)"
  echo "NEWSINTEL_MANIFEST_TREE_HASH=$(ni_tree_hash "$root")"
  echo "NEWSINTEL_IMAGE_BACKEND=$NEWSINTEL_IMAGE_BACKEND"
  echo "NEWSINTEL_IMAGE_BACKEND_ID=$(docker image inspect --format '{{.Id}}' "$NEWSINTEL_IMAGE_BACKEND")"
  echo "NEWSINTEL_IMAGE_BACKEND_NER=$NEWSINTEL_IMAGE_BACKEND_NER"
  echo "NEWSINTEL_IMAGE_BACKEND_NER_ID=$(docker image inspect --format '{{.Id}}' "$NEWSINTEL_IMAGE_BACKEND_NER")"
  echo "NEWSINTEL_IMAGE_FRONTEND=$NEWSINTEL_IMAGE_FRONTEND"
  echo "NEWSINTEL_IMAGE_FRONTEND_ID=$(docker image inspect --format '{{.Id}}' "$NEWSINTEL_IMAGE_FRONTEND")"
  echo "NEWSINTEL_IMAGE_FRONTEND_TEST=$NEWSINTEL_IMAGE_FRONTEND_TEST"
  echo "NEWSINTEL_IMAGE_FRONTEND_TEST_ID=$(docker image inspect --format '{{.Id}}' "$NEWSINTEL_IMAGE_FRONTEND_TEST")"
} > "$ni_manifest"

# Bring dependencies up without waiting so Elasticsearch's slow healthcheck overlaps the static
# checks below (which touch no service) instead of blocking wall time in front of them; the hard
# `--wait` barrier just before alembic.upgrade still guarantees migrations never race startup.
ni_stage deps.up
$compose up -d test-postgres test-redis test-elasticsearch

if [ "${NEWSINTEL_TEST_CONCURRENT_STATIC:-0}" = 1 ]; then
  ni_stage static
  $compose run --rm --no-deps backend-test ruff check . > "$artifacts/ruff.log" 2>&1 &
  ni_ruff_pid=$!
  $compose run --rm --no-deps backend-test mypy app > "$artifacts/mypy.log" 2>&1 &
  ni_mypy_pid=$!
  ni_ruff_status=0
  ni_mypy_status=0
  wait "$ni_ruff_pid" || ni_ruff_status=$?
  wait "$ni_mypy_pid" || ni_mypy_status=$?
  cat "$artifacts/ruff.log"
  cat "$artifacts/mypy.log"
  [ "$ni_ruff_status" -eq 0 ] || { echo "ruff failed" >&2; exit "$ni_ruff_status"; }
  [ "$ni_mypy_status" -eq 0 ] || { echo "mypy failed" >&2; exit "$ni_mypy_status"; }
else
  ni_stage ruff
  $compose run --rm --no-deps backend-test ruff check .
  ni_stage mypy
  $compose run --rm --no-deps backend-test mypy app
fi

ni_stage deps.wait
$compose up -d --wait --wait-timeout 180 test-postgres test-redis test-elasticsearch

ni_stage alembic.upgrade
$compose run --rm backend-test alembic upgrade head
ni_stage alembic.single-head
ni_single_head "$compose" backend-test "Migrations must have exactly one head" --no-deps

ni_stage pytest
$compose run --rm -v "$artifacts:/artifacts" backend-test \
  bash -o pipefail -c '
    python tests/fixtures/server.py 18080 > /artifacts/fixture-server.log 2>&1 &
    fixture_pid=$!
    trap "kill $fixture_pid >/dev/null 2>&1 || true" EXIT
    for attempt in {1..30}; do
      python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:18080/feed.xml\")" >/dev/null 2>&1 && break
      [ "$attempt" -lt 30 ] || { echo "Fixture server did not start" >&2; exit 1; }
      sleep 1
    done
    python -m pytest -q -rs tests | tee /artifacts/pytest.log
    pytest_status=${PIPESTATUS[0]}
    [ "$pytest_status" -eq 0 ] || exit "$pytest_status"
    ! grep -Eq "[0-9]+ skipped" /artifacts/pytest.log
  '

ni_stage restore
infra/test-restore.sh "$($compose ps -q test-postgres)" newsintel_tests

# `stop`, not `down`/`rm`: keeps the containers (and compose logs) available for the failure trap
# while every remaining stage runs --no-deps or against its own separate Compose project.
ni_stage deps.stop
$compose stop test-postgres test-redis test-elasticsearch

ni_stage contract.openapi
$compose run --rm --no-deps -v "$artifacts:/artifacts" backend-test \
  sh -c "python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > /artifacts/openapi.json"
cmp frontend/openapi.json "$artifacts/openapi.json" || {
  echo "frontend/openapi.json is stale" >&2
  exit 1
}

ni_stage contract.types
$compose run --rm --no-deps -v "$artifacts:/artifacts" frontend-test sh -c \
  'cp src/lib/types.generated.ts /tmp/types.generated.ts && cp /artifacts/openapi.json openapi.json && npm run generate:api && cmp /tmp/types.generated.ts src/lib/types.generated.ts' || {
  echo "frontend/src/lib/types.generated.ts is stale" >&2
  exit 1
}

ni_stage frontend.unit
$compose run --rm --no-deps frontend-test npm test
ni_stage frontend.typecheck
$compose run --rm --no-deps frontend-test npm run typecheck
ni_stage frontend.build
$compose run --rm --no-deps frontend-test npm run build

for group in search investigations monitors graph; do
  ni_stage "e2e.$group"
  NEWSINTEL_E2E_ARTIFACTS="$artifacts/e2e-$group" infra/test-e2e.sh --reuse-images "$ni_manifest" "$group"
done

echo "Docker test gate passed; diagnostics directory: $artifacts"
