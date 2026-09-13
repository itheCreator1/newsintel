# Phase 3 Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing feed-to-extracted-article pipeline reliable in Docker Compose, then advance to a separately planned Elasticsearch search milestone.

**Architecture:** Preserve PostgreSQL-backed jobs, leases, Dramatiq dispatch, and independent fetching/extraction stages. Complete worker registration, persistent object storage, lifecycle recovery, and integration verification before adding search. Keep Elasticsearch outside the canonical ingestion dependency path.

**Tech Stack:** Existing Python/FastAPI/SQLAlchemy/Alembic, Dramatiq/Redis, Trafilatura, Docker Compose, Vue/TypeScript/TanStack Query, pytest and Vitest.

**Spec:** [spec.md](../../../spec.md), especially sections 2–4, 8–12.

## Global Constraints

- PostgreSQL must remain the canonical source of truth.
- Ingestion must continue when Elasticsearch is unavailable.
- Jobs must be idempotent, safely retryable, and observable, with exponential backoff and an explicit failed/dead-letter state after repeated failures.
- Raw HTML, when enabled, must be stored through the object-storage abstraction, initially on a local filesystem/Docker volume.
- Removing a feed must not automatically remove archived articles.
- Containers must provide appropriate health/readiness checks and run as non-root where practical.
- Use cursor-based pagination for large article/result collections.
- The core application must function without LLMs, cloud AI services, or API keys.
- Preserve existing uncommitted changes; review overlapping edits before implementation. This planning task does not authorize implementation.

---

## Evidence and recommended next milestone

Assessment date: 2026-09-13. This is a source inspection plus local checks, not a production or full-stack certification.

| Phase | Evidence | Assessment |
| --- | --- | --- |
| 1: Foundation | Compose, authentication, migrations, health routes, Vue shell | Substantial foundation exists; operational requirements remain incomplete |
| 2: Feeds | Feed configuration/API/UI, scheduler, conditional fetching, URL identity, discovery records, cursor APIs | Substantial implementation; database integration tests were skipped in this assessment |
| 3: Extraction/jobs | Migration `0003`, processing stages, content hashes, filesystem storage, job API/UI | Implemented in part, but not ready to declare complete |
| 4: Search | Elasticsearch service and URL setting only; no search domain, indexer, or search API | Next major feature after stabilization |
| 5–9 | No NLP, saved-search, clustering, analytics, or export domains found | Subsequent milestones, not part of this execution plan |

Verification performed:

- Backend `.venv/bin/pytest -q`: **33 passed, 2 skipped**. Both skipped tests require the PostgreSQL fixture stack.
- Backend `.venv/bin/ruff check .`: **failed**, one E501 violation at `backend/app/articles/processing.py:43`.
- Backend `.venv/bin/mypy app`: **passed**, 43 source files.
- Frontend `npm test`: **2 passed**; `npm run typecheck`: **passed**.
- No full Compose pipeline, real Elasticsearch test, or browser E2E run was performed.

Concrete gaps driving priority:

1. `compose.yaml` starts Dramatiq with diagnostics and ingestion modules but omits `app.jobs.articles`. The scheduler dispatches that actor.
2. Article storage defaults to `/var/lib/newsintel/articles`; Compose has no article-storage volume, and the non-root image does not prepare a writable directory there.
3. `process_claim()` assigns `old_html_key` only for existing content and reads it after the transaction even for new content. The surrounding exception handler runs after success clears the claim, so this path can silently swallow a post-commit exception.
4. Processing tests cover normalization, empty extraction, storage round trips, and retry timing, but not actual job transitions, concurrent requests, expired leases, successful extraction, or recovery.
5. `article_temporary_html_hours` exists without a cleanup implementation. Filesystem cleanup and committed database outcomes need separate failure handling.
6. The Articles view requests only the first article page despite a cursor-capable API. Source selection also only loads the first feed page.
7. The README mainly documents Phase 2. Extraction storage, recovery, and verification are not covered.

Additional tracked gaps are not prerequisites for adding search: root `/infra` is absent; Tailwind/headless components/ECharts are not installed; NLP/geographic data is absent; source-title/content-hash duplicate handling needs further definition and verification; SSE and comprehensive structured operational logging are incomplete. Do not equate this milestone with complete specification compliance.

## Task 1: Reproduce processing failures and verify stage transitions

**Files:**

- Create `backend/tests/test_phase3_postgres.py`: real PostgreSQL job/content lifecycle tests.
- Create `backend/tests/fixtures/article.html`: deterministic readable article fixture.
- Modify `backend/app/articles/processing.py`: extraction state and cleanup boundaries.
- Modify `backend/app/articles/service.py`: only where recovery/concurrency tests demonstrate a defect.
- Modify `backend/app/articles/routes.py`: retry reuse and temporary-object ownership, if reproduced.
- Modify `backend/tests/test_article_processing.py`: HTTP failure classification tests.

**Interfaces:** Preserve `request_processing(db, article_id, requested_mode, *, automatic=False)`, `claim_due_job(db, job_id, lease_seconds)`, and `process_claim(job_id, token)`. Preserve existing API response shapes unless a tested defect requires a documented additive change.

- [ ] Create uniquely identified PostgreSQL fixtures using the existing session factory; configure a disposable test database, never the user's archive. Mock outbound HTML fetches and use `tmp_path` storage for lifecycle tests.
- [ ] Add tests for first extraction, repeated identical content, changed content, retained HTML replacement, and transition from retained HTML to text-only. Assert hashes, change count, timestamps, final job status, and referenced object existence.
- [ ] Add concurrent processing-request and retry-request tests: one active job per article, strongest requested mode preserved, reused jobs never have their stage or object reference overwritten by a retry of another job.
- [ ] Add expired/replaced-claim tests: stale workers must not update content or another worker's attempt, and the scheduler can reclaim abandoned work.
- [ ] Add retry tests for timeout, 429, 5xx, permanent 4xx, missing temporary HTML, and storage failure. Verify bounded retries and visible terminal failures. Include this direct classification regression:

```python
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(429, ("http_transient", True)),
     (503, ("http_transient", True)),
     (404, ("http_permanent", False))],
)
def test_article_http_failure_classification(status_code, expected):
    from app.articles.processing import ArticleHttpStatus, _failure
    assert _failure(ArticleHttpStatus(status_code)) == expected
```

- [ ] Run the new tests before changing behavior; record which cases fail and why. Instrument/capture cleanup errors so the swallowed first-extraction exception is observable in its regression test.
- [ ] Initialize prior-object state for both content branches. Separate post-commit cleanup failures from fetch/extraction failures; log cleanup failures with job/article/object context and leave committed successful extraction successful.

```python
# Prior-object state must exist on both insert and update paths.
old_html_key: str | None = None
```

- [ ] Fix only additional lifecycle defects reproduced by the tests, retaining the user's existing lease, retry, and serialization changes. Format the long HTTP classification expression.
- [ ] Run `.venv/bin/pytest -q tests/test_article_processing.py tests/test_phase3_postgres.py` from `backend` with the test database enabled. Run `.venv/bin/ruff check .` and `.venv/bin/mypy app`.
- [ ] Review the scoped diff and checkpoint the change separately from deployment wiring.

**Exit:** Real PostgreSQL tests prove stage transitions, idempotency, concurrency, content changes, and retry recovery; no cleanup error is silently discarded.

## Task 2: Make extraction executable and persistent in Compose

**Files:**

- Modify `compose.yaml`: register article actor and mount a named article-storage volume.
- Modify `backend/Dockerfile`: create the storage directory with ownership for UID/GID 10001 before `USER newsintel`.
- Modify `.env.example`: document article-storage and fetching settings used by deployment.
- Modify `compose.e2e.yaml`: provide isolated fixture configuration for container tests.
- Modify `README.md`: migration, storage, worker, and fixture verification commands.

**Interfaces:** Keep `/var/lib/newsintel/articles` as the default storage root. All worker instances processing the same jobs must see the same backing storage. API/scheduler mounts are needed only if they access objects directly.

- [ ] Confirm the effective Compose worker command and reproduce actor availability/storage permissions in an isolated test stack.
- [ ] Extend the worker command:

```yaml
command: ["dramatiq", "app.jobs.diagnostics", "app.jobs.ingestion", "app.jobs.articles", "--processes", "1", "--threads", "4"]
```

- [ ] Add named volume `article-data` mounted at `/var/lib/newsintel/articles` on the worker. Prepare that directory in the image with ownership matching the existing non-root user; verify the mounted directory is writable on a fresh volume.
- [ ] Pass documented article settings through Compose consistently with Pydantic settings. Restrict fixture-host exceptions to test configuration.
- [ ] Apply migrations explicitly in the isolated stack; ingest the deterministic feed/article fixture through scheduler → Redis → worker, without directly invoking `process_claim()`.
- [ ] Recreate the worker and verify retained HTML and readable PostgreSQL content remain available. Verify RSS-only mode creates no article fetch work.
- [ ] Repeat ingestion with Elasticsearch unavailable and confirm canonical ingestion/extraction still succeed.
- [ ] Validate `docker compose config --quiet`, document outcomes, and checkpoint deployment wiring.

**Exit:** A non-root Compose worker consumes article jobs and retained objects survive container recreation.

## Task 3: Formalize storage/extraction boundaries and recover abandoned objects

**Files:**

- Modify `backend/app/articles/storage.py`: object-storage protocol plus local implementation.
- Modify `backend/app/articles/extraction.py`: extractor protocol plus default implementation.
- Create `backend/app/articles/maintenance.py`: bounded object cleanup.
- Modify `backend/app/articles/processing.py`: use the documented interfaces.
- Modify `backend/app/cli.py`: explicit article-storage cleanup command.
- Create `backend/tests/test_article_maintenance.py`: preservation and cleanup tests.
- Modify `README.md`: cleanup operation and raw-object backup requirements.

**Interfaces:** Define `ObjectStorage` with `put(content: bytes) -> str`, `get(key: str) -> bytes | None`, and `delete(key: str) -> None`; define `Extractor` with `name: str`, `version: str`, and `extract(html: bytes) -> str`. Keep local inventory/age inspection in maintenance support rather than requiring every future extractor to know storage details.

- [ ] Add tests proving cleanup preserves retained content and all referenced active/failed-job temporary objects, ignores recent files, and removes only sufficiently old unreferenced files in the configured storage directory.
- [ ] Add a dry-run test that reports eligible files without deleting them. Include a race test preventing cleanup from deleting a newly published/referenceable object.
- [ ] Define the protocols, preserving current method behavior:

```python
from typing import Protocol

class ObjectStorage(Protocol):
    def put(self, content: bytes) -> str: ...
    def get(self, key: str) -> bytes | None: ...
    def delete(self, key: str) -> None: ...
```

- [ ] Implement bounded local inventory and batched PostgreSQL reference checks, using the configured age threshold and a final reference check. Coordinate with publication or use an age/lease rule that demonstrably covers in-flight writes; do not assume a check followed by deletion is race-free.
- [ ] Add `python -m app.cli cleanup-article-storage --dry-run` and an explicit `--apply` mode. Preserve existing user-management commands. Report scanned, eligible, deleted, and failed counts; restrict targets to validated opaque object keys.
- [ ] Run `.venv/bin/pytest -q tests/test_article_maintenance.py tests/test_article_processing.py` and relevant PostgreSQL race tests; checkpoint this task separately.

**Exit:** Storage and extraction have explicit extension boundaries, and safe cleanup is documented and testable without introducing S3 or full version history.

## Task 4: Complete the usable article-processing workflow

**Files:**

- Modify `frontend/src/views/ArticlesView.vue`: paginated browsing, request states, detail preservation.
- Modify `frontend/src/views/JobsView.vue`: retry visibility and refresh behavior where required.
- Modify `frontend/src/api.ts` and `frontend/src/api-types.ts`: reuse generated request/response contracts.
- Regenerate `frontend/openapi.json` and `frontend/src/types.generated.ts` only when the backend contract changes.
- Create `frontend/src/views/ArticlesView.test.ts` and `frontend/src/views/JobsView.test.ts`.

**Interfaces:** Use the existing `api.articles(feedId?, cursor?)`, `api.feeds(cursor?)`, article-detail and process/retry methods. Use TanStack Query for pagination and mutation invalidation. Preserve `feed` and `article` URL parameters until the investigation-state phase establishes its complete schema.

- [ ] Write component tests for loading another article page, selecting a feed beyond the first page, preserving detail selection, resetting cursors after source changes, process errors, and successful retry refresh.
- [ ] Run `npm test -- src/views/ArticlesView.test.ts src/views/JobsView.test.ts` and confirm missing behaviors fail.
- [ ] Implement cursor consumption using `useInfiniteQuery`, following the existing API contract:

```ts
const articles = useInfiniteQuery({
  queryKey: ['articles', feedId],
  initialPageParam: undefined as string | undefined,
  queryFn: ({ pageParam }) => api.articles(feedId.value, pageParam),
  getNextPageParam: page => page.next_cursor ?? undefined,
})
```

- [ ] Provide explicit load-more navigation, loading/error/empty states, and disabled pending mutation controls. Make the feed selector page through available sources rather than silently omitting them.
- [ ] Run `npm test`, `npm run typecheck`, and `npm run build`; checkpoint the workflow change.

**Exit:** Users can find articles beyond the first page, request extraction, see errors, and retry without losing article context.

## Task 5: Establish the milestone acceptance gate

**Files:**

- Create `infra/test-phase3.sh`: repeatable isolated integration workflow with bounded readiness waits and nonzero failure exits.
- Create `frontend/e2e/article-processing.spec.ts`: login → feed → ingestion → extraction → detail → failed job → retry.
- Modify `frontend/package.json` and lockfile: browser-test runner and explicit E2E script.
- Create `frontend/playwright.config.ts`: isolated base URL and deterministic browser setup.
- Modify `README.md`: actual Phase 3 state, verification, migration, storage/backup, and failure-recovery documentation.

**Interfaces:** Test-only Compose project and database, deterministic local fixtures, existing authenticated APIs and CSRF workflow. The test script must never remove existing project volumes or alter a running archive.

- [ ] Add the browser test using actual UI controls and deterministic fixture responses; assert extracted text and visible failure/retry results rather than relying on screenshots alone.
- [ ] Implement the script to create/start an isolated stack, explicitly migrate, create its test user, run backend integration and browser tests, and retain logs on failure. Use condition polling with deadlines.
- [ ] Run all backend tests with PostgreSQL tests enabled, Ruff, mypy, frontend tests, type checking, production build, Compose validation, and the new full-stack workflow.
- [ ] Confirm retained objects survive worker recreation, indexing unavailability does not block ingestion, and no required integration test is skipped in the acceptance run.
- [ ] Review changes against spec sections 2–4 and 8–12; record passed checks and any unresolved requirements. Checkpoint only scoped changes.

**Exit:** Phase 3 can be declared complete for this bounded milestone with reproducible evidence. Do not claim all five-million-article performance requirements or later-phase features are satisfied.

## Following milestone: Phase 4 search

Create a separate search implementation plan after the Phase 3 gate passes. This keeps database/job stabilization independently reviewable from the search subsystem. Recommended order:

1. **Durable indexing intent:** PostgreSQL indexing state/version and recoverable dispatch. Commit archive updates plus pending intent together; publish work after commit. Scheduler recovery must cover a crash between commit and queue publication.
2. **Versioned index and bulk worker:** `backend/app/search/` for documents, mappings, indexing, and query logic; a domain-local Dramatiq actor; stable article UUID document IDs; bounded batches; partial-batch retry handling; monotonic versions preventing stale jobs overwriting new documents.
3. **Rebuild workflow:** Keyset-read PostgreSQL into a new versioned index, capture/reconcile concurrent changes, verify catch-up, then atomically move the alias. Expose progress and failures; retain the prior index until an explicit cleanup decision.
4. **Search API:** `/api/v1/search`, plain text/phrases/AND and documented field syntax, GUI-compatible structured filters, highlights, stable cursor pagination, and relevance/date sorting. Reject malformed queries/cursors and report search unavailability explicitly.
5. **Search UI:** Search input, source/date/source-country filters, safe highlighted snippets, cursor navigation, article detail, and indexing backlog. Escape untrusted content; do not inject unrestricted highlight HTML.
6. **Search acceptance:** Prove RSS → worker → PostgreSQL → Elasticsearch → FastAPI → Vue; test duplicate jobs, outages/recovery, concurrent updates/rebuild, and stable pagination over equal sort values. Document query/index plans and bounded memory/payload behavior.

Avoid inventing unsupported data: current articles lack detected language, primary-story/mentioned-country annotations, entities, keywords, and story clusters. Prepare extension points, but add their functional filters when Phases 5 and 7 supply canonical data. Define popularity ranking against an actual measurable signal rather than fabricating it. Full URL investigations/saved searches remain Phase 6.

After search: Phase 5 NLP → Phase 6 investigations/timeline → Phase 7 clustering/graph → Phase 8 the three analytics modules → Phase 9 exports, broader reprocessing, backups, and performance validation. Track earlier-phase compliance gaps alongside this roadmap so they are not forgotten.
