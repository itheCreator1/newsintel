import { describe, expect, it } from 'vitest'
import type { InvestigationState } from './api-types'
import { brushRange, bucketEnd, emptyInvestigation, eventHref, fromSaved, queryFromState, refine, searchParams, sourceHref, stateFromQuery } from './investigation'

function params(entries: Record<string, string | string[]>): URLSearchParams {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(entries)) for (const item of Array.isArray(value) ? value : [value]) query.append(key, item)
  return query
}

describe('investigation URL state', () => {
  it('reads every field as the saved-search state shape with array filters', () => {
    const state = stateFromQuery(params({
      q: 'climate', source_id: ['s1', 's2'], country: 'gr', after: '2026-01-01', before: '2026-02-01', content_available: 'true',
      processing_status: 'failed', language: ['en', 'de'], entity_id: 'e1', entity_type: 'ORG', keyword_id: ['k1'],
      story_country: 'de', mentioned_country: 'FR', story_cluster_id: 'cluster-1', sort: 'newest', interval: 'week',
    }))

    expect(state).toEqual({
      q: 'climate', source_id: ['s1', 's2'], source_country: ['GR'], after: '2026-01-01', before: '2026-02-01', content_available: true,
      processing_status: ['failed'], language: ['en', 'de'], entity_id: ['e1'], entity_type: ['ORG'], keyword_id: ['k1'],
      story_country: ['DE'], mentioned_country: ['FR'], story_cluster_id: ['cluster-1'], sort: 'newest', interval: 'week',
    })
    const typed: InvestigationState = state
    expect(typed.sort).toBe('newest')
  })

  it('round-trips through the URL and omits defaults', () => {
    const state = { ...emptyInvestigation(), q: 'grid', source_country: ['US', 'GR'], content_available: false, story_country: ['DE'], interval: 'day' as const }

    const query = queryFromState(state)

    expect(query.toString()).toBe('q=grid&country=US&country=GR&story_country=DE&content_available=false&interval=day')
    expect(stateFromQuery(query)).toEqual(state)
    expect(queryFromState(emptyInvestigation()).toString()).toBe('')
  })

  it('splits comma-separated list values from hand-written URLs', () => {
    expect(stateFromQuery(params({ country: 'gr, us', entity_type: ['ORG,PERSON'], q: 'iran, israel' }))).toMatchObject({ source_country: ['GR', 'US'], entity_type: ['ORG', 'PERSON'], q: 'iran, israel' })
  })

  it('falls back to defaults for unknown sort, interval, and boolean values', () => {
    expect(stateFromQuery(params({ sort: 'random', interval: 'minute', content_available: 'maybe' }))).toEqual(emptyInvestigation())
  })

  it('maps state to API parameters without the timeline interval', () => {
    const state = { ...emptyInvestigation(), q: 'grid', source_country: ['GR'], content_available: true, interval: 'week' as const }

    expect(searchParams(state)).toEqual({ q: 'grid', source_country: ['GR'], content_available: 'true', sort: 'relevance' })
    expect(searchParams(state, { interval: true })).toEqual({ q: 'grid', source_country: ['GR'], content_available: 'true', interval: 'week' })
  })
})

describe('saved investigations', () => {
  it('fills fields a stored state omitted so it restores the exact URL', () => {
    const state = fromSaved({ q: 'grid', source_country: ['GR'], sort: 'newest', interval: 'auto' })

    expect(state).toEqual({ ...emptyInvestigation(), q: 'grid', source_country: ['GR'], sort: 'newest' })
    expect(queryFromState(state).toString()).toBe('q=grid&country=GR&sort=newest')
  })
})

describe('cross-filtering', () => {
  it('narrows one filter to the clicked value and keeps the rest of the investigation', () => {
    const state = { ...emptyInvestigation(), q: 'grid', entity_id: ['e1', 'e2'], after: '2026-01-01' }

    expect(refine(state, 'entity_id', 'e3')).toEqual({ ...state, entity_id: ['e3'] })
    expect(refine(state, 'story_country', 'de').story_country).toEqual(['DE'])
    expect(state.entity_id).toEqual(['e1', 'e2'])
  })

  it('treats story_cluster_id as a UUID list field, not a country field', () => {
    const state = { ...emptyInvestigation(), q: 'grid' }

    expect(refine(state, 'story_cluster_id', 'cluster-1')).toEqual({ ...state, story_cluster_id: ['cluster-1'] })
    expect(queryFromState({ ...state, story_cluster_id: ['cluster-1'] }).toString()).toBe('q=grid&story_cluster_id=cluster-1')
    expect(stateFromQuery(params({ q: 'grid', story_cluster_id: 'cluster-1' })).story_cluster_id).toEqual(['cluster-1'])
  })
})

describe('timeline brushing', () => {
  it('finds the exclusive end of a calendar bucket in UTC', () => {
    expect(bucketEnd('2026-01-31T23:00:00Z', 'hour')).toBe('2026-02-01T00:00:00.000Z')
    expect(bucketEnd('2026-02-28T00:00:00Z', 'day')).toBe('2026-03-01T00:00:00.000Z')
    expect(bucketEnd('2026-12-28T00:00:00Z', 'week')).toBe('2027-01-04T00:00:00.000Z')
    expect(bucketEnd('2026-12-01T00:00:00Z', 'month')).toBe('2027-01-01T00:00:00.000Z')
    expect(bucketEnd('2026-01-01T00:00:00Z', 'year')).toBe('2027-01-01T00:00:00.000Z')
  })

  it('turns a bucket selection into an exclusive whole-day range', () => {
    expect(brushRange('2026-01-05T00:00:00Z', '2026-01-12T00:00:00Z')).toEqual({ after: '2026-01-05', before: '2026-01-12' })
    expect(brushRange('2026-03-01T00:00:00Z', '2026-04-01T00:00:00Z')).toEqual({ after: '2026-03-01', before: '2026-04-01' })
  })

  it('widens hour selections to cover their days so after stays before before', () => {
    expect(brushRange('2026-01-05T08:00:00Z', '2026-01-05T11:00:00Z')).toEqual({ after: '2026-01-05', before: '2026-01-06' })
    expect(brushRange('2026-01-05T22:00:00Z', '2026-01-06T00:00:00Z')).toEqual({ after: '2026-01-05', before: '2026-01-06' })
  })
})

describe('eventHref', () => {
  it('opens the dossier through a query-param route and keeps the origin', () => {
    expect(eventHref('ev-1')).toBe('/events/detail/?id=ev-1')
    expect(eventHref('ev-1', '/events/?status=active')).toBe('/events/detail/?id=ev-1&from=%2Fevents%2F%3Fstatus%3Dactive')
  })
})

describe('sourceHref', () => {
  it('opens the source dossier through a query-param route and keeps the origin', () => {
    expect(sourceHref('s-1')).toBe('/sources/detail/?id=s-1')
    expect(sourceHref('s-1', '/entities/?id=e1')).toBe('/sources/detail/?id=s-1&from=%2Fentities%2F%3Fid%3De1')
  })
})
