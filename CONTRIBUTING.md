# Contributing to NewsIntel

This guide covers running NewsIntel locally, the test loops, the generated API contract, database migrations and CI. For what the system is and how it works, start with the [README](README.md).

Everything runs in containers. You need Docker with Compose v2, Git and ordinary POSIX shell utilities; Python, Node, browsers, databases and test tools all come from the images.

## Contents

1. [Running it locally](#1-running-it-locally)
2. [Repository layout](#2-repository-layout)
3. [The test loops](#3-the-test-loops)
4. [The OpenAPI and TypeScript contract](#4-the-openapi-and-typescript-contract)
5. [Database migrations](#5-database-migrations)
6. [CI](#6-ci)
7. [Test-harness reference](#7-test-harness-reference)
8. [Code conventions](#8-code-conventions)

## 1. Running it locally

```sh
cp .env.example .env    # then set POSTGRES_PASSWORD, NEWSINTEL_SECRET_KEY (32+ characters) and
                        # NEWSINTEL_ADMIN_USERNAME / NEWSINTEL_ADMIN_PASSWORD (12+ characters)
alias dc='docker compose --env-file .env -f docker/compose.yaml -f docker/compose.dev.yaml'

dc up -d --build        # the setup service applies migrations and creates that account
dc run --rm api python -m app.cli rebuild-search
```

The app is then on `http://127.0.0.1:8080` (`NEWSINTEL_PORT`). The API is served under `/api/v1`, and nginx in the frontend image proxies to it.

The one-shot `setup` service runs `alembic upgrade head` and `bootstrap-admin` on every `up`, and the application services wait for it. It creates the account only while no user exists, so later runs leave existing users alone. Because it migrates on every `up`, back up a database you care about before `dc up -d --build` on a branch with a new migration.

`docker/compose.dev.yaml` mounts `backend/app` read-only into the `api` container and runs uvicorn with `--reload`, so backend route changes show up without a rebuild. The workers and scheduler do not reload: `dc restart worker nlp-worker scheduler` after changing job code, and rebuild the frontend image (`dc up -d --build frontend`) after changing the UI.

**Entity recognition is off by default.** spaCy is not in the default image (`NEWSINTEL_NLP_NER_ENABLED=false`), so no entities are extracted: the Graph and entity dossiers stay empty, and story clustering and event association work without their entity signal. Add `-f docker/compose.ner.yaml` to the alias to build a backend image with spaCy, `en_core_web_sm` and `el_core_news_sm` and turn NER on for `api` and `nlp-worker`.

**Greek entities are off by default too**, even with the NER image. Turn them on with the Greek entities switch in Settings; no restart is needed. The switch stays disabled, with the reason shown, until the NER image is running. Greek names are stored without accents or the commonest case endings, so "Τσίπρας", "ΤΣΙΠΡΑΣ" and "Τσίπρα" are one entity; Greek and English spellings ("Τσίπρας", "Tsipras") stay separate. Back up first: turning the switch on also queues the Greek articles already in the archive (untick the checkbox under it to skip that), and the scheduler feeds them to the NLP worker in batches of 100. Later you can reprocess them by hand with `dc run --rm nlp-worker python -m app.cli reprocess-nlp --processors entities --language el --all --apply`. Turning the switch off keeps the Greek entities already found. Greek articles had no entities before, so they lose nothing while they wait in the queue. Keywords and countries are still English-only.

Other useful commands, all run as `dc run --rm api python -m app.cli <command>`:

| Command | What it does |
| --- | --- |
| `create-user <name>`, `reset-password <name>` | Manage logins; passwords need at least 12 characters. Resetting revokes the user's sessions. |
| `bootstrap-admin` | Create the first account from `NEWSINTEL_ADMIN_USERNAME`/`NEWSINTEL_ADMIN_PASSWORD` while no user exists. The `setup` service runs it on every `up`. |
| `rebuild-search`, `resume-search-rebuild <id>`, `search-index-status` | Build a new search index beside the live one and move the alias (see README §6). |
| `reprocess-nlp`, `resume-nlp-reprocessing <id>`, `nlp-status` | Rerun NLP processors over a selection (`--article-id`, `--from-date`/`--to-date`, or `--all`, optionally narrowed with `--language el`; dry run unless `--apply`). |
| `recluster` | Recluster a selection of articles, chosen the same way. |
| `authority seed-countries` | Tie every other spelling of a country ("USA", "America", "U.K.") to the country's entity in the authority file. Safe to rerun; spellings that already hold articles are reported, not merged. Dry run unless `--apply`. |
| `authority export [--file PATH] [--language el]` | Write the authority file as JSON (stdout without `--file`): roots with their preferred name, status, note and variants, plus pairs marked different. Entities travel by language, type and normalized name, never by id. |
| `authority import --file PATH` | Apply such a file, here or after a rebuild: missing names are created, so the choices are in place before NLP runs again. A name already tied to another root is reported as a conflict and left alone. Dry run unless `--apply`. |
| `cleanup-article-storage` | Delete expired temporary article HTML (dry run unless `--apply`). |

## 2. Repository layout

| Path | Contents |
| --- | --- |
| `backend/app/<domain>/` | One module per domain (`feeds`, `articles`, `nlp`, `search`, `clustering`, `events`, …), each with its models, service layer and routes. |
| `backend/app/jobs/` | Dramatiq actors. `backend/app/scheduler.py` claims due job rows and sends them. |
| `backend/migrations/versions/` | Alembic migrations. |
| `backend/tests/` | pytest suite; `classification.toml` marks each module unit or integration and names the services it needs. |
| `frontend/src/` | Next.js App Router UI (static export). `src/lib/types.generated.ts` is generated, never hand-edited. |
| `frontend/openapi.json` | The checked-in API contract. |
| `frontend/e2e/` | Playwright browser workflows. |
| `docker/` | Compose files: `compose.yaml` (production stack), `compose.dev.yaml`, `compose.ner.yaml`, and the `compose.test*.yaml`/`compose.e2e*.yaml`/`compose.quick.yaml` test stacks. |
| `infra/` | Test scripts and the shared helpers in `lib.sh`. |

## 3. The test loops

Pick the smallest loop that covers your change, and finish with the full gate before merging.

When a loop fails, fix it in the smallest loop that reproduces the failure, not by rerunning the full gate: the failing integration test through `test-integration.sh`, the failing browser group through `test-e2e.sh`, plus `test-quick.sh`. Rerun the full gate once, when those pass. Every failure is in the run's artifacts (§7), so a failed full gate already names the loop to rerun.

| Loop | Command | Covers |
| --- | --- | --- |
| Quick | `./infra/test-quick.sh` | ruff, mypy, unit pytest, the OpenAPI/TypeScript contract, vitest, frontend typecheck. No service containers. CI runs this. |
| Integration | `./infra/test-integration.sh <pytest-node-id>...` | Exactly the named integration modules, with only the services they need. |
| Browser | `./infra/test-e2e.sh [--stop-after <spec>] <search\|investigations\|monitors\|graph>` | One Playwright group against a fresh Compose stack. `--stop-after` ends the group once that spec passes. |
| Full gate | `./infra/test-docker.sh` | Everything: all of the above, migrations, the single-head check, the real-model NER tests (English and Greek spaCy models), `npm run build`, the backup/restore rehearsal and all four browser groups. |

`./infra/test-docker.sh` is the only run that counts as a full regression run. It rejects skipped tests and stale generated contracts, and it is the sole source of the timing and memory baselines in §7. Run it before merging: on most pull requests CI runs it only after the merge (see §6).

The quick loop starts nothing but the backend and frontend test images (`--no-deps` throughout, `network_mode: none`). It does not run integration tests, migrations, the restore rehearsal, `npm run build` or any browser group.

The integration loop resolves its selection before starting anything: an unknown or empty selection, or a unit module (those belong in the quick loop), fails before any container starts. Node ids may be given relative to the repository root (`backend/tests/test_x.py::test_y`) or to `backend/`.

Each browser group uses a fresh database and an isolated Compose network, and its containers and volumes are always removed.

The specs in a group run in order and depend on what earlier specs seeded, so a single spec cannot run on its own. `--stop-after "<spec>"` runs the group up to and including that spec and stops there, which saves the specs after it. `<spec>` is one of the group's `e2e "..."` names in `infra/test-e2e.sh`; an unknown name fails before anything starts and lists the group's specs. It is for checking a fix, not for passing a group.

## 4. The OpenAPI and TypeScript contract

The frontend's API types come from `frontend/openapi.json`, which is generated from the FastAPI app. Both the quick loop and the full gate regenerate the spec and the types and fail if either differs from what is checked in (`frontend/openapi.json is stale` or `frontend/src/lib/types.generated.ts is stale`).

After changing a route, a request or response schema, or anything else that shows up in the API, regenerate both and commit them with your change:

```sh
alias dt='docker compose -f docker/compose.test.yaml -f docker/compose.quick.yaml'
dt build backend-test frontend-test

dt run --rm --no-deps -T backend-test \
  python -c 'import json; from app.main import create_app; print(json.dumps(create_app().openapi(), indent=2))' \
  > frontend/openapi.json

dt run --rm --no-deps -v "$PWD/frontend:/host" frontend-test \
  sh -c 'cp /host/openapi.json openapi.json && npm run generate:api && cp src/lib/types.generated.ts /host/src/lib/'
```

Rebuild the images first: they copy the source at build time, so a stale image produces a stale spec. `-T` keeps a TTY from adding carriage returns to the JSON.

## 5. Database migrations

PostgreSQL is the source of truth (README, P1), so every schema change is an Alembic migration in `backend/migrations/versions/`.

- **Naming.** Files are `NNNN_short_description.py` with `revision = "NNNN"` and `down_revision` set to the previous number. Take the next number after the highest existing one.
- **One head.** The full gate and every browser group check that the migrations have exactly one head (`alembic.single-head`). If your branch and `main` both added a migration, renumber yours after merging `main`.
- **Write it by hand.** Models live in each domain module; compare your migration against them rather than trusting autogenerate.
- **Apply it locally** with `dc run --rm api alembic upgrade head`. The full gate and the browser groups run `alembic upgrade head` against an empty database, and the restore rehearsal runs it against a dump.
- **Existing data.** Operators upgrade in place and restore older dumps before upgrading (README §6), so a migration must work on a populated database, not only an empty one.

## 6. CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and every push to `main`:

- **Quick gate**: `./infra/test-quick.sh`, inside the repository's own test images. Its diagnostics are uploaded as the `test-quick-artifacts` artifact on every run, so a failure can be read without rerunning it.
- **Frontend build**: `npm ci && npm run build` in `frontend/`, because the quick loop skips the static export.
- **Workflow lint**: `actionlint` over the workflow files.

A second workflow (`.github/workflows/full-gate.yml`) runs `./infra/test-docker.sh` after every merge to `main`, nightly, on demand from the Actions tab, and on pull requests that change `infra/`, `docker/`, the Dockerfiles, `.github/actions/` or the workflow itself. Its diagnostics are uploaded as an artifact on every run.

Run on demand ("Run workflow", on any branch), it can instead run one targeted loop, for checking a fix where Docker is not available:

| `run` | `target` | Runs |
| --- | --- | --- |
| `full` (default) | ignored | `./infra/test-docker.sh` |
| `e2e` | `search`, `investigations`, `monitors` or `graph` | `./infra/test-e2e.sh <target>`, with `--stop-after` when `stop_after` is set |
| `integration` | pytest node ids, separated by spaces | `./infra/test-integration.sh <target>` |

A third workflow (`.github/workflows/targeted.yml`) runs the same loops from a push, for a session that can push but can neither run Docker nor start a workflow by hand. On a push to any branch but `main`, it reads a trailer on the head commit:

```text
ci-run: e2e graph
ci-stop-after: map workflow
```

`ci-run:` takes the same `run` and `target` as the table above (`ci-run: integration backend/tests/test_x.py`, `ci-run: full`); `ci-stop-after:` is optional and only for `e2e`. Put the trailer on the commit that carries the fix. A push without it skips the job. A newer push to the same branch cancels the run in progress, so wait for it before pushing again.

A targeted run never stands in for the full gate before a merge.

Neither workflow starts for a pull request or a push that changes nothing but Markdown files, `assets/` or `LICENSE` (`paths-ignore`); a change that touches anything else as well runs as usual. A newer push to the same pull request cancels the run in progress. On most pull requests CI still runs no integration tests, migrations, restore rehearsal or browser groups, so `./infra/test-docker.sh` is still the check to run before merging.

## 7. Test-harness reference

### Browser lanes in the full gate

By default the four browser groups run in two concurrent lanes that start as soon as the images are built, alongside the backend and frontend stages. Every stage and every spec still runs and still has to pass; only wall-clock time differs (about 7 minutes against 17 on the 12-core/15 GB reference machine, with a sampled memory peak of about 6 GiB).

- `NEWSINTEL_TEST_E2E_JOBS` (1, 2 or 4; default 2) sets the number of lanes. The groups, their specs and their order within a lane are unchanged: each is still its own Compose project with a fresh database and no published ports. In a concurrent run each group's output goes to `e2e-<group>.log` in the artifacts directory (a failed group's log is printed in full at the end), a failing group stops only its own lane, and an interrupt or a failure elsewhere in the gate stops every lane and removes its containers.
- `NEWSINTEL_TEST_E2E_OVERLAP` (0 or 1; default 1) decides whether the lanes start right after the image build or only after the backend and frontend stages. A failure in either half fails the gate. Under overlap the frontend unit tests run after the lanes have finished, because their one-second render waits timed out when they shared the machine with two browser stacks.
- `NEWSINTEL_TEST_E2E_JOBS=1 NEWSINTEL_TEST_E2E_OVERLAP=0` restores the fully sequential gate, for a smaller machine or to rule out contention when a run fails.

### Reusing images in a browser group

`./infra/test-e2e.sh --reuse-images <manifest> <group>` skips the group's own image build and uses the images recorded in `<manifest>`, an `image-manifest.env` written by a `test-docker.sh` run (which dispatches its four groups this way). The manifest names each image tag and ID plus the git revision and a working-tree hash the images were built from. Before using them the script re-checks all of it: the revision must equal `HEAD`, the tree hash (from `git status --porcelain` and `git diff HEAD`, so uncommitted changes count) must still match, and each image ID must still match `docker image inspect`. Any mismatch exits 2 before a container starts, naming the check that failed.

### Artifacts and reports

Each script writes its diagnostics (Compose logs on failure, pytest output, Playwright traces and screenshots, and the reports below) to `docs/archive/testing/<date>-<label>-<n>/`, which is gitignored. `<label>` is `test`, `quick`, `integration`, `e2e-<group>` or `inventory`. Set `NEWSINTEL_TEST_ARTIFACTS` (or `NEWSINTEL_E2E_ARTIFACTS` for `test-e2e.sh`) to an absolute path to write elsewhere; it is bind-mounted into containers.

Every run, pass or fail, writes:

- `environment.txt`: git revision and dirty flag, Docker and Compose versions, `nproc`, total memory and a cache-state label (`NEWSINTEL_CACHE_STATE`).
- `timings.tsv` and `timings.txt`: each stage's start time, elapsed seconds and exit status, plus a slowest-first summary with a `TOTAL`. The total is the wall-clock span from the first stage's start to the last one's end, so concurrent browser groups and the aggregate `build.all`/`e2e.all` rows are not counted twice. A stage's status is known once the next stage starts or the run ends, so an interrupted run's last stage gets the interrupting signal's status (130 for `kill -INT`).
- `memory.tsv` and `memory.txt`: `docker stats --no-stream` sampled every `NEWSINTEL_MEM_SAMPLE_SECONDS` (default 10) against the run's own Compose project, with per-container and aggregate sampled peaks. The interval can miss short spikes, and the values are per-container cgroup memory, not the host's. BuildKit runs outside the Compose project, so build memory is not sampled.

`test-docker.sh` also writes `images.tsv` (every built image's ID) and `image-manifest.env`.

### Test inventory

`./infra/test-inventory.sh` is read-only and starts only the backend and frontend test images. It lists what each gate selects: `inventory/backend-full.txt` and `backend-quick.txt` (pytest `--collect-only`), `inventory/frontend-tests.txt` (`npx vitest list`), and `inventory/e2e-<group>-{specs,invocations}.txt` per browser group. Compare two inventories to confirm a change to the test scripts did not add, drop or rescope a test.

### Classifying a new backend test module

`backend/tests/classify.py` selects the quick loop's unit modules and the integration loop's services from `backend/tests/classification.toml`, and a unit test (`test_classification.py`) fails if a `test_*.py` module is missing from it. Add an entry for every new module, marking which of `postgres`, `elasticsearch` and `fixture_server` its source actually needs. Integration modules skip unless `NEWSINTEL_RUN_POSTGRES_TESTS=1`, and the gates reject skipped tests, so a module in the wrong class fails loudly rather than passing silently.

## 8. Code conventions

- **Backend**: Python 3.12+, SQLAlchemy 2 and Pydantic, with `ruff` (line length 100) and `mypy --strict`. Dependencies are managed with `uv`; change `pyproject.toml` and regenerate `uv.lock` rather than editing the lock. spaCy and its model are installed only in the NER image (`docker/compose.ner.yaml`).
- **Domains own their code.** New behaviour goes in the domain module it belongs to, with its own models, service and routes (README, P5).
- **Nothing slow in a request** (README, P6). Work that fetches, annotates or indexes is a job row written in the same transaction as the data it depends on, picked up by the scheduler.
- **Frontend**: TypeScript with `tsc --noEmit`, vitest for unit tests and Playwright for browser workflows. Call the API through the generated types.
