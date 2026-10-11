import type { AuthorityFilters, EntityType } from './api-types'
import { toHref } from './investigation'

/** The types a root can be given, in the order the pickers offer them. */
export const ENTITY_TYPES: EntityType[] = ['PERSON', 'ORG', 'GPE', 'LOCATION', 'EVENT', 'PRODUCT', 'TOPIC', 'OTHER']
const TYPE_LABELS: Record<EntityType, string> = {
  PERSON: 'Person', ORG: 'Organization', GPE: 'Country or city', LOCATION: 'Place', EVENT: 'Event', PRODUCT: 'Product', TOPIC: 'Topic', OTHER: 'Other',
}

/** A type in words; a type the app does not offer (an older NER label) stays as it is. */
export function typeLabel(type: string): string {
  return TYPE_LABELS[type as EntityType] ?? type
}

export const AUTHORITY_TABS = ['names', 'suggestions', 'wikidata', 'changes'] as const
export type AuthorityTab = (typeof AUTHORITY_TABS)[number]
const STATUSES = ['provisional', 'established'] as const
const WIKIDATA = ['linked', 'unlinked'] as const
const SORTS = ['articles', 'name'] as const

export interface AuthoritiesView {
  tab: AuthorityTab
  q: string
  language: string
  status?: (typeof STATUSES)[number]
  wikidata?: (typeof WIKIDATA)[number]
  type: EntityType[]
  sort: (typeof SORTS)[number]
  hidden: boolean
  /** The root open in the side panel. */
  entity?: string
}

const pick = <T extends string>(options: readonly T[], value: string | null): T | undefined =>
  options.includes(value as T) ? value as T : undefined

/** The page's view from its URL; anything the API would refuse falls back to the default. */
export function readAuthoritiesQuery(query: URLSearchParams): AuthoritiesView {
  return {
    tab: pick(AUTHORITY_TABS, query.get('tab')) ?? 'names',
    q: query.get('q') ?? '',
    language: query.get('language') ?? '',
    status: pick(STATUSES, query.get('status')),
    wikidata: pick(WIKIDATA, query.get('wikidata')),
    type: query.getAll('type').filter((value): value is EntityType => ENTITY_TYPES.includes(value as EntityType)),
    sort: pick(SORTS, query.get('sort')) ?? 'articles',
    hidden: query.get('hidden') === '1',
    entity: query.get('entity') || undefined,
  }
}

/** Authority file link; the tab, every filter and the open name are in the URL, so a view can be bookmarked. */
export function authoritiesHref({ tab, q, language, status, wikidata, type, sort, hidden, entity }: Partial<AuthoritiesView>): string {
  const query = new URLSearchParams()
  if (tab && tab !== 'names') query.set('tab', tab)
  if (q) query.set('q', q)
  if (language) query.set('language', language)
  if (status) query.set('status', status)
  if (wikidata) query.set('wikidata', wikidata)
  for (const value of type ?? []) query.append('type', value)
  if (sort && sort !== 'articles') query.set('sort', sort)
  if (hidden) query.set('hidden', '1')
  if (entity) query.set('entity', entity)
  return toHref('/authorities', query)
}

/** What the list asks the API for: the Names tab's filters, trimmed, absent when empty. */
export function authorityFilters({ q, language, status, wikidata, type, sort, hidden }: AuthoritiesView): AuthorityFilters {
  return {
    ...(q.trim() ? { q: q.trim() } : {}),
    ...(language.trim() ? { language: language.trim() } : {}),
    ...(status ? { status } : {}),
    ...(wikidata ? { wikidata } : {}),
    ...(type.length ? { type } : {}),
    sort,
    ...(hidden ? { hidden } : {}),
  }
}
