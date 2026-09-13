# NewsIntel

NewsIntel is a self-hosted foundation for collecting, searching, and investigating a long-running news archive. PostgreSQL is authoritative; Redis handles background work and Elasticsearch is a rebuildable search index.

## Architecture

The Compose stack contains a Vue frontend, FastAPI API, Dramatiq worker, PostgreSQL, Redis, and Elasticsearch. The browser reaches only the frontend on `127.0.0.1:8080`; nginx serves the application and proxies `/api` to FastAPI. Application containers never run migrations automatically.

PostgreSQL also owns feed schedules and expiring claims. The `scheduler` service claims due feeds and hands them to Dramatiq. Workers fetch RSS or Atom, archive canonical articles and their per-feed discovery records, and update fetch history. This path has no Elasticsearch dependency.

## Start from a clean checkout

1. Copy `.env.example` to `.env` and choose a strong PostgreSQL password.
2. Build and start dependencies: `docker compose up -d postgres redis elasticsearch`.
3. Apply migrations: `docker compose run --rm api alembic upgrade head`.
4. Create the initial account: `docker compose run --rm api python -m app.cli create-user admin`.
5. Start the application: `docker compose up -d --build`.
6. Open <http://127.0.0.1:8080>.

Stop services with `docker compose down`. Named volumes retain data. `docker compose down -v` intentionally destroys local data.

## Development checks

Backend: `cd backend && uv sync && uv run pytest && uv run ruff check . && uv run mypy app`

Frontend: `cd frontend && npm install && npm test && npm run typecheck && npm run build`

Validate Compose with `docker compose config --quiet`. Generate a current OpenAPI document with `cd backend && uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))"`.

## Feed polling configuration

New sources poll every 30 minutes and accept a minimum interval of 5 minutes. The scheduler checks for due work every 10 seconds. Configure outbound requests with `NEWSINTEL_FEED_USER_AGENT`, `NEWSINTEL_FEED_TIMEOUT_SECONDS`, `NEWSINTEL_FEED_MAX_RESPONSE_BYTES`, `NEWSINTEL_FEED_REDIRECT_LIMIT`, and `NEWSINTEL_FEED_HOST_MIN_INTERVAL_SECONDS`. Private, loopback, link-local, credentialed, and non-HTTP URLs are rejected. `NEWSINTEL_FEED_TEST_ALLOWED_HOSTS` is only for narrowly scoped fixture hosts in test environments; leave it empty in normal deployments.

After updating from Phase 1, apply `docker compose run --rm api alembic upgrade head`, then start or recreate both `worker` and `scheduler`. A source can be polled immediately from Sources. Successful, unchanged (`304`), and failed cycles appear in its fetch history. Retiring a source stops polling and hides it from active management while preserving articles and provenance.

Feeds in `full_text` mode enqueue article fetching and extraction. `full_text_html` also retains the source HTML in the `article-data` Docker volume; extracted text and its current/previous hashes remain in PostgreSQL. Recreating containers preserves this volume. Include `article-data`, PostgreSQL, and configuration in backups; Elasticsearch remains rebuildable.

If polling stalls, check `docker compose logs scheduler worker api`, confirm Redis and PostgreSQL health, and inspect the source's fetch history. Security rejections usually mean DNS resolved to a non-public address. Timeouts, `429`, and server errors retry up to three times; the next normal cycle remains scheduled after failure. Elasticsearch may be stopped while ingesting and browsing RSS entries.

If article processing stalls, confirm the worker command includes `app.jobs.articles`, inspect Jobs for queued/retrying/failed stages, and retry terminal failures there. Configure article downloads with `NEWSINTEL_ARTICLE_TIMEOUT_SECONDS`, `NEWSINTEL_ARTICLE_MAX_RESPONSE_BYTES`, `NEWSINTEL_ARTICLE_REDIRECT_LIMIT`, and `NEWSINTEL_ARTICLE_HOST_MIN_INTERVAL_SECONDS`.

## Deployment

Deploy behind an existing HTTPS reverse proxy that forwards to port 8080. Set `NEWSINTEL_ENVIRONMENT=production` and `NEWSINTEL_SESSION_COOKIE_SECURE=true`. Restrict proxy access to the host network and configure the public hostname in `NEWSINTEL_ALLOWED_HOSTS` using Pydantic's JSON-list syntax. Back up the PostgreSQL volume and configuration; Elasticsearch remains derived and rebuildable.
