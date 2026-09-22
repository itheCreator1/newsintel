import { ApiError } from './api'
import { emptyInvestigation, queryFromState, toHref, type Investigation, type ListField } from './investigation'

export type FilterChip = { key: string; label: string }
/** Display names already loaded by the page, per ID field; a missing name falls back to the full ID. */
export type FilterLabels = Partial<Record<ListField, Map<string, string>>>

const FIELD_NAMES: Record<ListField, string> = {
  source_id: 'Source', source_country: 'Source country', processing_status: 'Processing', language: 'Language', entity_id: 'Entity',
  entity_type: 'Entity type', keyword_id: 'Keyword', story_country: 'Story country', mentioned_country: 'Mentioned country', story_cluster_id: 'Story',
}

/** The fields each page tucks under Advanced filters; `content` counts as one when set explicitly. */
export const SEARCH_ADVANCED: ListField[] = ['source_id', 'source_country', 'language', 'entity_id', 'entity_type', 'keyword_id', 'story_country', 'mentioned_country', 'processing_status']
export const GRAPH_ADVANCED: ListField[] = ['source_id', 'source_country', 'story_country', 'entity_type']
/** Search chips cover every list criterion; Graph only the ones its requests actually send. */
export const SEARCH_CHIP_FIELDS: ListField[] = [...SEARCH_ADVANCED, 'story_cluster_id']
export const GRAPH_CHIP_FIELDS = GRAPH_ADVANCED

const unique = (values: string[]) => [...new Set(values)]

export function advancedCount(state: Investigation, fields: ListField[], { content = false } = {}): number {
  return fields.reduce((total, field) => total + unique(state[field]).length, 0) + (content && state.content_available !== null ? 1 : 0)
}

export function filterChips(state: Investigation, fields: ListField[], labels: FilterLabels = {}, { content = false } = {}): FilterChip[] {
  const chips: FilterChip[] = []
  if (state.q) chips.push({ key: 'q', label: `Query: ${state.q}` })
  if (state.after) chips.push({ key: 'after', label: `After: ${state.after}` })
  if (state.before) chips.push({ key: 'before', label: `Before: ${state.before}` })
  for (const field of fields) {
    for (const value of unique(state[field])) chips.push({ key: `${field}:${value}`, label: `${FIELD_NAMES[field]}: ${labels[field]?.get(value) ?? value}` })
  }
  if (content && state.content_available !== null) chips.push({ key: 'content_available', label: `Content: ${state.content_available ? 'Available' : 'RSS only'}` })
  return chips
}

/** Removes one applied criterion by chip key; every repeat of a list value goes with it. */
export function removeFilter(state: Investigation, key: string): Investigation {
  if (key === 'q') return { ...state, q: '' }
  if (key === 'after' || key === 'before' || key === 'content_available') return { ...state, [key]: null }
  const split = key.indexOf(':')
  const field = key.slice(0, split) as ListField, value = key.slice(split + 1)
  return field in FIELD_NAMES ? { ...state, [field]: state[field].filter(item => item !== value) } : state
}

/** Search's clear-all: every criterion goes, display preferences stay. */
export function clearCriteria(state: Investigation): Investigation {
  return { ...emptyInvestigation(), sort: state.sort, interval: state.interval }
}

/** Graph applies only its own criteria, so its link carries those and nothing Graph would hold invisibly. */
export function graphHref(state: Investigation): string {
  const carried: Investigation = { ...emptyInvestigation(), q: state.q, after: state.after, before: state.before }
  for (const field of GRAPH_ADVANCED) carried[field] = state[field]
  return toHref('/graph', queryFromState(carried))
}

/** Applied Search criteria a view limited to `fields` would not apply, by display name (e.g. ['Entity', 'Content']). */
export function unsupportedCriteria(state: Investigation, fields: ListField[]): string[] {
  const names = SEARCH_CHIP_FIELDS.filter(field => !fields.includes(field) && state[field].length).map(field => FIELD_NAMES[field])
  return state.content_available === null ? names : [...names, 'Content']
}

/** Worth a Retry: the network failed or the server did. Domain answers (4xx, 409 upgrade) are not. */
export function isTransient(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500
}

/** The structured `detail.code` of an API error (`search_upgrade_required`, `restart_search`), or ''. */
export function errorCode(error: unknown): string {
  return error instanceof ApiError && error.detail && typeof error.detail === 'object' && 'code' in error.detail ? String(error.detail.code) : ''
}
