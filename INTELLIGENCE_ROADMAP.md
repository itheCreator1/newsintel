# NewsIntel Intelligence Roadmap

Last updated: 2026-09-20

This document is the persistent implementation state for NewsIntel's intelligence and investigation roadmap. It must be updated after every completed phase and whenever repository discoveries change the proposed architecture.

## Session state

- Current phase: Phase 12A — Event domain/schema (branch `phase/12a-event-schema`)
- Current status: `COMPLETE`
- Next action: review Phase 12A; merge into local `main` only on explicit approval (nothing is pushed). Phase 12B (event association engine) starts afterwards from `main`.
- Deferred work: Every phase after 12A remains `NOT STARTED` until the preceding phase meets its acceptance criteria. Deferred inside 11D/11E: entity/source/country/cluster "Watch" buttons (the API supports those kinds), editing a monitor's criteria in the UI, an unseen badge in the navigation, persisted per-window change snapshots (would make story growth fully reproducible), and a lookback limit or seen-set table for the "never matched before" check.
- Verification state: Phases 10A (`2841cdc`), 10B (`0fd4126`), 10C (`dacd349`), 11A (`957b8aa`), 11B (`0f39ae0`), 11C (`74073cb`), 11D (`5c0e22c`) and 11E (`4e553ed`) are merged into local `main` (unpushed); a review fix to the graph edge-evidence cursor is merged as `678cec1`. Phase 12A is implemented on its branch and not yet merged. Phase 11E `infra/test-phase11e.sh` (monitor backend tests, Ruff, mypy, Vitest 126 tests three times in a row, typecheck, static build, OpenAPI drift check, Alembic head still `0010`, the real Elasticsearch/PostgreSQL change-summary tests, `rebuild-search`, and the Playwright monitor workflow against the Compose stack) passed on 2026-09-20. Phase 12A `infra/test-phase12a.sh` (migration `0010`↔`0011` round trip, 16 event tests including the PostgreSQL-gated ones, Ruff, mypy, OpenAPI drift check) passed on 2026-09-20.

## Current architecture

> Reality-check status: `COMPLETE` for roadmap initialization. Directly relevant code must still be reinspected at the start of every phase.

- **Backend composition:** `backend/app/main.py` builds one FastAPI application and mounts health, auth, feeds/articles, NLP, clustering, search, graph, investigations, and analytics routers under `/api/v1`. Domains keep models, schemas, routes, and focused service/execution modules together; there is no generic repository layer.
- **Canonical articles and sources:** `backend/app/feeds/models.py` stores feeds, feed-fetch history, canonical articles, many-feed provenance through `feed_articles`, extracted content, and durable processing jobs/attempts. Articles use `published_at` where known and `first_discovered_at` as the ingestion timestamp/fallback.
- **NLP and entities:** `backend/app/nlp/models.py` stores versioned processor state/runs plus canonical `nlp_entities`, current/historical article-entity associations, keywords, language annotations, and country annotations. Entity identity is unique on `(language, entity_type, normalized_text)`; `display_text` is the canonical label. Per-article `occurrences` JSON holds extraction offsets/evidence, not persisted surface text; no alias table exists.
- **Story clusters:** `backend/app/clustering/models.py` stores derived clusters and one membership row per clustered article. Clusters cache article/source counts, publication bounds, representative article, and algorithm version. `GET /api/v1/clusters/{id}` exposes keyset-paginated members and feed references.
- **Search:** PostgreSQL stores delivery/rebuild coordination; Elasticsearch holds versioned, rebuildable article indices. Typed criteria support query text, sources/countries, dates, processing/language, entities/types, keywords, story/mentioned countries, and story clusters. Search and timeline share these criteria.
- **Graph:** `backend/app/graph` builds a bounded entity co-occurrence graph from Elasticsearch nested aggregations (`MAX_NODES = 50`, `MAX_EDGES = 150`) and resolves labels from PostgreSQL. Edges expose article co-occurrence weight; `GET /graph/edges/evidence` resolves one edge (same filters and focus) to its articles and stories. Entity dossiers live under `/entities`.
- **Investigations:** saved searches are user-owned PostgreSQL JSONB records containing a strictly validated, versioned `InvestigationState`. Listing uses keyset pagination and service queries enforce ownership. Monitors (`backend/app/monitors`, table `monitors`) embed a per-kind-validated `InvestigationState` snapshot plus compact evaluation cursors, unseen counters and lease fields; the scheduler claims due monitors and a Dramatiq actor (`app.jobs.monitors`, queue `monitors`) evaluates them incrementally through `app.monitors.evaluation`; the API (`/api/v1/monitors`, including `GET /monitors/{id}/changes`) and the watchlist UI (`/monitors`, detail at `/monitors/?id=`, with a "What changed" panel) are in place.
- **Events (12A, storage only):** `backend/app/events` holds `Event`, `EventCluster` and `EventEntity` (tables `events`, `event_clusters`, `event_entities`, migration `0011`) plus repository primitives; no association logic, route or UI yet. An event carries an `algorithm_version`, a `status` (`active`/`closed`/`superseded`), a cached time span and `primary_country`; clusters and entities attach through normalized association rows.
- **Analytics:** current SQL-backed analytics provide a 30-day ingestion timeline with deterministic spike detection, top entities, and primary story countries. Top aggregations are bounded to ten items.
- **Jobs and scheduling:** durable feed, extraction, indexing, NLP, and clustering jobs use status/due/lease fields. `backend/app/scheduler.py` claims bounded batches (feeds, articles, NLP, clustering, search, monitors) and dispatches Dramatiq actors via Redis. Separate NLP, clustering, and search queues exist, and status/failure routes feed the Jobs UI.
- **Database and migrations:** Alembic revisions `0001`–`0011` cover auth, ingestion, processing, search, NLP, saved searches, clusters, country-rule version widening, monitors, and events. PostgreSQL JSONB is already used for durable structured state.
- **Frontend:** Next.js App Router has overview, search, articles, sources, clusters, entities (dossier), graph (with edge evidence), jobs, saved searches, watchlist (monitors), and settings routes. `frontend/src/lib/api.ts` consumes types generated from checked-in `frontend/openapi.json`; aliases live in `api-types.ts`. TanStack Query, URL-backed investigation state, `GlassPanel`, `PageHeader`, charts, and explicit loading/error/empty states are established patterns.
- **Tests and acceptance:** pytest includes unit/API tests and PostgreSQL integration modules gated by `NEWSINTEL_RUN_POSTGRES_TESTS=1`. Vitest/Testing Library covers frontend routes; Playwright covers search, NLP, processing, investigations, and relationships. Every implementation phase has a checked-in acceptance script at `infra/test-phase<id>.sh`; scripts exist for phases 3–7, 10A, 10B, 10C, 11A, 11B, 11C, 11D, 11E and 12A.
- **Deployment:** Compose defines frontend, API, general worker, NLP worker, scheduler, PostgreSQL, Redis, and Elasticsearch, with development, E2E, and NER overlays. PostgreSQL is canonical and Elasticsearch is rebuildable.

## Target architecture

- **Articles** remain the primary evidence records. Every derived dossier, relationship, monitor result, event, comparison, and map result must be traceable to bounded article sets.
- **NLP annotations and Entities** provide canonical entity identity and observed mentions. Entity dossiers aggregate existing annotations rather than introduce a parallel identity system.
- **Story Clusters** remain groups of closely related articles. They contribute dossier context and graph evidence and become inputs to the higher-level Event domain.
- **Search** continues to use the existing investigation/query representation. Monitors persist and incrementally evaluate that representation instead of creating a second query language.
- **Graph** relationships represent explainable co-occurrence unless an explicit, evidence-bearing semantic relationship model is added later. Edge details resolve back to articles and clusters.
- **Investigations** remain bookmarkable and supply reusable saved/search state for monitors, comparisons, and geographic refinement.
- **Analytics** are deterministic SQL/search aggregations with explicit windows and bounded results. No LLM-generated conclusions or opaque scores are introduced.
- **Jobs** expose execution, retry, and failure state for monitor evaluation, event association, indexing, NLP, clustering, and operational health.
- **PostgreSQL** stores canonical entities, annotations, monitor state, events, associations, and durable cursors/checkpoints. Derived or cached state must be documented before introduction.
- **Elasticsearch** supports rebuildable article retrieval and existing search capabilities; it is not the sole store for durable monitor or event state.
- **Dramatiq** runs appropriate incremental background evaluation through the existing scheduler and observable job mechanisms.

## Delivery rules

1. Only one implementation phase may be `IN PROGRESS` at a time.
2. Reinspect directly relevant code before each phase.
3. Implement the smallest complete vertical slice using existing abstractions.
4. Use Alembic for schema changes and generated OpenAPI types for frontend API contracts.
5. Add tests before production behavior and observe the expected failure.
6. Run phase-relevant checks and fix regressions before marking a phase `COMPLETE`.
7. Run full suites before completing a major phase.
8. Update this roadmap with outcomes, discoveries, risks, deviations, and the exact next action.
9. Keep each logical phase suitable for a focused commit; never commit known-broken work.
10. Implement each phase on its own branch, created from the integration branch after the preceding phase has been reviewed and integrated. Use `phase/<phase-id>-<short-name>` names (for example, `phase/10a-entity-dossier-backend`). Do not stack a new phase on an unintegrated phase branch.
11. Roadmap-only corrections may be made on the current branch before Phase 10A implementation begins; once implementation starts, the roadmap updates belonging to that phase travel on the same phase branch.
12. Every implementation phase must add or update a checked-in acceptance script at `infra/test-phase<id>.sh`. It must provision any isolated dependencies, run that phase's focused checks, and clean up its resources. The script supplements focused tests; it does not replace them.

## Phase checklist

- [x] Phase 10A — Entity dossier backend (`COMPLETE`)
- [x] Phase 10B — Entity dossier frontend (`COMPLETE`)
- [x] Phase 10C — Evidence-backed graph relationships (`COMPLETE`)
- [x] Phase 11A — Monitor data model (`COMPLETE`)
- [x] Phase 11B — Monitor evaluation and scheduler (`COMPLETE`)
- [x] Phase 11C — Monitor API/backend (`COMPLETE`)
- [x] Phase 11D — Monitor frontend (`COMPLETE`)
- [x] Phase 11E — What Changed (`COMPLETE`)
- [x] Phase 12A — Event domain/schema (`COMPLETE`)
- [ ] Phase 12B — Event association engine (`NOT STARTED`)
- [ ] Phase 12C — Event API (`NOT STARTED`)
- [ ] Phase 12D — Event frontend (`NOT STARTED`)
- [ ] Phase 13A — Source dossiers (`NOT STARTED`)
- [ ] Phase 13B — Compare mode (`NOT STARTED`)
- [ ] Phase 13C — Investigation map (`NOT STARTED`)
- [ ] Phase 13D — Operational analytics (`NOT STARTED`)

## Phase details

### Phase 10A — Entity dossier backend

- **Status:** `COMPLETE`
- **Objective:** Provide a typed, paginated, database-backed entity dossier with identity, explicitly unavailable aliases, mention/article/cluster totals, first/last seen timestamps, a bounded mention timeline, recent articles and clusters, co-occurring entities, countries, and sources.
- **Existing components to reuse:** `Entity`, `ArticleEntity`, `ArticleCountryAnnotation`, `Article`, `FeedArticle`, `Feed`, `StoryClusterMember`, and `StoryCluster`; keyset cursor patterns from feed/clustering routes; `current_session` authorization; Pydantic response schemas; and the checked-in OpenAPI generation workflow.
- **Backend changes:** Add focused entity dossier queries and route handlers. Keep aggregation in SQL, avoid N+1 access, and define explicit bounds for timeline and top-N results.
- **Frontend changes:** None in this phase beyond regenerating API types if that is part of the repository's backend contract workflow.
- **Database changes:** Prefer none. Add an Alembic migration only if query-plan measurements justify a missing index.
- **API changes:** Expected dossier detail plus paginated article and cluster collections and a bounded relationships/aggregation response under `/api/v1/entities/{entity_id}` conventions. Final shape follows audited route conventions.
- **Expected indexes:** Annotation entity/article lookup; article publication time; cluster/article association lookup; source and country grouping paths. Confirm existing indexes and explain plans before adding any.
- **Expected tests:** Correct aggregation and distinct counts, unavailable aliases (`aliases: []` with an explicit status), timeline bounds, pagination, empty results, unknown entity behavior, relationship evidence foundations, authorization, and query-count/N+1 protection where the suite supports it; plus `infra/test-phase10a.sh` covering the isolated acceptance path.
- **Scalability:** SQL aggregation, bounded top lists, paginated evidence lists, no archive-wide Python ID materialization, stable ordering, and keyset pagination where existing conventions support it.
- **Dependencies:** Repository reality check; existing NLP/entity identity and article-cluster schema.
- **Acceptance criteria:** Typed endpoint responses return correct values from real persisted fixtures; lists are bounded/paginated; missing entities follow API conventions; relevant backend tests, lint, and type checks pass; `infra/test-phase10a.sh` passes; any schema change upgrades/downgrades cleanly; roadmap records measured index decisions.
- **Suggested commit:** `feat(entities): add entity dossier API`

### Phase 10B — Entity dossier frontend

- **Status:** `COMPLETE`
- **Objective:** Add `/entities?id=<uuid>` (the static export has no dynamic routes; see Decision log) as a dense analyst dossier and link high-value entity references to it.
- **Existing components to reuse:** App Router layout, generated API types, TanStack Query hooks/query keys, `GlassPanel`, `PageHeader`, chart primitives, loading/error/empty states, article/cluster cards, and existing entity renderers.
- **Backend changes:** Only contract corrections discovered by frontend integration.
- **Frontend changes:** Route, typed query hooks, dossier panels, mention timeline, bounded related lists, and selective entity links in article annotations, cluster detail, graph, analytics, and search results where useful.
- **Database changes:** None.
- **API changes:** Consume Phase 10A without duplicate handwritten response models.
- **Expected indexes:** None beyond 10A.
- **Expected tests:** Loading, error, empty, populated rendering, navigation, pagination, accessible interactions, and bookmarkable route behavior; Playwright coverage for an article-to-entity investigation path.
- **Scalability:** Paginate articles/clusters, lazy-load secondary panels when consistent with current patterns, and avoid requesting unbounded chart series.
- **Dependencies:** Phase 10A.
- **Acceptance criteria:** Route renders all required dossier sections from typed API data; investigation links work without turning arbitrary prose into links; frontend tests, typecheck, build, and relevant Playwright workflow pass.
- **Suggested commit:** `feat(entities): add entity dossier interface`

### Phase 10C — Evidence-backed graph relationships

- **Status:** `COMPLETE`
- **Objective:** Make each displayed entity graph edge inspectable as co-occurrence evidence.
- **Existing components to reuse:** Existing graph endpoint/UI, entity annotations, article-cluster relations, article cards, and graph selection behavior.
- **Backend changes:** Add bounded edge-evidence queries for co-occurring article/cluster counts, first/latest dates, recent articles, clusters, and snippets only if existing annotation offsets make them efficient and reliable.
- **Frontend changes:** Edge detail inspector with explicit co-occurrence wording and links to supporting evidence.
- **Database changes:** Prefer none; index only from measured query plans.
- **API changes:** Add or extend an edge evidence endpoint with stable typed responses.
- **Expected indexes:** Composite annotation lookup by entity/article and cluster membership indexes, subject to audit.
- **Expected tests:** Counts, date bounds, deduplication, pagination/bounds, traceability, missing edge behavior, UI states, and graph-to-evidence interaction.
- **Scalability:** Aggregate in SQL, cap evidence, require entity pair parameters, and avoid materializing whole graphs.
- **Dependencies:** 10A and 10B entity navigation.
- **Acceptance criteria:** Every rendered edge can disclose its deterministic meaning and supporting source records; backend/frontend checks pass.
- **Suggested commit:** `feat(graph): expose relationship evidence`

### Phase 11A — Monitor data model

- **Status:** `COMPLETE`
- **Objective:** Persist durable monitors that reuse saved investigation/search state and support search, entity, source, country, and suitable evolving-cluster targets.
- **Existing components to reuse:** Saved searches/investigations, users/authorization, timestamps, JSON configuration conventions, and job state.
- **Backend changes:** Domain model and repository primitives with typed monitor configuration validation.
- **Frontend changes:** Regenerated types only.
- **Database changes:** Reversible Alembic migration for monitor identity, owner, type/configuration, enabled state, evaluation/view cursors, latest match, unread counters, and timestamps; constraints for valid ownership/name semantics.
- **API changes:** None beyond schemas needed for later phases.
- **Expected indexes:** Owner/list ordering, enabled/due evaluation, target lookup where relational, and latest activity.
- **Expected tests:** Constraints, configuration validation, timestamp/counter defaults, ownership, update semantics, and migration round-trip.
- **Scalability:** Store compact query configuration and cursors, not copied result sets; design due-monitor lookup for indexed batching.
- **Dependencies:** Existing saved investigation/search representation audit.
- **Acceptance criteria:** Schema and model support required durable state, migration reversibility, and passing model/migration tests.
- **Suggested commit:** `feat(monitors): add durable monitor model`

### Phase 11B — Monitor evaluation and scheduler

- **Status:** `COMPLETE`
- **Objective:** Deterministically and incrementally evaluate enabled monitors through existing worker/scheduler infrastructure.
- **Existing components to reuse:** Search/database query execution, Dramatiq actors, scheduler, retries, and Jobs observability.
- **Backend changes:** Replaceable evaluators per monitor type, durable high-water marks, idempotent result accounting, scheduled batching, retry/failure reporting.
- **Frontend changes:** None.
- **Database changes:** Add evaluation run/match state only if existing job records and monitor cursors cannot safely provide idempotency/provenance.
- **API changes:** None required.
- **Expected indexes:** Due-monitor scan and incremental article/cluster timestamp/identifier scans.
- **Expected tests:** Incremental evaluation, idempotency, retries, disabled monitors, concurrent/repeated delivery, counter correctness, and observable failure state.
- **Scalability:** Never rescan the full archive; batch due monitors and new records; use stable high-water marks and bounded transactions.
- **Dependencies:** 11A and audited query model.
- **Acceptance criteria:** New records are counted exactly once across retries and reruns; failures appear through existing operations state; worker tests pass.
- **Suggested commit:** `feat(monitors): evaluate monitors incrementally`

### Phase 11C — Monitor API/backend

- **Status:** `COMPLETE`
- **Objective:** Provide authorized CRUD, list/detail, enable/disable, result access, and safe viewed-state operations.
- **Existing components to reuse:** API routers, ownership/auth dependencies, pagination, saved search schemas, and monitor repository/evaluator.
- **Backend changes:** Typed endpoints and transactional viewed-state update.
- **Frontend changes:** Regenerated OpenAPI types.
- **Database changes:** None expected.
- **API changes:** Monitor collection/detail/update/delete/result/viewed endpoints under `/api/v1` following repository conventions.
- **Expected indexes:** Owner plus activity ordering from 11A.
- **Expected tests:** Authorization isolation, CRUD validation, pagination, state transitions, viewed race safety, and enabled-state behavior.
- **Scalability:** Paginated lists/results and atomic counter/cursor updates.
- **Dependencies:** 11A–11B.
- **Acceptance criteria:** API exposes durable monitor state without leaking ownership and all contract tests/checks pass.
- **Suggested commit:** `feat(monitors): add monitor API`

### Phase 11D — Monitor frontend

- **Status:** `COMPLETE`
- **Objective:** Add or cleanly extend a watchlist interface with visible unseen activity and safe interactions.
- **Existing components to reuse:** Saved-search UI, shell/navigation, query-state conventions, panels, chips, controls, and generated types.
- **Backend changes:** Only integration corrections.
- **Frontend changes:** Monitor list/detail/create/edit flows; status, last checked/latest result, new article/cluster counts, enabled toggle, and viewed-state mutation.
- **Database changes:** None.
- **API changes:** Consume 11C.
- **Expected indexes:** None beyond earlier phases.
- **Expected tests:** Loading/error/empty/rendering, create/update/toggle, unseen styling, viewed-state mutation, URL state, and Playwright monitor workflow.
- **Scalability:** Paginated monitor/result lists and targeted cache invalidation.
- **Dependencies:** 11C.
- **Acceptance criteria:** Analysts can manage and inspect monitors with accurate unseen state; frontend checks and relevant Playwright tests pass.
- **Suggested commit:** `feat(monitors): add watchlist interface`

### Phase 11E — What Changed

- **Status:** `COMPLETE`
- **Objective:** Show deterministic changes since the monitor's previous viewed/evaluation boundary.
- **Existing components to reuse:** Monitor cursors/matches, article/cluster/entity/source timestamps, cluster source membership, and detail UI.
- **Backend changes:** Persist or derive auditable deltas for new articles, clusters, entities, sources, and material cluster source-count growth.
- **Frontend changes:** Plain-language deterministic change list with evidence links.
- **Database changes:** Add snapshots/delta records only if timestamp derivation cannot be made correct and efficient; document retention.
- **API changes:** Typed monitor change summary/evidence response.
- **Expected indexes:** Monitor/time ordered match/delta access.
- **Expected tests:** Boundary timestamps, repeated viewing, late-arriving data, cluster growth, deterministic wording inputs, and UI states.
- **Scalability:** Incremental deltas and bounded retention; no full-history comparison per request.
- **Dependencies:** 11A–11D.
- **Acceptance criteria:** Every change statement is reproducible from persisted state and links to evidence; suites pass.
- **Suggested commit:** `feat(monitors): add deterministic change summaries`

### Phase 12A — Event domain/schema

- **Status:** `COMPLETE`
- **Objective:** Add version-aware Events above story clusters with normalized cluster and entity associations.
- **Existing components to reuse:** Story cluster, entity, country/location, timestamps, model/migration conventions, and job provenance.
- **Backend changes:** Event domain model and repository operations.
- **Frontend changes:** Regenerated types only if schemas are exposed during this slice.
- **Database changes:** Reversible Alembic migration for events, event-cluster, and event-entity associations with foreign keys, uniqueness, status, and algorithm provenance.
- **API changes:** None required in this phase.
- **Expected indexes:** Event time/status/country, association reverse lookups, and algorithm version.
- **Expected tests:** Constraints, cascades, association uniqueness, timestamp/status behavior, and migration round-trip.
- **Scalability:** Compact normalized associations; no copied article lists; efficient cluster-to-event lookup.
- **Dependencies:** Stable cluster/entity domains.
- **Acceptance criteria:** Schema represents multiple clusters per event and future algorithm versions without losing provenance; migration and model tests pass.
- **Suggested commit:** `feat(events): add versioned event model`

### Phase 12B — Event association engine

- **Status:** `NOT STARTED`
- **Objective:** Implement a replaceable deterministic v1 engine using bounded time, entity, location, and available cluster similarity signals.
- **Existing components to reuse:** Clustering similarity/features, cluster summaries, entities, locations, Dramatiq/scheduler, and Jobs state.
- **Backend changes:** Versioned association interface, deterministic scoring/threshold rules, candidate retrieval, idempotent association updates, and provenance.
- **Frontend changes:** None.
- **Database changes:** None unless run-level provenance is required beyond event fields/jobs.
- **API changes:** None.
- **Expected indexes:** Candidate retrieval by time/country/entity and association reverse lookup.
- **Expected tests:** Each signal, threshold boundaries, deterministic tie-breaking, idempotency, algorithm version, retries, and replacement compatibility.
- **Scalability:** Narrow candidate windows before scoring; no all-pairs cluster scan; batch work and bounded transactions.
- **Dependencies:** 12A and audited clustering features.
- **Acceptance criteria:** Fixed fixtures associate predictably and repeatably, with stored version provenance and observable failures.
- **Suggested commit:** `feat(events): associate clusters deterministically`

### Phase 12C — Event API

- **Status:** `NOT STARTED`
- **Objective:** Expose paginated event discovery, detail, clusters, articles, and deterministic timeline data.
- **Existing components to reuse:** Pagination, filters, article/cluster response models, entity/country representations, and authorization.
- **Backend changes:** Event query service and typed routes.
- **Frontend changes:** Regenerated OpenAPI types.
- **Database changes:** None expected.
- **API changes:** `/api/v1/events`, `/api/v1/events/{id}`, and bounded cluster/article/timeline endpoints following conventions.
- **Expected indexes:** Event list filtering/ordering and association joins from 12A.
- **Expected tests:** Filters, pagination, aggregation correctness, timeline ordering, missing events, authorization, and bounded results.
- **Scalability:** Stable pagination, SQL aggregation, and limited timeline/evidence windows.
- **Dependencies:** 12A–12B.
- **Acceptance criteria:** API faithfully exposes stored event provenance and associated evidence with passing backend checks.
- **Suggested commit:** `feat(events): add event API`

### Phase 12D — Event frontend

- **Status:** `NOT STARTED`
- **Objective:** Add `/events` and `/events/[id]` for event discovery and investigation.
- **Existing components to reuse:** Page shell, list/detail patterns, charts/timelines, article/cluster/entity links, query state, and generated types.
- **Backend changes:** Only integration corrections.
- **Frontend changes:** Paginated/filterable event list and dossier with type, time span, places, entities, clusters, articles, source count, and deterministic timeline.
- **Database changes:** None.
- **API changes:** Consume 12C.
- **Expected indexes:** None beyond backend phase.
- **Expected tests:** Loading/error/empty/rendering, pagination/filter URL state, evidence navigation, timeline rendering, and Playwright event workflow.
- **Scalability:** Paginated/lazy evidence sections and bounded timeline.
- **Dependencies:** 12C plus entity/cluster routes.
- **Acceptance criteria:** Analysts can move from event to every supporting cluster/article/entity; frontend checks pass.
- **Suggested commit:** `feat(events): add event investigation interface`

### Phase 13A — Source dossiers

- **Status:** `NOT STARTED`
- **Objective:** Add `/sources/[id]` with feed identity/health, publishing and extraction metrics, topic coverage, ingestion history, recent evidence, and descriptive cluster timing position.
- **Existing components to reuse:** Source/feed models, ingestion/extraction jobs, article/source/cluster/entity/country data, operational status UI, and charts.
- **Backend changes:** Bounded source dossier aggregations and paginated evidence endpoints.
- **Frontend changes:** Source dossier route and selective source links.
- **Database changes:** Prefer none; add measured indexes only.
- **API changes:** Typed source detail/analytics/articles/clusters endpoints.
- **Expected indexes:** Source/published time, extraction status, feed/job time, and cluster/source timing joins.
- **Expected tests:** Rates/denominators, time windows, first/last seen, cluster position metrics, pagination, UI states, and navigation.
- **Scalability:** Bounded windows and SQL aggregation; no opaque quality score.
- **Dependencies:** Existing feeds/jobs, 10B navigation conventions, and clusters.
- **Acceptance criteria:** Metrics are descriptive, defined, and evidence-backed; backend/frontend suites pass.
- **Suggested commit:** `feat(sources): add source dossiers`

### Phase 13B — Compare mode

- **Status:** `NOT STARTED`
- **Objective:** Compare two entities, sources, or countries factually with bookmarkable URL state.
- **Existing components to reuse:** Dossier aggregations, investigation query state, charts, entities/sources/countries, and cluster/article filters.
- **Backend changes:** Typed comparison queries with explicit denominator/window definitions.
- **Frontend changes:** Type-aware compare route, selectors, shared/unique sets, timelines, and evidence links.
- **Database changes:** None expected.
- **API changes:** Comparison endpoint(s) or composition of existing dossier APIs, chosen after measuring request/query cost.
- **Expected indexes:** Reuse dossier lookup indexes.
- **Expected tests:** Symmetry, overlap math, empty denominators, type validation, URL state, loading/error/empty/rendering, and Playwright comparison flow.
- **Scalability:** Explicit time bounds and top-N lists; avoid transferring raw archives for client-side set operations.
- **Dependencies:** Entity/source dossiers and established country analytics.
- **Acceptance criteria:** All comparisons are factual, mathematically defined, bookmarkable, and free of overall scores/winners; checks pass.
- **Suggested commit:** `feat(compare): add factual comparison workflow`

### Phase 13C — Investigation map

- **Status:** `NOT STARTED`
- **Objective:** Add a self-hostable geographic investigation view that distinguishes source country, mentioned country, story country, and event location.
- **Existing components to reuse:** Country/location entities, investigation filters/query state, article/cluster/event results, and existing frontend visualization dependencies.
- **Backend changes:** Bounded geographic aggregation with explicit location-role dimensions.
- **Frontend changes:** Map/list coordination, role legend, filters, selection-driven investigation refinement, and URL state.
- **Database changes:** Avoid geospatial infrastructure unless audited requirements show point/polygon queries need it.
- **API changes:** Typed geographic aggregation/evidence endpoint.
- **Expected indexes:** Country/location role plus time/filter paths.
- **Expected tests:** Role separation, filtering, aggregation, URL state, selection, loading/error/empty, accessibility fallback, and Playwright workflow.
- **Scalability:** Aggregate before transfer; viewport/filter bounds if needed; open/self-hostable tiles or geometry only.
- **Dependencies:** Events, investigation state, and verified geographic data quality.
- **Acceptance criteria:** The UI never conflates location roles and map selection refines a bookmarkable investigation; checks pass.
- **Suggested commit:** `feat(map): add geographic investigation view`

### Phase 13D — Operational analytics

- **Status:** `NOT STARTED`
- **Objective:** Extend existing Jobs/Settings operations views with feed, extraction, deduplication, indexing, NLP, clustering, monitor, dependency-health, failure, and practical storage metrics.
- **Existing components to reuse:** Job state, feed health, scheduler/worker metadata, service health checks, Settings/operations UI, and Docker Compose topology.
- **Backend changes:** Define bounded operational snapshots and metric semantics using existing state; add inexpensive health probes and backlog/lag queries.
- **Frontend changes:** Dense status/metric panels with timestamps, definitions, drill-downs, and degraded/error states.
- **Database changes:** None unless lightweight snapshots are required to avoid expensive live queries; document retention and derivation.
- **API changes:** Typed operations summary/detail endpoints with appropriate authorization.
- **Expected indexes:** Job type/status/time, feed status/poll time, processing status/time, and monitor due/failure state.
- **Expected tests:** Metric correctness, health degradation, authorization, timeout/failure handling, UI states, and operational drill-down.
- **Scalability:** Cheap indexed counts, bounded probes, cached short-lived snapshots where justified; no Prometheus/Grafana dependency by default.
- **Dependencies:** Monitor/event jobs and audited existing operations model.
- **Acceptance criteria:** Operators can identify backlogs and failures from transparent metrics using existing infrastructure; full backend/frontend/Playwright/acceptance suites pass.
- **Suggested commit:** `feat(operations): extend operational analytics`

## Decision log

| Decision | Reason |
| --- | --- |
| Use this file as the sole persistent roadmap state | Future sessions need one accurate resume point rather than competing plans. |
| Keep PostgreSQL canonical and Elasticsearch rebuildable | Durable identity, monitor, event, and provenance state must survive search-index rebuilds. |
| Reuse the existing Entity/annotation identity system | Parallel entity storage would split identity and make evidence inconsistent. This decision remains subject to confirming the existing schema. |
| Model graph edges as co-occurrence | Existing annotations can support traceable evidence; semantic claims require a separate explicit evidence model. |
| Reuse saved investigation/search state for monitors | Avoids a second query language and keeps monitored results consistent with interactive search. |
| Keep event association deterministic and versioned | Results must be explainable and replaceable without introducing embeddings or opaque inference. |
| Split monitor delivery into schema, evaluation, API, UI, and changes | Each slice can be tested and leave the repository healthy. |
| Write the roadmap before the repository reality check | Explicit user instruction on 2026-09-20; provisional statements are labeled and will be replaced with code-backed findings next. |
| Dossier route is `/entities?id=<uuid>`, not `/entities/[id]` | `next.config.ts` uses `output: 'export'` and existing detail pages (`/clusters?id=`, `/articles?article=`) use query params; arbitrary entity ids are unknown at build time. |
| Edge evidence is resolved in Elasticsearch with the graph's own query, then hydrated from PostgreSQL | The edge weight is filtered co-occurrence (text, source, date, entity-type filters, plus the focus entity), which SQL cannot reproduce; running `build_query` + `focus_query` + both entities makes the evidence total equal the drawn weight (asserted in e2e). Records shown are canonical PostgreSQL rows; ids the index holds but the archive lacks are dropped and reported as `missing_from_archive`. Deviation from "aggregate in SQL". |
| Snippets omitted from edge evidence | `ArticleEntity.occurrences` are offsets, not reliable surface text; the roadmap allowed omission. |
| Selected edge is the `edge=<idA>:<idB>` graph URL param | Keeps the inspector bookmarkable and the article "Back to graph" link returning to it; ids are sorted so the param is canonical. |
| Monitors embed a versioned `InvestigationState` snapshot; no FK to `saved_searches` | Saved searches are editable, and a silent edit would invalidate a monitor's cursors and counters. Still one query representation, no second language. 11D can offer "create monitor from saved search" by copying. |
| Monitor `kind` (`search`/`entity`/`source`/`country`/`cluster`) is a validated discriminator over the same state | Each kind must actually point at its target (entity ids, source ids, a country filter, exactly one `story_cluster_id`; search needs a query or any filter). A CHECK constrains the stored kind. |
| Changing a monitor's kind/state resets cursors, counters, latest match, lease and error; rename/toggle keep history; re-enable schedules an evaluation now | Old cursors describe a different query. Resending an identical state is a no-op. |
| Evaluation lease/outcome fields live on the `monitors` row; no run/match table yet | Mirrors the job-row shape (`next_evaluation_at`, `claim_token`, `claim_expires_at`, error fields). 11B may add run tables only if idempotency needs them. |
| No relational `target_id` column on monitors | The target lives in the JSONB state; a duplicate column can drift. Add it, with an index, when "monitors watching this entity" is needed. |
| Monitors evaluate through one search-backed evaluator behind a per-kind registry (`EVALUATORS`) | Kinds differ only in target validation (11A), not in query language, so all five call the same evaluator today; a kind that needs its own query swaps its registry entry. |
| Monitor evaluation counts by time window and never fetches pages | Window is `first_discovered_at` in `(eval_cursor_at, horizon]`, one bounded Elasticsearch request (size 0/1 plus a cardinality aggregation), so cost never scales with match count or archive size. |
| Unseen counters are derived from cursors, not accumulated | An empty delta only advances `eval_cursor_at`; otherwise `unseen_article_count`, `unseen_cluster_count` (distinct `story_cluster_id`) and `latest_match_*` are *set* from the `(viewed_cursor_at, horizon]` window. Reruns and retries therefore give identical numbers, and distinct stories are counted once. No run/match table and no migration; a per-article ledger is deferred to 11E. |
| Evaluation commits atomically under `FOR UPDATE` and is discarded if the lease, target or viewed boundary changed | Cursor, counters, latest match, timestamps, cleared claim/error and `next_evaluation_at` publish together only when `claim_token` matches, the lease is live (wall clock) and `viewed_cursor_at` is unchanged. **11C invariant:** the viewed-state operation must set `viewed = eval cursor`, counters 0 and clear `claim_token` in one transaction. |
| A new (or reset) monitor baselines on its first evaluation | `eval = viewed = horizon`, nothing counted (the existing archive is not "unseen"), newest existing match recorded as `latest_match_*`. |
| `monitor_settle_seconds` (120) holds the horizon back from "now" | Gives indexing, NLP and clustering time to finish and absorbs commit-order skew (PostgreSQL `now()` is transaction start). It is the documented completeness ceiling for annotation-dependent kinds. |
| Monitor failures live on the row and in logs, with a fixed retry delay | Categories `search_unavailable`, `search_upgrade_required`, `invalid_state`, `evaluation_error`; message ≤1000 chars; retry after `monitor_retry_seconds`. There is no attempt counter to back off with, and no route to expose it until 11C. Success clears the error. |
| Fixed monitor interval and no lease heartbeat | `monitor_interval_seconds` (300) for every monitor; an evaluation is at most two Elasticsearch round trips, inside `monitor_lease_seconds` (120). Per-monitor intervals and a heartbeat are deferrals. |
| `eval_cursor_article_id` / `viewed_cursor_article_id` stay unused in 11B | Both sides of every window use the same Elasticsearch millisecond precision, so no article can fall in two windows and no tie-break is needed. 11E must not assume they are populated. |
| `latest_match_article_id` is set only if PostgreSQL still has the article | The index is rebuildable and can name a deleted article; the foreign key would otherwise fail the whole commit. |
| Monitor API lives at `/api/v1/monitors` with owner-scoped 404s | List (`order=name|activity`), create, get, patch (name/enabled/kind+state), delete, `results` and `viewed`. Mutations require CSRF like saved searches; a foreign monitor is indistinguishable from a missing one. No new setting or migration. |
| `MonitorResponse` exposes `evaluated_through` and `viewed_through` | The client must name the viewed boundary; both are the existing cursors. Additive contract change. |
| Monitor results are the exact window the counters use | `GET /monitors/{id}/results?scope=unseen|recent` runs the monitor's criteria over `first_discovered_at` in `(viewed_through, evaluated_through]` (`recent`: up to `evaluated_through`), newest first, so the unseen list has `unseen_article_count` articles. Items reuse `SearchResult` (`search_result(hit)` extracted from `/search`); no highlights. Paging uses ES `search_after` and an HMAC-signed cursor that pins monitor, session, scope and both window bounds, so paging is stable while evaluations advance the cursor. |
| Viewed-state takes the boundary the analyst saw and rewinds the evaluator when it has moved on | `POST /monitors/{id}/viewed {through}` (aware datetime) locks the row: `through` beyond `evaluated_through` is 422, an unevaluated monitor 409, `through <= viewed_through` a no-op. Otherwise `viewed = through`, counters 0 and `claim_token` cleared in one transaction (the 11B invariant). If `through < evaluated_through` the evaluation cursor is rewound to `through` and the monitor is scheduled now: evaluation is derived and idempotent, so the next run recounts what lies after `through`. Unseen items are never marked seen; counters read 0 for at most one scheduler tick. |
| Monitor PATCH takes no row lock | A state edit clears the claim and history, so an in-flight evaluation can only lose; delete makes its publish a no-op (`item is None`). Only `viewed` locks. |
| Watchlist is `/monitors/` with the detail at `/monitors/?id=<uuid>` | Same static-export constraint as the dossier and cluster routes; one route file switches on `id`. The nav label is "Watchlist"; the API keeps the name "monitors". |
| Monitors are created from a search on screen or from a saved search, always as kind `search`, with a copy of the state | Reuses the two places analysts already build a query without a second query editor; a copy keeps later saved-search edits from invalidating a monitor's cursors (11A decision). Entity/source/country/cluster kinds stay API-only for now. |
| The UI edits a monitor by rename, pause/resume and delete, not by criteria | Changing criteria resets a monitor's history anyway, so "watch a new search" is the honest equivalent; revisit if analysts ask for in-place edits. |
| Mark-as-seen sends the `window_end` of the results on screen, never a fresher boundary or a blind "mark all" | The API recounts anything after `through` (11C), so what the analyst did not see stays unseen. |
| The UI treats "recount pending" as its own state and polls faster (5 s, else 30 s) | After a `viewed` behind the live cursor the counters and the unseen list read empty until the next tick (11C risk); a due `next_evaluation_at` shows "Checking for newer articles…" and never "Nothing new". |
| The detail's result list is refetched whenever `evaluated_through`, `viewed_through` or the unseen count changes | A refetch of an infinite query chains fresh cursors from the first page, so pages of two windows never mix. |
| Monitor models import the `User` and `Article` models they reference | The scheduler and worker load `Monitor` without the API's auth/feed imports, so `monitors.user_id` and `latest_match_article_id` failed to resolve there (found by the first Playwright run). Fix is at the model, not in each process. |
| Change summaries are derived on request from the counter window; no delta table or migration | `GET /monitors/{id}/changes` aggregates the same `(viewed_through, evaluated_through]` window the counters and results use (`window_filter`), so a repeat request gives identical output and mark-as-seen empties it. Alembic head stays `0010`; retention does not arise. Snapshot tables stay deferred unless reproducibility of story growth (see Risks) is required. |
| A source, entity or story is "new" when it occurs in the window and never in the monitor's matches at or before `viewed_through` | Two bounded Elasticsearch requests: top 10 per kind in the window (order `count desc, key asc`), then a candidate-only `include` check against the prior matches. Entities need schema ≥ 2 and stories schema ≥ 3; kinds the index cannot answer are omitted rather than failing. |
| Story growth comes from immutable `feed_articles.discovered_at`, never the cached `StoryCluster.source_count` | For a touched story, distinct `feed_id` over its current members reported by `window_start` and by `window_end`; both numbers come from one query so "3 → 5 (+2)" always adds up. A story "grew" at `MATERIAL_SOURCE_GROWTH = 2` distinct sources; a story with no earlier match is `new`. Growth is measured over all current members of a touched story, not only matching articles, deliberately. |
| The server returns typed facts with up to three evidence articles per item; wording is a pure frontend function | `describeChanges` maps fields to sentences in a fixed order (articles, sources, entities, stories, "more…" lines), so wording is deterministic and tested; every line links to its subject (search on that source, entity dossier, cluster) and its evidence articles. |
| Events are three normalized tables (`events`, `event_clusters`, `event_entities`); country is a column | Exactly the roadmap's associations; a fourth `event_countries` table has no rule behind it yet. `primary_country` is the dominant *story*-role (`primary`) country of the member articles, never mentioned or source countries, so roles stay separate. |
| An event is one algorithm version's view; a cluster belongs to at most one event per version | `event_clusters.algorithm_version` repeats the event's version, pinned by the composite foreign key `(event_id, algorithm_version) → events(id, algorithm_version)`, with `UNIQUE (algorithm_version, cluster_id)`. A newer version can associate the same cluster again and the old event keeps its provenance; within a version 12B moves associations instead of duplicating them. |
| Event `status` is `active` / `closed` / `superseded` | `superseded` means replaced by a newer version's event and kept for provenance. No `merged_into` pointer, `event_type`, cached article/source counts or representative-cluster column: none has a defined rule yet and each is a cheap additive migration when 12B/12C/12D needs it. |
| Events cache only `started_at`, `ended_at` and `primary_country` | They exist so events filter and order by index. All three are derived (member clusters' publication bounds, story-country annotations); whoever changes the associations recomputes them (`refresh_span` does the span). Article and source counts are aggregated in SQL by 12C, never stored. |
| `event_clusters.signals` (JSONB) and `score` store why a cluster joined | Lets 12B explain a deterministic decision without a schema change. `event_entities.article_count` is the derived distinct-article count of an entity within the event. |
| Deleting a cluster or entity removes only its association; an event that loses every cluster is left to 12B | The database cascades associations, not events; deciding what an emptied event becomes belongs to the engine. |
| Use one branch per phase | Keeps each vertical slice independently reviewable and prevents later work from obscuring a phase's acceptance state. Each branch starts from the integration branch containing all accepted predecessors. |

## Discoveries

- `docs/` and root `AGENTS.md` are intentionally ignored as of commit `7e3d99e`; this tracked roadmap therefore lives at repository root, as allowed by the master instruction.
- Entity aliases are not normalized in a dedicated table. `Entity.display_text` is canonical, while `ArticleEntity.occurrences` stores offsets rather than reliable surface text. Phase 10A therefore returns `aliases: []` with `aliases_status: "unavailable"`.
- Migration `0008` already provides the needed partial entity-first index: `(entity_id, article_id) WHERE is_current`. `EXPLAIN (ANALYZE, BUFFERS)` for a dossier lookup used an Index Only Scan (six shared-buffer hits), so no Phase 10A migration was warranted. ORM metadata now declares that existing index.
- Country annotations distinguish `role` and `inferred`, while search separates `story_country` from `mentioned_country`. The later map must preserve these meanings.
- `StoryClusterMember.article_id` is the primary key, so an article belongs to at most one current story cluster.
- Graph weights currently come from Elasticsearch article co-occurrence. Phase 10C needs PostgreSQL-backed evidence queries, or a carefully reconciled hybrid, so each edge traces to canonical articles and clusters.
- Saved searches already persist the complete `/search` query shape as versioned JSONB and use user-scoped keyset pagination. Monitor configuration should reuse this schema.
- Existing list APIs favor opaque keyset cursors: articles by discovery time/id, clusters by effective date/article id, saved searches by name/id, and failures by time/id.
- OpenAPI generation is two-step: refresh checked-in `frontend/openapi.json` from `create_app().openapi()`, then run `npm run generate:api`. No repository command currently combines both steps.
- The graph and analytics views already render entity names and are high-value locations for selective dossier links in Phase 10B.
- Acceptance scripts currently stop at Phase 7. The roadmap now requires one for every implementation phase; Phase 10A must add `infra/test-phase10a.sh` before it is marked complete.
- Phase 10B keeps the window selector in component state (not the URL); only the entity `id` is bookmarkable. Article rows and cluster rows carry `from=` return links so the articles page shows "Back to entity".
- Search `entity_id` filters are OR, so an edge's articles cannot be listed by searching both entities; evidence is paginated by the endpoint itself (ES `search_after`, no PIT — pages can shift if the index is rebuilt mid-scroll).
- With `focus=A`, edge weights between other nodes count only articles that also contain A, so the evidence endpoint takes `focus_entity_id` too.
- Graph specs need the schema-3 search index; acceptance scripts must run `rebuild-search`/`resume-search-rebuild` (as Phase 7 does) after seeding. `infra/test-phase10b.sh` does not, so it cannot run the graph or search workflow specs.
- Monitor due-batching uses the partial index `ix_monitors_due (next_evaluation_at, id) WHERE enabled`; a PostgreSQL test confirms the planner picks it for the due query (with sequential scans disabled, since the test table is tiny). `ix_monitors_user_activity` exists for 11C's activity ordering and is not yet exercised by a query.
- Monitors need no new API/OpenAPI surface in 11A; the drift check confirms `openapi.json` and generated types are unchanged.
- `search_criteria` is a plain async function whose parameter names equal `InvestigationState` fields, so a monitor state maps to `SearchCriteria` with `state.model_dump(exclude={"sort", "interval"})` and no second parser.
- The search index has `first_discovered_at` but no `indexed_at`, and articles are re-indexed as NLP and clustering annotate them, so annotation-dependent monitors can match after first discovery; the settle lag is the mitigation until an `indexed_at` field exists.
- The first 11B acceptance run failed on my own tests: they passed a future evaluation time while the lease check used the wall clock, and the Elasticsearch test re-selected already delivered rows. The lease check now always uses the real clock (`now` only supplies the horizon and scheduling times); no design change.
- `MonitorResponse`, `MonitorPage`, `MonitorResultPage` and `MonitorViewed` are new OpenAPI schemas; `frontend/src/lib/types.generated.ts` changed and no frontend code did (aliases wait for 11D). The acceptance script compares the regenerated files with the checked-in ones (`cmp`) instead of `git diff`, so it also works before the phase is committed.
- Empty evaluation windows advance `eval_cursor_at` every interval, so a client's boundary is usually behind the live cursor by the time it acts; that is why `viewed` rewinds instead of rejecting.
- `ix_monitors_user_activity` is now exercised by `order=activity` (nulls last); with a handful of monitors per user the planner choice was not measured.
- Vitest intermittently reports an unhandled `ReferenceError: window is not defined` (raised by the React scheduler after jsdom teardown, attributed to `src/components/Shell.test.tsx`) while all 93 tests pass; it made `infra/test-phase11c.sh` exit non-zero on 3 of 5 runs and none of 3 standalone `npm test` runs. It predates this phase (no frontend source changed) and was not fixed here; the run recorded in the ledger is one where it did not occur. **Resolved in 11D:** Vitest has no globals, so Testing Library never unmounted between tests; `vitest.setup.ts` now calls `afterEach(cleanup)`. Before the fix 1 of 4 runs failed; after it, 8 standalone runs and every script run (3 per script) were clean.
- The 11B scheduler and worker could not evaluate any monitor in a real deployment: `Monitor` was never imported together with `User`/`Article` there, so the mapper raised `NoReferencedTableError` on the first claim. The PostgreSQL and Elasticsearch tests import every model and could not see it; only running the Compose stack did. `test_monitors.py` now imports each entry point in a fresh interpreter.
- Monitors count by `first_discovered_at`, which needs the schema-3 index, so acceptance scripts run `rebuild-search` on the empty archive first (as 10C does). `docker/compose.e2e.yaml` sets the worker's `NEWSINTEL_MONITOR_INTERVAL_SECONDS=5` and `NEWSINTEL_MONITOR_SETTLE_SECONDS=10` for the monitor workflow; settle must stay above indexing latency or articles indexed after the horizon moved on are missed by design.
- `infra/test-phase6.sh` fails before reaching its Playwright steps (`uv run pytest`: no `pytest` executable; later scripts use `uv run python -m pytest`). Not touched in 11D.
- Vitest module mocks of `../../lib/api` list only the calls a page uses, so every page that starts using `api.createMonitor` needs it added to its test's mock (search and saved searches did).
- The 11E acceptance script runs the real-Elasticsearch/PostgreSQL change-summary tests (`test_phase11e_elasticsearch.py`) in its own stage because nothing else can prove the aggregation shapes (`nested` → `terms` → `reverse_nested` → `top_hits`, candidate `include`) or the grouped growth SQL; the pure tests only pin the request bodies and the assembly.
- The E2E stack runs neither NER nor clustering, so the Playwright monitor workflow can only assert the article and new-source lines of "What changed"; new entities and new/grown stories are covered by the gated backend tests.
- `StoryCluster` alone already resolves its `articles` foreign key in a fresh interpreter, but `app/events/models.py` still imports `StoryCluster`, `Entity` and `Article` (the 11D scheduler/worker lesson); `tests/test_events.py` checks it in a fresh interpreter.
- The first `infra/test-phase12a.sh` runs failed on my script (test paths copied from 11A) and one Postgres startup race (`createdb` while the container's init server was shutting down); rerunning passed. One of my own tests read an object after `expunge_all`.
- Existing entity-search chips on the article page are kept; the dossier is a separate "Dossier" link so search refinement is unchanged.

## Outstanding risks

- Monitor completeness ceiling: an entity, country or cluster match whose annotations land later than `monitor_settle_seconds` after discovery is not counted. Upgrade path: an `indexed_at` field (search schema v4) or a re-scan overlap.
- `unseen_cluster_count` counts distinct `story_cluster_id` values among the unseen matches, so articles that are not clustered yet contribute articles but no stories ("3 new articles, 0 new stories" is valid). The cardinality aggregation is approximate above its `precision_threshold` of 3000 distinct stories; article counts are exact. 11D/11E should word the UI accordingly.
- Monitors retry at a fixed delay because the row has no attempt counter; a persistently failing monitor is retried every `monitor_retry_seconds`. Add a counter column if backoff is wanted.
- Unseen counts are recomputed over the whole `(viewed_cursor_at, horizon]` window when there is news; a monitor that is never viewed has a growing window (still one aggregation request).
- Monitor results and the counters both read Elasticsearch; an index rebuilt between evaluation and a results request can change the list slightly (same trade-off as edge evidence). Result paging uses `search_after` without a point in time.
- A `viewed` call behind the live cursor makes the counters read 0 **and the unseen results list empty** until the next scheduler tick (≤ scheduler interval) restores them, although matches after `through` exist. 11D must not cache that state as "nothing new".
- The watchlist polls (30 s, 5 s while a recount is due) rather than pushing, and counters are labelled "counted through <evaluated_through>"; with the fixed 5-minute monitor interval the list can be up to that stale. There is no unseen badge in the navigation, so a new match is only visible on the watchlist.
- Mark-as-seen marks everything up to the results' `window_end` as seen even when the analyst loaded only the first page; the button shows the unseen count so the scope is visible, but there is no per-article "seen".
- **Story growth is only partly reproducible (deviation from the 11E acceptance wording).** Cluster membership is mutable (`clustering/engine.py` deletes/re-adds members and merges clusters), so the same window can give a different "grew by N sources" after reclustering. Articles, new sources, new entities and new stories reproduce from the index and PostgreSQL. Fully reproducible growth needs a persisted per-window snapshot (a migration); not done in 11E.
- The "never matched before" check reads that monitor's whole match history for at most 30 candidate keys (`include` bounds the buckets, not the documents scanned). Named ceiling; upgrade path is a lookback limit or a per-monitor seen-set table.
- "New entity" reflects current annotations in the index, so an entity attached to an old article after the monitor viewed it can read as new for a later window; the summary lists the top 10 per kind with a "more" flag, and only stories touched by a matching article in the window are considered.
- Event schema (12A): `started_at`, `ended_at` and `primary_country` are cached and only as fresh as the last `refresh_span`/engine update; nothing recomputes them yet (12B). Deleting a cluster (reclustering) leaves its event with fewer or no clusters until 12B reconciles it.
- Event schema (12A): 12D lists an event "type" and "places"; neither has a column. A type needs a deterministic vocabulary, places can come from member articles' country annotations; both are open questions for 12B–12D, not resolved here.
- Surface aliases remain unavailable until a future canonical surface-text model exists; Phase 10A must not derive them from offsets or normalized names.
- Cluster totals and co-occurrence require distinct joins across annotations, membership, and provenance; careless joins can multiply mention and source counts.
- Existing geographic fields may not reliably distinguish source, mention, story, and event roles.
- Checked-in OpenAPI JSON can drift from backend schemas because generation is not one atomic repository command.
- Full test suites may depend on Docker services; exact local prerequisites must be identified in the audit.

## Verification ledger

| Phase | Tests/checks run | Result | Notes |
| --- | --- | --- | --- |
| Roadmap initialization | `git check-ignore -v`, repository structure/model/route/service/test/config inspection | PASS | `docs/` is ignored, so the roadmap was moved to tracked repository root. No application behavior changed. |
| Phase 10A | `infra/test-phase10a.sh`: isolated PostgreSQL migration, dossier tests without Elasticsearch, Ruff, mypy, OpenAPI/type generation, frontend Vitest/typecheck/webpack build | PASS | The script cleaned up its disposable Compose stack; dossier tests ran with Elasticsearch deliberately unreachable. |
| Existing Phase 7 Elasticsearch integration | Full backend suite: 204 passed with Elasticsearch-dependent checks unavailable in the PostgreSQL-only stack; then both affected tests rerun with the isolated Elasticsearch fixture | PASS | The two reruns passed. |
| Phase 10B | `infra/test-phase10b.sh`: Vitest (82 tests), typecheck, static-export build, Playwright `relationships seed` and `entity dossier workflow` against the NER-enabled stack | PASS | Backend untouched, so no backend suites were rerun; the script cleaned up its Compose stack. |
| Phase 10C | `infra/test-phase10c.sh`: 41 backend tests (evidence body/parse/route + graph suite + PostgreSQL hydration), Ruff, mypy, OpenAPI/type drift check, Vitest (92), typecheck, static-export build, Playwright `relationships seed`, `relationships workflow`, `graph edge evidence workflow` | PASS | Full backend suite also run outside the script: 177 passed, 48 skipped (PostgreSQL/Elasticsearch-gated). After the script, a small panel refinement (archive gaps summed across pages, top-stories note) was re-verified with Vitest (93 tests), typecheck, Ruff, mypy and the evidence unit tests, not a second full acceptance run. The e2e asserts the evidence total equals the drawn edge weight. `EntityGraph` edge click is not unit-tested (needs canvas); covered through the accessible connection list and page tests. |
| Phase 11A | `infra/test-phase11a.sh`: `alembic upgrade head` / `downgrade 0009` / `upgrade head` with table-presence checks, monitor unit tests, saved-search regression, PostgreSQL monitor tests (defaults, constraints, ownership, keyset paging, update semantics, cascades, due-index plan, migration round trip) with Elasticsearch unreachable, Ruff, mypy, OpenAPI/type drift check, frontend typecheck | PASS | Script run: 51 backend tests passed. Full backend suite also run outside the script: 203 passed, 58 skipped (PostgreSQL/Elasticsearch-gated; the 10 gated 11A tests are the ones the script runs). The first script run failed on two of my own tests (expired ORM attributes read after a rollback); fixed in the tests, no model change. |
| Phase 11B | `infra/test-phase11b.sh`: `alembic upgrade head` (head still `0010`), 71 backend tests (monitor and evaluator units, saved-search regression, PostgreSQL claim/scheduler/idempotency/stale-claim/failure tests, real-Elasticsearch tests for `search`, `source` and `cluster` monitors), Ruff, mypy, OpenAPI/type drift check, frontend typecheck | PASS | Script run: 71 passed, none skipped. Full backend suite outside the script without the PostgreSQL/Elasticsearch gates: 210 passed, 71 skipped (gated). The first script runs failed on my own tests (see Discoveries) and one script bug (`docker compose run` output needed `\r` stripped); fixed in the tests and script, no design change. |
| Phase 11C | `infra/test-phase11c.sh`: `alembic upgrade head` (head still `0010`), 93 backend tests (monitor API contract/cursor units, evaluator and saved-search regression, PostgreSQL API tests for isolation, CRUD errors, both list orderings, viewed boundaries and the viewed/evaluation race, real-Elasticsearch results and rewind-recount tests), Ruff, mypy, regenerated OpenAPI/types compared with the checked-in files, Vitest (93), typecheck | PASS | Tests: the pure `test_monitor_api.py` was written and seen failing (missing module) before the implementation; the two gated PostgreSQL/Elasticsearch files were written after it and first passed together. Script run: 93 passed, none skipped. Full backend suite outside the script without the gates: 216 passed, 83 skipped (gated). The first script run passed all tests and stopped only at the stale-types check before the regenerated files were in the tree; a deprecation warning (`HTTP_422_UNPROCESSABLE_ENTITY`) was fixed. |
| Phase 11D | `infra/test-phase11d.sh`: monitor backend unit tests (42), Ruff, mypy, Alembic head still `0010`, Vitest (119) run three times, typecheck, `next build`, OpenAPI drift check (no API change), `alembic upgrade head`, `rebuild-search`, Playwright `monitor workflow` (watch a search before any article exists, wait for the baseline, ingest two feeds, six unseen, open the detail, mark seen, recent scope, pause/resume, rename, delete) | PASS | Tests: `monitors.test.ts`, the `api.test.ts` monitor case, the watchlist component tests and the entry-point tests were written and seen failing (missing modules/handlers) before the implementation. The first script run failed in Playwright because monitors were never evaluated (scheduler `NoReferencedTableError`, see Discoveries); a fresh-interpreter regression test was added and failed first, then the model imports fixed it. The second run passed. Full backend suite outside the script without the gates: 219 passed, 83 skipped (gated); the PostgreSQL/Elasticsearch-gated suites were not rerun because they import every model and so cannot see this class of bug; the only backend change is two model imports. The existing Playwright `investigation seed`/`investigation workflow`/`invalid saved search` specs, which drive the two edited pages, were run once against a fresh stack (the checked-in `infra/test-phase6.sh` currently fails before them on `uv run pytest`, a pre-existing script problem left alone) and passed; `infra/test-phase11d.sh` does not run them. |
| Phase 11E | `infra/test-phase11e.sh`: 51 monitor backend tests (incl. 9 new pure change tests), Ruff, mypy, Vitest (126) run three times, typecheck, `next build`, regenerated OpenAPI compared with the checked-in file, Alembic head still `0010`, 2 gated real-Elasticsearch/PostgreSQL tests (new sources/entities/stories, growth, repeat identical, empty after viewed; growth boundaries at `after`/`upto` and late reports), `rebuild-search`, Playwright `monitor workflow` (extended: "6 new articles", two "New source" lines with evidence links, panel empties after mark-as-seen) | PASS | The pure `test_monitor_changes.py` was written and seen failing (missing module) before the implementation; the frontend tests were written before the implementation but first run after it (not watched failing). The gated tests were written after the module and passed on their first run. Full backend suite outside the script without the gates: 228 passed, 85 skipped (gated). Two deviations recorded in Risks: story growth is only partly reproducible after reclustering, and the "never matched before" check scans a monitor's whole match history. After the script, review fixes ("More … matched in this window" wording, a "Counted through" caption on the panel, and an owner-isolation check for `/changes`) were re-verified with Vitest (126, three runs), typecheck, Ruff and the two gated tests, not a second full acceptance run. Entities and stories are not covered by Playwright (the E2E stack runs neither NER nor clustering); the gated backend tests cover them. |
| Phase 12A | `infra/test-phase12a.sh`: `alembic upgrade head` → tables present → `downgrade 0010` (events tables gone, `monitors` and clusters intact) → `upgrade head`; 16 tests (5 pure model tests incl. fresh-interpreter import, 11 PostgreSQL-gated: defaults and `updated_at`, status/span/count checks, one event per cluster per version, composite-FK version pinning, idempotent and moving association, entity replacement, `refresh_span`, cascades, planner index use, article deletion, migration round trip) with Elasticsearch unreachable; Ruff; mypy; regenerated OpenAPI/types unchanged | PASS | The pure tests were written and seen failing (missing module) before the implementation; the gated tests were written next and first ran in the script. Full backend suite outside the script without the gates: 234 passed, 96 skipped (gated). Older acceptance scripts were not rerun; no shared code changed except `migrations/env.py` importing the new models. |

## Change log

- 2026-09-20: Initialized the persistent roadmap from the approved master requirements. Marked Phase 10A `IN PROGRESS`; all repository-specific architecture statements remain explicitly pending code audit.
- 2026-09-20: Completed the initial code reality check, replaced provisional architecture notes with verified module/schema/workflow details, recorded Phase 10A index and alias risks, and added the required branch-per-phase workflow.
- 2026-09-20: Completed Phase 10A on `phase/10a-entity-dossier-backend`. Added PostgreSQL-backed dossier detail, evidence pages, and bounded relationships; regenerated OpenAPI/types; confirmed the existing entity-first partial index; and recorded passing verification.
- 2026-09-20: Established a required checked-in acceptance script for every implementation phase. Phase 10A is returned to `IN PROGRESS` until `infra/test-phase10a.sh` passes.
- 2026-09-20: Added and passed `infra/test-phase10a.sh`; Phase 10A is `COMPLETE` again and ready for review without merge or publication authorization.
- 2026-09-20: Corrected the stale Phase 10A detail-block status from `IN PROGRESS` to `COMPLETE`.
- 2026-09-20: Phase 10A merged into local `main` (`2841cdc`). Began Phase 10B on `phase/10b-entity-dossier-frontend`: dossier route, typed API client methods, selective entity links from article annotations and the graph panel, Vitest and Playwright coverage, and `infra/test-phase10b.sh`.
- 2026-09-20: Phase 10B passed `infra/test-phase10b.sh` and is `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Phase 10C implemented on `phase/10c-graph-edge-evidence`: `GET /graph/edges/evidence` (ES-resolved evidence hydrated from PostgreSQL), edge inspector with bookmarkable `edge=` param and accessible connection list, regenerated OpenAPI/types, and `infra/test-phase10c.sh`. Passed and marked `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Phase 10C merged into local `main` (`dacd349`). Phase 11A implemented on `phase/11a-monitor-data-model`: `monitors` table (migration `0010`), per-kind validated monitor schemas, owner-scoped repository primitives, PostgreSQL and unit tests, and `infra/test-phase11a.sh`. Marked `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Refreshed `README.md` (entity dossiers, edge evidence, `entities`/`monitors` modules, ten UI routes, roadmap pointer) and the roadmap's frontend architecture line; no code changes.
- 2026-09-20: Phase 11A merged into local `main` (`957b8aa`). Phase 11B implemented on `phase/11b-monitor-evaluation`: `app.monitors.evaluation` (windowed, idempotent evaluator with guarded atomic publish and per-row failure state), `app.jobs.monitors` actor, `schedule_due_monitors`, monitor settings, worker module registration, PostgreSQL and real-Elasticsearch tests, and `infra/test-phase11b.sh`. No migration or API change. Marked `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Phase 11B merged into local `main` (`0f39ae0`). Phase 11C implemented on `phase/11c-monitor-api`: `/api/v1/monitors` (list with name/activity ordering, create, get, patch, delete), `results` over the counter window with signed keyset paging, race-safe `viewed` that rewinds the evaluator when it has moved on, regenerated OpenAPI/types, PostgreSQL and real-Elasticsearch tests, and `infra/test-phase11c.sh`. No migration. Marked `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Phase 11C merged into local `main` (`74073cb`). Phase 11D implemented on `phase/11d-monitor-frontend`: watchlist and monitor detail (`/monitors`), "Watch search" on Search and "Watch" on Saved Searches, mark-as-seen against the shown window boundary, pause/resume, rename, delete, polling with an explicit recount-pending state, Vitest cleanup fix, monitor model imports (scheduler/worker FK fix), `docker/compose.e2e.yaml` monitor timing, Playwright monitor workflow and `infra/test-phase11d.sh`. No migration or API change. Marked `COMPLETE`, ready for review without merge or publication authorization.
- 2026-09-20: Phase 11D merged into local `main` (`5c0e22c`). Roadmap-only correction: session state now points at Phase 11E.
- 2026-09-20: Phase 11E implemented on `phase/11e-monitor-changes`: `GET /monitors/{id}/changes` (new sources, entities and stories and material story growth over the counter window, derived from Elasticsearch aggregations and immutable `feed_articles.discovered_at`, with evidence articles), "What changed" panel on the monitor detail with deterministic wording, regenerated OpenAPI/types, gated Elasticsearch/PostgreSQL tests, extended Playwright monitor workflow and `infra/test-phase11e.sh`. No migration. Marked `COMPLETE`, ready for review without merge or publication authorization; story growth is only partly reproducible (see Risks).
- 2026-09-20: Phase 11E merged into local `main` (`4e553ed`). Roadmap-only correction: session state now points at Phase 12A.
- 2026-09-20: Phase 12A implemented on `phase/12a-event-schema`: versioned `events`, `event_clusters` and `event_entities` (migration `0011`, composite foreign key pinning association versions, indexes for time/status/country and reverse lookups), repository primitives, PostgreSQL and pure tests, and `infra/test-phase12a.sh`. No API or frontend change. Marked `COMPLETE`, ready for review without merge or publication authorization.
