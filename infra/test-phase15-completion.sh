#!/bin/sh
# Phase 15 completion gate: every final check of the Elasticsearch expansion, run locally as CI would.
# Backend lint and types, the whole backend suite gated with nothing skipped (PostgreSQL 17.6,
# Elasticsearch 9.1.3, Redis 8.2, feed fixtures), the restore rehearsal, contract regeneration,
# frontend tests and build, then all four browser groups (search includes the live-alias cutover).
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase15-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_ELASTICSEARCH_PORT=${NEWSINTEL_TEST_ELASTICSEARCH_PORT:-$(pick_port)}
NEWSINTEL_TEST_FIXTURE_PORT=${NEWSINTEL_TEST_FIXTURE_PORT:-$(pick_port)}
redis_port=$(pick_port)
export NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT NEWSINTEL_TEST_FIXTURE_PORT
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml"
redis="$project-redis"
cleanup() {
  status=$?
  [ "$status" -ne 0 ] && echo "Acceptance artifacts: $artifacts" >&2
  docker rm -f "$redis" >/dev/null 2>&1 || true
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
export UV_CACHE_DIR="$root/backend/.uv-cache"
$compose up -d --wait --wait-timeout 180 postgres elasticsearch fixture
docker run -d --rm --name "$redis" -p "127.0.0.1:$redis_port:6379" redis:8.2-alpine >/dev/null
$compose exec -T postgres createdb -U newsintel newsintel_tests
export NEWSINTEL_RUN_POSTGRES_TESTS=1
export NEWSINTEL_DATABASE_URL="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
export NEWSINTEL_ELASTICSEARCH_URL="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
export NEWSINTEL_REDIS_URL="redis://127.0.0.1:$redis_port/0"
export NEWSINTEL_FEED_TEST_ALLOWED_HOSTS='["localhost"]'

# Backend: lint, types, a single migration head.
(cd backend && uv run --frozen ruff check . && uv run --frozen python -m mypy app)
(cd backend && uv run --frozen python -m alembic upgrade head)
[ "$(cd backend && uv run --frozen python -m alembic heads | grep -c '(head)')" = 1 ] || { echo "Migrations must have a single head" >&2; exit 1; }

# Integration: the whole suite with nothing skipped, then dump and restore what it wrote.
if ! (cd backend && uv run --frozen python -m pytest -q -rs tests) > "$artifacts/pytest.log" 2>&1; then
  tail -40 "$artifacts/pytest.log" >&2
  exit 1
fi
tail -3 "$artifacts/pytest.log"
if grep -Eq '[0-9]+ skipped' "$artifacts/pytest.log"; then
  grep SKIPPED "$artifacts/pytest.log" >&2
  echo "The gated suite skipped tests" >&2
  exit 1
fi
infra/test-restore.sh "$($compose ps -q postgres)" newsintel_tests

# Contract: regenerated OpenAPI and TypeScript types must match the checked-in ones.
(cd backend && uv run --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' > ../frontend/openapi.json)
(cd frontend && npm run generate:api)
git diff --exit-code frontend/openapi.json frontend/src/lib/types.generated.ts

# Frontend.
(cd frontend && npm test && npm run typecheck && npm run build)

# Browser groups, each on its own disposable stack; free this project's ports first.
docker rm -f "$redis" >/dev/null
$compose down -v --remove-orphans >/dev/null
for group in search investigations monitors graph; do
  env -u NEWSINTEL_TEST_POSTGRES_PORT -u NEWSINTEL_TEST_ELASTICSEARCH_PORT -u NEWSINTEL_TEST_FIXTURE_PORT NEWSINTEL_E2E_ARTIFACTS="$artifacts/e2e-$group" infra/test-e2e.sh "$group"
done

echo "Phase 15 completion passed: project=$project artifacts=$artifacts"
