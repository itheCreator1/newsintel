import type { Article, ArticleDetail, Backlog, CursorPage, Feed, FeedFetch, IndexFailurePage, IndexStatus, ProcessingJob, SearchPage, SearchSourcePage } from './api-types'

export interface User { id: string; username: string }

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly detail?: unknown) { super(message) }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown }
    const detail = typeof payload.detail === 'string' ? payload.detail : payload.detail && typeof payload.detail === 'object' && 'message' in payload.detail ? String(payload.detail.message) : undefined
    throw new ApiError(response.status === 401 ? 'Invalid username or password' : detail || 'Request failed', response.status, payload.detail)
  }
  return response.status === 204 ? undefined as T : response.json()
}

export const api = {
  me: () => request<User>('/auth/me'),
  login: async (username: string, password: string) => {
    const { csrf_token } = await request<{ csrf_token: string }>('/auth/csrf')
    return request<User>('/auth/login', {
      method: 'POST', headers: { 'X-CSRF-Token': csrf_token }, body: JSON.stringify({ username, password }),
    })
  },
  status: () => request<{ status: string }>('/health/ready'),
  logout: async () => {
    const { csrf_token } = await request<{ csrf_token: string }>('/auth/csrf')
    return request<void>('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': csrf_token } })
  },
  feeds: (cursor?: string) => request<CursorPage<Feed>>(`/feeds${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  createFeed: (payload: { name: string; url: string; source_country?: string; expected_language?: string; tags?: string[]; poll_interval_minutes?: number; fetching_mode?: 'rss' | 'full_text' | 'full_text_html' }) => mutate<Feed>('/feeds', 'POST', payload),
  updateFeed: (id: string, payload: Partial<Feed>) => mutate<Feed>(`/feeds/${id}`, 'PATCH', payload),
  retireFeed: (id: string) => mutate<void>(`/feeds/${id}`, 'DELETE'),
  pollFeed: (id: string) => mutate<{ fetch_id: string; status: string; reused: boolean }>(`/feeds/${id}/poll`, 'POST'),
  fetches: (id: string, cursor?: string) => request<CursorPage<FeedFetch>>(`/feeds/${id}/fetches${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  articles: (feedId?: string, cursor?: string) => {
    const params = new URLSearchParams()
    if (feedId) params.set('feed_id', feedId)
    if (cursor) params.set('cursor', cursor)
    return request<CursorPage<Article>>(`/articles${params.size ? `?${params}` : ''}`)
  },
  article: (id: string) => request<ArticleDetail>(`/articles/${id}`),
  processArticle: (id: string, mode: 'full_text' | 'full_text_html') => mutate<{ job_id: string; status: string; reused: boolean }>(`/articles/${id}/process`, 'POST', { mode }),
  jobs: (filters: { articleId?: string; stage?: string; status?: string; cursor?: string } = {}) => {
    const params = new URLSearchParams()
    if (filters.articleId) params.set('article_id', filters.articleId)
    if (filters.stage) params.set('stage', filters.stage)
    if (filters.status) params.set('status', filters.status)
    if (filters.cursor) params.set('cursor', filters.cursor)
    return request<CursorPage<ProcessingJob>>(`/jobs${params.size ? `?${params}` : ''}`)
  },
  job: (id: string) => request<ProcessingJob>(`/jobs/${id}`),
  backlog: () => request<Backlog>('/jobs/backlog'),
  retryJob: (id: string) => mutate<{ job_id: string; status: string; reused: boolean }>(`/jobs/${id}/retry`, 'POST'),
  search: (filters: Record<string, string | string[] | undefined>, cursor?: string) => {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(filters)) for (const item of Array.isArray(value) ? value : value === undefined || value === '' ? [] : [value]) params.append(key, item)
    if (cursor) params.set('cursor', cursor)
    return request<SearchPage>(`/search?${params}`)
  },
  searchSources: (q = '', cursor?: string) => request<SearchSourcePage>(`/search/sources?${new URLSearchParams({ ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) })}`),
  indexingStatus: () => request<IndexStatus>('/search/indexing/status'),
  indexingFailures: (cursor?: string) => request<IndexFailurePage>(`/search/indexing/failures${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  retryIndexing: (articleId: string) => mutate<{ status: string }>(`/search/indexing/articles/${articleId}/retry`, 'POST'),
}

async function mutate<T>(path: string, method: string, body?: unknown): Promise<T> {
  const { csrf_token } = await request<{ csrf_token: string }>('/auth/csrf')
  return request<T>(path, { method, headers: { 'X-CSRF-Token': csrf_token }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
}
