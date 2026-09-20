# NewsIntel Intelligence Roadmap

Last updated: 2026-09-20

This document is the persistent implementation state for NewsIntel's intelligence and investigation roadmap. It must be updated after every completed phase and whenever repository discoveries change the proposed architecture.

## Session state

- Current phase: Phase 10A — Entity dossier backend
- Current status: `COMPLETE`
- Next action: Review and integrate `phase/10a-entity-dossier-backend`; Phase 10B remains pending and must begin from the integration branch after this phase is accepted.
- Deferred work: Every phase after 10A remains `NOT STARTED` until the preceding phase meets its acceptance criteria.
- Verification state: Phase 10A backend, PostgreSQL, OpenAPI generation, frontend tests/typecheck/build, and relevant existing Elasticsearch integration checks passed on 2026-09-20.

## Current architecture

> Reality-check status: `COMPLETE` for roadmap initialization. Directly relevant code must still be reinspected at the start of every phase.

- **Backend composition:** `backend/app/main.py` builds one FastAPI application and mounts health, auth, feeds/articles, NLP, clustering, search, graph, investigations, and analytics routers under `/api/v1`. Domains keep models, schemas, routes, and focused service/execution modules together; there is no generic repository layer.
- **Canonical articles and sources:** `backend/app/feeds/models.py` stores feeds, feed-fetch history, canonical articles, many-feed provenance through `feed_articles`, extracted content, and durable processing jobs/attempts. Articles use `published_at` where known and `first_discovered_at` as the ingestion timestamp/fallback.
- **NLP and entities:** `backend/app/nlp/models.py` stores versioned processor state/runs plus canonical `nlp_entities`, current/historical article-entity associations, keywords, language annotations, and country annotations. Entity identity is unique on `(language, entity_type, normalized_text)`; `display_text` is the canonical label. Per-article `occurrences` JSON holds extraction offsets/evidence, not persisted surface text; no alias table exists.
- **Story clusters:** `backend/app/clustering/models.py` stores derived clusters and one membership row per clustered article. Clusters cache article/source counts, publication bounds, representative article, and algorithm version. `GET /api/v1/clusters/{id}` exposes keyset-paginated members and feed references.
- **Search:** PostgreSQL stores delivery/rebuild coordination; Elasticsearch holds versioned, rebuildable article indices. Typed criteria support query text, sources/countries, dates, processing/language, entities/types, keywords, story/mentioned countries, and story clusters. Search and timeline share these criteria.
- **Graph:** `backend/app/graph` builds a bounded entity co-occurrence graph from Elasticsearch nested aggregations (`MAX_NODES = 50`, `MAX_EDGES = 150`) and resolves labels from PostgreSQL. Edges expose article co-occurrence weight only; no edge-evidence or dossier endpoint exists.
- **Investigations:** saved searches are user-owned PostgreSQL JSONB records containing a strictly validated, versioned `InvestigationState`. Listing uses keyset pagination and service queries enforce ownership. This is the concrete foundation for monitors.
- **Analytics:** current SQL-backed analytics provide a 30-day ingestion timeline with deterministic spike detection, top entities, and primary story countries. Top aggregations are bounded to ten items.
- **Jobs and scheduling:** durable feed, extraction, indexing, NLP, and clustering jobs use status/due/lease fields. `backend/app/scheduler.py` claims bounded batches and dispatches Dramatiq actors via Redis. Separate NLP, clustering, and search queues exist, and status/failure routes feed the Jobs UI.
- **Database and migrations:** Alembic revisions `0001`–`0009` cover auth, ingestion, processing, search, NLP, saved searches, clusters, and country-rule version widening. PostgreSQL JSONB is already used for durable structured state.
- **Frontend:** Next.js App Router has overview, search, articles, sources, clusters, graph, jobs, saved searches, and settings routes. `frontend/src/lib/api.ts` consumes types generated from checked-in `frontend/openapi.json`; aliases live in `api-types.ts`. TanStack Query, URL-backed investigation state, `GlassPanel`, `PageHeader`, charts, and explicit loading/error/empty states are established patterns.
- **Tests and acceptance:** pytest includes unit/API tests and PostgreSQL integration modules gated by `NEWSINTEL_RUN_POSTGRES_TESTS=1`. Vitest/Testing Library covers frontend routes; Playwright covers search, NLP, processing, investigations, and relationships. Every implementation phase has a checked-in acceptance script at `infra/test-phase<id>.sh`; existing scripts cover phases 3–7 and Phase 10A must add `infra/test-phase10a.sh`.
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
- [ ] Phase 10B — Entity dossier frontend (`NOT STARTED`)
- [ ] Phase 10C — Evidence-backed graph relationships (`NOT STARTED`)
- [ ] Phase 11A — Monitor data model (`NOT STARTED`)
- [ ] Phase 11B — Monitor evaluation and scheduler (`NOT STARTED`)
- [ ] Phase 11C — Monitor API/backend (`NOT STARTED`)
- [ ] Phase 11D — Monitor frontend (`NOT STARTED`)
- [ ] Phase 11E — What Changed (`NOT STARTED`)
- [ ] Phase 12A — Event domain/schema (`NOT STARTED`)
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

- **Status:** `NOT STARTED`
- **Objective:** Add `/entities/[id]` as a dense analyst dossier and link high-value entity references to it.
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

- **Status:** `NOT STARTED`
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

## Outstanding risks

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

## Change log

- 2026-09-20: Initialized the persistent roadmap from the approved master requirements. Marked Phase 10A `IN PROGRESS`; all repository-specific architecture statements remain explicitly pending code audit.
- 2026-09-20: Completed the initial code reality check, replaced provisional architecture notes with verified module/schema/workflow details, recorded Phase 10A index and alias risks, and added the required branch-per-phase workflow.
- 2026-09-20: Completed Phase 10A on `phase/10a-entity-dossier-backend`. Added PostgreSQL-backed dossier detail, evidence pages, and bounded relationships; regenerated OpenAPI/types; confirmed the existing entity-first partial index; and recorded passing verification.
- 2026-09-20: Established a required checked-in acceptance script for every implementation phase. Phase 10A is returned to `IN PROGRESS` until `infra/test-phase10a.sh` passes.
- 2026-09-20: Added and passed `infra/test-phase10a.sh`; Phase 10A is `COMPLETE` again and ready for review without merge or publication authorization.
- 2026-09-20: Corrected the stale Phase 10A detail-block status from `IN PROGRESS` to `COMPLETE`.
