#!/bin/sh
set -eu

project="newsintel-phase3-acceptance"
export NEWSINTEL_PORT=18081
export NEWSINTEL_TEST_POSTGRES_PORT=55433
export NEWSINTEL_TEST_FIXTURE_PORT=18082
compose="docker compose -p $project -f compose.yaml -f compose.e2e.yaml"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    $compose logs --no-color > /tmp/newsintel-phase3-acceptance.log 2>&1 || true
    echo "Acceptance logs: /tmp/newsintel-phase3-acceptance.log" >&2
  fi
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

$compose up -d postgres redis fixture
$compose run --rm api alembic upgrade head
printf 'phase3-password\nphase3-password\n' | $compose run --rm -T api python -m app.cli create-user phase3
$compose up -d --build api worker scheduler frontend fixture

deadline=$(( $(date +%s) + 90 ))
until curl -fsS http://127.0.0.1:18081/api/v1/health/live >/dev/null 2>&1; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Stack readiness timed out" >&2; exit 1; }
  sleep 2
done

NEWSINTEL_RUN_POSTGRES_TESTS=1 \
NEWSINTEL_DATABASE_URL=postgresql+asyncpg://newsintel:newsintel@127.0.0.1:55433/newsintel \
NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' \
NEWSINTEL_TEST_FIXTURE_PORT=18082 \
UV_CACHE_DIR="$PWD/backend/.uv-cache" \
  uv run --project backend --frozen pytest -q backend/tests

(cd frontend && npm test && npm run typecheck && npm run build && npm run e2e)
