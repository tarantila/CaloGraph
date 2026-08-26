<script setup lang="ts">
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsOption } from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { i18n } from '../i18n'

const props = withDefaults(
  defineProps<{ title: string; option: EChartsOption; empty?: boolean; height?: number }>(),
  { empty: false, height: 300 },
)
const t = i18n.global.t.bind(i18n.global)
const element = ref<HTMLDivElement>()
use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

let chart: ECharts | null = null
let observer: ResizeObserver | null = null

function disposeChart() {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
}

function mountChart() {
  if (props.empty || chart || !element.value) return
  chart = init(element.value)
  chart.setOption(props.option)
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(element.value)
}

onMounted(mountChart)

watch(
  () => props.empty,
  (empty) => {
    if (empty) {
      disposeChart()
      return
    }
    void nextTick(mountChart)
  },
)

watch(
  () => props.option,
  (option) => chart?.setOption(option, true),
  { deep: true },
)

onBeforeUnmount(disposeChart)
</script>

<template>
  <section class="card chart-card">
    <div class="chart-card-header">
      <h2>{{ title }}</h2>
      <slot name="header-actions"></slot>
    </div>
    <div v-if="empty" class="empty">{{ t('charts.empty') }}</div>
    <div v-else ref="element" :style="{ height: `${height}px` }" role="img" :aria-label="title"></div>
  </section>
</template>
