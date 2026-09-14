# Repository guidance

## Project orientation

- NewsIntel is a self-hosted news archive with a Python backend and Vue frontend.
- The backend lives in `backend/` and uses FastAPI, SQLAlchemy, Alembic, Dramatiq, and Redis.
- The frontend lives in `frontend/` and uses Vue, TypeScript, and TanStack Query.
- `compose.yaml` defines the application, worker, scheduler, PostgreSQL, Redis, and Elasticsearch services.
- `compose.dev.yaml` and `compose.e2e.yaml` provide development and test overrides.
- PostgreSQL integration tests live with the backend tests and require an explicitly configured test database.
- Read [the product specification](spec.md) for target requirements.
- Read [the README](README.md) for verified setup and operating instructions.
- Read [the Phase 3 stabilization plan](docs/superpowers/plans/2026-09-13-phase3-stabilization.md) for historical findings and the current milestone.
- Treat the specification as the target, not proof that a feature exists.
- Verify implementation in code and tests before describing it as complete.
- Preserve historical findings in plans when recording newer evidence.

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
- Follow existing Vue composition and TanStack Query patterns under `frontend/src/`.
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
- Run migrations explicitly with `docker compose run --rm api alembic upgrade head`.
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
- Validate Compose with `docker compose config --quiet`.
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
- Never run `docker compose down -v` against the user's normal Compose project.
- Never reset, truncate, or migrate the user's archive as part of a test.
- Never remove the user's PostgreSQL or article-storage volumes.
- Prefer dry-run maintenance commands before apply mode.
- Resolve cleanup candidates against current database references before deleting objects.
- Capture failure logs before tearing down an isolated acceptance environment.
