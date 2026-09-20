#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase12d-$run_id"
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
# Events link entities, so real NER runs in the one stack (as in Phase 10B).
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

# The frontend suite is flaky-sensitive (Phase 11E removed an unmounted-component race), so run it repeatedly.
for attempt in 1 2 3; do (cd frontend && npm test) > "$artifacts/vitest-$attempt.log" 2>&1 || { cat "$artifacts/vitest-$attempt.log"; echo "npm test failed on attempt $attempt" >&2; exit 1; }; done
tail -n 8 "$artifacts/vitest-3.log"
(cd frontend && npm run typecheck)
(cd frontend && npm run build)
# 12D changes no API: the checked-in contract must still match the application.
PYTHONPATH=backend UV_CACHE_DIR="$root/backend/.uv-cache" uv run --directory backend --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > "$artifacts/openapi.json"
cmp "$artifacts/openapi.json" frontend/openapi.json || { echo "frontend/openapi.json is stale" >&2; exit 1; }

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head
# 12D adds no schema: the head must still be 0012.
$compose run --rm api alembic heads | tr -d '\r' | grep -q '^0012 (head)' || { echo "Phase 12D must not add a migration" >&2; exit 1; }
# `relationships seed` (reused fixture collection) signs in as phase7; the entity spec uses phase10b, the event spec phase12d.
printf 'phase7-password\nphase7-password\n' | $compose run --rm -T api python -m app.cli create-user phase7
printf 'phase10b-password\nphase10b-password\n' | $compose run --rm -T api python -m app.cli create-user phase10b
printf 'phase12d-password\nphase12d-password\n' | $compose run --rm -T api python -m app.cli create-user phase12d
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

(cd frontend && npm run e2e -- --grep "relationships seed")
# The event spec waits for the scheduler's own association run; the entity spec proves the new chip did not break its links.
(cd frontend && npm run e2e -- --grep "event workflow")
(cd frontend && npm run e2e -- --grep "entity dossier workflow")

echo "Phase 12D acceptance passed: project=$project artifacts=$artifacts"
