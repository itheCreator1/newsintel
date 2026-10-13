'use client'

import { BarChart as EchartsBarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use as registerParts, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useEffectEvent, useRef } from 'react'
import { useChartTheme } from '../lib/chart-theme'

// echarts calls this `use`; the alias keeps the hooks lint from taking it for React's use().
registerParts([EchartsBarChart, GridComponent, TooltipComponent, CanvasRenderer])


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
  const chartTheme = useChartTheme()
  const select = useEffectEvent((index: number) => {
    const item = items[index]
    if (item) onSelect(item)
  })

  useEffect(() => {
    const chart = init(elementRef.current!, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('click', (event: { dataIndex?: number }) => {
      if (event.dataIndex !== undefined) select(event.dataIndex)
    })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current!)
    return () => { observer.disconnect(); chart.dispose() }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const horizontal = orientation === 'horizontal'
    const categoryAxis = { type: 'category' as const, data: items.map(item => item.label), axisLabel: { color: chartTheme.axisLabel }, axisLine: { lineStyle: { color: chartTheme.axisLine } } }
    const valueAxis = { type: 'value' as const, minInterval: 1, axisLabel: { color: chartTheme.axisLabel }, splitLine: { lineStyle: { color: chartTheme.splitLine } } }
    chart.setOption({
      animation: false,
      grid: { left: horizontal ? 130 : 44, right: 16, top: 16, bottom: horizontal ? 16 : 32, containLabel: horizontal },
      tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} ${valueLabel}` },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? { ...categoryAxis, inverse: true } : valueAxis,
      series: [{
        type: 'bar',
        data: items.map(item => ({
          value: item.value,
          itemStyle: item.highlight
            ? { color: chartTheme.highlight, shadowBlur: 8, shadowColor: chartTheme.highlightShadow }
            : { color: chartTheme.bar },
        })),
        barMaxWidth: 28,
        itemStyle: { borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
      }],
    }, true)
  }, [items, orientation, valueLabel, chartTheme])

  return <div ref={elementRef} className="bar-chart" role="img" aria-label={ariaLabel} />
}
