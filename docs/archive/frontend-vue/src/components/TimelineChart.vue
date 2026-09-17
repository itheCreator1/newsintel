<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { BarChart } from 'echarts/charts'
import { BrushComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { SearchTimeline } from '../api-types'
import { bucketEnd } from '../investigation'

use([BarChart, BrushComponent, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ buckets: SearchTimeline['buckets']; interval: SearchTimeline['interval'] }>()
const emit = defineEmits<{ select: [range: { start: string; end: string }] }>()
const element = ref<HTMLDivElement>()
let chart: ECharts | undefined

const label = (start: string) => {
  const date = new Date(start)
  if (props.interval === 'hour') return date.toISOString().slice(0, 16).replace('T', ' ')
  if (props.interval === 'month') return date.toISOString().slice(0, 7)
  if (props.interval === 'year') return date.toISOString().slice(0, 4)
  return date.toISOString().slice(0, 10)
}

function selectBuckets(first: number, last: number) {
  const low = Math.max(0, Math.min(first, last)), high = Math.min(props.buckets.length - 1, Math.max(first, last))
  if (low > high) return
  emit('select', { start: props.buckets[low].start, end: bucketEnd(props.buckets[high].start, props.interval) })
}

function render() {
  if (!chart) return
  chart.setOption({
    animation: false,
    grid: { left: 44, right: 16, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis', valueFormatter: (value: unknown) => `${value} articles` },
    brush: { xAxisIndex: 0, brushType: 'lineX', brushMode: 'single', throttleType: 'debounce', brushStyle: { color: 'rgba(99, 208, 194, .18)', borderColor: '#63d0c2' } },
    xAxis: { type: 'category', data: props.buckets.map(bucket => label(bucket.start)), axisLabel: { color: '#91a7b4' }, axisLine: { lineStyle: { color: '#35505e' } } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#91a7b4' }, splitLine: { lineStyle: { color: '#1d313c' } } },
    series: [{ type: 'bar', data: props.buckets.map(bucket => bucket.count), itemStyle: { color: '#63d0c2' }, barMaxWidth: 28 }],
  }, true)
  chart.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: 'lineX', brushMode: 'single' } })
}

onMounted(() => {
  chart = init(element.value!, undefined, { renderer: 'canvas' })
  chart.on('brushEnd', (event: unknown) => {
    const range = (event as { areas?: { coordRange?: number[] }[] }).areas?.[0]?.coordRange
    if (range?.length === 2) selectBuckets(range[0], range[1])
    chart?.dispatchAction({ type: 'brush', areas: [] })
  })
  chart.on('click', (event: { dataIndex?: number }) => { if (event.dataIndex !== undefined) selectBuckets(event.dataIndex, event.dataIndex) })
  render()
})
watch(() => [props.buckets, props.interval], render)
useResizeObserver(element, () => chart?.resize())
onBeforeUnmount(() => chart?.dispose())
</script>

<template>
  <div ref="element" class="timeline-chart" role="img" :aria-label="`Article counts per ${interval} across ${buckets.length} buckets. Drag across bars or click one to filter by date.`" />
</template>
