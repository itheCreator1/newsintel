# Next step: Phase 7 — Relationships

## Context

Phases 1–6 are done with recorded evidence: Phase 6's acceptance gate passed on 2026-09-15 with
130 backend tests and 0 skips, then again with 50 frontend tests after code-review fixes; the
Phase 5 regression check passed after one exact-locator fix. `spec.md` §3.2, §6.1, §7.1, §7.4,
§10–12 describe Phase 7 next: **clustering, the entity graph, and the relationships UI**. Nothing
for it existed at the start of this branch: no `story_clusters`/`cluster_jobs` tables, no
`/clusters/{id}` or `/graph/entities` endpoints, no Graph nav item.

What we built on:
- Clustering runs on PostgreSQL rules only, so related-reporting detection keeps working while
  Elasticsearch is down (§3.2); the entity graph is computed from Elasticsearch aggregations
  (§7.4) since only the search index has per-document entity co-occurrence at query time.
- Independently published articles about the same event stay separate `Article` rows; clustering
  groups by similarity, it does not deduplicate (§3.2, acceptance #3).
- The graph API is always a bounded top-N subgraph, never the full archive (§7.4, acceptance #12).
- The only new nav item is "Graph"; clustering surfaces through search, article detail, and a
  dedicated cluster view instead of its own nav entry.

The plan's five steps were executed as five sequential SDD tasks (one logical commit range and one
code review per step), on `feature/phase7-relationships` off `main`.

## Plan (durable copy → `docs/superpowers/plans/2026-09-15-phase7-relationships.md`)

### 1. Clustering domain (commits `7e5ac4d..3d63ae4`)
Added migration `0008` (`story_clusters`, `story_cluster_members`, `article_cluster_state`,
`cluster_jobs`, plus a `COALESCE(published_at, first_discovered_at)` expression index and a
current-entity `(entity_id, article_id)` index for candidate lookups) and the `rule-1` clustering
engine (`app/clustering/engine.py`): a candidate is any article published within 48 hours whose
normalized title hash matches exactly, or that shares at least 2 current ORG/PERSON/GPE/EVENT
entities; candidates score `0.5 × title Jaccard + 0.35 × entity Jaccard + 0.15 × time proximity`
and join at `≥0.45`. The smaller of two merging clusters folds into the larger (ties keep the
older cluster); a cluster that drops below two members is dissolved. All cluster-graph writes for
one assignment serialize behind a single PostgreSQL advisory lock. Durable clustering intent
(`ArticleClusterState`/`ClusterJob`, mirroring the NLP job model) means a crash mid-assignment is
recoverable, not lost. A `recluster` CLI command (dry-run by default; `--article-id`, a date
range, or `--all`; `--apply`; `--batch-size` up to 500) lets an operator re-run the algorithm over
a selection. Clustering triggers automatically once the entities NLP processor succeeds for an
article — entities feed candidate selection, so clustering follows their publication, not raw
ingestion.

### 2. Cluster API and search integration (commits `3d63ae4..c1e2e48`)
Added `GET /api/v1/clustering/status`, `GET /api/v1/clustering/failures` (with retry), and
`GET /api/v1/clusters/{id}` (cursor-paginated members, article count, source count, publication
span). Bumped the search schema to version 3: indexed documents now carry `story_cluster_id` and
the cluster's `cluster_source_count`, so `article.related`/`article.story_cluster` (article detail)
and a `story_cluster_id` search/timeline filter both became possible. `rebuild-search` now always
builds schema version 3.

### 3. Entity co-occurrence graph (commits `c1e2e48..46b5079`)
Added `GET /api/v1/graph/entities`: an Elasticsearch `terms`+`significant terms`-style aggregation
(implemented as bucketed terms with a manual co-occurrence pass) over the current search criteria,
returning nodes (entity id/text/type/article_count) and edges (co-occurring pairs and their
article-count weight), each independently bounded — at most `MAX_NODES=50` nodes and
`MAX_EDGES=150` edges, with a `truncated` flag when either bound is hit and a `min_edge_weight`
(default 2) that drops sparse edges. Requires at least the schema-version-2 index (annotation
filters), else a `search_upgrade_required` 409 like the existing annotation-filter behavior.

### 4. Frontend relationships UI (commits `46b5079..725d530`)
Added `ClusterView.vue` (`/clusters/:id`), `GraphView.vue` + `EntityGraph.vue` (`/graph`, the only
new nav item), and cross-links: search results show "Also reported by N other sources" when a
result's cluster has more than one source; article detail shows related articles from the same
cluster and a "Filter by story" link; the cluster view offers "Search within this story". The
graph view keeps an accessible fallback node list (`<ul aria-label="Entities in this graph">`)
alongside the ECharts canvas, which is also the browser-test click target since the canvas itself
isn't reliably interactive in Playwright. Two runtime bugs were caught and fixed during
implementation: a compile-time-only route-param cast that crashed on unmount in `ClusterView.vue`,
and an unbounded `/graph` `nodes` URL parameter that could 422 against the backend's cap (now
clamped through one chokepoint). A follow-up fixed the entity-graph tooltip formatter, which read
node-only fields against edge hover data.

### 5. Acceptance gate, e2e coverage, docs (this task)
Added `infra/test-phase7.sh` (copied from `test-phase6.sh`): unique Compose project/ports/volumes;
a downgrade-to-`0007`/re-upgrade cycle asserting `story_clusters`, `story_cluster_members`,
`article_cluster_state`, and `cluster_jobs` are all gone and the canonical article count is
unchanged; lint/types/Vitest/build; a `phase7` user; two new fixture feeds
(`relationships-wire.xml`/`relationships-daily.xml`, 5 articles total: two byte-identical-titled
"same event" pairs across the two feeds plus one unrelated story, all with entity-rich
descriptions reusing the same "Barack Obama"/PERSON, "Microsoft"/ORG entity phrases test-phase5.sh
already smoke-tests, plus "United Nations"/"Brussels" for the second pair) seeded and polled to
completion (article count, then clustering settlement via `/clustering/status`); `rebuild-search`
to schema v3 and resume; a "relationships workflow" Playwright spec covering the search
cross-link, the cluster view, article detail's related article, "Search within this story", and
the entity graph (node render, connected-entity click-through, cross-filter into search);
`psql` assertions on cluster shape and the unrelated article's non-membership; and an authenticated
`curl` check that `nodes=50` never exceeds the documented bounds. Added `frontend/e2e/relationships.spec.ts`
(the plan's Critical Files list names it `e2e/relationships.spec.ts`, but every existing Playwright
spec in this repo lives under `frontend/e2e/`, so it was created there instead — a path
correction, not a scope change).

**Ruling R5 (binding architecture deviation, carried into this task from mid-plan discovery):**
Phase 5 and 6's gates only ever smoke-test the NER-enabled image in isolation, then run every real
workflow against the default NER-disabled stack — nothing in those phases needs real entities to
pass. Phase 7's gate cannot follow that precedent: with NER disabled, indexed documents carry zero
entities, so `/graph/entities` would return zero nodes for any query, and the acceptance plan's own
"the graph renders nodes" bullet could never be honestly demonstrated. `test-phase7.sh` therefore
builds `compose.ner.yaml`'s `nlp-worker` and keeps NER enabled for its *entire* run — one Compose
stack, not a separate short-lived NER stack torn down after a smoke test. This is scoped to this
one gate script; it is not a change to the default (NER-disabled) application configuration or to
any other phase's gate.

**Ruling R6 (binding fixture design, carried into this task):** `normalized_title_hash` only
casefolds and collapses whitespace, so the two "same event" pairs use byte-identical titles across
the wire/daily fixture feeds — this guarantees clustering via the title-hash arm regardless of NER
extraction quality. Entity-rich, differently-worded descriptions make the entity-overlap arm and
the graph genuinely exercised too, using entity phrases already proven reliable with this exact
spaCy model rather than invented names.

A carried-forward gap (from Task 3): the existing phase gate scripts never export
`NEWSINTEL_ELASTICSEARCH_URL` for host-side pytest, because no prior phase's Postgres-gated suite
talked to a real Elasticsearch from the host process. `tests/test_phase7_postgres.py` does, and
since every phase gate's pytest step runs the whole `tests/` directory, this turned out to be a
real regression, not just a Phase-7-local concern: the first Phase 6 regression run in this task
failed two tests with a DNS resolution error, because `test-phase6.sh` collects
`test_phase7_postgres.py` too. The fix is in `compose.e2e.yaml` (a `NEWSINTEL_TEST_ELASTICSEARCH_PORT`
host port publish for `elasticsearch`, default `0` so scripts that don't allocate it still get a
free kernel-chosen port) plus three lines in both `test-phase6.sh` and `test-phase7.sh` (port
allocation, export, and `NEWSINTEL_ELASTICSEARCH_URL` on the pytest invocation, mirroring how the
host database URL is already handled). `test-phase3.sh`, `test-phase4.sh`, and `test-phase5.sh`
have the identical latent gap and were left unfixed as out of this task's scope; each needs the
same three-line change.

## Verification
`cd backend && uv run pytest && uv run ruff check . && uv run mypy app`;
`cd frontend && npm test && npm run typecheck && npm run build`;
`docker compose config --quiet`;
`sh infra/test-phase7.sh` (0 skipped) run twice consecutively, then once more after the
Elasticsearch-port fix below (three clean runs total, all 196 backend / 0 skipped, 69 frontend,
both browser scenarios, graph bounded at 11 nodes/6 edges);
`sh infra/test-phase6.sh` as the regression check — failed once (2 of 196 backend tests, both in
`test_phase7_postgres.py`, on an unresolvable `elasticsearch` hostname from the host pytest
process), fixed as described above, then passed cleanly (196 backend / 0 skipped, 69 frontend, all
three browser scenarios).

## Evidence (2026-09-16)
- Commits: `655b131`/`3d63ae4` clustering domain, `c1e2e48` cluster API + search v3, `46b5079`
  entity graph, `d8519e4`/`d4b116a`/`725d530` frontend relationships UI, `6aaba63`/`257b45e`/`86d68d1`
  acceptance gate + fixtures + docs, `0d5c3a1` final-review fix round. Branch point `7e5ac4d` off
  `main`; final `0d5c3a1` — 12 commits total.
- `infra/test-phase7.sh` passed 3 clean runs (project prefix `newsintel-phase7-*`): 196 backend
  passed / 0 skipped (real Postgres + Elasticsearch, NER enabled for the whole run per Ruling R5),
  69 frontend, ruff/mypy clean, both browser scenarios (relationships seed, relationships
  workflow), graph bounded at 11 nodes/6 edges every run — deterministic. Migration downgrade to
  `0007` removed all four Phase 7 tables with an unchanged canonical article count.
- `infra/test-phase6.sh` regression: failed once for a real, pre-existing reason (§ above), fixed,
  then passed cleanly. `docker compose config --quiet` passed throughout.
- Zero bugs found in Tasks 1–4's application code across all three clean `test-phase7.sh` runs —
  a positive signal on incremental task quality, not just the final gate.
- Final whole-branch review (opus, diff `7e5ac4d..86d68d1`, 11 commits): **Changes requested**, no
  Critical findings. The safety-critical bounding invariant (§7.4, acceptance #12) was traced
  end-to-end — route clamp, aggregation-size clamp, parse-time clamp, and a final backstop, each
  with a dedicated unit test plus a live authenticated assertion in the gate — and holds under
  every path the reviewer could construct, including degenerate direct-service inputs (fails
  closed, not open). Migration `0008`'s downgrade was confirmed genuinely lossless (adds no column
  to any pre-existing table; drops only what it created, in FK-safe order). No auth/CSRF gaps, no
  injection paths in the new SQL or ES queries.
- Four Important findings, one escalated security minor, and one escalated cosmetic minor required
  a fix round (all frontend + docs, no backend changes): a rollback runbook that named only one of
  four services actually reading the dropped tables; a "reported by N other sources" count that
  used the wrong subtraction whenever an article itself spans more than one of a cluster's feeds
  (caught by the branch's own test fixture, which was internally self-contradictory); a graph
  Source filter that the backend already supported but the frontend actively stripped from the
  request; an active story-cluster filter with no visible clear affordance; a back-link label
  hardcoded to "search" even from the two new non-search origins; and an ECharts tooltip formatter
  returning unescaped NER-derived text into the library's default HTML render mode (no demonstrated
  exploit, but the only such sink in the frontend).
- Fix round (commit `0d5c3a1`, BASE `86d68d1`): all seven findings fixed in one commit, confirmed
  by direct source inspection to require no backend changes. Frontend 69/69 tests, typecheck, and
  build clean.
- Scoped re-review of the fix commit (opus): **Acceptable**. Re-verified every fix against primary
  sources rather than trusting the report — recomputed the corrected source-count formula against
  `documents.py`/`engine.py` by hand, confirmed `/graph/entities` genuinely accepts `source_id`,
  confirmed the four README-listed services are the *complete* set of DB-touching app containers,
  and proved the corrected test fixture is a genuine regression guard (reverting only the formula
  made the test fail, then restored). No test assertions were weakened to make them pass. Two
  minor residuals parked, not fixed: the graph's side-panel "top articles" query still omits
  `source_id` even though the main graph request now honors it (one-line follow-up); the new
  "clear story filter" banner has no dedicated test despite the e2e spec already walking through
  the state where it renders.

## Code review follow-up (2026-09-16)
Per-task reviews (opus for Tasks 1/3/final review, sonnet for Tasks 2/4/5 and all fix-round
re-reviews) found no Critical issues at any stage. Fixed during task-level fix loops:
- Graph API: `MAX_NODES` clamp itself had zero direct test coverage (both existing tests only
  exercised the per-request `nodes` param) — closed with two mutation-verified tests.
- `EntityGraph.vue`'s tooltip formatter crashed/garbled on edge hover, reading node-only fields
  against edge data — one-line `dataType === 'node'` guard.
- `test-phase3.sh`/`test-phase4.sh`/`test-phase5.sh` all needed the Elasticsearch-host-port fix
  Task 5 applied to `test-phase6.sh` — but `test-phase3.sh` needed a *different* fix (Ruling R7):
  it deliberately never starts Elasticsearch at all (pre-search era), so the fix there is
  `--ignore=tests/test_phase7_postgres.py`, not a port export.

Fixed in the final-review fix round (commit `0d5c3a1`, all listed under Evidence above): the
rollback runbook's incomplete service list, the "other sources" count formula, the missing graph
Source filter, the invisible story-cluster filter, the hardcoded back-link label, and the tooltip
HTML-sink hardening.

Recorded deviations and rulings (see the SDD ledger at
`.superpowers/sdd/serialized-riding-wave/progress.md` for full rationale):
- R1: effective-date index is a functional index on `COALESCE(published_at, first_discovered_at)`.
- R2: OpenAPI regenerated at the end of both Step 2 and Step 3, not only Step 2 as the plan text
  literally said.
- R3: this doc's Evidence and Code-review-follow-up sections were written by the controller after
  the final whole-branch review, not by Task 5 — mirroring how `2026-09-15-phase6-investigations.md`
  was actually assembled.
- R4: `frontend/e2e/relationships.spec.ts` was authored in Task 5, not Task 4.
- R5, R6: see the binding rulings recorded inline in the Plan section above.
- R7: `test-phase3.sh`'s Elasticsearch-related gap needed `--ignore`, not a port-export fix, since
  that script deliberately predates search/ES entirely.

Confirmed non-escalations, parked for a future phase rather than fixed here: `current_search_target`'s
unordered `.limit(1)` (single-current-row is code-enforced by the rebuild transaction, not a live
bug, though now more load-bearing since it also gates the graph); the clustering advisory lock plus
single-threaded `nlp-worker` is a genuine archive-wide throughput ceiling, now documented in the
README (M-3) rather than changed; catalogue-dropped graph nodes not setting `truncated` (cosmetic);
stale `cluster_source_count` in Elasticsearch after an article deletion cascades a member away
(only reachable via Phase 9's not-yet-built deletion workflow); two new non-concurrent index builds
in migration `0008` (consistent with every prior migration in this codebase, not a regression).

After the fix round, `frontend` (69/69 tests, typecheck, build) and `backend` (ruff/mypy) were
re-verified clean. The Docker-based acceptance gates were not re-run after the fix round — the
final reviewer judged, and the scoped re-reviewer confirmed, that none of the seven fixes touch
backend/gate-relevant code paths.
