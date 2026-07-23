<script setup lang="ts">
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{ title: string; option: EChartsOption; empty?: boolean; height?: number }>(),
  { empty: false, height: 300 },
)
const element = ref<HTMLDivElement>()
use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

let chart: ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!element.value) return
  chart = init(element.value)
  chart.setOption(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value)
})

watch(
  () => props.option,
  (option) => chart?.setOption(option, true),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <section class="card chart-card">
    <div class="chart-card-header">
      <h2>{{ title }}</h2>
      <slot name="header-actions"></slot>
    </div>
    <div v-if="empty" class="empty">Für diesen Zeitraum liegen keine Werte vor.</div>
    <div v-else ref="element" :style="{ height: `${height}px` }" role="img" :aria-label="title"></div>
  </section>
</template>
