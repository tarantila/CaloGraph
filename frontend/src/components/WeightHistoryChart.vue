<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed } from 'vue'

import { chartColors } from '../chart-theme'
import { formatGermanDate } from '../date-format'
import { i18n } from '../i18n'
import type { WeightPoint } from '../types'
import ChartPanel from './ChartPanel.vue'

const props = withDefaults(defineProps<{
  points: readonly WeightPoint[]
  title?: string
  height?: number
  dateRange?: { start: string; end: string }
}>(), { title: undefined, height: 300, dateRange: undefined })
const t = i18n.global.t.bind(i18n.global)
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  return {
    backgroundColor: palette.background,
    grid: { left: 12, right: 18, top: 32, bottom: 16, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
    },
    xAxis: {
      type: 'time',
      min: props.dateRange ? Date.parse(`${props.dateRange.start}T00:00:00Z`) : undefined,
      max: props.dateRange ? Date.parse(`${props.dateRange.end}T00:00:00Z`) : undefined,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: {
        color: palette.label,
        hideOverlap: true,
        formatter: (value: string | number) => formatGermanDate(new Date(value).toISOString().slice(0, 10)),
      },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: palette.axis } },
      name: 'kg',
      scale: true,
      nameTextStyle: { color: palette.label },
      axisLabel: { color: palette.label },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: t('analysisV2.weightTitle'),
      type: 'line',
      smooth: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 8,
      data: props.points.map((point) => [Date.parse(`${point.date}T00:00:00Z`), point.weight_kg]),
      lineStyle: { color: palette.violet, width: 2 },
      itemStyle: { color: palette.violet },
      areaStyle: { color: palette.violet, opacity: 0.12 },
    }],
  }
})
</script>

<template>
  <ChartPanel :title="title ?? t('analysisV2.weightHistory')" :option="chartOption" :height="height" :empty="!points.length" />
</template>
