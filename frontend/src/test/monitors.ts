import type { Monitor } from '../lib/api-types'

export const monitor = (overrides: Partial<Monitor> = {}): Monitor => ({
  id: 'm1', name: 'Harbor watch', kind: 'search', enabled: true, state_version: 1,
  state: { q: 'harbor', sort: 'relevance', interval: 'auto' }, problem: null,
  unseen_article_count: 0, unseen_cluster_count: 0,
  evaluated_through: '2026-09-20T11:55:00Z', viewed_through: '2026-09-20T11:55:00Z',
  latest_match_at: null, latest_match_article_id: null,
  last_evaluated_at: '2026-09-20T11:56:00Z', next_evaluation_at: '2026-09-20T12:01:00Z',
  error_category: null, error_message: null,
  created_at: '2026-09-20T10:00:00Z', updated_at: '2026-09-20T10:00:00Z', ...overrides,
})
