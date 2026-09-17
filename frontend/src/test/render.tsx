import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'

/**
 * Takes a factory, not a pre-built element: React bails out of re-rendering a subtree whose element
 * reference is unchanged, so `rerenderSame` must build a fresh element tree each call (same `client`
 * instance, so the query cache persists) or components would never re-read updated navigation-harness
 * state after a simulated `router.push`/`replace`.
 */
export function renderWithQuery(factory: () => ReactElement, options: { retry?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: options.retry ?? false } } })
  const build = () => <QueryClientProvider client={client}>{factory()}</QueryClientProvider>
  const result = render(build())
  return { ...result, rerenderSame: () => result.rerender(build()) }
}
