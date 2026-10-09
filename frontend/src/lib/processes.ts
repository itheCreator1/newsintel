import type { BadgeTone } from '../components/StatusBadge'
import type { ActivityFilter, ActivityLinkKind, ActivityStatus, ProcessCard, ProcessGroup, ProcessKey } from './api-types'
import { entityHref, sourceHref, toHref } from './investigation'

export const HOURS = [1, 24, 168] as const
export const DEFAULT_HOURS = 24
export const HOUR_LABELS: Record<number, string> = { 1: '1 h', 24: '24 h', 168: '7 d' }
export const LIVE_SECONDS = 5

export const GROUPS: [ProcessGroup, string, string][] = [
  ['per_item', 'Per item', 'one job per article, feed or monitor'],
  ['bulk', 'Bulk runs', 'long runs with progress'],
  ['scheduled', 'Scheduled', 'run on a timer'],
]
// In page order, as the API sends the cards; the list's process filter offers them in this order.
export const PROCESS_KEYS: ProcessKey[] = [
  'feeds', 'articles', 'nlp', 'clustering', 'search', 'monitors',
  'reprocessing', 'authority', 'rebuild', 'source_refresh',
  'events', 'retention', 'wikidata_refresh', 'wikidata_candidates',
]
export const STATUS_FILTERS: [ActivityFilter, string][] = [
  ['attention', 'Needs attention'], ['failed', 'Failed'], ['running', 'Running'], ['queued', 'Queued'], ['done', 'Done'], ['all', 'All'],
]
export const FEED_VIEWS = ['all', 'attention'] as const
export type FeedView = (typeof FEED_VIEWS)[number]

export interface ProcessesView { hours: number; process?: ProcessKey; status: ActivityFilter; q: string; feeds: FeedView }

/** The page's view from its URL; anything the API would refuse falls back to the default. */
export function readProcessesQuery(query: URLSearchParams): ProcessesView {
  const hours = Number(query.get('hours'))
  const process = query.get('process') as ProcessKey
  const status = query.get('status') as ActivityFilter
  const feeds = query.get('feeds') as FeedView
  return {
    hours: (HOURS as readonly number[]).includes(hours) ? hours : DEFAULT_HOURS,
    process: PROCESS_KEYS.includes(process) ? process : undefined,
    status: STATUS_FILTERS.some(([value]) => value === status) ? status : 'attention',
    q: query.get('q') ?? '',
    feeds: FEED_VIEWS.includes(feeds) ? feeds : 'all',
  }
}

/** Processes link; the window and every filter are in the URL, so a view can be bookmarked. */
export function processesHref({ hours, process, status, q, feeds }: Partial<ProcessesView>): string {
  const query = new URLSearchParams()
  if (hours && hours !== DEFAULT_HOURS) query.set('hours', String(hours))
  if (process) query.set('process', process)
  if (status && status !== 'attention') query.set('status', status)
  if (q) query.set('q', q)
  if (feeds && feeds !== 'all') query.set('feeds', feeds)
  return toHref('/processes', query)
}

/** Where the old Jobs and Operations addresses lead: here, with the window when it is still offered. */
export function legacyHref(query: URLSearchParams): string {
  const hours = Number(query.get('hours'))
  return processesHref({ hours: (HOURS as readonly number[]).includes(hours) ? hours : undefined })
}

export function itemHref(kind: ActivityLinkKind | null | undefined, id: string | null | undefined): string | null {
  if (!kind || !id) return null
  if (kind === 'article') return toHref('/articles', new URLSearchParams({ article: id }))
  if (kind === 'feed') return sourceHref(id)
  if (kind === 'entity') return entityHref(id)
  return toHref('/monitors', new URLSearchParams({ id }))
}

const STATES: Record<ProcessCard['state'], [BadgeTone, string]> = {
  ok: ['healthy', 'Up to date'], working: ['active', 'Working'], retrying: ['degraded', 'Retrying'], stalled: ['degraded', 'Stalled'],
  failing: ['error', 'Failed'], idle: ['pending', 'Idle'], off: ['pending', 'Off'],
}

/** How far a run is; a run still in hand never shows 100%. */
export function percent(done: number, total: number): number {
  return Math.min(99, Math.round(done / total * 100))
}

/** A card's state pill: a bulk run in hand shows its progress, a failing queue how many failed. */
export function cardBadge(card: ProcessCard): { tone: BadgeTone; label: string } {
  const [tone, label] = STATES[card.state]
  const total = card.progress?.total
  if (card.state === 'working' && card.group === 'bulk' && card.progress && total) return { tone, label: `${percent(card.progress.done, total)}%` }
  if (card.state === 'failing' && card.failed_in_window) return { tone, label: `${card.failed_in_window.toLocaleString('en')} failed` }
  return { tone, label }
}

const STATUSES: Record<ActivityStatus, [BadgeTone, string]> = {
  failed: ['error', 'Failed'], retrying: ['degraded', 'Retrying'], running: ['active', 'Running'],
  queued: ['pending', 'Queued'], succeeded: ['healthy', 'Done'], stopped: ['pending', 'Stopped'],
}

export function activityBadge(status: ActivityStatus): { tone: BadgeTone; label: string } {
  const [tone, label] = STATUSES[status]
  return { tone, label }
}
