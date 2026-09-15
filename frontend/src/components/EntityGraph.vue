<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { GraphChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { GraphEdge, GraphNode } from '../api-types'

use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{ nodes: GraphNode[]; edges: GraphEdge[]; focus?: string }>()
const emit = defineEmits<{ select: [id: string] }>()
const element = ref<HTMLDivElement>()
let chart: ECharts | undefined

function render() {
  if (!chart) return
  const categories = [...new Set(props.nodes.map(node => node.type))]
  chart.setOption({
    animation: false,
    tooltip: { formatter: (params: { data?: { name?: string; value?: number } }) => params.data ? `${params.data.name} · ${params.data.value} articles` : '' },
    legend: [{ data: categories, textStyle: { color: '#91a7b4' }, top: 0 }],
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      categories: categories.map(name => ({ name })),
      force: { repulsion: 140, edgeLength: [50, 170] },
      label: { show: true, color: '#e8eef5', position: 'right' },
      lineStyle: { color: '#35505e', curveness: 0.1 },
      data: props.nodes.map(node => ({
        id: node.id,
        name: node.text,
        value: node.article_count,
        symbolSize: Math.max(16, Math.min(60, 12 + node.article_count * 4)),
        category: categories.indexOf(node.type),
        itemStyle: node.id === props.focus ? { borderColor: '#63d0c2', borderWidth: 3 } : undefined,
      })),
      edges: props.edges.map(edge => ({ source: edge.source, target: edge.target, lineStyle: { width: Math.max(1, Math.min(8, edge.weight)) } })),
    }],
  }, true)
}

onMounted(() => {
  chart = init(element.value!, undefined, { renderer: 'canvas' })
  chart.on('click', (event: unknown) => {
    const params = event as { dataType?: string; data?: { id?: string } }
    if (params.dataType === 'node' && params.data?.id) emit('select', params.data.id)
  })
  render()
})
watch(() => [props.nodes, props.edges, props.focus], render)
useResizeObserver(element, () => chart?.resize())
onBeforeUnmount(() => chart?.dispose())
</script>

<template>
  <div ref="element" class="entity-graph" role="img" :aria-label="`Entity co-occurrence graph with ${nodes.length} entities and ${edges.length} connections. Use the entity list below to select one.`" />
</template>
