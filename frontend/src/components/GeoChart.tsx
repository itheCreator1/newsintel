'use client'

import { MapChart } from 'echarts/charts'
import { TooltipComponent, VisualMapComponent } from 'echarts/components'
import { init, registerMap, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import world from '../lib/world.geo.json'
import { chartTheme } from '../lib/chart-theme'

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
  const pendingRef = useRef<Parameters<ECharts['setOption']>[0] | undefined>(undefined)
  const syncRef = useRef(() => {})
  const stateRef = useRef({ items, onSelect })
  stateRef.current = { items, onSelect }

  useEffect(() => {
    const element = elementRef.current!
    const chart = init(element, undefined, { renderer: 'canvas' })
    chart.on('click', (event: { name?: string }) => {
      if (event.name) stateRef.current.onSelect(event.name)
    })
    // ECharts 6.1 lays a map out by inverting its view transform and throws on the singular one a
    // box with no width or no height gives. A container reports that size while hidden and for a
    // moment after it leaves the page, so the chart is drawn and resized only while it has area;
    // an option that arrives meanwhile waits here.
    syncRef.current = () => {
      if (element.clientWidth === 0 || element.clientHeight === 0) return
      if (chart.getWidth() !== element.clientWidth || chart.getHeight() !== element.clientHeight) chart.resize()
      if (pendingRef.current) {
        chart.setOption(pendingRef.current, true)
        pendingRef.current = undefined
      }
    }
    const observer = new ResizeObserver(() => syncRef.current())
    observer.observe(element)
    return () => { observer.disconnect(); syncRef.current = () => {}; chart.dispose() }
  }, [])

  useEffect(() => {
    pendingRef.current = {
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
        text: [unit, ''], textStyle: { color: chartTheme.axisLabel }, inRange: { color: [chartTheme.mapLow, chartTheme.highlight] },
      },
      series: [{
        type: 'map', map: 'world', nameProperty: 'code', roam: false, selectedMode: 'single', label: { show: false },
        itemStyle: { areaColor: chartTheme.mapArea, borderColor: chartTheme.mapBorder },
        emphasis: { label: { show: false }, itemStyle: { areaColor: chartTheme.mapHover } },
        select: { label: { show: false }, itemStyle: { areaColor: chartTheme.mapSelected } },
        data: items.map(item => ({ name: item.code, value: item.value, selected: item.code === selected })),
      }],
    }
    syncRef.current()
  }, [items, selected, unit])

  return <div ref={elementRef} className="geo-chart" role="img" aria-label={ariaLabel} />
}
