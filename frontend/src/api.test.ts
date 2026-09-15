import { beforeEach, expect, it, vi } from 'vitest'

import { api } from './api'

beforeEach(() => vi.restoreAllMocks())

it('keeps JSON content type when a mutation adds a CSRF header', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch')
  fetchMock
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 'user-one', username: 'phase3' }), { status: 200 }),
    )

  await api.login('phase3', 'phase3-password')

  const headers = new Headers(fetchMock.mock.calls[1][1]?.headers)
  expect(headers.get('Content-Type')).toBe('application/json')
  expect(headers.get('X-CSRF-Token')).toBe('token')
})

it('requests the timeline with repeated filters and the chosen interval', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ interval: 'day', total: 0, buckets: [] }), { status: 200 }))

  await api.timeline({ q: 'grid', source_country: ['US', 'GR'], interval: 'week' })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/search/timeline?q=grid&source_country=US&source_country=GR&interval=week')
})

it('manages saved searches with CSRF-protected mutations', async () => {
  const saved = { id: 'saved-one', name: 'Grid', state_version: 1, state: { q: 'grid', sort: 'relevance' as const, interval: 'auto' as const }, problem: null, created_at: '', updated_at: '' }
  const csrf = () => new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 })
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [saved], next_cursor: null }), { status: 200 }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 201 }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(new Response(JSON.stringify(saved), { status: 200 }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(new Response(null, { status: 204 }))

  expect((await api.savedSearches('after one')).items).toEqual([saved])
  await api.createSavedSearch('Grid', saved.state)
  await api.updateSavedSearch('saved-one', { name: 'Power grid' })
  await api.deleteSavedSearch('saved-one')

  const calls = fetchMock.mock.calls.map(([url, init]) => [url, init?.method ?? 'GET', init?.body])
  expect(calls).toEqual([
    ['/api/v1/saved-searches?cursor=after+one', 'GET', undefined],
    ['/api/v1/auth/csrf', 'GET', undefined], ['/api/v1/saved-searches', 'POST', JSON.stringify({ name: 'Grid', state: saved.state })],
    ['/api/v1/auth/csrf', 'GET', undefined], ['/api/v1/saved-searches/saved-one', 'PATCH', JSON.stringify({ name: 'Power grid' })],
    ['/api/v1/auth/csrf', 'GET', undefined], ['/api/v1/saved-searches/saved-one', 'DELETE', undefined],
  ])
  expect(new Headers(fetchMock.mock.calls[6][1]?.headers).get('X-CSRF-Token')).toBe('token')
})
