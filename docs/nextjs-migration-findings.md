# Next.js migration — findings

Reference doc for the Vue → Next.js/React frontend rewrite. Read this before touching routing in `frontend-next/` — it records the decisions so they aren't re-litigated.

## Current stack (frontend/)

- Vue 3.5 + Vite 6 + vue-router 4 (`createWebHistory`) + `@tanstack/vue-query` 5.66 + Pinia 3 + `@vueuse/core` + `echarts` 6 (tree-shaken: `echarts/core`, `echarts/charts`, `echarts/components`, `echarts/renderers`).
- `npm`, `package-lock.json`. Scripts: `dev` (`vite --host 0.0.0.0`), `build` (`vue-tsc -b && vite build`), `generate:api` (`openapi-typescript openapi.json -o src/types.generated.ts`), `test` (`vitest run`), `typecheck` (`vue-tsc -b`), `e2e` (`playwright test`).
- 9 routes (`src/router.ts`): `/`, `/sources`, `/articles`, `/search`, `/clusters/:id`, `/graph`, `/saved-searches`, `/jobs`, `/settings`.
- `src/App.vue` is the whole shell: plain `ref<User|null>`, login form when signed out, `<aside>` nav + `<RouterView>` when signed in. No route guards — it's a single client-side gate.
- `src/api.ts` — one hand-written fetch client (`credentials: 'same-origin'`, CSRF-token dance via `/auth/csrf` before any mutation, `ApiError` with parsed FastAPI validation details). No Vue dependency.
- `src/investigation.ts` — pure URL-state logic (`stateFromQuery`/`queryFromState`/`refine`/`brushRange` etc.) shared by Search, Graph, and Cluster views. Only Vue-router-typed surface is the `LocationQueryRaw` parameter type.
- `src/components/EntityGraph.vue`, `TimelineChart.vue` — thin echarts wrappers using `@vueuse/core`'s `useResizeObserver`.

## Backend API contract (unaffected by this migration)

- All calls go through `/api/v1/...`, `credentials: 'same-origin'`.
- Auth: `GET /auth/me` (session probe), `GET /auth/csrf` → `{csrf_token}` fetched fresh before every mutating call, sent as `X-CSRF-Token`. Session is a cookie; no bearer tokens client-side.
- `ApiError` surfaces `response.status` and a parsed `detail` (string, FastAPI validation-list, or `{code, message}` object) — several views branch on `error.status`/`detail.code` (e.g. `409 restart_search`, `409 search_upgrade_required`, `422 timeline_too_fine`).

## Infra (must survive the migration unchanged in shape)

- `frontend/infra/nginx.conf`: `location /api/ { proxy_pass http://api:8000; ... }`, `location / { try_files $uri $uri/ /index.html; }`.
- `frontend/Dockerfile`: two-stage — `node:24-alpine` builds, `nginx:1.29-alpine` serves `dist/` (→ `out/` for Next) on port 8080.
- `docker/compose.yaml`: `frontend: build: ../frontend`, port `${NEWSINTEL_PORT:-8080}:8080`, healthcheck hits `/`.

## The routing decision (why `/clusters/:id` becomes `/clusters/?id=`)

Checked against the current Next.js static-export docs (v16.3.5):

1. **No supported dynamic route for arbitrary, build-unknown IDs under `output: 'export'`.** Both "dynamic routes without `generateStaticParams()`" and "dynamic routes with `dynamicParams: true`" are explicitly unsupported for static export. `generateStaticParams: () => []` builds but emits zero HTML for the route — any request to `/clusters/<id>` falls through nginx's `try_files` to `/index.html`, which is the **Overview page's own prerendered shell** in an App Router export, not a generic SPA shell. It hydrates as Overview, not ClusterView.
2. **Default static export emits `/route.html`, not `/route/index.html`.** The docs' own config comment: `// Optional: Change links /me -> /me/ and emit /me.html -> /me/index.html`. Without `trailingSlash: true`, `frontend/infra/nginx.conf`'s `try_files $uri $uri/ /index.html;` would serve the wrong page for every route except `/` (e.g. `/search` → no `/search` file, no `/search/` dir → falls back to `/index.html` = Overview). This breaks every deep link, every hard refresh, and the e2e suite's first `page.goto('/search?q=...')`.

**Decision:** `next.config.ts` sets `output: 'export', trailingSlash: true`. This makes `frontend/infra/nginx.conf` a **verbatim copy** — zero edits needed. `/clusters/:id` becomes `app/clusters/page.tsx`, reached as `/clusters/?id=<id>`, a static client-rendered page reading the id via `useSearchParams()`. The trailing slash keeps the literal substring `/clusters/` so `frontend/e2e/relationships.spec.ts:58,72` (`toHaveURL(/\/clusters\//)`) and `ArticlesView.vue`'s `from.startsWith('/clusters/')` check keep working unmodified. `useSearchParams()` requires `'use client'` + a `<Suspense>` boundary — affects `clusters/page.tsx`, `search/page.tsx`, `articles/page.tsx`.

## `toHref()` always appends the trailing slash itself — don't rely on next/link alone

`next/link`'s href resolution (`resolve-href.js` → `normalizePathTrailingSlash`) actively adds *or strips*
a trailing slash based on a build-time constant (`process.env.__NEXT_TRAILING_SLASH`) that `next build`/
`next dev` inject from `trailingSlash` in `next.config.ts`. It **overrides** whatever the `href` prop
already contains — confirmed empirically: a manually-slashed href rendered through `<Link>` came back
un-slashed in a bare Vitest run, because that constant is never set outside Next's own build pipeline.
Two consequences, both already handled:
- `src/lib/investigation.ts`'s `toHref()` unconditionally appends the trailing slash itself, so every
  caller — `<Link>` hrefs and programmatic `router.push`/`replace` strings alike — gets the same shape
  deterministically, not dependent on which navigation path Next happens to normalize.
- `vitest.config.ts` still sets `define: { 'process.env.__NEXT_TRAILING_SLASH': 'true' }` so `<Link>`
  components rendered in tests match real production behavior instead of stripping the slash back off.

## AGENTS.md conflict (must update as part of this work)

Live Vue/Pinia/Tailwind references, with line numbers as of this migration:
- Line 5: "Python backend and Vue frontend."
- Line 7: "The frontend lives in `frontend/` and uses Vue, TypeScript, and TanStack Query."
- Line 51 (stack table): `Vue 3, TypeScript, Pinia, TanStack Query, Tailwind CSS, headless UI components, Apache ECharts`.
- Line 224: "...database logic must not live in Vue components."
- Line 238: "Use Pinia for appropriate application/client state and TanStack Query for server/cache state. Do not manually reproduce server-cache behavior in Pinia."
- Line 340: "Follow existing Vue composition and TanStack Query patterns under `frontend/src/`."

Lines 365–382 (OpenAPI contract, `frontend/openapi.json`, `types.generated.ts`, `cd frontend && npm run ...`) need **no edits** as long as the new app lands at `frontend/` with `src/` as its root and the same npm script names — guaranteed by the Phase 9 cutover (rename, not restructure).

## Confirmed dead code / stale spec claims

- **Pinia is unused.** `main.ts` installs it (`createPinia()`), but `grep -rn "defineStore" frontend/src` returns nothing. `App.vue`'s `user` is a plain `ref`. Nothing to port — React gets a plain `AuthProvider` context.
- **Tailwind is not used.** No dependency in `package.json`; `style.css` is 32 lines of hand-written CSS. AGENTS.md's stack-table claim is stale and is corrected (dropped), not preserved, by this migration.

## e2e suite: framework-agnostic but not selector-agnostic

Mostly role/text-based Playwright selectors (good regression coverage across a framework swap), but these must be ported byte-for-byte, not just "similarly":
- `investigations.spec.ts` selects `.timeline-chart canvas` and hard-codes echarts grid pixel geometry (`left: 44, right: 16` in `TimelineChart.vue`) for its mouse-drag brush test — a comment in the spec ties the two together explicitly.
- Several specs key off `.search-result` and `.result-open` (also present in `ClusterView.vue`, `SearchView.vue`).
- `relationships.spec.ts:58,72` assert `toHaveURL(/\/clusters\//)` — depends on the trailing-slash routing decision above.
- `style.css` has a `.router-link-active` class the ported `NavLink` must reuse.

Two **unit tests** (not e2e) need small literal updates for the new `/clusters/?id=` URL shape: `SearchView.test.ts` (cluster-link `toHaveURL`/query assertions) and `ClusterView.test.ts` (the `from` path assertion). This is a known, budgeted diff — not a regression.

## Two bugs found only by the `infra/test-phaseN.sh` acceptance suites (not by manual click-through)

Manual verification (and phases 3–5) used `launch.sh`, where the host-mapped port always equals nginx's
internal port (8080=8080), which hid both of these. The acceptance scripts use a random host port, which
exposed them on phase 6's `investigations.spec.ts`:

1. **nginx's automatic trailing-slash redirect hardcoded its own listen port.** `trailingSlash: true`
   means every route is a real directory (`out/search/index.html`), so a hard navigation to a bare path
   (`page.goto('/search?q=Harbor')`, no trailing slash) hits nginx's directory-redirect branch of
   `try_files $uri $uri/ /index.html;`. That redirect's `Location` header used nginx's own internal
   `listen 8080` port regardless of the `Host` header the client actually sent, so whenever the external
   (docker-mapped) port differs from 8080, the browser gets redirected to the wrong origin entirely — in
   the failing test, straight into an unrelated, unauthenticated frontend instance on port 8080. Fixed
   with `absolute_redirect off;` in `frontend/infra/nginx.conf`, which makes the redirect relative
   (`Location: /search/?q=Harbor`) and therefore port-mapping-agnostic. The old Vue app's flat, single-
   `index.html` dist output never had subdirectories, so this branch was never exercised there — this is
   a real characteristic of the new per-route static-export layout, not a regression in application code.
2. **Next's App Router always renders a second `role="alert"` node** (`#__next-route-announcer__`, its
   built-in screen-reader route announcer) alongside the app's own error banners. Playwright's bare
   `getByRole('alert')` is therefore ambiguous (strict-mode violation) wherever the app also shows an
   alert. `investigations.spec.ts`'s duplicate-saved-search-name assertion was the one spec that hit this;
   fixed by scoping the locator to `p.error[role="alert"]`. No app-side fix is possible — the announcer is
   unconditional framework runtime, not something application code renders or can suppress.

Both fixes, plus the `/\/search\?/` → `/\/search\/\?/` and `/\/articles\?/` → `/\/articles\/\?/` URL-regex
updates in `investigations.spec.ts` and `relationships.spec.ts` (the same trailing-slash decision as the
already-documented `/clusters/?id=` shape, just two assertions the initial port missed), are exercised by
`infra/test-phase6.sh` and `infra/test-phase7.sh`, both of which now pass end-to-end.
