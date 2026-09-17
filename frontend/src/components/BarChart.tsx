'use client'

import { BarChart as EchartsBarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

use([EchartsBarChart, GridComponent, TooltipComponent, CanvasRenderer])

const COLOR = '#63d0c2'
const HIGHLIGHT_COLOR = '#e08a3c'

export interface BarChartItem {
  id: string
  label: string
  value: number
  highlight?: boolean
}

interface Props {
  items: BarChartItem[]
  orientation?: 'vertical' | 'horizontal'
  valueLabel: string
  ariaLabel: string
  onSelect(item: BarChartItem): void
}

export function BarChart({ items, orientation = 'vertical', valueLabel, ariaLabel, onSelect }: Props) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | undefined>(undefined)
  const stateRef = useRef({ items, onSelect })
  stateRef.current = { items, onSelect }

  useEffect(() => {
    const chart = init(elementRef.current!, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('click', (event: { dataIndex?: number }) => {
      if (event.dataIndex === undefined) return
      const { items: currentItems, onSelect: currentOnSelect } = stateRef.current
      const item = currentItems[event.dataIndex]
      if (item) currentOnSelect(item)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current!)
    return () => { observer.disconnect(); chart.dispose() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const horizontal = orientation === 'horizontal'
    const categoryAxis = { type: 'category' as const, data: items.map(item => item.label), axisLabel: { color: '#91a7b4' }, axisLine: { lineStyle: { color: '#35505e' } } }
    const valueAxis = { type: 'value' as const, minInterval: 1, axisLabel: { color: '#91a7b4' }, splitLine: { lineStyle: { color: '#1d313c' } } }
    chart.setOption({
      animation: false,
      grid: { left: horizontal ? 130 : 44, right: 16, top: 16, bottom: horizontal ? 16 : 32, containLabel: horizontal },
      tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} ${valueLabel}` },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
      series: [{
        type: 'bar',
        data: items.map(item => ({ value: item.value, itemStyle: { color: item.highlight ? HIGHLIGHT_COLOR : COLOR } })),
        barMaxWidth: 28,
      }],
    }, true)
  }, [items, orientation, valueLabel])

  return <div ref={elementRef} className="bar-chart" role="img" aria-label={ariaLabel} />
}
