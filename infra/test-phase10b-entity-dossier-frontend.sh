#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase10b-$run_id"
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
# The dossier workflow needs real extracted entities, so NER is merged into the one stack (as in Phase 7).
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

(cd frontend && npm test)
(cd frontend && npm run typecheck)
(cd frontend && npm run build)

$compose up -d --wait --wait-timeout 120 postgres redis elasticsearch fixture
$compose run --rm api alembic upgrade head
# `relationships seed` (reused fixture collection) signs in as phase7; the dossier spec uses phase10b.
printf 'phase7-password\nphase7-password\n' | $compose run --rm -T api python -m app.cli create-user phase7
printf 'phase10b-password\nphase10b-password\n' | $compose run --rm -T api python -m app.cli create-user phase10b
$compose up -d --build api worker nlp-worker scheduler frontend
deadline=$(( $(date +%s) + 120 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }; sleep 1; done

(cd frontend && npm run e2e -- --grep "relationships seed")
(cd frontend && npm run e2e -- --grep "entity dossier workflow")

echo "Phase 10B acceptance passed: project=$project artifacts=$artifacts"
