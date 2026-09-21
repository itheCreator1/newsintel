import { fireEvent, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../lib/auth-context'
import { monitor } from '../test/monitors'
import { navigationHarness, resetNavigationHarness } from '../test/navigation-harness'
import { renderWithQuery } from '../test/render'
import { Shell } from './Shell'

describe('application shell', () => {
  beforeEach(() => { vi.restoreAllMocks(); resetNavigationHarness() })

  it('shows the product identity and sign-in form', () => {
    renderWithQuery(() => <AuthProvider><Shell>content</Shell></AuthProvider>)

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
    renderWithQuery(() => <AuthProvider><Shell>content</Shell></AuthProvider>)
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

  function signedIn() {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) =>
      String(input).endsWith('/auth/me') ? new Response(JSON.stringify({ id: '1', username: 'analyst' })) : new Response('{}')))
    return renderWithQuery(() => <AuthProvider><Shell><p>content</p></Shell></AuthProvider>)
  }

  it('groups every destination and marks only the exact current one', async () => {
    resetNavigationHarness({ pathname: '/search/' })
    signedIn()
    const nav = await screen.findByRole('navigation', { name: 'Main navigation' })
    const groups = ['Explore', 'Archive', 'Investigations', 'System'].map(name => within(nav).getByText(name).parentElement!)
    expect(groups.map(group => within(group).getAllByRole('link').map(link => link.textContent))).toEqual([
      ['Overview', 'Search', 'Graph', 'Events', 'Map', 'Compare'], ['Sources', 'Articles'], ['Saved Searches', 'Watchlist'], ['Jobs', 'Operations', 'Settings'],
    ])
    expect(within(nav).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page').map(link => link.textContent)).toEqual(['Search'])
  })

  it('does not select a parent destination for a contextual dossier route', async () => {
    resetNavigationHarness({ pathname: '/sources/detail/' })
    signedIn()
    const nav = await screen.findByRole('navigation', { name: 'Main navigation' })
    expect(within(nav).getAllByRole('link').some(link => link.hasAttribute('aria-current'))).toBe(false)
  })

  it('shows who is signed in, a skip link, and a focusable main target', async () => {
    signedIn()
    expect(await screen.findByText('analyst')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
    expect(screen.getByRole('main')).toHaveAttribute('tabindex', '-1')
  })

  it('opens the menu inline and closes it on Escape, on a destination, and on navigation', async () => {
    const { rerenderSame } = signedIn()
    const menu = await screen.findByRole('button', { name: 'Menu' })
    const panel = document.getElementById(menu.getAttribute('aria-controls')!)!
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(panel.className).toMatch(/(^| )hidden( |$)/)

    fireEvent.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    expect(panel.className).not.toMatch(/(^| )hidden( |$)/)
    fireEvent.keyDown(within(panel).getByRole('link', { name: 'Graph' }), { key: 'Escape' })
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(document.activeElement).toBe(menu)

    fireEvent.click(menu)
    fireEvent.click(within(panel).getByRole('link', { name: 'Overview' }))
    expect(menu).toHaveAttribute('aria-expanded', 'false')

    fireEvent.click(menu)
    navigationHarness.pathname = '/graph/'
    rerenderSame()
    expect(menu).toHaveAttribute('aria-expanded', 'false')
  })

  function withMonitors(response: () => Response) {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/auth/me')) return new Response(JSON.stringify({ id: '1', username: 'analyst' }))
      if (path.includes('/monitors?')) return response()
      return new Response('{}')
    }))
    return renderWithQuery(() => <AuthProvider><Shell><p>content</p></Shell></AuthProvider>)
  }

  it('counts the monitors with new results on the Watchlist link', async () => {
    withMonitors(() => new Response(JSON.stringify({ items: [monitor({ id: 'a', unseen_article_count: 2 }), monitor({ id: 'b', unseen_article_count: 1 }), monitor({ id: 'c' })], next_cursor: null })))
    const link = await screen.findByRole('link', { name: 'Watchlist, 2 with new results' })
    expect(link).toHaveAttribute('href', '/monitors/')
    expect(within(link).getByText('2')).toBeTruthy()
    expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/monitors?order=activity&limit=100'))).toBe(true)
  })

  it('shows no badge when nothing is new or the monitors cannot be loaded', async () => {
    withMonitors(() => new Response(JSON.stringify({ items: [monitor({ id: 'c' })], next_cursor: null })))
    expect(await screen.findByRole('link', { name: 'Watchlist' })).toBeTruthy()
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/monitors?'))).toBe(true))
    expect(screen.queryByRole('link', { name: /with new results/ })).toBeNull()
  })

  it('keeps the plain Watchlist link when the monitors request fails', async () => {
    withMonitors(() => new Response(JSON.stringify({ detail: 'boom' }), { status: 500 }))
    expect(await screen.findByRole('link', { name: 'Watchlist' })).toBeTruthy()
    await vi.waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/monitors?'))).toBe(true))
    expect(screen.queryByRole('link', { name: /with new results/ })).toBeNull()
  })
})
