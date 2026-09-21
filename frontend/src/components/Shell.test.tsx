import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../lib/auth-context'
import { resetNavigationHarness } from '../test/navigation-harness'
import { Shell } from './Shell'

describe('application shell', () => {
  beforeEach(() => { vi.restoreAllMocks(); resetNavigationHarness() })

  it('shows the product identity and sign-in form', () => {
    render(<AuthProvider><Shell>content</Shell></AuthProvider>)

    expect(screen.getByRole('heading', { name: 'NewsIntel' })).toBeTruthy()
    expect(screen.getByLabelText('Username')).toBeTruthy()
    expect(screen.getByLabelText('Password')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })

  it('offers routed source, article, search, and job management after sign in', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/auth/me')) return new Response(JSON.stringify({ id: '1', username: 'admin' }))
      if (path.endsWith('/health/ready')) return new Response(JSON.stringify({ status: 'ready' }))
      if (path.includes('/feeds')) return new Response(JSON.stringify({ items: [], next_cursor: null }))
      if (path.includes('/articles')) return new Response(JSON.stringify({ items: [], next_cursor: null }))
      return new Response('{}')
    }))
    render(<AuthProvider><Shell>content</Shell></AuthProvider>)
    expect(await screen.findByRole('link', { name: 'Sources' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Articles' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Search' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Graph' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Events' })).toHaveAttribute('href', '/events/')
    expect(screen.getByRole('link', { name: 'Compare' })).toHaveAttribute('href', '/compare/')
    expect(screen.getByRole('link', { name: 'Map' })).toHaveAttribute('href', '/map/')
    expect(screen.getByRole('link', { name: 'Saved Searches' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Watchlist' })).toHaveAttribute('href', '/monitors/')
    expect(screen.getByRole('link', { name: 'Jobs' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Operations' })).toHaveAttribute('href', '/operations/')
  })
})
