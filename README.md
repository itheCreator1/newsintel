# NewsIntel

**A self-hosted news-intelligence platform that turns a firehose of RSS feeds into a searchable, clustered, investigable archive — no LLM or cloud API required.**

NewsIntel continuously collects news, deduplicates it, extracts entities and keywords, clusters independent reporting on the same event, and gives you a fast full-text search and an interactive relationship graph over all of it. It's built to hold its own at real scale — the architecture targets roughly five million archived articles without a rewrite — while staying entirely self-hosted, auditable, and free of API keys.

![Backend](https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLAlchemy%202-009688)
![Frontend](https://img.shields.io/badge/frontend-Next.js%20%2B%20React%20%2B%20TypeScript-000000)
![Data](https://img.shields.io/badge/data-PostgreSQL%20%C2%B7%20Elasticsearch%20%C2%B7%20Redis-336791)
![License](https://img.shields.io/badge/license-MIT-blue)

## Why it's interesting

- **PostgreSQL is the single source of truth; Elasticsearch is disposable.** Every write lands in Postgres first and is committed before an indexing job is enqueued — the search index can be wiped and rebuilt from scratch at any time, and ingestion keeps working even if Elasticsearch is down.
- **Story clustering, not just deduplication.** Independently published articles about the same event stay distinct records, but the UI surfaces "also reported by N other sources" by clustering on title/text similarity, entity overlap, and timing — behind a swappable interface.
- **An entity relationship graph**, built from versioned NLP annotations (spaCy NER), lets you explore who and what keeps showing up together across the archive.
- **Domain-driven backend**, not a god-object API: `feeds`, `articles`, `ingestion`, `extraction`, `nlp`, `search`, `clustering`, `analytics`, `graph`, `investigations`, and `jobs` are separate modules under `backend/app`, each owning its models, service layer, and routes.
- **Async background processing** via Dramatiq + Redis, with a dedicated scheduler and NLP worker so feed polling, extraction, and entity/keyword tagging never block a request.
- **Saved investigations** — bookmark searches and clusters and come back to them as a running case file.
- **A dark glassmorphism UI** across all nine routes (overview, search, articles, sources, clusters, graph, jobs, saved searches, settings) built with Next.js App Router, TanStack Query, and Apache ECharts.
- **Typed and tested end to end** — SQLAlchemy 2 + Pydantic on the backend, TypeScript + generated API types on the frontend, 30+ backend test modules, `ruff`/`mypy` on Python, and per-phase acceptance gates in `infra/test-phaseN.sh`.

## Architecture at a glance

```
            ┌────────────┐      ┌──────────────┐
   feeds →  │  scheduler │ ───▶ │    worker    │ ───▶  PostgreSQL (source of truth)
            └────────────┘      │  (Dramatiq)  │            │
                                 └──────────────┘            │ enqueue indexing job
                                        │                    ▼
                                        ▼             ┌──────────────┐
                                 ┌──────────────┐      │ Elasticsearch │  (rebuildable index)
                                 │  nlp-worker  │      └──────────────┘
                                 │ (entities,   │             ▲
                                 │  keywords,   │             │
                                 │  clustering) │      ┌──────────────┐
                                 └──────────────┘      │  FastAPI api │ ◀── Next.js frontend
                                                        └──────────────┘
```

Six Compose services (`frontend`, `api`, `worker`, `nlp-worker`, `scheduler`, plus `postgres`/`redis`/`elasticsearch`) each do one job. See [AGENTS.md](AGENTS.md) for the full product specification, data model, and architecture rules.

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, SQLAlchemy 2, Alembic, Pydantic, `uv` |
| Background jobs | Dramatiq, Redis |
| Canonical storage | PostgreSQL |
| Search | Elasticsearch (versioned indices behind an alias, zero-downtime reindexing) |
| NLP | spaCy NER, pluggable/versioned processors |
| Extraction | Trafilatura, behind a replaceable extractor interface |
| Frontend | Next.js (App Router, static export), React, TypeScript, TanStack Query, Apache ECharts |
| Deployment | Docker Compose |

## Quickstart

1. Copy `.env.example` to `.env` and choose a strong PostgreSQL password.
2. Build and start dependencies: `docker compose --env-file .env -f docker/compose.yaml up -d postgres redis elasticsearch`.
3. Apply migrations: `docker compose --env-file .env -f docker/compose.yaml run --rm api alembic upgrade head`.
4. Create the initial account: `docker compose --env-file .env -f docker/compose.yaml run --rm api python -m app.cli create-user admin`.
5. Start the application: `docker compose --env-file .env -f docker/compose.yaml up -d --build`.
6. Open <http://127.0.0.1:8080>.

Stop services with `docker compose --env-file .env -f docker/compose.yaml down`. Named volumes retain data; `docker compose --env-file .env -f docker/compose.yaml down -v` intentionally destroys local data.

## Development checks

Backend: `cd backend && uv sync && uv run pytest && uv run ruff check . && uv run mypy app`

Frontend: `cd frontend && npm install && npm test && npm run typecheck && npm run build`

Per-phase acceptance gates live in `infra/test-phaseN.sh`. See [AGENTS.md](AGENTS.md) for feed/search/NLP/clustering conventions and deployment notes.

## License

[MIT](LICENSE)
