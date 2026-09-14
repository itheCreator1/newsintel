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
