import type { GraphEdge, GraphNode } from './api-types'

export interface Communities {
  /** Each node's group, numbered by group size, largest first. */
  group: Map<string, number>
  /** Each group's name: its busiest entity. */
  names: string[]
}

/**
 * Groups entities that are linked more to each other than to the rest, by weighted label
 * propagation: every node keeps taking the group its strongest links carry until nothing moves.
 * Deterministic, so the same graph always colours the same way.
 */
export function communities(nodes: GraphNode[], edges: GraphEdge[], rounds = 20): Communities {
  const order = [...nodes].sort((a, b) => b.article_count - a.article_count || a.id.localeCompare(b.id))
  const label = new Map(order.map((node, index) => [node.id, index]))
  const links = new Map<string, [string, number][]>(order.map(node => [node.id, []]))
  for (const edge of edges) {
    links.get(edge.source)?.push([edge.target, edge.score])
    links.get(edge.target)?.push([edge.source, edge.score])
  }
  for (let round = 0; round < rounds; round++) {
    let changed = false
    for (const node of order) {
      const totals = new Map<number, number>()
      for (const [other, score] of links.get(node.id)!) {
        const theirs = label.get(other)
        if (theirs !== undefined) totals.set(theirs, (totals.get(theirs) ?? 0) + score)
      }
      let best = label.get(node.id)!
      let bestTotal = -1
      for (const [candidate, total] of totals) {
        if (total > bestTotal || (total === bestTotal && candidate < best)) [best, bestTotal] = [candidate, total]
      }
      if (best !== label.get(node.id)) { label.set(node.id, best); changed = true }
    }
    if (!changed) break
  }
  const members = new Map<number, GraphNode[]>()
  for (const node of order) members.set(label.get(node.id)!, [...(members.get(label.get(node.id)!) ?? []), node])
  // `order` is busiest first, so each group's first member is its busiest entity.
  const ranked = [...members.values()].sort((a, b) => b.length - a.length || b[0].article_count - a[0].article_count || a[0].id.localeCompare(b[0].id))
  const group = new Map<string, number>()
  ranked.forEach((nodesInGroup, index) => nodesInGroup.forEach(node => group.set(node.id, index)))
  return { group, names: ranked.map(nodesInGroup => nodesInGroup[0].text) }
}
