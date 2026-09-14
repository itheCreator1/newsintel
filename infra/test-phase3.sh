#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase3-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"

pick_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
}

NEWSINTEL_PORT=$(pick_port)
NEWSINTEL_TEST_POSTGRES_PORT=$(pick_port)
NEWSINTEL_TEST_FIXTURE_PORT=$(pick_port)
export NEWSINTEL_PORT NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_FIXTURE_PORT
export NEWSINTEL_E2E_BASE_URL="http://127.0.0.1:$NEWSINTEL_PORT"
export NEWSINTEL_E2E_OUTPUT_DIR="$artifacts/playwright"

compose="docker compose -p $project -f compose.yaml -f compose.e2e.yaml"

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
docker compose -f compose.yaml -f compose.e2e.yaml config --quiet
$compose build api worker scheduler
$compose up -d --wait --wait-timeout 90 postgres redis fixture

deadline=$(( $(date +%s) + 90 ))
while :; do
  if $compose exec -T postgres psql -U newsintel -d newsintel -Atc 'select 1' >/dev/null 2>&1; then
    sleep 2
    if $compose exec -T postgres psql -U newsintel -d newsintel -Atc 'select 1' >/dev/null 2>&1; then
      break
    fi
  fi
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Stable PostgreSQL readiness timed out" >&2; exit 1; }
  sleep 1
done

expect_fixture_status() {
  path=$1
  expected=$2
  actual=$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$NEWSINTEL_TEST_FIXTURE_PORT/$path")
  [ "$actual" = "$expected" ] || {
    echo "Fixture $path returned $actual, expected $expected" >&2
    exit 1
  }
}
expect_fixture_status article.html 200
expect_fixture_status permanent.html 404
expect_fixture_status transient.html 503

$compose exec -T postgres createdb -U newsintel newsintel_tests
test_database="postgresql+asyncpg://newsintel:newsintel@postgres:5432/newsintel_tests"
$compose run --rm -e NEWSINTEL_DATABASE_URL="$test_database" api alembic upgrade head

host_test_database="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
(
  cd backend
  NEWSINTEL_RUN_POSTGRES_TESTS=1 \
  NEWSINTEL_DATABASE_URL="$host_test_database" \
  NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]' \
  UV_CACHE_DIR="$root/backend/.uv-cache" \
    uv run --frozen pytest -q -rs tests
) > "$artifacts/pytest.log"
cat "$artifacts/pytest.log"
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped|^SKIPPED ' "$artifacts/pytest.log"; then
  echo "Required backend tests were skipped" >&2
  exit 1
fi
(cd backend && uv run --frozen ruff check . && uv run --frozen mypy app)

(cd frontend && npm test && npm run typecheck && npm run build)

$compose run --rm api alembic upgrade head
printf 'phase3-password\nphase3-password\n' | $compose run --rm -T api python -m app.cli create-user phase3
$compose up -d --build api worker scheduler frontend

deadline=$(( $(date +%s) + 90 ))
until curl -fsS "$NEWSINTEL_E2E_BASE_URL/api/v1/health/live" >/dev/null 2>&1; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "Application readiness timed out" >&2; exit 1; }
  sleep 1
done

if $compose ps --status running --services | grep -qx elasticsearch; then
  echo "Elasticsearch unexpectedly started during Phase 3 acceptance" >&2
  exit 1
fi

(cd frontend && npm run e2e -- --grep "failure and retry workflow")

html_key=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc \
  "select c.html_object_key from article_contents c join articles a on a.id=c.article_id where a.title='Fixture story'")
[ -n "$html_key" ] || { echo "Retained HTML key is missing" >&2; exit 1; }
$compose exec -T worker test -f "/var/lib/newsintel/articles/$html_key"
content_before=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc \
  "select c.content_hash || ':' || length(c.text) from article_contents c join articles a on a.id=c.article_id where a.title='Fixture story'")

$compose up -d --force-recreate worker
$compose exec -T worker test -f "/var/lib/newsintel/articles/$html_key"
content_after=$($compose exec -T postgres psql -U newsintel -d newsintel -Atc \
  "select c.content_hash || ':' || length(c.text) from article_contents c join articles a on a.id=c.article_id where a.title='Fixture story'")
[ "$content_before" = "$content_after" ] || { echo "PostgreSQL article content changed after worker recreation" >&2; exit 1; }

(cd frontend && npm run e2e -- --grep "retained detail survives worker recreation")
echo "Phase 3 acceptance passed: project=$project artifacts=$artifacts"
