import { vi } from 'vitest'

/**
 * A minimal stand-in for the App Router's client navigation hooks, used because there is no official
 * in-memory router for `next/navigation` outside a real Next runtime (the standard testing pattern is
 * to mock the module). `push`/`replace` update `pathname`/`searchParams` in place; after triggering one
 * from a test, call the render's `rerender()` so the component under test re-reads the new values.
 * A small history stack backs `back`/`forward` for the one test (SearchView's cross-filter flow) that
 * needs real back-navigation semantics.
 */
export const navigationHarness = {
  pathname: '/',
  searchParams: new URLSearchParams(),
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
}

let history: { pathname: string; search: string }[] = [{ pathname: '/', search: '' }]
let historyIndex = 0

function snapshot() {
  return { pathname: navigationHarness.pathname, search: navigationHarness.searchParams.toString() }
}

function restore(entry: { pathname: string; search: string }) {
  navigationHarness.pathname = entry.pathname
  navigationHarness.searchParams = new URLSearchParams(entry.search)
}

function applyHref(href: string, { push }: { push: boolean }) {
  const url = new URL(href, 'http://n')
  navigationHarness.pathname = url.pathname
  navigationHarness.searchParams = url.searchParams
  if (push) {
    history = history.slice(0, historyIndex + 1)
    history.push(snapshot())
    historyIndex = history.length - 1
  } else {
    history[historyIndex] = snapshot()
  }
}

navigationHarness.push.mockImplementation((href: string) => applyHref(href, { push: true }))
navigationHarness.replace.mockImplementation((href: string) => applyHref(href, { push: false }))
navigationHarness.back.mockImplementation(() => { if (historyIndex > 0) restore(history[--historyIndex]) })
navigationHarness.forward.mockImplementation(() => { if (historyIndex < history.length - 1) restore(history[++historyIndex]) })

export function resetNavigationHarness(initial: { pathname?: string; search?: string } = {}) {
  navigationHarness.pathname = initial.pathname ?? '/'
  navigationHarness.searchParams = new URLSearchParams(initial.search ?? '')
  navigationHarness.push.mockClear()
  navigationHarness.replace.mockClear()
  navigationHarness.back.mockClear()
  navigationHarness.forward.mockClear()
  history = [snapshot()]
  historyIndex = 0
}
