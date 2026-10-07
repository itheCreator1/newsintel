import { expect, it } from 'vitest'
import { isNewEdge } from './graph-edges'

const edge = (weight: number, recent_weight: number) => ({ source: 'a', target: 'b', weight, score: 0.5, recent_weight })
const since = '2026-10-01T00:00:00Z'

it('marks a link new when every one of its articles is recent', () => {
  expect(isNewEdge(edge(3, 3), since)).toBe(true)
})

it('leaves a link with older articles unmarked', () => {
  expect(isNewEdge(edge(3, 2), since)).toBe(false)
})

it('leaves a link with no recent articles unmarked', () => {
  expect(isNewEdge(edge(0, 0), since)).toBe(false)
})

it.each([undefined, null, ''])('marks nothing new when the graph has no recent span (%s)', recentSince => {
  expect(isNewEdge(edge(3, 3), recentSince)).toBe(false)
})
