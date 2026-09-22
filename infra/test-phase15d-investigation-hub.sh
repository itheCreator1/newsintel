#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
run_id="$$-$(date +%s)"
project="newsintel-phase15d-$run_id"
artifacts="/tmp/$project"
mkdir -p "$artifacts"
pick_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'; }
NEWSINTEL_TEST_POSTGRES_PORT=${NEWSINTEL_TEST_POSTGRES_PORT:-$(pick_port)}
NEWSINTEL_TEST_ELASTICSEARCH_PORT=${NEWSINTEL_TEST_ELASTICSEARCH_PORT:-$(pick_port)}
export NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT
compose="docker compose -p $project -f docker/compose.yaml -f docker/compose.e2e.yaml"
cleanup() {
  status=$?
  [ "$status" -ne 0 ] && echo "Acceptance artifacts: $artifacts" >&2
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT INT TERM

cd "$root"
export UV_CACHE_DIR="$root/backend/.uv-cache"
$compose up -d --wait --wait-timeout 120 postgres elasticsearch
$compose exec -T postgres createdb -U newsintel newsintel_tests
export NEWSINTEL_DATABASE_URL="postgresql+asyncpg://newsintel:newsintel@127.0.0.1:$NEWSINTEL_TEST_POSTGRES_PORT/newsintel_tests"
export NEWSINTEL_ELASTICSEARCH_URL="http://127.0.0.1:$NEWSINTEL_TEST_ELASTICSEARCH_PORT"
# 15D adds no migration; the head must stay single.
(cd backend && uv run --frozen python -m alembic upgrade head)
[ "$(cd backend && uv run --frozen python -m alembic heads | grep -c '(head)')" = 1 ] || { echo "Migrations must have a single head" >&2; exit 1; }

# 15D changes no backend code; the facet and search routes it consumes run as a regression.
(cd backend && NEWSINTEL_RUN_POSTGRES_TESTS=1 uv run --frozen python -m pytest -q \
  tests/test_search_facets.py tests/test_search_facets_elasticsearch.py tests/test_search_results.py \
  tests/test_search_timeline.py tests/test_search_pagination_elasticsearch.py \
  tests/test_graph_entities.py tests/test_graph_edge_evidence.py) > "$artifacts/pytest.log"
tail -3 "$artifacts/pytest.log"
(cd backend && uv run --frozen ruff check app && uv run --frozen python -m mypy app)

# 15D changes no API, so the committed contract must still match a regeneration.
cp frontend/openapi.json "$artifacts/openapi.before.json"
cp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts"
(cd backend && uv run --frozen python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))') > frontend/openapi.json
(cd frontend && npm run generate:api)
cmp frontend/openapi.json "$artifacts/openapi.before.json" || { echo "frontend/openapi.json was stale" >&2; exit 1; }
cmp frontend/src/lib/types.generated.ts "$artifacts/types.before.ts" || { echo "generated types were stale" >&2; exit 1; }
(cd frontend && npm test && npm run typecheck && npm run build) > "$artifacts/frontend.log" 2>&1 || { cat "$artifacts/frontend.log"; exit 1; }

# Facet refinement and the Graph link run in the investigations workflow; search is a regression.
# Each group brings its own stack, so release this one's ports first; inheriting them collides.
$compose down -v --remove-orphans >/dev/null 2>&1
unset NEWSINTEL_TEST_POSTGRES_PORT NEWSINTEL_TEST_ELASTICSEARCH_PORT NEWSINTEL_DATABASE_URL NEWSINTEL_ELASTICSEARCH_URL
infra/test-e2e.sh investigations
infra/test-e2e.sh search

echo "Phase 15D acceptance passed: project=$project artifacts=$artifacts"
