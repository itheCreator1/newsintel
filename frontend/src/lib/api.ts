import type { AnnotationLookupPage, Article, ArticleAnnotations, ArticleDetail, Backlog, ClusterDetail, CursorPage, EdgeEvidence, EntityArticlePage, EntityClusterPage, EntityDossier, EntityRelationships, EventArticlePage, EventClusterPage, EventDetail, EventPage, EventTimelinePage, Feed, FeedFetch, GraphResponse, IndexFailurePage, IndexStatus, IngestionTimeline, InvestigationState, NlpFailurePage, NlpStatus, MonitorChanges, MonitorPage, MonitorResultPage, Monitor, ProcessingJob, SavedSearch, SavedSearchPage, SearchPage, SearchSourcePage, SearchTimeline, SourceArticlePage, SourceClusterPage, SourceCoverage, SourceDetail, SourceFetchPage, SourceTiming, StopWords, TopCountries, TopEntities } from './api-types'

export interface User { id: string; username: string }

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly detail?: unknown) { super(message) }
}

function validationMessage(problems: unknown[]): string | undefined {
  const messages = problems.flatMap(problem => {
    if (!problem || typeof problem !== 'object' || !('msg' in problem)) return []
    const location = 'loc' in problem && Array.isArray(problem.loc) ? problem.loc.filter(part => part !== 'body' && part !== 'query').join('.') : ''
    return [location ? `${location}: ${String(problem.msg)}` : String(problem.msg)]
  })
  return messages.join('; ') || undefined
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown }
    const detail = typeof payload.detail === 'string' ? payload.detail : Array.isArray(payload.detail) ? validationMessage(payload.detail) : payload.detail && typeof payload.detail === 'object' && 'message' in payload.detail ? String(payload.detail.message) : undefined
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
  articleAnnotations: (id: string) => request<ArticleAnnotations>(`/articles/${id}/annotations`),
  processArticle: (id: string, mode: 'full_text' | 'full_text_html') => mutate<{ job_id: string; status: string; reused: boolean }>(`/articles/${id}/process`, 'POST', { mode }),
  reprocessArticle: (id: string, processors: string[] = []) => mutate<{ status: string; jobs_created: number }>(`/articles/${id}/nlp/reprocess`, 'POST', { processors }),
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
  search: (filters: Filters, cursor?: string) => {
    const params = filterParams(filters)
    if (cursor) params.set('cursor', cursor)
    return request<SearchPage>(`/search?${params}`)
  },
  timeline: (filters: Filters) => request<SearchTimeline>(`/search/timeline?${filterParams(filters)}`),
  cluster: (id: string, cursor?: string) => request<ClusterDetail>(`/clusters/${id}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  entityDossier: (id: string, days: number) => request<EntityDossier>(`/entities/${id}?days=${days}`),
  entityArticles: (id: string, cursor?: string) => request<EntityArticlePage>(`/entities/${id}/articles${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  entityClusters: (id: string, cursor?: string) => request<EntityClusterPage>(`/entities/${id}/clusters${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  entityRelationships: (id: string, days: number) => request<EntityRelationships>(`/entities/${id}/relationships?days=${days}`),
  events: (filters: EventFilters, cursor?: string) => request<EventPage>(`/events${query({ ...filters, cursor })}`),
  event: (id: string) => request<EventDetail>(`/events/${id}`),
  eventClusters: (id: string, cursor?: string) => request<EventClusterPage>(`/events/${id}/clusters${query({ cursor })}`),
  eventArticles: (id: string, cursor?: string) => request<EventArticlePage>(`/events/${id}/articles${query({ cursor })}`),
  eventTimeline: (id: string, after?: string) => request<EventTimelinePage>(`/events/${id}/timeline${query({ after })}`),
  source: (id: string, days: number) => request<SourceDetail>(`/sources/${id}${query({ days: String(days) })}`),
  sourceCoverage: (id: string, days: number) => request<SourceCoverage>(`/sources/${id}/coverage${query({ days: String(days) })}`),
  sourceTiming: (id: string, days: number) => request<SourceTiming>(`/sources/${id}/timing${query({ days: String(days) })}`),
  sourceArticles: (id: string, cursor?: string) => request<SourceArticlePage>(`/sources/${id}/articles${query({ cursor })}`),
  sourceClusters: (id: string, cursor?: string) => request<SourceClusterPage>(`/sources/${id}/clusters${query({ cursor })}`),
  sourceFetches: (id: string, cursor?: string) => request<SourceFetchPage>(`/sources/${id}/fetches${query({ cursor })}`),
  entityGraph: (filters: Filters) => request<GraphResponse>(`/graph/entities?${filterParams(filters)}`),
  edgeEvidence: (filters: Filters, cursor?: string) => {
    const params = filterParams(filters)
    if (cursor) params.set('cursor', cursor)
    return request<EdgeEvidence>(`/graph/edges/evidence?${params}`)
  },
  savedSearches: (cursor?: string) => request<SavedSearchPage>(`/saved-searches${cursor ? `?${new URLSearchParams({ cursor })}` : ''}`),
  createSavedSearch: (name: string, state: InvestigationState) => mutate<SavedSearch>('/saved-searches', 'POST', { name, state }),
  updateSavedSearch: (id: string, payload: { name?: string; state?: InvestigationState }) => mutate<SavedSearch>(`/saved-searches/${id}`, 'PATCH', payload),
  deleteSavedSearch: (id: string) => mutate<void>(`/saved-searches/${id}`, 'DELETE'),
  monitors: (order: 'name' | 'activity', cursor?: string) => request<MonitorPage>(`/monitors?${new URLSearchParams({ order, ...(cursor ? { cursor } : {}) })}`),
  monitor: (id: string) => request<Monitor>(`/monitors/${id}`),
  monitorResults: (id: string, scope: 'unseen' | 'recent', cursor?: string) => request<MonitorResultPage>(`/monitors/${id}/results?${new URLSearchParams({ scope, ...(cursor ? { cursor } : {}) })}`),
  monitorChanges: (id: string) => request<MonitorChanges>(`/monitors/${id}/changes`),
  createMonitor: (name: string, state: InvestigationState) => mutate<Monitor>('/monitors', 'POST', { name, kind: 'search', state }),
  updateMonitor: (id: string, payload: { name?: string; enabled?: boolean }) => mutate<Monitor>(`/monitors/${id}`, 'PATCH', payload),
  markMonitorViewed: (id: string, through: string) => mutate<Monitor>(`/monitors/${id}/viewed`, 'POST', { through }),
  deleteMonitor: (id: string) => mutate<void>(`/monitors/${id}`, 'DELETE'),
  searchSources: (q = '', cursor?: string) => request<SearchSourcePage>(`/search/sources?${new URLSearchParams({ ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) })}`),
  indexingStatus: () => request<IndexStatus>('/search/indexing/status'),
  indexingFailures: (cursor?: string) => request<IndexFailurePage>(`/search/indexing/failures${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  retryIndexing: (articleId: string) => mutate<{ status: string }>(`/search/indexing/articles/${articleId}/retry`, 'POST'),
  nlpStatus: () => request<NlpStatus>('/nlp/status'),
  nlpFailures: (cursor?: string) => request<NlpFailurePage>(`/nlp/failures${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
  retryNlpJob: (jobId: string) => mutate<{ status: string; jobs_created: number }>(`/nlp/jobs/${jobId}/retry`, 'POST'),
  stopWords: () => request<StopWords>('/nlp/stop-words'),
  updateStopWords: (currentRevision: number, words: string[]) => mutate<StopWords>('/nlp/stop-words', 'PUT', { current_revision: currentRevision, words }),
  nlpEntities: (q = '', cursor?: string) => annotationLookup('/nlp/entities', q, cursor),
  nlpKeywords: (q = '', cursor?: string) => annotationLookup('/nlp/keywords', q, cursor),
  ingestionTimeline: () => request<IngestionTimeline>('/analytics/ingestion-timeline'),
  topEntities: (entityType?: string) => request<TopEntities>(`/analytics/top-entities${entityType ? `?entity_type=${encodeURIComponent(entityType)}` : ''}`),
  topCountries: () => request<TopCountries>('/analytics/top-countries'),
}

type Filters = Record<string, string | string[] | undefined>
export interface EventFilters { status?: string; country?: string; entity_id?: string; from?: string; to?: string }

function query(params: Record<string, string | undefined>): string {
  const search = filterParams(params)
  return search.size ? `?${search}` : ''
}

function filterParams(filters: Filters): URLSearchParams {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) for (const item of Array.isArray(value) ? value : value === undefined || value === '' ? [] : [value]) params.append(key, item)
  return params
}

function annotationLookup(path: string, q: string, cursor?: string): Promise<AnnotationLookupPage> {
  const params = new URLSearchParams({ ...(q ? { q } : {}), ...(cursor ? { cursor } : {}) })
  return request<AnnotationLookupPage>(`${path}?${params}`)
}

async function mutate<T>(path: string, method: string, body?: unknown): Promise<T> {
  const { csrf_token } = await request<{ csrf_token: string }>('/auth/csrf')
  return request<T>(path, { method, headers: { 'X-CSRF-Token': csrf_token }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
}
