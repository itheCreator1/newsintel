'use client'

import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import type { GraphEdge, GraphNode } from '../lib/api-types'

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const TYPE_COLORS: Record<string, string> = {
  PERSON: '#7ba0ff', ORG: '#63d0c2', GPE: '#f2b35e', COUNTRY: '#e8795f',
  LOCATION: '#b48cf2', EVENT: '#f07fae', PRODUCT: '#9fd36b', OTHER: '#8891ab',
}
const FALLBACK_COLOR = '#8891ab'
const HIGHLIGHT = '#7ba0ff'
/** Only the busiest nodes carry a permanent label; hovering reveals the rest. */
const LABELLED_NODES = 12

const edgeKey = (a: string, b: string) => [a, b].sort().join(':')
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value))

interface OptionInput {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focus?: string
  selectedEdge?: string
}

export function graphOption({ nodes, edges, focus, selectedEdge }: OptionInput) {
  const categories = [...new Set(nodes.map(node => node.type))]
  const names = new Map(nodes.map(node => [node.id, node.text]))
  const maxWeight = Math.max(1, ...edges.map(edge => edge.weight))
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
      formatter: (params: { dataType?: string; data?: { name?: string; type?: string; value?: number; source?: string; target?: string } }) => {
        const data = params.data
        if (!data) return ''
        if (params.dataType === 'node') return `${data.name} (${data.type}) · ${data.value} articles`
        if (params.dataType === 'edge') return `${names.get(data.source!)} — ${names.get(data.target!)} · ${data.value} articles`
        return ''
      },
    },
    legend: [{ type: 'scroll', data: categories, icon: 'circle', itemWidth: 10, itemHeight: 10, textStyle: { color: '#8891ab' }, pageTextStyle: { color: '#8891ab' }, top: 0 }],
    series: [{
      type: 'graph',
      layout: 'force',
      top: 36,
      roam: true,
      draggable: true,
      categories: categories.map(name => ({ name, itemStyle: { color: TYPE_COLORS[name] ?? FALLBACK_COLOR } })),
      // A static layout: labelLayout.hideOverlap only runs once, so an animated settle would leave
      // labels overlapping where nodes end up (and it would ignore prefers-reduced-motion).
      force: { repulsion: 160, gravity: 0.18, edgeLength: [50, 150], friction: 0.2, layoutAnimation: false },
      label: { show: false, position: 'right', color: '#e9edfb', fontSize: 12, overflow: 'truncate', width: 120, textBorderColor: 'rgba(8, 11, 22, 0.85)', textBorderWidth: 3 },
      labelLayout: { hideOverlap: true },
      lineStyle: { color: 'rgba(140, 165, 255, 1)', curveness: 0.1 },
      itemStyle: { borderColor: 'rgba(8, 11, 22, 0.6)', borderWidth: 1 },
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
          category: categories.indexOf(node.type),
          label: { show: highlighted || (labelled.has(node.id) && !dimmed(node.id)), fontWeight: highlighted ? 600 : 400 },
          itemStyle: highlighted
            ? { borderColor: HIGHLIGHT, borderWidth: 3, shadowBlur: 14, shadowColor: 'rgba(123, 160, 255, 0.6)' }
            : { opacity: dimmed(node.id) ? 0.25 : 1 },
        }
      }),
      edges: edges.map(edge => {
        const ratio = Math.sqrt(edge.weight / maxWeight)
        const isSelected = edge === selected
        const faded = neighbourhood && !isSelected && edge.source !== focus && edge.target !== focus
        return {
          source: edge.source,
          target: edge.target,
          value: edge.weight,
          lineStyle: isSelected
            ? { color: HIGHLIGHT, width: 7, opacity: 1 }
            : { width: 1 + 5 * ratio, opacity: faded ? 0.05 : 0.15 + 0.45 * ratio },
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
  onSelect(id: string): void
  onSelectEdge(source: string, target: string): void
}

export function EntityGraph({ nodes, edges, focus, selectedEdge, onSelect, onSelectEdge }: Props) {
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
    // Merge rather than replace: the series model keeps its node positions, so selecting a node or
    // edge restyles the graph in place instead of re-running the layout from scratch.
    chartRef.current?.setOption(graphOption({ nodes, edges, focus, selectedEdge }))
  }, [nodes, edges, focus, selectedEdge])

  return <div ref={elementRef} className="entity-graph" role="img" aria-label={`Entity co-occurrence graph with ${nodes.length} entities and ${edges.length} connections. Use the entity and connection lists below to select one.`} />
}
