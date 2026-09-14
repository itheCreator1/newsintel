# Phase 5: Versioned NLP and Searchable Annotations

## Goal

Build an independently recoverable NLP pipeline that enriches archived articles with detected
language, keywords and keyphrases, optional English named entities, and conservative country
annotations. PostgreSQL owns every annotation and processing outcome. Article detail, Search,
Settings, and Jobs expose the results. The base installation works without spaCy, external AI
services, or API keys.

This plan follows Phase 4 at commit `6b47d13`. Preserve the Phase 3 and Phase 4 evidence in
`2026-09-13-phase3-stabilization.md`; record newer evidence here only from executed commands.

## 1. Canonical annotations and durable processing intent

- [ ] Add `backend/app/nlp/` with processor interfaces, persistence, scheduling, and execution
  separated.
- [ ] Add an Alembic migration for normalized entities and keywords, article associations,
  country annotations, processor runs, durable jobs, stop-word revisions, and resumable
  reprocessing progress.
- [ ] Record processor and algorithm/model versions, configuration and input fingerprints,
  timestamps, counts, relevance, and occurrence references.
- [ ] Use extensible text entity types and normalize identities by language, type, and normalized
  text. Do not merge aliases or infer real-world identity.
- [ ] Maintain independent requested/completed generations, attempts, retry times, ownership,
  leases, and bounded errors for each article and processor.
- [ ] Request NLP transactionally when title, RSS input, or extracted text changes. Unchanged input
  must not create redundant work.
- [ ] Mark annotations for superseded inputs stale while preserving provenance and excluding them
  from current search annotations.
- [ ] Ensure downgrade removes only Phase 5 data. Backfill must run through worker commands.

## 2. Deterministic input and local processors

- [ ] Build input from canonical title and extracted body, or title and distinct RSS descriptions
  in deterministic order. Store section/offset references tied to the input fingerprint.
- [ ] Fail visibly and recoverably above the one-million-character default; never truncate.
- [ ] Detect language locally with Lingua. Record its version and relative confidence. Return `und`
  below 50 alphabetic characters, 0.80 top confidence, or a 0.20 winner margin.
- [ ] Ship English processors first and record unsupported-language outcomes for other languages.
- [ ] Rank one-to-three-token keywords/keyphrases using YAKE plus normalized occurrence counts;
  retain 50 with deterministic tie-breaking and store raw score and relevance.
- [ ] Provide a versioned global English stop-word list shared by token and phrase processing.
- [ ] Support optional local `en_core_web_sm` NER with pinned compatible artifacts and no runtime
  download. Preserve model labels and map them to PERSON, ORG, GPE, COUNTRY, LOCATION, EVENT,
  PRODUCT, or OTHER. Disabled and missing-model states must be visible and independent.
- [ ] Use a checked-in, attributed, versioned ISO country lexicon with reviewed aliases. Match only
  explicit country names; exclude ambiguous standalone aliases such as “us” and “Georgia.”
- [ ] Store source country, mentioned countries, and inferred primary story country separately.
  Infer primary only when one resolved country occurs in both title and body/description.

## 3. Independent execution and resumable reprocessing

- [ ] Add a dedicated Dramatiq NLP queue and worker service with one process/thread by default.
- [ ] Dispatch with PostgreSQL leases and recover publication failures and post-claim crashes.
- [ ] Read input/generation from a consistent snapshot, process outside transactions, renew leases,
  and publish only when ownership, generation, processor version, and configuration still match.
- [ ] Atomically replace only the successful processor output. Empty success clears previous output;
  one processor failure must not erase another result. Request search indexing in the same
  publication/invalidation transaction.
- [ ] Use five attempts with exponential backoff from 30 seconds through 15 minutes. Keep permanent
  input/configuration errors manually retryable.
- [ ] Add `python -m app.cli reprocess-nlp`, `nlp-status`, and
  `resume-nlp-reprocessing RUN_ID`.
- [ ] Require article IDs, a UTC range, or explicit `--all`; default dry-run and require `--apply`.
  Persist selection/cursor and scan keyset batches of 100.
- [ ] Apply stop-word changes to new work immediately and to archives only through explicit
  resumable reprocessing.

## 4. Search and authenticated API contracts

- [ ] Add bounded article annotations, NLP status/failures, retry/reprocess, revision-checked stop
  words, and cursor-paginated entity/keyword lookup endpoints. Preserve auth and CSRF.
- [ ] Add language, entity ID/type, keyword ID, primary-country, and mentioned-country filters plus
  `entity:`, `keyword:`, `language:`, `story_country:`, and `mentioned_country:` syntax.
- [ ] Resolve normalized names exactly, AND categories/clauses, OR GUI selections, and use nested
  entity matching so ID/type constraints target the same association.
- [ ] Add annotation text to full-text search while preserving title boosts and safe highlights.
- [ ] Introduce schema version 2. Serialize per target version, keep v1 delivery/rebuild/search
  operational, require upgrade only for annotation-dependent search, and bind PIT cursors to their
  original schema/query behavior.
- [ ] Regenerate `frontend/openapi.json` and `frontend/src/types.generated.ts` together.

## 5. Article detail, Search, Settings, and Jobs

- [ ] Show language, keywords, entities, country meanings, provenance, and stale/unsupported/
  disabled/failure states in article detail.
- [ ] Add bounded annotation filter pickers and syntax help. Commit criteria to the URL, keep
  pagination transient, reset pagination on criteria change, and ignore late responses.
- [ ] Add Settings for stop words and processor/model capabilities, explaining that existing
  annotations require reprocessing.
- [ ] Show NLP backlog/failures/attempts/retries/reprocessing in Jobs and refresh affected queries
  after mutations.
- [ ] Preserve search state into detail and annotation refinements. Escape extracted text and all
  annotations.

## 6. Acceptance and operations

- [ ] Add executable `infra/test-phase5.sh` with a unique Compose project/volumes, configurable
  ports, bounded waits, failure artifacts, and invocation-scoped teardown.
- [ ] Exercise PostgreSQL, Redis, Elasticsearch, scheduler, NLP worker, RSS/extracted input,
  duplicate delivery, expired claims, queue failures, processor failures, input races,
  reprocessing/resume, language edge cases, empty/oversized input, ambiguous countries, base and
  optional NER, Elasticsearch outage/catch-up, v1/v2 rebuild/cutover, and browser workflows.
- [ ] Prove migration upgrade/downgrade/upgrade preserves canonical archive data.
- [ ] Run backend pytest, Ruff, mypy, frontend tests/typecheck/build, Compose validation, Phase 3
  and Phase 4 gates, and Phase 5 acceptance. Record pass/fail/skip counts.
- [ ] Record representative query plans, batch/bounds, and NLP memory/processing measurements.
  Document model installation, migration, initial backfill, search rebuild, status, recovery, and
  rollback order in README.

## Completion boundary

Phase 5 is complete when feed → PostgreSQL → NLP → Elasticsearch → API → article detail/Search
works in base and optional-NER configurations with tested recovery. Saved investigations,
timelines, clustering, entity disambiguation, analytics, multilingual annotation models, broad
operational reprocessing UI, and five-million-article certification remain later milestones.

## Executed evidence

- 2026-09-14 baseline on `6b47d13`: backend `pytest -q` 51 passed, 20 skipped because the isolated
  PostgreSQL fixture stack was not enabled; Ruff passed; strict mypy passed for 56 files. Frontend
  Vitest passed 16 tests, typecheck passed, production build passed, and Compose configuration
  validated.
