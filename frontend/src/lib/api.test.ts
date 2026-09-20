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

it('requests a cluster with an optional cursor', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ id: 'cluster-1', article_count: 2, source_count: 2, first_published_at: null, last_published_at: null, representative_article_id: null, members: { items: [], next_cursor: null } }), { status: 200 }))

  await api.cluster('cluster-1')
  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/clusters/cluster-1')

  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: 'cluster-1', article_count: 2, source_count: 2, first_published_at: null, last_published_at: null, representative_article_id: null, members: { items: [], next_cursor: null } }), { status: 200 }))
  await api.cluster('cluster-1', 'next')
  expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/clusters/cluster-1?cursor=next')
})

it('requests the entity graph with repeated filters and a focus entity', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify({ nodes: [], edges: [], truncated: false }), { status: 200 }))

  await api.entityGraph({ q: 'grid', source_country: ['US', 'GR'], entity_type: ['ORG'], focus_entity_id: 'entity-one', nodes: '40' })

  expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/graph/entities?q=grid&source_country=US&source_country=GR&entity_type=ORG&focus_entity_id=entity-one&nodes=40')
})

it('explains validation errors returned as a list of field problems', async () => {
  vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [
      { loc: ['body', 'state'], msg: 'Value error, after must be earlier than before', type: 'value_error' },
      { loc: ['body', 'state', 'source_country', 0], msg: "String should match pattern '^[A-Za-z]{2}$'", type: 'string_pattern_mismatch' },
    ] }), { status: 422 }))

  await expect(api.createSavedSearch('Grid', { q: '', sort: 'relevance', interval: 'auto' })).rejects.toThrow(
    "state: Value error, after must be earlier than before; state.source_country.0: String should match pattern '^[A-Za-z]{2}$'",
  )
})

it('reads monitors with order, cursor and scope, and mutates them with CSRF', async () => {
  const csrf = () => new Response(JSON.stringify({ csrf_token: 'token' }), { status: 200 })
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
  const fetchMock = vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(json({ items: [], next_cursor: null }))
    .mockResolvedValueOnce(json({ items: [], next_cursor: null }))
    .mockResolvedValueOnce(json({ id: 'm1' }))
    .mockResolvedValueOnce(json({ items: [], next_cursor: null, window_start: null, window_end: null }))
    .mockResolvedValueOnce(json({ article_count: 0 }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(json({ id: 'm1' }, 201))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(json({ id: 'm1' }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(json({ id: 'm1' }))
    .mockResolvedValueOnce(csrf()).mockResolvedValueOnce(new Response(null, { status: 204 }))

  await api.monitors('activity')
  await api.monitors('name', 'next page')
  await api.monitor('m1')
  await api.monitorResults('m1', 'recent', 'c 1')
  await api.monitorChanges('m1')
  await api.createMonitor('Grid', { q: 'grid', sort: 'relevance', interval: 'auto' })
  await api.updateMonitor('m1', { enabled: false })
  await api.markMonitorViewed('m1', '2026-09-20T12:00:00Z')
  await api.deleteMonitor('m1')

  const calls = fetchMock.mock.calls
  expect(calls[0][0]).toBe('/api/v1/monitors?order=activity')
  expect(calls[1][0]).toBe('/api/v1/monitors?order=name&cursor=next+page')
  expect(calls[2][0]).toBe('/api/v1/monitors/m1')
  expect(calls[3][0]).toBe('/api/v1/monitors/m1/results?scope=recent&cursor=c+1')
  expect(calls[4][0]).toBe('/api/v1/monitors/m1/changes')
  const sent = (index: number) => ({ url: calls[index][0], method: calls[index][1]?.method, csrf: new Headers(calls[index][1]?.headers).get('X-CSRF-Token'), body: calls[index][1]?.body })
  expect(sent(6)).toEqual({ url: '/api/v1/monitors', method: 'POST', csrf: 'token', body: JSON.stringify({ name: 'Grid', kind: 'search', state: { q: 'grid', sort: 'relevance', interval: 'auto' } }) })
  expect(sent(8)).toMatchObject({ url: '/api/v1/monitors/m1', method: 'PATCH', csrf: 'token', body: JSON.stringify({ enabled: false }) })
  expect(sent(10)).toMatchObject({ url: '/api/v1/monitors/m1/viewed', method: 'POST', body: JSON.stringify({ through: '2026-09-20T12:00:00Z' }) })
  expect(sent(12)).toMatchObject({ url: '/api/v1/monitors/m1', method: 'DELETE' })
})
