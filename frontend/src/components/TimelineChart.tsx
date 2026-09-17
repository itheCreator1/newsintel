'use client'

import { BarChart } from 'echarts/charts'
import { BrushComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'
import { bucketEnd } from '../lib/investigation'
import type { SearchTimeline } from '../lib/api-types'

use([BarChart, BrushComponent, GridComponent, TooltipComponent, CanvasRenderer])

interface Props {
  buckets: SearchTimeline['buckets']
  interval: SearchTimeline['interval']
  onSelect(range: { start: string; end: string }): void
}

export function TimelineChart({ buckets, interval, onSelect }: Props) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | undefined>(undefined)
  const stateRef = useRef({ buckets, interval, onSelect })
  stateRef.current = { buckets, interval, onSelect }

  function label(start: string) {
    const date = new Date(start)
    const current = stateRef.current.interval
    if (current === 'hour') return date.toISOString().slice(0, 16).replace('T', ' ')
    if (current === 'month') return date.toISOString().slice(0, 7)
    if (current === 'year') return date.toISOString().slice(0, 4)
    return date.toISOString().slice(0, 10)
  }

  function selectBuckets(first: number, last: number) {
    const { buckets: currentBuckets, interval: currentInterval, onSelect: currentOnSelect } = stateRef.current
    const low = Math.max(0, Math.min(first, last)), high = Math.min(currentBuckets.length - 1, Math.max(first, last))
    if (low > high) return
    currentOnSelect({ start: currentBuckets[low].start, end: bucketEnd(currentBuckets[high].start, currentInterval) })
  }

  useEffect(() => {
    const chart = init(elementRef.current!, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    chart.on('brushEnd', (event: unknown) => {
      const range = (event as { areas?: { coordRange?: number[] }[] }).areas?.[0]?.coordRange
      if (range?.length === 2) selectBuckets(range[0], range[1])
      chart.dispatchAction({ type: 'brush', areas: [] })
    })
    chart.on('click', (event: { dataIndex?: number }) => { if (event.dataIndex !== undefined) selectBuckets(event.dataIndex, event.dataIndex) })
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current!)
    return () => { observer.disconnect(); chart.dispose() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    chart.setOption({
      animation: false,
      grid: { left: 44, right: 16, top: 16, bottom: 32 },
      tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} articles` },
      brush: { xAxisIndex: 0, brushType: 'lineX', brushMode: 'single', throttleType: 'debounce', brushStyle: { color: 'rgba(99, 208, 194, .18)', borderColor: '#63d0c2' } },
      xAxis: { type: 'category', data: buckets.map(bucket => label(bucket.start)), axisLabel: { color: '#91a7b4' }, axisLine: { lineStyle: { color: '#35505e' } } },
      yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#91a7b4' }, splitLine: { lineStyle: { color: '#1d313c' } } },
      series: [{ type: 'bar', data: buckets.map(bucket => bucket.count), itemStyle: { color: '#63d0c2' }, barMaxWidth: 28 }],
    }, true)
    chart.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'lineX', brushMode: 'single' } })
  }, [buckets, interval])

  return <div ref={elementRef} className="timeline-chart" role="img" aria-label={`Article counts per ${interval} across ${buckets.length} buckets. Drag across bars or click one to filter by date.`} />
}
