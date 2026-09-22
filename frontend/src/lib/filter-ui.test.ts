import { describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { advancedCount, clearCriteria, filterChips, GRAPH_CHIP_FIELDS, graphHref, isTransient, removeFilter, SEARCH_ADVANCED, SEARCH_CHIP_FIELDS, unsupportedCriteria } from './filter-ui'
import { emptyInvestigation, parseHref, stateFromQuery } from './investigation'

const state = stateFromQuery(new URLSearchParams('q=grid&after=2026-09-01&country=gr&country=GR&entity_id=e1&entity_id=e2&content_available=false&sort=newest&interval=week&story_cluster_id=c1'))

describe('filter chips', () => {
  it('describes applied criteria with explicit labels, deduplicated, falling back to the full ID', () => {
    expect(filterChips(state, SEARCH_CHIP_FIELDS, { entity_id: new Map([['e1', 'Acme']]) }, { content: true })).toEqual([
      { key: 'q', label: 'Query: grid' },
      { key: 'after', label: 'After: 2026-09-01' },
      { key: 'source_country:GR', label: 'Source country: GR' },
      { key: 'entity_id:e1', label: 'Entity: Acme' },
      { key: 'entity_id:e2', label: 'Entity: e2' },
      { key: 'story_cluster_id:c1', label: 'Story: c1' },
      { key: 'content_available', label: 'Content: RSS only' },
    ])
  })

  it('keeps chip keys stable when labels arrive later', () => {
    const keys = (labels = {}) => filterChips(state, SEARCH_CHIP_FIELDS, labels).map(chip => chip.key)
    expect(keys({ entity_id: new Map([['e2', 'Jane']]) })).toEqual(keys())
  })

  it('counts unique advanced values and an explicit content choice, not query, dates or preferences', () => {
    expect(advancedCount(state, SEARCH_ADVANCED, { content: true })).toBe(4)
    expect(advancedCount(emptyInvestigation(), SEARCH_ADVANCED, { content: true })).toBe(0)
  })

  it('removes one criterion, every repeat of it, and nothing else', () => {
    const next = removeFilter(state, 'source_country:GR')
    expect(next.source_country).toEqual([])
    expect({ ...next, source_country: state.source_country }).toEqual(state)
    expect(removeFilter(state, 'content_available').content_available).toBeNull()
    expect(removeFilter(state, 'q').q).toBe('')
  })

  it('clears criteria but keeps sort and interval', () => {
    expect(clearCriteria(state)).toEqual({ ...emptyInvestigation(), sort: 'newest', interval: 'week' })
  })

  it('offers retry only for network and server failures', () => {
    expect(isTransient(new TypeError('Failed to fetch'))).toBe(true)
    expect(isTransient(new ApiError('down', 503))).toBe(true)
    expect(isTransient(new ApiError('bad', 422))).toBe(false)
    expect(isTransient(new ApiError('upgrade', 409))).toBe(false)
  })
})

describe('graph link', () => {
  it('carries the criteria Graph applies, keeps repeated values, and names the ones it drops', () => {
    const search = stateFromQuery(new URLSearchParams('q=grid&after=2026-09-01&country=GR&country=US&entity_type=ORG&entity_id=e1&keyword_id=k1&sort=newest&interval=week&content_available=true'))
    const href = graphHref(search)
    expect(href.startsWith('/graph/?')).toBe(true)
    const query = parseHref(href)
    expect(Object.fromEntries([...new Set(query.keys())].map(key => [key, query.getAll(key)]))).toEqual({ q: ['grid'], country: ['GR', 'US'], entity_type: ['ORG'], after: ['2026-09-01'] })
    expect(unsupportedCriteria(search, GRAPH_CHIP_FIELDS)).toEqual(['Entity', 'Keyword', 'Content'])
  })

  it('names nothing when every applied criterion reaches Graph', () => {
    expect(unsupportedCriteria(stateFromQuery(new URLSearchParams('q=grid&story_country=DE')), GRAPH_CHIP_FIELDS)).toEqual([])
    expect(graphHref(emptyInvestigation())).toBe('/graph/')
  })
})
