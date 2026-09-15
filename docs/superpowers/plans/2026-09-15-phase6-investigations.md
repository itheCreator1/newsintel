# Next step: Phase 6 — Investigations

## Context

Phases 1–5 are done with recorded evidence: every Phase 5 checkbox is `[x]`, the acceptance run on 2026-09-15 passed 94 backend tests with 0 skipped, Phase 3/4 regression checks passed again, and the post-review fix is in `c9f5f23`. In `spec.md` §12, Phase 6 comes next: **timeline, cross-filtering, URL investigation state, saved searches**. The matching acceptance criteria are 11 and 12 (timeline part). Nothing for Phase 6 exists yet: there is no `SavedSearch` code, no Elasticsearch aggregations, and no chart library.

What we can build on:
- `frontend/src/views/SearchView.vue` already writes search criteria to the URL (`submit()` calls `router.push`) and reads them back with `routeForm()`/`criteria`. Pagination stays out of the URL. So "URL state" is mostly done. The gaps are the timeline range, the bucket size, and a round-trip into article detail and back.
- `backend/app/search/routes.py` `search_articles` (line ~160) already turns query syntax and GUI filters into an ES query and resolves sources and annotations. The histogram should use that same query builder so its counts always match the results.
- Pinia is installed but unused. TanStack Query holds server state and should stay that way.
- Decision: **add Apache ECharts** (the spec requires it, and Phases 7 and 8 can reuse it). Load it only on the Search page.

## Plan (durable copy → `docs/superpowers/plans/2026-09-15-phase6-investigations.md`)

### 1. Timeline aggregation API
- Pull the ES query/filter construction out of `search_articles` into a reusable function in `backend/app/search/query.py` (or next to it), without changing behavior. The existing `test_search_query.py` tests must still pass.
- Add `GET /api/v1/search/timeline`. It takes the same criteria as `/search` plus `interval=auto|hour|day|week|month`. It runs a `date_histogram` on the effective date with `size: 0`. Auto mode picks the bucket size from the date range and caps the number of buckets (for example ≤ 200). It returns `{interval, buckets:[{start, count}]}`.
- Return 409 `search_upgrade_required` and 503 in the same cases `/search` does. Require auth. Nothing is written to the database.
- Tests: bucket-size selection, the filters match `/search`, an unavailable ES returns 503, and a schema-v2 check against real ES in `test_phase6_postgres.py`.

### 2. Saved searches (canonical in PostgreSQL)
- Add an Alembic migration `0006` with a `saved_searches` table: UUID, user_id FK, unique (user_id, name), a `state` JSONB field with a `version` inside, and created/updated timestamps. Test downgrade → re-upgrade.
- Add a `backend/app/investigations/` domain with models, a service, routes, and schemas. Endpoints: list (cursor-paginated), create, get, rename/update, delete. Writes require CSRF. Validate `state` against the same criteria schema `/search` uses, so a saved search can't hold values the search rejects.
- Regenerate `frontend/openapi.json` and `types.generated.ts` together.

### 3. Frontend investigation state
- Add `npm i echarts`. Create a `TimelineChart.vue` component that uses a brush selection to set `after`/`before`, with a manual bucket-size selector (writes `interval` to the URL) and loading/empty/error states. Import it dynamically.
- Move the URL ↔ criteria mapping from SearchView into a composable (`useInvestigationState`) so SearchView, the timeline, and saved searches share it. Changing criteria still resets pagination, and late responses are still ignored.
- Cross-filtering: clicking a source, entity, keyword, or country in search results or article detail adds it to the active investigation (URL push, so back/forward works). Keep the existing `from` return path.
- Add a Saved Searches view and nav item: save the current state by name, open (restores the full URL state), rename, and delete. Show a clear message when a saved state no longer validates.
- Vitest: URL round-trip, brush → date range, cross-filter push, restoring a saved search.

### 4. Acceptance
- Add `infra/test-phase6.sh` modeled on `test-phase5.sh`: unique Compose project, ports, and volumes, plus a migration cycle.
- Playwright `investigations.spec.ts`: search → brush the timeline → reload/back/forward keeps the state → cross-filter from a result → save → clear → restore the saved search.
- Run the full gate (pytest with 0 skipped, ruff, mypy, vitest, typecheck, build, compose config, Phase 5 regression) and record the counts in the plan doc. Update the README.

## Separate spec gaps (not Phase 6; context only)
- `OverviewView.vue` is a 6-line health stub, while §7.1 describes a full dashboard.
- Missing: SSE (§8), structured JSON logging (§9), exports and backup/restore scripts (§9 → Phase 9), Tailwind and headless UI (§2.1).
- Phase 7 (clustering/graph) and Phase 8 (analytics) are not started.
- Maintainability: the Vue views pack their templates onto single lines up to ~600 characters. Worth reformatting as its own commit, not mixed in with other changes.
- Housekeeping: the checkboxes in `docs/superpowers/plans/2026-09-13-phase3-stabilization.md` were never ticked, even though the README records the gate passing. Tick them from that evidence in a docs-only commit.

## Verification
`cd backend && uv run pytest && uv run ruff check . && uv run mypy app`; `cd frontend && npm test && npm run typecheck && npm run build`; `docker compose config --quiet`; `sh infra/test-phase6.sh` (0 skipped) plus `sh infra/test-phase5.sh` as the regression check.

## Evidence (2026-09-15)
- Commits: `fa41326` timeline API, `33af473` saved searches, `cf26f40` filterable result references, `3275587` frontend investigation state, plus the acceptance commit.
- `infra/test-phase6.sh`: passed on the third run (project `newsintel-phase6-69106-1789465344`). Backend pytest 130 passed / 0 skipped; Ruff and mypy (73 files) clean; Vitest 46 passed across 8 files; `vue-tsc` and `vite build` passed; migration downgrade to 0006 removed `saved_searches` with an unchanged article count. Playwright: seed, investigation workflow (brush 1–5 September → 4 of 6 articles, clear, source cross-filter → 3, back/forward, reload, manual week interval, save, duplicate-name 409, open restores the exact URL, rename, delete), and the invalid stored state message.
- Earlier runs failed in the harness only: run 1 dragged an off-screen chart (fixed by scrolling it into view); run 2 miscounted `psql` output from `UPDATE … RETURNING` (fixed with a counting CTE). Only one full green run of all three scenarios exists.
- Regression: `infra/test-phase5.sh` first failed because `getByRole('link', { name: 'Search' })` also matched the new Saved Searches link; with `exact: true` it passed (130 backend, 46 frontend, all three browser scenarios, project `newsintel-phase5-88363-1789465757`).
- `docker compose config --quiet` passed.
- Deferred: a standalone Timeline navigation item (spec §7) is not part of this plan; the long Search filter form pushes the timeline below the fold at 720 px and is worth a collapsible-filters follow-up.

## Code review follow-up (2026-09-15)
Review of `250badd..fe8ac51` found no critical issues. Fixed:
- Brushing a calendar-aligned edge bucket could widen an active `after`/`before`; the selection is now clamped so it only narrows.
- FastAPI validation errors arrive as a list and surfaced as "Request failed"; the client now shows each field and message, so a state that `/search` tolerates but a saved search rejects (for example `after` ≥ `before` or a three-letter country) is explained.
- Hand-written comma-separated list values in the URL are split; the query text keeps its commas.
- Saving a search refreshes the Saved Searches list.

Recorded deviations and follow-ups:
- Migration is 0007, not 0006. URL state lives in the pure `src/investigation.ts` module rather than a `useInvestigationState` composable.
- Cross-filtering replaces the clicked field's values (narrowing), not appends. Search results offer source, source country, and story country; entity/keyword cross-filters come from article detail because results carry no annotations.
- Backend timeline tests use a fake Elasticsearch adapter; real-Elasticsearch timeline coverage comes from the manual check during step 1 and the Phase 6 browser gate, not a schema-v2 integration test.
- Click-to-select a single bar is wired in `TimelineChart.vue` but not browser-verified, so the README documents dragging only.
- Follow-ups: order saved searches case-insensitively (current order and its test depend on byte collation); map only the name constraint's `IntegrityError` to a 409; keep extra `processing_status` values when the form is resubmitted; one validated criteria model shared by `/search`, the timeline, and saved searches; keyboard access and theme tokens for the chart.
- After these fixes `infra/test-phase6.sh` passed again (project `newsintel-phase6-102864-1789466311`): 130 backend passed / 0 skipped, Ruff and mypy clean, Vitest 50 passed, typecheck and build, all three browser scenarios.

