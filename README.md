# NewsIntel

NewsIntel is a self-hosted foundation for collecting, searching, and investigating a long-running news archive. PostgreSQL is authoritative; Redis handles background work and Elasticsearch is a rebuildable search index.

## Documentation

The product specification, architecture rules, and operating conventions all live in [AGENTS.md](AGENTS.md). This README covers only the quickstart below.

## Start from a clean checkout

1. Copy `.env.example` to `.env` and choose a strong PostgreSQL password.
2. Build and start dependencies: `docker compose --env-file .env -f docker/compose.yaml up -d postgres redis elasticsearch`.
3. Apply migrations: `docker compose --env-file .env -f docker/compose.yaml run --rm api alembic upgrade head`.
4. Create the initial account: `docker compose --env-file .env -f docker/compose.yaml run --rm api python -m app.cli create-user admin`.
5. Start the application: `docker compose --env-file .env -f docker/compose.yaml up -d --build`.
6. Open <http://127.0.0.1:8080>.

Stop services with `docker compose --env-file .env -f docker/compose.yaml down`. Named volumes retain data. `docker compose --env-file .env -f docker/compose.yaml down -v` intentionally destroys local data.

## Development checks

Backend: `cd backend && uv sync && uv run pytest && uv run ruff check . && uv run mypy app`

Frontend: `cd frontend && npm install && npm test && npm run typecheck && npm run build`

Per-phase acceptance gates live in `infra/test-phaseN.sh`. See [AGENTS.md](AGENTS.md) for feed/search/NLP/clustering conventions and deployment notes.
