import { render, screen } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { VueQueryPlugin } from '@tanstack/vue-query'

import App from './App.vue'
import { router } from './router'

describe('application shell', () => {
  beforeEach(() => vi.restoreAllMocks())
  it('shows the product identity and sign-in form', () => {
    render(App, { global: { plugins: [VueQueryPlugin, router] } })

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
    render(App, { global: { plugins: [VueQueryPlugin, router] } })
    expect(await screen.findByRole('link', { name: 'Sources' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Articles' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Search' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Jobs' })).toBeTruthy()
  })
})
