import type { LocationQueryRaw } from 'vue-router'
import type { InvestigationState } from './api-types'

export const SORTS = ['relevance', 'newest', 'oldest', 'most_sources'] as const
export const INTERVALS = ['auto', 'hour', 'day', 'week', 'month', 'year'] as const
const LIST_FIELDS = ['source_id', 'source_country', 'processing_status', 'language', 'entity_id', 'entity_type', 'keyword_id', 'story_country', 'mentioned_country'] as const
const COUNTRY_FIELDS = new Set<ListField>(['source_country', 'story_country', 'mentioned_country'])

export type ListField = typeof LIST_FIELDS[number]
export type Investigation = Required<Omit<InvestigationState, 'after' | 'before' | 'content_available'>> & { after: string | null; before: string | null; content_available: boolean | null }

// Source country keeps its historical `country` URL key so existing bookmarks still open.
const urlKey = (field: ListField) => field === 'source_country' ? 'country' : field
const normalize = (field: ListField, value: string) => COUNTRY_FIELDS.has(field) ? value.trim().toUpperCase() : value.trim()

export function emptyInvestigation(): Investigation {
  return { q: '', source_id: [], source_country: [], after: null, before: null, content_available: null, processing_status: [], language: [], entity_id: [], entity_type: [], keyword_id: [], story_country: [], mentioned_country: [], sort: 'relevance', interval: 'auto' }
}

function values(query: LocationQueryRaw, key: string): string[] {
  const value = query[key]
  return (Array.isArray(value) ? value : [value]).filter((item): item is string => typeof item === 'string' && item.trim() !== '')
}

// List filters also accept hand-written comma-separated values; scalar fields such as `q` keep their commas.
function many(query: LocationQueryRaw, key: string): string[] {
  return values(query, key).flatMap(item => item.split(',')).map(item => item.trim()).filter(Boolean)
}

function one(query: LocationQueryRaw, key: string): string {
  return values(query, key)[0] ?? ''
}

function pick<T extends readonly string[]>(options: T, value: string, fallback: T[number]): T[number] {
  return (options as readonly string[]).includes(value) ? value as T[number] : fallback
}

export function stateFromQuery(query: LocationQueryRaw): Investigation {
  const state = emptyInvestigation()
  for (const field of LIST_FIELDS) state[field] = many(query, urlKey(field)).map(value => normalize(field, value))
  const content = one(query, 'content_available')
  return {
    ...state, q: one(query, 'q'), after: one(query, 'after') || null, before: one(query, 'before') || null,
    content_available: content === 'true' ? true : content === 'false' ? false : null,
    sort: pick(SORTS, one(query, 'sort'), 'relevance'), interval: pick(INTERVALS, one(query, 'interval'), 'auto'),
  }
}

export function queryFromState(state: Investigation): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (state.q) query.q = state.q
  for (const field of LIST_FIELDS) if (state[field].length) query[urlKey(field)] = [...state[field]]
  if (state.after) query.after = state.after
  if (state.before) query.before = state.before
  if (state.content_available !== null) query.content_available = String(state.content_available)
  if (state.sort !== 'relevance') query.sort = state.sort
  if (state.interval !== 'auto') query.interval = state.interval
  return query
}

export function fromSaved(state: InvestigationState): Investigation {
  const restored = emptyInvestigation()
  return { ...restored, ...Object.fromEntries(Object.entries(state).filter(([key, value]) => key in restored && value !== undefined)) }
}

export function searchParams(state: Investigation, options: { interval?: boolean } = {}): Record<string, string | string[]> {
  const params: Record<string, string | string[]> = {}
  if (state.q) params.q = state.q
  for (const field of LIST_FIELDS) if (state[field].length) params[field] = state[field]
  if (state.after) params.after = state.after
  if (state.before) params.before = state.before
  if (state.content_available !== null) params.content_available = String(state.content_available)
  if (options.interval) params.interval = state.interval
  else params.sort = state.sort
  return params
}

export function refine(state: Investigation, field: ListField, value: string): Investigation {
  return { ...state, [field]: [normalize(field, value)] }
}

export type BucketInterval = Exclude<typeof INTERVALS[number], 'auto'>

export function bucketEnd(start: string, interval: BucketInterval): string {
  const date = new Date(start)
  if (interval === 'hour') date.setUTCHours(date.getUTCHours() + 1)
  else if (interval === 'day') date.setUTCDate(date.getUTCDate() + 1)
  else if (interval === 'week') date.setUTCDate(date.getUTCDate() + 7)
  else if (interval === 'month') date.setUTCMonth(date.getUTCMonth() + 1)
  else date.setUTCFullYear(date.getUTCFullYear() + 1)
  return date.toISOString()
}

const DAY_MS = 86_400_000
const isoDay = (ms: number) => new Date(ms).toISOString().slice(0, 10)

/** Converts a brushed bucket span [start, end) to the whole-day, end-exclusive range search accepts. */
export function brushRange(start: string, end: string): { after: string; before: string } {
  const first = Math.floor(Date.parse(start) / DAY_MS) * DAY_MS
  const last = Math.max(Math.ceil(Date.parse(end) / DAY_MS) * DAY_MS, first + DAY_MS)
  return { after: isoDay(first), before: isoDay(last) }
}
