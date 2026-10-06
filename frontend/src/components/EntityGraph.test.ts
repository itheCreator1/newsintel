import { expect, it } from 'vitest'
import { graphOption } from './EntityGraph'

const node = (id: string, type: string, article_count: number) => ({ id, text: id.toUpperCase(), type, article_count })
const edge = (source: string, target: string, weight: number, score = weight / 10) => ({ source, target, weight, score })

const nodes = [
  node('a', 'PERSON', 30), node('b', 'ORG', 20), node('c', 'GPE', 10),
  ...Array.from({ length: 12 }, (_, i) => node(`z${i}`, 'OTHER', 1)),
]
const edges = [edge('a', 'b', 8), edge('b', 'c', 2), edge('c', 'z0', 1)]
const series = (input: Partial<Parameters<typeof graphOption>[0]> = {}) => graphOption({ nodes, edges, ...input }).series[0]
const item = (s: ReturnType<typeof series>, id: string) => s.data.find(datum => datum.id === id)!

it('colours each entity type on its category, not the whole series', () => {
  const s = series()
  const colors = s.categories.map(category => category.itemStyle.color)
  expect(new Set(colors).size).toBe(colors.length)
  expect(s.itemStyle).not.toHaveProperty('color')
})

it('labels only the busiest nodes', () => {
  const s = series()
  expect(item(s, 'a').label.show).toBe(true)
  expect(s.data.filter(datum => datum.label.show)).toHaveLength(12)
})

it('dims everything outside the focused node’s neighbourhood', () => {
  const s = series({ focus: 'b' })
  expect(item(s, 'b').itemStyle).toHaveProperty('borderColor')
  expect(item(s, 'a').itemStyle).toEqual({ opacity: 1 })
  expect(item(s, 'z0').itemStyle).toEqual({ opacity: 0.25 })
  expect(s.edges.find(e => e.target === 'z0')!.lineStyle.opacity).toBe(0.05)
})

it('highlights the selected edge and labels its endpoints', () => {
  const s = series({ selectedEdge: 'c:z0' })
  expect(s.edges.find(e => e.target === 'z0')!.lineStyle).toMatchObject({ color: '#7ba0ff', width: 7 })
  expect(item(s, 'z0').label.show).toBe(true)
})

it('draws lines by link strength rather than raw article count', () => {
  const s = graphOption({ nodes, edges: [edge('a', 'b', 40, 0.1), edge('b', 'c', 3, 0.9)] }).series[0]
  const width = (target: string) => s.edges.find(e => e.target === target)!.lineStyle.width!
  expect(width('c')).toBeGreaterThan(width('b'))
})
