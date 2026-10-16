<script setup lang="ts">
import { graphic, type EChartsOption } from 'echarts'
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
function withAlpha(color: string, alpha: number): string {
  const match = /^#([0-9a-f]{6})$/i.exec(color)
  if (!match) return color
  const value = Number.parseInt(match[1]!, 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`
}

const chartData = computed<Array<[number, number]>>(() => {
  const dateRange = props.dateRange
  return props.points
    .filter((point) => !dateRange || (point.date >= dateRange.start && point.date <= dateRange.end))
    .sort((left, right) => left.date.localeCompare(right.date))
    .map((point): [number, number] => [
      Date.parse(`${point.date}T00:00:00Z`), point.weight_kg,
    ])
})
const yDomain = computed(() => {
  let minimum = Number.POSITIVE_INFINITY
  let maximum = Number.NEGATIVE_INFINITY
  for (const [, value] of chartData.value) {
    minimum = Math.min(minimum, value)
    maximum = Math.max(maximum, value)
  }
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum)) return null
  const padding = Math.max((maximum - minimum) * 0.12, 0.5)
  return { minimum: minimum - padding, maximum: maximum + padding }
})
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
      min: yDomain.value?.minimum,
      max: yDomain.value?.maximum,
      nameTextStyle: { color: palette.label },
      axisLabel: { color: palette.label },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: t('analysisV2.weightTitle'),
      type: 'line',
      smooth: 0.4,
      smoothMonotone: 'x',
      connectNulls: true,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 8,
      data: chartData.value,
      lineStyle: { color: palette.violet, width: 2, cap: 'round', join: 'round' },
      itemStyle: { color: palette.violet },
      areaStyle: {
        color: new graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(palette.violet, 0.24) },
          { offset: 0.6, color: withAlpha(palette.violet, 0.14) },
          { offset: 1, color: withAlpha(palette.violet, 0.04) },
        ], false),
      },
    }],
  }
})
</script>

<template>
  <ChartPanel :title="title ?? t('analysisV2.weightHistory')" :option="chartOption" :height="height" :empty="!points.length" />
</template>
