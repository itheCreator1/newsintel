import { expect, it } from 'vitest'
import { communities } from './graph-communities'

const node = (id: string, article_count = 1) => ({ id, text: id.toUpperCase(), type: 'ORG', article_count })
const edge = (source: string, target: string, score: number) => ({ source, target, weight: 2, score, recent_weight: 0 })

it('splits two tight clusters joined by one weak link', () => {
  const nodes = ['a', 'b', 'c', 'x', 'y', 'z'].map((id, index) => node(id, 10 - index))
  const edges = [
    edge('a', 'b', 0.8), edge('b', 'c', 0.7), edge('a', 'c', 0.6),
    edge('x', 'y', 0.8), edge('y', 'z', 0.7), edge('x', 'z', 0.6),
    edge('c', 'x', 0.05),
  ]

  const { group, names } = communities(nodes, edges)

  expect(new Set(['a', 'b', 'c'].map(id => group.get(id))).size).toBe(1)
  expect(new Set(['x', 'y', 'z'].map(id => group.get(id))).size).toBe(1)
  expect(group.get('a')).not.toBe(group.get('x'))
  // Equal sizes rank by the busiest member, and a group is named after it.
  expect(names.slice(0, 2)).toEqual(['A', 'X'])
})

it('keeps an unlinked entity in a group of its own', () => {
  const { group, names } = communities([node('a', 5), node('b', 4), node('lonely', 9)], [edge('a', 'b', 0.5)])

  expect(group.get('a')).toBe(group.get('b'))
  expect(group.get('lonely')).not.toBe(group.get('a'))
  expect(names).toHaveLength(2)
})
