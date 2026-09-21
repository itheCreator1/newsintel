import type { Monitor, MonitorChanges } from './api-types'
import { plural } from './utils'

export type MonitorStatus = 'invalid' | 'paused' | 'error' | 'waiting' | 'pending' | 'active'

export const monitorKeys = {
  all: ['monitors'] as const,
  list: (order: string) => ['monitors', 'list', order] as const,
  detail: (id: string) => ['monitors', 'detail', id] as const,
  results: (id: string, scope: string) => ['monitors', 'results', id, scope] as const,
  changes: (id: string) => ['monitors', 'changes', id] as const,
  // Under the list prefix, so every change that refreshes the lists refreshes the badge too.
  badge: ['monitors', 'list', 'badge'] as const,
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

/** Monitors with something new to look at; paused ones keep old counts, so they do not call for attention. */
export const unseenMonitors = (items: Monitor[] | undefined) => items?.filter(item => item.enabled && item.unseen_article_count > 0).length ?? 0

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

export type ChangeEvidence = MonitorChanges['sources'][number]['evidence'][number]
export type ChangeLine = { key: string; text: string; subject: { kind: 'articles' } | { kind: 'source' | 'entity' | 'story'; id: string } | null; evidence: ChangeEvidence[] }

/** Wording is a function of the typed fields only, in a fixed order, so the same window always reads the same. */
export function describeChanges(changes: MonitorChanges): ChangeLine[] {
  const lines: ChangeLine[] = []
  if (changes.article_count > 0) lines.push({ key: 'articles', text: plural(changes.article_count, 'new article'), subject: { kind: 'articles' }, evidence: [] })
  for (const item of changes.sources) lines.push({ key: `source-${item.source_id}`, text: `New source: ${item.name} (${plural(item.article_count, 'article')})`, subject: { kind: 'source', id: item.source_id }, evidence: item.evidence })
  for (const item of changes.entities) lines.push({ key: `entity-${item.entity_id}`, text: `New entity: ${item.name} (${item.entity_type}) — ${plural(item.article_count, 'article')}`, subject: { kind: 'entity', id: item.entity_id }, evidence: item.evidence })
  for (const item of changes.stories) {
    const title = item.title ?? 'Untitled story'
    const text = item.status === 'new' ? `New story: ${title} — ${plural(item.source_count, 'source')}` : `Story grew: ${title} — ${item.source_count - item.sources_added} → ${item.source_count} sources (+${item.sources_added})`
    lines.push({ key: `story-${item.cluster_id}`, text, subject: { kind: 'story', id: item.cluster_id }, evidence: item.evidence })
  }
  const more = [[changes.more_sources, 'sources'], [changes.more_entities, 'entities'], [changes.more_stories, 'stories']] as const
  for (const [flag, noun] of more) if (flag) lines.push({ key: `more-${noun}`, text: `More ${noun} matched in this window than are listed.`, subject: null, evidence: [] })
  return lines
}
