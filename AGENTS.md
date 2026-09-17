# Repository guidance

## Project orientation

- NewsIntel is a self-hosted news archive with a Python backend and React (Next.js) frontend.
- The backend lives in `backend/` and uses FastAPI, SQLAlchemy, Alembic, Dramatiq, and Redis.
- The frontend lives in `frontend/` and uses React, Next.js (App Router, static export), TypeScript, and TanStack Query.
- `docker/compose.yaml` defines the application, worker, scheduler, PostgreSQL, Redis, and Elasticsearch services.
- `docker/compose.dev.yaml` and `docker/compose.e2e.yaml` provide development and test overrides.
- PostgreSQL integration tests live with the backend tests and require an explicitly configured test database.
- See "Product specification" below for target requirements.
- Read [the README](README.md) for verified setup and operating instructions.
- Read [the Phase 3 stabilization plan](docs/superpowers/plans/2026-09-13-phase3-stabilization.md) for historical findings and the current milestone.
- Treat the specification as the target, not proof that a feature exists.
- Verify implementation in code and tests before describing it as complete.
- Preserve historical findings in plans when recording newer evidence.

## Product specification

### 1. Purpose and scope

NewsIntel is a production-quality, self-hosted news-intelligence platform for continuous RSS/news collection, long-term archiving, search, analysis, and investigation. Docker Compose is the primary deployment target. The architecture must support approximately five million archived articles without a fundamental rewrite.

This specification consolidates `masterPrompt`. It defines the target product, not a claim that the current repository implements every requirement. Mandatory requirements use **must**; recommended approaches and examples retain their flexibility. Changes to the prescribed architecture require a clear explanation of the technical problem before implementation.

The core application must function without LLMs, cloud AI services, or API keys. Future AI integrations may be optional plugins only.

Primary user capabilities:

- Archive news from configurable feeds and optionally retrieve full articles.
- Search historical content and filter by date, source, geography, language, entities, and keywords.
- Identify trends, compare source coverage, and explore geographic coverage.
- Explore entity co-occurrence and related reporting about the same event.
- Save, bookmark, and reuse investigations.
- Monitor feed health, processing failures, and recovery operations.

Initial deployment is single-user with local authentication. V1 excludes Kubernetes, mandatory enterprise orchestration, mandatory Prometheus/Grafana, full article-body revision history, and mandatory AI processing. OIDC/OAuth and S3-compatible storage are future extension points, not required initial implementations.

### 2. Architecture and repository

#### 2.1 Required stack

| Area | Technology and responsibility |
| --- | --- |
| Backend | Python, FastAPI, asynchronous request/database architecture, uv, `pyproject.toml`, SQLAlchemy 2, Alembic, Pydantic settings |
| Canonical database | PostgreSQL |
| Background processing | Dramatiq and Redis |
| Search | Elasticsearch |
| Extraction | Replaceable extractor interface; Trafilatura by default |
| NLP | Replaceable, versioned processors; optional local spaCy NER |
| Frontend | Next.js (App Router, static export), React, TypeScript, TanStack Query, Apache ECharts |
| Live updates | Server-Sent Events (SSE) where useful |
| Raw HTML storage | Object-storage interface backed initially by a local filesystem/Docker volume |

The monorepo must contain `/backend`, `/frontend`, and `/infra`. Use domain-oriented modules, keeping worker tasks near their domains and avoiding giant utility folders or a single oversized `tasks.py`.

Suggested backend domains: `api`, `auth`, `feeds`, `articles`, `ingestion`, `extraction`, `nlp`, `search`, `clustering`, `analytics`, `jobs`, `exports`, `storage`, `db`, and `core` under `backend/app`.

Suggested frontend organization: feature modules for articles, search, feeds, analytics, graph, jobs, and settings, plus shared components, API integration, stores, and routing under `frontend/src`.

The Compose stack should contain approximately `frontend`, `api`, `worker`, `postgres`, `redis`, and `elasticsearch`. Additional services justified by these responsibilities may be used. Containers must provide appropriate health/readiness checks and run as non-root where practical.

#### 2.2 Data ownership and indexing

PostgreSQL must remain the canonical source of truth. Elasticsearch is a derived index and must be completely rebuildable from PostgreSQL. No archive data may exist exclusively in Elasticsearch.

The normal persistence sequence is:

1. Persist or update canonical data in PostgreSQL.
2. Commit successfully.
3. Enqueue an Elasticsearch indexing job.
4. Update Elasticsearch through a worker.
5. Track indexing state and version in PostgreSQL.

Ingestion must continue when Elasticsearch is unavailable. Pending or failed indexing must remain visible and recoverable. Use versioned indices, such as `articles-v1` and `articles-v2`, behind an alias such as `articles-current`, allowing future zero-downtime reindexing.

### 3. Data model and integrity

#### 3.1 Core concepts

Names may vary with justification, but preserve these separations:

| Concept | Required responsibility |
| --- | --- |
| Article | Canonical article metadata, identity, dates, URLs, language, and relevant change metadata |
| ArticleContent | Large extracted text, separated from article metadata; raw HTML object reference when retained |
| Feed | Configuration, source attributes, fetching mode, schedule, and health |
| FeedFetch | Persisted polling history, outcomes, errors, and scheduling information |
| FeedArticle | Many-to-many discovery/provenance relationship between feeds and canonical articles |
| Entity / ArticleEntity | Normalized entities and versioned article annotations |
| Keyword / ArticleKeyword | Normalized keywords and versioned article annotations |
| Country | ISO-coded geography supporting distinct source and story meanings |
| StoryCluster | Related reporting across independent articles |
| Processing/indexing state | Independent stage status, versions, failures, and timestamps |

A single article may be discovered through multiple feeds. `FeedArticle` must preserve feed-specific information including feed ID, original RSS GUID, RSS title, RSS description, discovery timestamp, feed-specific URL, and feed metadata.

Expose stable UUID-style identifiers through the API. Efficient composite or integer keys are appropriate for high-volume internal association tables; random UUIDs need not be every table's physical key strategy.

Use foreign keys, appropriate uniqueness constraints, and meaningful check constraints in addition to application validation. Use Alembic for schema changes.

#### 3.2 Identity, deduplication, and stories

- Preserve both the original URL and normalized/canonical URL.
- Normalize hostname casing, fragments, common tracking parameters such as `utm_*`, and known redirect/tracking wrappers where safe.
- Generate hashes of normalized titles and normalized extracted text.
- Prioritize canonical URLs, normalized titles, and exact content hashes for initial duplicate detection.
- Run near-duplicate detection and event clustering asynchronously, outside initial ingestion.
- Keep independently published reporting as separate articles even when outlets cover the same event. Deduplication must respect the distinction between article identity and story similarity.

Clustering must sit behind a replaceable interface. An initial implementation may combine title similarity, text similarity, entity overlap, and timestamps. The UI must expose related coverage, such as “Also reported by 7 other sources,” and allow users to open the full cluster.

#### 3.3 Content, geography, and language

Store current extracted article text in PostgreSQL. Do not implement full historical body versioning in v1. Retain content hashes, timestamps, and useful metadata showing that content changed; do not silently overwrite content without change metadata.

Raw HTML, when enabled, must be stored through the object-storage abstraction, initially on a local filesystem/Docker volume. PostgreSQL stores its reference/path. The interface must permit a later S3-compatible implementation without redesigning article storage.

Keep `source_country`, `primary_story_country`, and `mentioned_countries` distinct, using ISO country codes. A source based in Greece is not equivalent to a story about Greece.

The architecture must support multiple languages, with English processing shipped first. Store feed-declared expected language and article-detected language separately using standard language codes. Processors must be able to declare supported languages.

### 4. Feed management and ingestion

#### 4.1 Feed configuration and history

Users must be able to add, edit, enable, and disable feeds; assign country, expected language, categories/tags; configure polling interval and article-fetching behavior; and inspect feed health and errors.

Each feed must independently support modes equivalent to:

1. RSS only.
2. RSS plus full-article fetching.
3. RSS plus full-article fetching and raw HTML retention.

Use per-feed polling intervals and conditional HTTP requests with ETag and Last-Modified where supported. Persist fetch history including HTTP status, duration, discovered entries, new articles, errors, ETag, Last-Modified, timestamps, and next scheduled fetch.

Removing a feed must not automatically remove archived articles. Feed removal/disabling and article purging are separate operations. Provide an explicit purge workflow; do not add blanket soft-deletion columns without a concrete reason.

#### 4.2 Pipeline and recovery

The conceptual pipeline is:

```text
Fetch feed → normalize entries → canonicalize → deduplicate → persist
  → optionally fetch article → extract → NLP → cluster → index
```

Stages must be independently retryable and recoverable. Do not implement one giant transaction or job that restarts the entire pipeline when one stage fails. Jobs must be idempotent, safely retryable, and observable, with exponential backoff and an explicit failed/dead-letter state after repeated failures. Administrators must have retry mechanisms.

Track fetching, extraction, NLP, clustering, and indexing separately, including failures and timestamps where useful. A single `processed=true` field is insufficient. Support re-running extraction, NLP, clustering, and indexing for selected articles or date ranges.

#### 4.3 Fetching security and etiquette

Implement per-domain concurrency and rate limits and a configurable, identifiable User-Agent. Validate URLs and schemes, protect outbound fetching against SSRF, and reject unsafe internal/private network destinations unless explicitly supported by configuration. Outbound-network defaults must be conservative.

### 5. NLP and annotations

Use a plugin/processor architecture, with responsibilities such as keyword, entity, country, and keyphrase processing separated from worker orchestration. Every generated annotation must identify its processor, processor version, and relevant algorithm/model version so selected portions of a large archive can be reprocessed.

Keywords must use normalized records and article relationships retaining frequency/count, relevance/score, algorithm, and processor version. Provide user-editable global stop-word lists. Support layered processing such as term frequency, TF-IDF or equivalent, keyphrases, and named entities; simple token frequency alone is insufficient.

Entities must use normalized records. Supported concepts include PERSON, ORG, GPE/COUNTRY, LOCATION, EVENT, PRODUCT, and OTHER, while permitting future processor-defined types. Do not use a rigid PostgreSQL enum that prevents extension. Article relationships must support occurrence count, relevance, processor/version, and offsets or references where practical.

Local spaCy NER is optional. Disabling it must not prevent the application from functioning.

### 6. Search and investigations

#### 6.1 Search capabilities

Search must use Elasticsearch and cover title, RSS description, extracted body, entities, and keywords by default.

Support plain text and structured syntax, including these examples:

```text
NATO
"Nuclear weapons"
iran AND israel
source:reuters
country:GR
entity:"Donald Trump"
after:2026-01-01
```

Provide an advanced GUI filter builder so users do not need syntax. Filters must include time range, source, country, language, entity, entity type, keyword, story cluster, and processing state where useful. Geographic filters must preserve the source/story/mentioned-country distinction.

Support relevance, newest, oldest, and most-mentioned or another appropriate popularity ordering. Default ranking may combine Elasticsearch relevance and sensible recency weighting. Return highlighted matching snippets.

Use cursor-based pagination for large article/result collections. Do not rely on deep OFFSET pagination.

#### 6.2 Reusable investigation state

Encode query/filter state in the browser URL where practical. Users must be able to bookmark, reload, locally share, and navigate backward/forward through investigations without losing filters.

Cross-filtering must allow entities, sources, countries, chart segments, graph nodes, and timeline selections to refine the active investigation.

Saved searches must be named and preserve the complete relevant state: query, filters, date range, sorting, and other investigation settings.

### 7. User interface and analysis

Navigation should provide Overview, Articles, Timeline, Analytics, Graph, Sources, Saved Searches, Jobs, and Settings, with modular extension points.

#### 7.1 Overview and articles

The overview should include total articles, articles today, ingestion rate, active/failing feeds, failed jobs, indexing backlog, extraction failures, top entities/countries, and notable recent spikes. Charts must serve analysis rather than decoration.

Article browsing must use a dense table with customizable visible columns. Useful columns include publication time, source, source country, primary story country, title, major entities, language, and processing status. Use split-pane/detail interaction where practical to preserve investigation context.

Article detail must expose title, original URL, source, RSS metadata, extracted text, entities, keywords, countries, language, story cluster, related articles, processing metadata, and extraction/indexing status.

#### 7.2 Timeline

Search results must have interactive frequency-over-time context. Automatically choose useful hour/day/week/month buckets based on date range, with manual override. Brushing a timeline region must apply its date range to the active investigation.

#### 7.3 Initial analytics

Implement exactly three initial analytics areas:

| Module | Capabilities |
| --- | --- |
| Entity Trends | Show entities gaining/losing coverage; filter by source, country, entity type, and date; drill down to matching articles |
| Source Comparison | Compare selected outlets' topic/event coverage; potential dimensions include article volume, entities, keywords, geography, and time distribution |
| Geographic Coverage | Analyze source locations, primary story countries, mentioned countries, and volume over time as distinct concepts |

Use a registry-style frontend/backend design so modules can be added independently without rewriting the shell. Components must consume stable APIs; database logic must not live in frontend components. Permit richer source-comparison metrics later.

#### 7.4 Entity graph

Graph nodes represent entities; edges represent article co-occurrence, weighted by count or weighted count. Support date, source, country, and entity-type filters. Clicking a node must select/filter the entity, expose connected entities, and show relevant articles in a side panel.

Graph APIs must return constrained/top-N subgraphs using thresholds and user filters. Never send the full archive graph to the browser.

### 8. API, authentication, and frontend state

Use versioned REST endpoints under `/api/v1/...`. FastAPI/OpenAPI is the contract; generate TypeScript types/client from OpenAPI rather than maintaining duplicate DTO definitions. Keep handlers thin and introduce services, repositories, or query objects when they provide real separation, avoiding unnecessary CRUD abstraction layers.

Implement local username/password login with strong password hashing, server-side sessions and secure session cookies, applicable CSRF protection, and secure cookie settings. HTTP Basic Auth must not be the primary login. Document initial-user creation. Keep authentication abstracted to permit future OIDC/OAuth.

Use React context/local component state for appropriate application/client state and TanStack Query for server/cache state. Do not manually reproduce server-cache behavior in client state.

Use SSE for useful operational updates, including new ingestion counts, feed/job state, indexing backlog, and processing progress. Introduce WebSockets only if bidirectional realtime communication becomes necessary.

### 9. Operations, exports, and configuration

The jobs UI must expose queue backlog, failed jobs, indexing backlog, extraction failures, last successful feed fetch, feed errors, and retries, with safe retry/reprocessing actions.

Support CSV and JSON exports. Small exports may be synchronous where reasonable. Large exports must run asynchronously and provide status plus downloadable results.

Use structured JSON logging with relevant request/job ID, feed, article ID, job type, stage, and error category. Provide health and readiness endpoints, persisted feed errors/history, and visible job failures. Allow future metrics integration without requiring Prometheus/Grafana containers in v1.

Configuration must use `.env`, typed Pydantic settings, documented environment variables, and `.env.example`. Validate critical configuration at startup. Never commit real secrets or bake them into images.

Migrations must have an explicit documented command/process; application containers must not race to migrate on startup.

Provide Docker-friendly PostgreSQL backup/restore scripts or workflows. Document backup coverage for PostgreSQL, raw HTML/object storage, and configuration. Elasticsearch is rebuildable and is not an authoritative backup requirement.

The security baseline also includes SQL injection prevention through ORM/query APIs, sensible CORS policy, strict input validation, dependency hygiene, and no arbitrary shell execution from user input.

### 10. Scale and engineering quality

Design for approximately five million articles without overengineering for billions. Major query paths must be assessed for query plans, index usage, pagination, payload size, N+1 behavior, memory usage, and the appropriate execution location: PostgreSQL, Elasticsearch, or workers.

Use appropriate PostgreSQL indexes, efficient association tables, bulk inserts/upserts, bulk entity/keyword association writes, and batched Elasticsearch indexing where beneficial. Avoid individual queries/transactions for thousands of annotations, deep OFFSET pagination, unbounded graph responses, and blocking large exports. Use efficient Elasticsearch mappings and document significant scaling assumptions.

Do not prematurely partition every table. Keep schemas/migrations amenable to later partitioning of high-volume time-based tables if profiling justifies it.

Require formatting, linting, static typing, clear naming, predictable errors, concise documentation, and manageable file sizes. Prefer explicit code and domain boundaries over unnecessary patterns. Document public interfaces whose purposes are not obvious.

No specific latency, throughput, deployment hardware, or export-size threshold is supplied by the prompt. Define these when implementing and measuring the relevant feature; do not treat invented numbers as source requirements.

### 11. Testing and acceptance criteria

Maintain meaningful backend unit, API integration, ingestion, canonicalization/deduplication, and job-idempotency tests, plus search/indexing integration tests where practical. Maintain important frontend component tests and selected E2E flows. Prioritize critical behavior and failure modes over coverage percentages.

The completed product must demonstrate:

1. Login, feed creation, ingestion, search, filtering, article detail, saved searches, and analytics through selected E2E flows.
2. Multiple feed discoveries can reference one canonical article while preserving each discovery's metadata.
3. Different outlets reporting the same event remain separate articles and can be grouped into a story cluster.
4. Conditional feed requests and fetch history expose successful, unchanged, and failed polling outcomes.
5. Fetching modes control full-content retrieval and raw HTML retention; retained HTML resides outside PostgreSQL with a stored reference.
6. Elasticsearch downtime does not prevent canonical ingestion; indexing state remains recoverable, and an index can be rebuilt from PostgreSQL behind a versioned alias.
7. Repeated jobs do not corrupt or duplicate canonical data; stage failures, backoff, terminal failures, and administrative recovery are observable.
8. Source, primary story, and mentioned-country semantics remain distinct across storage, filters, and analytics.
9. NLP annotations retain processor/model provenance and can be selectively reprocessed; the core works without spaCy or external AI services.
10. Search supports the documented query forms, GUI filters, snippets, sorting, and cursor pagination.
11. URL state survives reload/back/forward and reproduces an investigation; named saved searches restore the full relevant state.
12. Timeline brushing and analytic/graph drill-down refine the active investigation; graph payloads remain bounded.
13. Feed removal preserves archived articles; article purging is an explicit separate workflow.
14. Large exports run asynchronously with status/download handling; documented backup/restore and reindex workflows preserve recoverability.
15. Authentication, CSRF where applicable, outbound SSRF protection, URL validation, and database constraints have meaningful verification.
16. Major archive queries and batch paths are assessed against the five-million-article scale target, with scaling assumptions documented.

### 12. Delivery phases

Implement coherent vertical phases and keep the repository runnable after each phase. Dependencies may justify ordering adjustments while preserving the architecture.

| Phase | Scope and exit condition |
| --- | --- |
| 1 — Foundation | Monorepo, Compose, PostgreSQL, Redis, Elasticsearch, FastAPI, Vue, settings, migrations, authentication, and health checks run together |
| 2 — Feed ingestion | Feed models/API/UI, scheduler, RSS fetching, fetch history, conditional requests, article persistence, and canonicalization/deduplication work end to end |
| 3 — Article processing | Dramatiq stages, article fetching, extraction, ArticleContent, retry/failure handling, and operational visibility work |
| 4 — Search | Elasticsearch indexing, search API/UI, filters, highlighting, and cursor pagination work |
| 5 — NLP | Processor framework, keywords, countries, entities, language handling, and versioned outputs work |
| 6 — Investigations | Timeline, cross-filtering, URL investigation state, and saved searches work |
| 7 — Relationships | Story clustering, related reporting, and constrained entity co-occurrence graph work |
| 8 — Analytics | Entity Trends, Source Comparison, and Geographic Coverage work as modular features |
| 9 — Operational maturity | Exports, reprocessing tools, backup/restore tooling, performance tuning, and stronger E2E coverage are delivered |

Before substantial implementation, inspect the existing repository and report structural conflicts, create/update a concise architectural README, establish missing monorepo/Compose/migration foundations, and demonstrate the smallest working slice:

```text
RSS feed → worker fetch → PostgreSQL article → Elasticsearch index
  → FastAPI search → Vue result
```

This initial slice may bring minimal indexing/search forward from Phase 4; full search capabilities remain a later phase. Do not infer phase completion from the presence of files alone.

Document starting/stopping the stack, dependency installation, local backend/frontend development, migrations, initial-user creation, tests, formatting, linting, type checking, Elasticsearch rebuilding, manual reprocessing, PostgreSQL backup, and PostgreSQL restore.

Routine low-level decisions may be made during implementation. Seek clarification only for decisions that materially change the prescribed architecture, data model, security model, or product behavior.

## Architecture rules

- PostgreSQL owns canonical application data.
- Elasticsearch is a derived, rebuildable search index.
- Feed ingestion and article extraction must continue when Elasticsearch is unavailable.
- Keep fetch, extraction, indexing, and other background stages independently retryable.
- Make background work idempotent so duplicate delivery is safe.
- Use explicit claims or ownership checks for concurrent workers.
- A stale worker must not overwrite newer job or article state.
- Keep durable state transitions in database transactions.
- Preserve the ability to rebuild derived systems from PostgreSQL and retained objects.

## Implementation conventions

- Follow the existing domain boundaries under `backend/app/`.
- Keep backend code fully typed and compatible with strict mypy settings.
- Use SQLAlchemy models and services consistently with neighboring modules.
- Keep external I/O behind focused interfaces when a boundary is needed.
- Follow existing React component and TanStack Query patterns under `frontend/src/`.
- Keep list queries bounded; never add an unbounded archive scan to a request path.
- Use cursor pagination for growing or large collections.
- Reset cursors when filters or sort criteria change.
- Preserve source provenance when deduplicating canonical articles.
- Preserve archived articles when a source is retired or removed from active use.
- Avoid database migrations unless the data model actually changes.
- When a migration is necessary, make forward and rollback behavior explicit.

## Security and operations

- Preserve session authentication and authorization checks on protected routes.
- Preserve CSRF validation for state-changing browser requests.
- Preserve SSRF defenses for feed and article fetching.
- Private or loopback fixture-host exceptions belong only in tests.
- Never weaken production network validation to make a fixture pass.
- Never commit passwords, tokens, private keys, `.env`, or generated secrets.
- Use `.env.example` only for safe example values and documented settings.
- Application containers do not run migrations automatically.
- Run migrations explicitly with `docker compose -f docker/compose.yaml run --rm api alembic upgrade head`.
- Confirm worker and scheduler registration when adding a background actor.
- Keep persistent article storage mounted where workers and maintenance commands need it.

## API contracts

- The checked-in OpenAPI snapshot is `frontend/openapi.json`.
- Generated frontend API types are `frontend/src/types.generated.ts`.
- Regenerate the OpenAPI snapshot and frontend types together when contracts change.
- Generate types with `cd frontend && npm run generate:api` after updating the snapshot.
- Never hand-edit `frontend/src/types.generated.ts`.
- Preserve existing HTTP contracts unless the requested change explicitly alters them.
- Update backend tests and frontend consumers in the same contract-changing commit.

## Verification

- Install backend dependencies with `cd backend && uv sync`.
- Run backend tests with `cd backend && uv run pytest`.
- Run backend lint with `cd backend && uv run ruff check .`.
- Run backend type checking with `cd backend && uv run mypy app`.
- Install frontend dependencies with `cd frontend && npm install`.
- Run frontend tests with `cd frontend && npm test`.
- Run frontend type checking with `cd frontend && npm run typecheck`.
- Run the production frontend build with `cd frontend && npm run build`.
- Validate Compose with `docker compose -f docker/compose.yaml config --quiet`.
- Use the PostgreSQL URL and opt-in variables documented by the relevant integration tests.
- A skipped PostgreSQL test does not establish integration success.
- Record pass, fail, and skip counts from the commands actually run.
- Do not document a future acceptance command until its script exists and is executable.
- Match verification effort to the changed behavior, then run the milestone gate before completion.

## Working practices

- Inspect `git status` and relevant diffs before editing.
- Preserve unrelated user changes in a dirty worktree.
- Read neighboring implementation and tests before choosing a pattern.
- Reproduce a defect with a focused failing test before fixing it.
- Keep tests focused on observable behavior and meaningful failure modes.
- Avoid unrelated refactoring, formatting churn, and dependency upgrades.
- Keep generated artifacts out of commits unless the source contract changed.
- Update operational documentation when commands or runtime behavior change.
- Reconcile plan checkboxes only from recorded verification evidence.
- Report the commands run and their actual outcomes, including skips and limitations.
- Report remaining specification gaps separately from completed milestone work.

## Atomic commits

- Make one independently reviewable logical change per commit.
- Include a change's tests and necessary documentation in the same commit.
- Keep unrelated defects in separate commits.
- Stage explicit files rather than staging the entire worktree.
- Review the staged diff before committing.
- Use a commit message that describes the behavior or guidance changed.
- Do not mix generated artifacts, formatting cleanup, or opportunistic refactors into a commit.
- Each commit must be understandable, testable, reviewable, and revertible on its own.

## Data protection

- Use isolated test databases; never point tests at the user's archive database.
- Use uniquely named Compose projects for acceptance and destructive test workflows.
- Use isolated named volumes for PostgreSQL, Redis, Elasticsearch, and article storage tests.
- Bind test services to configurable, non-production ports.
- Never run `docker compose -f docker/compose.yaml down -v` against the user's normal Compose project.
- Never reset, truncate, or migrate the user's archive as part of a test.
- Never remove the user's PostgreSQL or article-storage volumes.
- Prefer dry-run maintenance commands before apply mode.
- Resolve cleanup candidates against current database references before deleting objects.
- Capture failure logs before tearing down an isolated acceptance environment.
