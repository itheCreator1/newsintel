import { describe, expect, it } from 'vitest'
import type { InvestigationState } from './api-types'
import { brushRange, bucketEnd, emptyInvestigation, fromSaved, queryFromState, refine, searchParams, stateFromQuery } from './investigation'

describe('investigation URL state', () => {
  it('reads every field as the saved-search state shape with array filters', () => {
    const state = stateFromQuery({
      q: 'climate', source_id: ['s1', 's2'], country: 'gr', after: '2026-01-01', before: '2026-02-01', content_available: 'true',
      processing_status: 'failed', language: ['en', 'de'], entity_id: 'e1', entity_type: 'ORG', keyword_id: ['k1'],
      story_country: 'de', mentioned_country: 'FR', sort: 'newest', interval: 'week',
    })

    expect(state).toEqual({
      q: 'climate', source_id: ['s1', 's2'], source_country: ['GR'], after: '2026-01-01', before: '2026-02-01', content_available: true,
      processing_status: ['failed'], language: ['en', 'de'], entity_id: ['e1'], entity_type: ['ORG'], keyword_id: ['k1'],
      story_country: ['DE'], mentioned_country: ['FR'], sort: 'newest', interval: 'week',
    })
    const typed: InvestigationState = state
    expect(typed.sort).toBe('newest')
  })

  it('round-trips through the URL and omits defaults', () => {
    const state = { ...emptyInvestigation(), q: 'grid', source_country: ['US', 'GR'], content_available: false, story_country: ['DE'], interval: 'day' as const }

    const query = queryFromState(state)

    expect(query).toEqual({ q: 'grid', country: ['US', 'GR'], content_available: 'false', story_country: ['DE'], interval: 'day' })
    expect(stateFromQuery(query)).toEqual(state)
    expect(queryFromState(emptyInvestigation())).toEqual({})
  })

  it('falls back to defaults for unknown sort, interval, and boolean values', () => {
    expect(stateFromQuery({ sort: 'random', interval: 'minute', content_available: 'maybe' })).toEqual(emptyInvestigation())
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
    expect(queryFromState(state)).toEqual({ q: 'grid', country: ['GR'], sort: 'newest' })
  })
})

describe('cross-filtering', () => {
  it('narrows one filter to the clicked value and keeps the rest of the investigation', () => {
    const state = { ...emptyInvestigation(), q: 'grid', entity_id: ['e1', 'e2'], after: '2026-01-01' }

    expect(refine(state, 'entity_id', 'e3')).toEqual({ ...state, entity_id: ['e3'] })
    expect(refine(state, 'story_country', 'de').story_country).toEqual(['DE'])
    expect(state.entity_id).toEqual(['e1', 'e2'])
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
