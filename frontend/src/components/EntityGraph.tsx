'use client'

import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import type { GraphEdge, GraphNode } from '../lib/api-types'

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  focus?: string
  onSelect(id: string): void
  onSelectEdge(source: string, target: string): void
}

export function EntityGraph({ nodes, edges, focus, onSelect, onSelectEdge }: Props) {
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
    const chart = chartRef.current
    if (!chart) return
    const categories = [...new Set(nodes.map(node => node.type))]
    chart.setOption({
      animation: false,
      // richText mode renders the formatter's returned string as escaped text rather than innerHTML, so
      // spaCy-extracted entity text that happens to contain HTML-like characters can't be interpreted as markup.
      tooltip: { renderMode: 'richText', formatter: (params: { dataType?: string; data?: { name?: string; value?: number } }) => params.dataType === 'node' && params.data ? `${params.data.name} · ${params.data.value} articles` : '' },
      legend: [{ data: categories, textStyle: { color: '#8891ab' }, top: 0 }],
      series: [{
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: categories.map(name => ({ name })),
        force: { repulsion: 140, edgeLength: [50, 170] },
        label: { show: true, color: '#e9edfb', position: 'right' },
        lineStyle: { color: 'rgba(140, 165, 255, 0.2)', curveness: 0.1 },
        itemStyle: { color: '#4f7dfb' },
        data: nodes.map(node => ({
          id: node.id,
          name: node.text,
          value: node.article_count,
          symbolSize: Math.max(16, Math.min(60, 12 + node.article_count * 4)),
          category: categories.indexOf(node.type),
          itemStyle: node.id === focus ? { borderColor: '#7ba0ff', borderWidth: 3, shadowBlur: 12, shadowColor: 'rgba(123, 160, 255, 0.6)' } : undefined,
        })),
        edges: edges.map(edge => ({ source: edge.source, target: edge.target, lineStyle: { width: Math.max(1, Math.min(8, edge.weight)) } })),
      }],
    }, true)
  }, [nodes, edges, focus])

  return <div ref={elementRef} className="entity-graph" role="img" aria-label={`Entity co-occurrence graph with ${nodes.length} entities and ${edges.length} connections. Use the entity and connection lists below to select one.`} />
}
