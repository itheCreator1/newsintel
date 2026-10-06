'use client'

import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import type { GraphEdge, GraphNode } from '../lib/api-types'
import { chartTheme, entityColors, groupColors } from '../lib/chart-theme'
import { communities } from '../lib/graph-communities'
import { isNewEdge } from '../lib/graph-edges'

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const TYPE_COLORS = entityColors
const FALLBACK_COLOR = entityColors.OTHER
const HIGHLIGHT = chartTheme.highlight
/** Group colours, largest group first; smaller groups share the fallback grey as "Other groups". */
const GROUP_COLORS = groupColors
const OTHER_GROUPS = 'Other groups'
/** Connections whose every article falls in the recent span. */
const NEW_EDGE = chartTheme.newEdge
/** Only the busiest nodes carry a permanent label; hovering reveals the rest. */
const LABELLED_NODES = 12

const edgeKey = (a: string, b: string) => [a, b].sort().join(':')
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))

export type ColourBy = 'type' | 'group'

interface OptionInput {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focus?: string
  selectedEdge?: string
  colourBy?: ColourBy
  recentSince?: string | null
}

function colouring(nodes: GraphNode[], edges: GraphEdge[], colourBy: ColourBy) {
  if (colourBy === 'type') {
    const names = [...new Set(nodes.map(node => node.type))]
    return { names, colours: names.map(name => TYPE_COLORS[name] ?? FALLBACK_COLOR), of: (node: GraphNode) => names.indexOf(node.type) }
  }
  const { group, names: groupNames } = communities(nodes, edges)
  const named = groupNames.slice(0, GROUP_COLORS.length)
  const names = groupNames.length > named.length ? [...named.map(name => `${name} group`), OTHER_GROUPS] : named.map(name => `${name} group`)
  return {
    names,
    colours: names.map((_, index) => GROUP_COLORS[index] ?? FALLBACK_COLOR),
    of: (node: GraphNode) => Math.min(group.get(node.id) ?? named.length, named.length),
  }
}

export function graphOption({ nodes, edges, focus, selectedEdge, colourBy = 'type', recentSince }: OptionInput) {
  const { names: categories, colours, of: categoryOf } = colouring(nodes, edges, colourBy)
  // Fewer nodes get more room; a crowded graph is pulled tighter so it still fits the panel.
  const crowded = nodes.length > 40
  const names = new Map(nodes.map(node => [node.id, node.text]))
  // Lines are drawn by link strength, not raw count, so busy entities don't swamp the picture.
  const maxScore = Math.max(Number.EPSILON, ...edges.map(edge => edge.score))
  const labelled = new Set([...nodes].sort((a, b) => b.article_count - a.article_count).slice(0, LABELLED_NODES).map(node => node.id))
  const selected = edges.find(edge => edgeKey(edge.source, edge.target) === selectedEdge)
  const endpoints = new Set(selected ? [selected.source, selected.target] : [])
  const neighbourhood = focus && names.has(focus)
    ? new Set([focus, ...edges.flatMap(edge => edge.source === focus ? [edge.target] : edge.target === focus ? [edge.source] : [])])
    : undefined
  const dimmed = (id: string) => Boolean(neighbourhood && !neighbourhood.has(id) && !endpoints.has(id))

  return {
    animation: false,
    // richText mode renders the formatter's returned string as escaped text rather than innerHTML, so
    // spaCy-extracted entity text that happens to contain HTML-like characters can't be interpreted as markup.
    tooltip: {
      renderMode: 'richText',
      formatter: (params: { dataType?: string; data?: { name?: string; type?: string; value?: number; weight?: number; isNew?: boolean; source?: string; target?: string } }) => {
        const data = params.data
        if (!data) return ''
        if (params.dataType === 'node') return `${data.name} (${data.type}) · ${data.value} articles`
        if (params.dataType === 'edge') return `${names.get(data.source!)} — ${names.get(data.target!)} · ${data.weight} articles · ${Math.round((data.value ?? 0) * 100)}% overlap${data.isNew ? ' · new' : ''}`
        return ''
      },
    },
    legend: [{ type: 'scroll', data: categories, icon: 'circle', itemWidth: 10, itemHeight: 10, textStyle: { color: chartTheme.axisLabel }, pageTextStyle: { color: chartTheme.axisLabel }, top: 0 }],
    series: [{
      type: 'graph',
      layout: 'force',
      top: 36,
      roam: true,
      draggable: true,
      categories: categories.map((name, index) => ({ name, itemStyle: { color: colours[index] } })),
      // A static layout: labelLayout.hideOverlap only runs once, so an animated settle would leave
      // labels overlapping where nodes end up (and it would ignore prefers-reduced-motion).
      // An edge's value is its link strength, so strongly linked pairs sit closer together.
      force: { repulsion: clamp(Math.round(7000 / Math.max(nodes.length, 1)), 90, 320), gravity: crowded ? 0.24 : 0.14, edgeLength: crowded ? [35, 120] : [55, 170], friction: 0.2, layoutAnimation: false },
      label: { show: false, position: 'right', color: chartTheme.labelText, fontSize: 12, overflow: 'truncate', width: 120, textBorderColor: chartTheme.labelHalo, textBorderWidth: 3 },
      labelLayout: { hideOverlap: true },
      lineStyle: { color: chartTheme.edge, curveness: 0.1 },
      itemStyle: { borderColor: chartTheme.nodeBorder, borderWidth: 1 },
      emphasis: { focus: 'adjacency', label: { show: true }, lineStyle: { opacity: 0.9 } },
      blur: { itemStyle: { opacity: 0.15 }, lineStyle: { opacity: 0.05 }, label: { show: false } },
      data: nodes.map(node => {
        const isFocus = node.id === focus
        const highlighted = isFocus || endpoints.has(node.id)
        return {
          id: node.id,
          name: node.text,
          type: node.type,
          value: node.article_count,
          symbolSize: clamp(10 + 6 * Math.sqrt(node.article_count), 12, 56),
          category: categoryOf(node),
          label: { show: highlighted || (labelled.has(node.id) && !dimmed(node.id)), fontWeight: highlighted ? 600 : 400 },
          itemStyle: highlighted
            ? { borderColor: HIGHLIGHT, borderWidth: 3, shadowBlur: 14, shadowColor: chartTheme.highlightShadow }
            : { opacity: dimmed(node.id) ? 0.25 : 1 },
        }
      }),
      edges: edges.map(edge => {
        const ratio = Math.sqrt(edge.score / maxScore)
        const isSelected = edge === selected
        const faded = neighbourhood && !isSelected && edge.source !== focus && edge.target !== focus
        const isNew = isNewEdge(edge, recentSince)
        return {
          source: edge.source,
          target: edge.target,
          value: edge.score,
          weight: edge.weight,
          isNew,
          lineStyle: isSelected
            ? { color: HIGHLIGHT, width: 7, opacity: 1 }
            : { width: 1 + 5 * ratio, opacity: faded ? 0.05 : isNew ? 0.55 + 0.4 * ratio : 0.15 + 0.45 * ratio, ...(isNew ? { color: NEW_EDGE, type: 'dashed' as const } : {}) },
        }
      }),
    }],
  }
}

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focus?: string
  selectedEdge?: string
  colourBy?: ColourBy
  recentSince?: string | null
  onSelect(id: string): void
  onSelectEdge(source: string, target: string): void
}

export function EntityGraph({ nodes, edges, focus, selectedEdge, colourBy, recentSince, onSelect, onSelectEdge }: Props) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | undefined>(undefined)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect
  const onSelectEdgeRef = useRef(onSelectEdge)
  onSelectEdgeRef.current = onSelectEdge

  useEffect(() => {
    const chart = init(elementRef.current!, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('click', (event: unknown) => {
      const params = event as { dataType?: string; data?: { id?: string; source?: string; target?: string } }
      if (params.dataType === 'node' && params.data?.id) onSelectRef.current(params.data.id)
      if (params.dataType === 'edge' && params.data?.source && params.data.target) onSelectEdgeRef.current(params.data.source, params.data.target)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current!)
    return () => { observer.disconnect(); chart.dispose() }
  }, [])

  useEffect(() => {
    // Merge rather than replace: the series model keeps its node positions by id, so selecting a node
    // restyles the graph in place, and expanding one adds its neighbours around the nodes already drawn.
    chartRef.current?.setOption(graphOption({ nodes, edges, focus, selectedEdge, colourBy, recentSince }))
  }, [nodes, edges, focus, selectedEdge, colourBy, recentSince])

  return <div ref={elementRef} className="entity-graph" role="img" aria-label={`Entity co-occurrence graph with ${nodes.length} entities and ${edges.length} connections. Use the entity and connection lists below to select one.`} />
}
