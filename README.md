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
- **An entity relationship graph**, built from versioned NLP annotations (spaCy NER), lets you explore who and what keeps showing up together across the archive. Every edge is explainable co-occurrence: open one to see the exact articles and stories behind it, under the same filters as the graph. Entity dossiers show an entity's articles, stories and closest neighbours.
- **Events.** A deterministic, versioned association engine groups related story clusters into events by time, shared entities, headline overlap and story country, and stores why each cluster joined. The **Events** UI lists them with filters (status, country, entity, date range) and opens a dossier with a UTC-day timeline, the member stories with their join scores and signals, the articles, and links to every entity — all served by the read-only `/api/v1/events` API.
- **Source dossiers.** Every source has a page with its health and fetch history, how many of its articles carry a publish date and were extracted (each metric shown against its denominator), what it covers (entities, countries by role, languages), and where it sits in story timing — first to publish in N of M shared stories, or the median minutes behind the first article. The metrics are descriptive; there is no quality score. Served by the read-only `/api/v1/sources` API.
- **Domain-driven backend**, not a god-object API: `feeds`, `articles`, `nlp`, `search`, `clustering`, `analytics`, `entities`, `graph`, `investigations`, `monitors`, `events`, `sources`, `compare`, `geo`, `operations`, and `jobs` are separate modules under `backend/app`, each owning its models, service layer, and routes.
- **Async background processing** via Dramatiq + Redis, with a dedicated scheduler and NLP worker so feed polling, extraction, and entity/keyword tagging never block a request.
- **Saved investigations and a watchlist.** Bookmark searches and come back to them as a running case file. Monitors watch a search, entity, source, country or story: start one from Search, Saved Searches, or an entity, source, story or map page. The scheduler evaluates them in the background. The **Watchlist** shows each monitor's new articles and stories, a deterministic "What changed" summary (new sources, entities and stories, and stories that gained sources) linked to the evidence, and a mark-as-seen action. A monitor's criteria are edited in Search, and the navigation counts the monitors with something new. Served by `/api/v1/monitors`.
- **A dark glassmorphism UI** across all fifteen routes (overview, search, articles, sources, clusters, entities, events, graph, map, compare, jobs, operations, saved searches, watchlist, settings) built with Next.js App Router, TanStack Query, and Apache ECharts.
- **Typed and tested end to end** — SQLAlchemy 2 + Pydantic on the backend, TypeScript + generated API types on the frontend, `ruff`/`mypy` on Python, and a CI gate that runs the PostgreSQL and Elasticsearch integration suite, an API-contract drift check, a backup-restore rehearsal and every Playwright browser workflow against a full Compose stack.

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

Five application services (`frontend`, `api`, `worker`, `nlp-worker`, `scheduler`) and three data services (`postgres`, `redis`, `elasticsearch`) run under Docker Compose, each doing one job.

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

## Operations

The Operations page (`/operations/`) shows dependency health, pipeline backlogs, feed health and storage. Two things it does not show:

- **Article file size.** Retained article HTML lives on the worker's `article-data` volume, which only the worker mounts. Measure it from the host: `docker compose --env-file .env -f docker/compose.yaml exec worker du -sh /var/lib/newsintel/articles`.
- **History retention.** The scheduler deletes, every hour, succeeded job rows older than 30 days that a newer row replaces, plus sessions that expired or were revoked more than 30 days ago. Failed rows are kept for diagnosis.

### Backup and restore

PostgreSQL holds everything that matters. Elasticsearch is rebuilt from it, so it needs no backup. Retained article HTML, on the worker's `article-data` volume, is optional. CI rehearses the dump and restore on every push (`infra/test-restore.sh`).

Back up:

```sh
alias dc='docker compose --env-file .env -f docker/compose.yaml'
dc exec -T postgres pg_dump -U newsintel -Fc newsintel > newsintel-$(date +%F).dump
dc run --rm --no-deps -T worker tar czf - -C /var/lib/newsintel articles > articles-$(date +%F).tgz  # optional
```

Restore:

```sh
dc stop api worker nlp-worker scheduler
dc exec -T postgres pg_restore -U newsintel -d newsintel --clean --if-exists --no-owner --exit-on-error < newsintel-DATE.dump
dc run --rm --no-deps -T worker tar xzf - -C /var/lib/newsintel < articles-DATE.tgz  # if backed up
dc run --rm api alembic upgrade head    # a dump from an older release needs the newer migrations
dc up -d
dc run --rm api python -m app.cli rebuild-search   # the index no longer matches the restored rows
```

## Development checks

Backend: `cd backend && uv sync && uv run pytest && uv run ruff check . && uv run mypy app`

Frontend: `cd frontend && npm install && npm test && npm run typecheck && npm run build`

Browser workflows: `infra/test-e2e.sh <search|investigations|monitors|graph>` builds a disposable Compose stack, runs that group's Playwright specs and cleans up; CI runs all four. The older `infra/test-phaseN.sh` scripts are kept as per-phase records.

## License

[MIT](LICENSE)
