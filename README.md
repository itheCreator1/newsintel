# NewsIntel

NewsIntel is a self-hosted foundation for collecting, searching, and investigating a long-running news archive. PostgreSQL is authoritative; Redis handles background work and Elasticsearch is a rebuildable search index.

## Architecture

The Compose stack contains a Vue frontend, FastAPI API, general and NLP Dramatiq workers, a
scheduler, PostgreSQL, Redis, and Elasticsearch. The browser reaches only the frontend on
`127.0.0.1:8080`; nginx serves the application and proxies `/api` to FastAPI. Application
containers never run migrations automatically. The NLP worker has its own queue and defaults to
one process with one execution thread, so CPU-heavy annotation cannot consume ingestion and
extraction capacity.

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

Run the isolated Phase 3 database and browser acceptance gate with `sh infra/test-phase3.sh`.
Each invocation chooses an unused frontend, fixture, and PostgreSQL port plus a unique
`newsintel-phase3-<pid>-<timestamp>` Compose project. It removes only that invocation's containers
and volumes. On failure it retains Compose logs and Playwright screenshots/traces under the printed
`/tmp/newsintel-phase3-<pid>-<timestamp>` artifact directory.

The completed gate was run twice consecutively on 2026-09-14. Both runs passed 54 backend tests
with no skips, Ruff, mypy, 13 frontend tests, type checking, the production build, Compose
validation, and both browser scenarios. The browser covered login, source creation, ingestion,
successful retained extraction, a three-attempt transient failure, retry, recovery, and article
detail after worker recreation. The runner also verified deterministic 200/404/503 fixtures,
kept Elasticsearch stopped, and compared retained HTML plus PostgreSQL content before and after
worker recreation.

Run the isolated Phase 4 gate with `sh infra/test-phase4.sh`. It uses its own Compose project,
ports, and named volumes; exercises migration upgrade/downgrade, real PostgreSQL, Redis,
Elasticsearch, scheduler and worker delivery, an index rebuild, browser search, and ingestion
during an Elasticsearch outage. Failure logs and browser artifacts are retained under the printed
`/tmp/newsintel-phase4-<pid>-<timestamp>` directory.

Run the isolated Phase 5 gate with `sh infra/test-phase5.sh`. It uses unique Compose projects,
ports, and named volumes; builds both the base and optional-NER images; rejects skipped database
tests; and exercises migration rollback, NLP dispatch and reprocessing, search catch-up during an
Elasticsearch outage, and browser annotation flows. It retains logs, query plans, measurements,
and Playwright artifacts under the printed `/tmp/newsintel-phase5-<pid>-<timestamp>` directory on
failure.

Run the isolated Phase 6 gate with `sh infra/test-phase6.sh`. It uses its own Compose project,
ports, and named volumes; rejects skipped database tests; checks that the saved-search migration
downgrades without touching canonical articles; seeds two dated fixture sources; rebuilds search;
and drives timeline brushing, manual intervals, cross-filtering, back/forward and reload, and saved
search save/open/rename/delete plus an invalid stored state in a real browser. Failure logs and
Playwright artifacts are retained under the printed `/tmp/newsintel-phase6-<pid>-<timestamp>`
directory.

The completed Phase 6 gate passed on 2026-09-15 with 130 backend tests and no skips, Ruff, mypy,
46 frontend tests, type checking, the production build, and all three browser scenarios, and passed
again with 50 frontend tests after the code-review fixes. The two
earlier attempts that day failed in the harness (an off-screen drag and a miscounted `psql` result)
and were fixed before that run. The Phase 5 gate was then rerun as a regression check and passed
after one browser locator was made exact, because the new Saved Searches link also matched `Search`.

Run the isolated Phase 7 gate with `sh infra/test-phase7.sh`. It uses its own Compose project,
ports, and named volumes; rejects skipped database tests; checks that the clustering migration
downgrades to `0007` (removing `story_clusters`, `story_cluster_members`, `article_cluster_state`,
and `cluster_jobs`) without touching the canonical article count; and, unlike every earlier phase
gate, keeps the optional NER-enabled `nlp-worker` running for its whole run instead of only
smoke-testing it in isolation, because the entity graph needs real entities in every seeded
document. It seeds two fixture feeds that report the same two events (byte-identical titles, so
clustering is guaranteed regardless of NER extraction quality) plus one unrelated story, waits for
NLP and clustering to settle, rebuilds search to schema version 3, and drives a real browser
through the search-to-cluster cross-link, the cluster view, article detail's related article,
"Search within this story", and the entity graph (node render, connected-entity click-through, and
a cross-filter into search by entity). It then asserts cluster shape and the unrelated article's
non-membership in PostgreSQL, and calls the live, authenticated entity-graph endpoint to confirm
`nodes=50` stays within the documented 50-node/150-edge bounds. Failure logs and Playwright
artifacts are retained under the printed `/tmp/newsintel-phase7-<pid>-<timestamp>` directory.

The completed Phase 7 gate passed twice in a row on 2026-09-15, with no fixes needed to the script
itself between runs: 196 backend tests and no skips (including real-PostgreSQL and
real-Elasticsearch clustering and graph coverage), Ruff and mypy clean, 69 frontend tests, type
checking, the production build, and both browser scenarios (seed, then the
search/cluster/article-detail/graph workflow) in each run. The bounded-graph API check reported 11
nodes and 6 edges against the fixture data, well within the 50-node/150-edge caps. Running the
Phase 6 gate as a regression check surfaced a real, pre-existing gap: `tests/test_phase7_postgres.py`
(collected by every phase gate's pytest run, since all of them run the whole `tests/` directory)
needs a real Elasticsearch reachable from the host-side pytest process, which no gate script before
Phase 7 published a port for. `compose.e2e.yaml` now publishes `elasticsearch` on
`NEWSINTEL_TEST_ELASTICSEARCH_PORT` (default `0`, letting the kernel choose a free port) so every
existing gate script keeps working unmodified, and `test-phase6.sh` additionally exports
`NEWSINTEL_ELASTICSEARCH_URL` for its pytest run, the same way it already overrides the database
URL. After that fix, `test-phase6.sh` and one more `test-phase7.sh` run both passed cleanly.
`test-phase3.sh`, `test-phase4.sh`, and `test-phase5.sh` have the same latent gap and are not fixed
here (out of this phase's scope); each needs the identical three-line change `test-phase6.sh` got.

Validate Compose with `docker compose config --quiet`. Generate a current OpenAPI document with `cd backend && uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))"`.

## Feed polling configuration

New sources poll every 30 minutes and accept a minimum interval of 5 minutes. The scheduler checks for due work every 10 seconds. Configure outbound requests with `NEWSINTEL_FEED_USER_AGENT`, `NEWSINTEL_FEED_TIMEOUT_SECONDS`, `NEWSINTEL_FEED_MAX_RESPONSE_BYTES`, `NEWSINTEL_FEED_REDIRECT_LIMIT`, and `NEWSINTEL_FEED_HOST_MIN_INTERVAL_SECONDS`. Private, loopback, link-local, credentialed, and non-HTTP URLs are rejected. `NEWSINTEL_FEED_TEST_ALLOWED_HOSTS` is only for narrowly scoped fixture hosts in test environments; leave it empty in normal deployments.

After updating from Phase 1, apply `docker compose run --rm api alembic upgrade head`, then start or recreate both `worker` and `scheduler`. A source can be polled immediately from Sources. Successful, unchanged (`304`), and failed cycles appear in its fetch history. Retiring a source stops polling and hides it from active management while preserving articles and provenance.

Feeds in `full_text` mode enqueue article fetching and extraction. `full_text_html` also retains the source HTML in the `article-data` Docker volume; extracted text and its current/previous hashes remain in PostgreSQL. Recreating containers preserves this volume. Include `article-data`, PostgreSQL, and configuration in backups; Elasticsearch remains rebuildable.

If polling stalls, check `docker compose logs scheduler worker api`, confirm Redis and PostgreSQL health, and inspect the source's fetch history. Security rejections usually mean DNS resolved to a non-public address. Timeouts, `429`, and server errors retry up to three times; the next normal cycle remains scheduled after failure. Elasticsearch may be stopped while ingesting and browsing RSS entries.

If article processing stalls, confirm the worker command includes `app.jobs.articles`, inspect Jobs for queued/retrying/failed stages, and retry terminal failures there. Configure article downloads with `NEWSINTEL_ARTICLE_TIMEOUT_SECONDS`, `NEWSINTEL_ARTICLE_MAX_RESPONSE_BYTES`, `NEWSINTEL_ARTICLE_REDIRECT_LIMIT`, and `NEWSINTEL_ARTICLE_HOST_MIN_INTERVAL_SECONDS`.

## Search index operations

After applying the Phase 4 migration and recreating `worker` and `scheduler`, initialize search with
`docker compose run --rm worker python -m app.cli rebuild-search`. The command creates a uniquely
named schema-versioned index, registers it as a delivery target, scans PostgreSQL in bounded
keyset batches, and prints its rebuild UUID. Check progress with
`docker compose run --rm worker python -m app.cli search-index-status`; resume an interrupted or
catching-up rebuild with `docker compose run --rm worker python -m app.cli resume-search-rebuild REBUILD_ID`.

Cutover waits for the scan, source refreshes, and replacement-index deliveries to finish, then
refreshes the replacement and atomically switches `articles-current`. If a process stops during
cutover, resume the same UUID; recovery checks the actual alias before acknowledging completion.
The prior index is retained. Inspect Elasticsearch indices and the alias before deleting a retained
index manually. The Jobs page reports indexing backlog and bounded failures and provides a
CSRF-protected per-article retry. Indexing requests default to at most 100 documents and 5 MiB;
oversized documents remain visible failures. This milestone records bounded behavior and does not
certify a five-million-article deployment.

Clean abandoned temporary article objects with
`docker compose run --rm worker python -m app.cli cleanup-article-storage --dry-run`.
Dry-run is the default and reports a bounded batch without changing storage. After reviewing
the counts, add `--apply`; use `--batch-size N` to cap each database batch between 1 and 5000
objects. Add `--complete-sweep` to stream successive bounded batches through the directory in one
invocation. Malformed keys, symbolic links, stat failures, and deletion failures are reported per
entry. Publication, reference transfer, and deletion use PostgreSQL transaction advisory locks.
The command preserves retained HTML, every object referenced by a processing job, and files
newer than `NEWSINTEL_ARTICLE_TEMPORARY_HTML_HOURS` (24 hours by default). Run cleanup only
against the same `article-data` volume used by workers.

For recovery, inspect `docker compose logs scheduler worker api`, then recreate only the worker
with `docker compose up -d --force-recreate worker`; PostgreSQL leases make abandoned work
claimable again. Retry terminal article failures from Jobs. Confirm storage with
`docker compose exec worker ls -la /var/lib/newsintel/articles` and inspect job/content rows in
PostgreSQL before applying cleanup. Re-run migrations with
`docker compose run --rm api alembic upgrade head`; Alembic uses `NEWSINTEL_DATABASE_URL`, including
documented host and Compose credentials.

## NLP operations

Apply the Phase 5 migration before starting the new worker:

```sh
docker compose run --rm api alembic upgrade head
docker compose up -d --build api scheduler nlp-worker
```

The base image provides local Lingua language detection, YAKE keywords and keyphrases, and the
checked-in ISO country lexicon without API keys or runtime downloads. Named-entity recognition is
disabled visibly in this configuration. To install and enable the pinned CPU-oriented spaCy model,
build and run the overlay:

```sh
docker compose -f compose.yaml -f compose.ner.yaml up -d --build nlp-worker
```

NLP input is limited to 1,000,000 characters and fails visibly instead of truncating. Transient
processor failures retry up to five attempts, beginning at 30 seconds and capped at 15 minutes.
Keyword output and annotation API responses are bounded to 50 records, and archive reprocessing
scans PostgreSQL in keyset batches of 100.

Preview the initial archive backfill, then apply the reviewed selection:

```sh
docker compose run --rm nlp-worker python -m app.cli reprocess-nlp --all
docker compose run --rm nlp-worker python -m app.cli reprocess-nlp --all --apply
docker compose run --rm nlp-worker python -m app.cli nlp-status
```

Limit a run with `--processors language keywords countries entities`, repeated `--article-id`
arguments, or a paired `--from-date` and `--to-date` UTC range. The apply command prints a run UUID. Resume
an interrupted scan with
`docker compose run --rm nlp-worker python -m app.cli resume-nlp-reprocessing RUN_ID`. Updating the
global English stop words affects new processing immediately; use an explicit keywords
reprocessing run to update existing annotations.

After the initial backfill, create and cut over the schema-version-2 search index with
`docker compose run --rm worker python -m app.cli rebuild-search`. Existing version-1 deliveries
remain version-1 documents while the replacement is built. Ordinary searches remain available;
annotation filters return a search-upgrade-required response until the active index supports them.
Use `search-index-status` and `resume-search-rebuild REBUILD_ID` as described above.

For recovery, inspect `docker compose logs scheduler nlp-worker api`. PostgreSQL leases recover
abandoned claims and prevent expired workers from publishing. The Jobs page shows bounded failures
and retries, including disabled, unsupported-language, and configuration outcomes. Fix a missing
model by rebuilding the optional image, then retry the failed article or start a selected
reprocessing run. Elasticsearch can remain stopped during ingestion, extraction, and NLP; restart
it and resume or create a search rebuild to catch indexing up.

To roll Phase 5 back, first stop `scheduler`, `nlp-worker`, and `api`. Restore or rebuild a
schema-version-1 search index before serving the older application, then run
`docker compose run --rm api alembic downgrade 0004`. This removes only Phase 5 annotations,
processor state, NLP jobs, stop-word revisions, and reprocessing runs; canonical articles,
extracted content, source provenance, and retained objects remain. Start the older application only
after its database and search schemas agree.

The 2026-09-15 isolated acceptance measurement processed a 58,000-character keyword input in
0.328 seconds with 65.9 MiB maximum process RSS; the running NLP worker reported 112.4 MiB. The
representative due-job query used `ix_nlp_jobs_due`, and current article entity retrieval used
`ix_article_nlp_entities_current`. These observations establish bounded behavior for the tested
fixture, not production capacity certification.

Entity disambiguation, analytics,
multilingual annotation models, broad operational reprocessing UI, complete structured operational
logging, SSE updates, production backup restoration, and five-million-article performance
certification remain later milestones. PostgreSQL remains authoritative and the
ingestion/extraction/NLP path continues while Elasticsearch is absent.

## Investigations

Apply the Phase 6 migration, then recreate the API and frontend:

```sh
docker compose run --rm api alembic upgrade head
docker compose up -d --build api frontend
```

Search keeps the whole investigation in the URL: query, sources, source/story/mentioned
countries, language, entities, entity types, keywords, date range, content and processing state,
sort order, and timeline interval. Bookmarks, reloads, and browser back/forward reproduce it.
Older links that use `country=` for source country keep working.

The timeline above results charts the same filtered query. It picks the finest hour, day, week,
month, or year bucket that stays within 200 buckets; a manual interval that would exceed that
limit is refused with a prompt to choose a larger one. Drag across bars to apply that span as the
date range; a selection only narrows an existing range. Date ranges are whole UTC days with an
exclusive end, so an hourly selection widens to the days it touches. Clicking a result's source, source country, or story
country, or an entity, keyword, or country in article detail, narrows that one filter to the clicked
value and keeps the rest of the investigation.

Saved searches store a named, versioned copy of that state in PostgreSQL per user; names are
unique per user ignoring case. Open one from Saved Searches to restore its exact URL, or rename
or delete it there. A stored state that no longer validates after an upgrade stays listed with the
reason and can be deleted, but is not opened. Saved-search mutations require the CSRF token like
other changes.

The timeline requires the active search index; annotation filters on the timeline need the
schema-version-2 index described above. To roll Phase 6 back, stop `api`, then run
`docker compose run --rm api alembic downgrade 0006`. This drops only saved searches; articles,
annotations, and search indices are unaffected.

## Relationships

Apply the Phase 7 migration, then recreate the API, workers, and frontend:

```sh
docker compose run --rm api alembic upgrade head
docker compose up -d --build api worker nlp-worker scheduler frontend
```

Clustering is PostgreSQL-only rule-based grouping (`rule-1`): whenever the entities NLP processor
succeeds for an article, it becomes a candidate against articles published within 48 hours whose
normalized title hash matches exactly, or that share at least 2 current ORG/PERSON/GPE/EVENT
entities. Candidates score `0.5 × title-token Jaccard + 0.35 × shared-entity Jaccard + 0.15 × time
proximity`; a candidate at or above `0.45` joins that article's story. An article from the same feed
as a candidate is never treated as separate related coverage of itself. Creating, joining, and
merging clusters (the smaller cluster merges into the larger; ties keep the older cluster) serialize
behind one PostgreSQL advisory lock, and a cluster that drops below two members is dissolved. Re-run
the algorithm over a selection with `recluster`:

```sh
docker compose run --rm nlp-worker python -m app.cli recluster --all
docker compose run --rm nlp-worker python -m app.cli recluster --all --apply
```

Dry-run (the default) reports the candidate count; `--apply` queues clustering jobs. `--article-id`
(repeatable) or a paired `--from-date`/`--to-date` UTC range narrow the selection instead of `--all`,
and `--batch-size` caps each scan batch between 1 and 500. `GET /api/v1/clustering/status` reports
queued/running/retrying/failed job counts and cluster/clustered-article totals; the Clustering
failures list supports a CSRF-protected per-job retry.

Clustering assignment is intentionally serial, and that has a throughput consequence worth stating
plainly: creating, joining, and merging clusters across the whole archive serializes behind the one
PostgreSQL transaction advisory lock above (id `728341906` in `app/clustering/engine.py`), and the
clustering job shares its worker with NLP processing — `compose.yaml` runs `app.jobs.nlp` and
`app.jobs.clustering` on the same `nlp-worker` process at `--processes 1 --threads 1`. A slow
clustering pass can therefore delay NLP throughput on that same worker, and a full `recluster --all`
over a large archive runs strictly one cluster assignment at a time rather than in parallel.

Search results, the story cluster endpoint, article detail's related articles, and the entity
co-occurrence graph all read from the schema-version-3 search index, which `rebuild-search` now
always builds:

```sh
docker compose run --rm worker python -m app.cli rebuild-search
```

Schema-version-3 documents carry each article's `story_cluster_id` and its cluster's source count,
so search can show "Also reported by N other sources", filter to one story with the
`story_cluster_id` annotation, and keep the schema-version-2 entity/keyword/language filters from
Phase 6 working. `GET /api/v1/graph/entities` aggregates entity co-occurrence over the current
search criteria into a bounded graph: at most 50 nodes (`nodes`, clamped server-side regardless of
the requested value) and at most 150 edges, with an edge dropped below `min_edge_weight` (default 2
co-occurring articles); a response that hit either bound is flagged `truncated` so the UI can say
so. The graph needs at least the schema-version-2 index; on an older index it returns the same
`search_upgrade_required` 409 as annotation search filters.

To roll Phase 7 back, stop `api`, `worker`, `nlp-worker`, and `scheduler`, then run `docker compose
run --rm api alembic downgrade 0007`. This drops only `story_clusters`, `story_cluster_members`,
`article_cluster_state`, and `cluster_jobs`; canonical articles, annotations, and the search index
are unaffected. Rebuild search again after downgrading, since existing schema-version-3 documents
keep stale cluster fields until the next rebuild; serve an older application only once search
matches its schema.

**Acceptance gate architecture note:** the Phase 5 and 6 gates only ever smoke-test the NER-enabled
image in isolation, then run every real workflow — including all annotation assertions — against
the default NER-disabled stack, because nothing in those phases needs real entities to pass.
`infra/test-phase7.sh` cannot follow that pattern: with NER disabled, indexed documents carry zero
entities and the entity graph would have zero nodes to render for any query, which the acceptance
plan requires to be non-empty. So this gate builds `compose.ner.yaml`'s `nlp-worker` and keeps NER
enabled for the entire run instead of tearing the NER-enabled stack down after a smoke test — a
deliberate, one-off deviation from the Phase 5/6 precedent, not a new default for future gates.

## Deployment

Deploy behind an existing HTTPS reverse proxy that forwards to port 8080. Set `NEWSINTEL_ENVIRONMENT=production` and `NEWSINTEL_SESSION_COOKIE_SECURE=true`. Restrict proxy access to the host network and configure the public hostname in `NEWSINTEL_ALLOWED_HOSTS` using Pydantic's JSON-list syntax. Back up the PostgreSQL volume and configuration; Elasticsearch remains derived and rebuildable.
