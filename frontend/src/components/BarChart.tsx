'use client'

import { BarChart as EchartsBarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

use([EchartsBarChart, GridComponent, TooltipComponent, CanvasRenderer])

const COLOR = 'rgba(79, 125, 251, 0.38)'
const HIGHLIGHT_COLOR = '#7ba0ff'

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
    const categoryAxis = { type: 'category' as const, data: items.map(item => item.label), axisLabel: { color: '#8891ab' }, axisLine: { lineStyle: { color: 'rgba(140, 165, 255, 0.16)' } } }
    const valueAxis = { type: 'value' as const, minInterval: 1, axisLabel: { color: '#8891ab' }, splitLine: { lineStyle: { color: 'rgba(140, 165, 255, 0.08)' } } }
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
            ? { color: HIGHLIGHT_COLOR, shadowBlur: 16, shadowColor: 'rgba(123, 160, 255, 0.55)' }
            : { color: COLOR },
        })),
        barMaxWidth: 28,
        itemStyle: { borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
      }],
    }, true)
  }, [items, orientation, valueLabel])

  return <div ref={elementRef} className="bar-chart" role="img" aria-label={ariaLabel} />
}
