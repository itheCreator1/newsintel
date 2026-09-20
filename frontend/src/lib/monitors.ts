import type { Monitor } from './api-types'
import { plural } from './utils'

export type MonitorStatus = 'invalid' | 'paused' | 'error' | 'waiting' | 'pending' | 'active'

export const monitorKeys = {
  all: ['monitors'] as const,
  list: (order: string) => ['monitors', 'list', order] as const,
  detail: (id: string) => ['monitors', 'detail', id] as const,
  results: (id: string, scope: string) => ['monitors', 'results', id, scope] as const,
}

/** `pending` means an evaluation is due now, so the counters may be about to change (or be recounted after a view). */
export function monitorStatus(item: Monitor, now = Date.now()): MonitorStatus {
  if (!item.state) return 'invalid'
  if (!item.enabled) return 'paused'
  if (item.error_category) return 'error'
  if (!item.evaluated_through) return 'waiting'
  return Date.parse(item.next_evaluation_at) <= now ? 'pending' : 'active'
}

export const countsLabel = (item: Monitor) => `${plural(item.unseen_article_count, 'new article')} · ${plural(item.unseen_cluster_count, 'new story', 'new stories')}`

export const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'

/** One sentence for every state that stops the counters from being the whole story; `null` when the monitor is simply active. */
export function statusText(item: Monitor, now = Date.now()): string | null {
  switch (monitorStatus(item, now)) {
    case 'invalid': return `This monitor can no longer be evaluated: ${item.problem || 'its stored state is not supported.'}`
    case 'paused': return 'Paused'
    case 'error': return `Last check failed: ${item.error_message || item.error_category}. It will be retried.`
    case 'waiting': return 'Waiting for the first check. Articles already in the archive are not counted as new.'
    case 'pending': return 'Checking for newer articles…'
    default: return null
  }
}

// Polling, not pushing: a due evaluation (including the recount after a view) should show up within seconds.
export const refetchEvery = (items: Monitor[] | undefined) => items?.some(item => monitorStatus(item) === 'pending') ? 5_000 : 30_000
