import type { GraphEdge } from './api-types'

/** A connection all of whose articles fall in the graph's recent span: it is new. */
export const isNewEdge = (edge: GraphEdge, recentSince?: string | null) => Boolean(recentSince) && edge.recent_weight > 0 && edge.recent_weight === edge.weight
