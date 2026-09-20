'use client'

import { MapChart } from 'echarts/charts'
import { TooltipComponent, VisualMapComponent } from 'echarts/components'
import { init, registerMap, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import world from '../lib/world.geo.json'

use([MapChart, TooltipComponent, VisualMapComponent, CanvasRenderer])
// The outline is bundled with the app (Natural Earth, public domain), so nothing is fetched.
registerMap('world', world as unknown as Parameters<typeof registerMap>[1])

export interface GeoChartItem {
  code: string
  label: string
  value: number
}

interface Props {
  items: GeoChartItem[]
  selected?: string
  unit: string
  ariaLabel: string
  onSelect(code: string): void
}

export function GeoChart({ items, selected, unit, ariaLabel, onSelect }: Props) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | undefined>(undefined)
  const stateRef = useRef({ items, onSelect })
  stateRef.current = { items, onSelect }

  useEffect(() => {
    const chart = init(elementRef.current!, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('click', (event: { name?: string }) => {
      if (event.name) stateRef.current.onSelect(event.name)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current!)
    return () => { observer.disconnect(); chart.dispose() }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption({
      animation: false,
      tooltip: {
        trigger: 'item',
        formatter: (params: { name: string; value?: number }) => {
          const label = items.find(item => item.code === params.name)?.label ?? params.name
          return `${label}: ${params.value ?? 0} ${unit}`
        },
      },
      visualMap: {
        min: 0, max: Math.max(1, ...items.map(item => item.value)), calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
        text: [unit, ''], textStyle: { color: '#8891ab' }, inRange: { color: ['rgba(79, 125, 251, 0.25)', '#7ba0ff'] },
      },
      series: [{
        type: 'map', map: 'world', nameProperty: 'code', roam: false, selectedMode: 'single', label: { show: false },
        itemStyle: { areaColor: 'rgba(140, 165, 255, 0.06)', borderColor: 'rgba(140, 165, 255, 0.28)' },
        emphasis: { label: { show: false }, itemStyle: { areaColor: '#7ba0ff' } },
        select: { label: { show: false }, itemStyle: { areaColor: '#ffffff' } },
        data: items.map(item => ({ name: item.code, value: item.value, selected: item.code === selected })),
      }],
    }, true)
  }, [items, selected, unit])

  return <div ref={elementRef} className="geo-chart" role="img" aria-label={ariaLabel} />
}
