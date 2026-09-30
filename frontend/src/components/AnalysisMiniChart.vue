<script setup lang="ts">
import { computed } from 'vue'

type AnalysisMiniChartColorRole = 'violet' | 'teal' | 'blue' | 'amber' | 'emerald' | 'lilac'
type MacroKey = 'protein_g' | 'carbohydrates_g' | 'fat_g'
type MacroSeries = { key: MacroKey; values: readonly (number | null)[]; colorRole: 'teal' | 'blue' | 'amber' }

const props = defineProps<{
  values: readonly (number | null)[]
  dates?: readonly (string | null)[]
  dateRange?: { start: string; end: string }
  label: string
  variant: 'line' | 'bars' | 'stacked-bars'
  colorRole: AnalysisMiniChartColorRole
  series?: readonly MacroSeries[]
}>()

const chartColor = computed(() => `var(--chart-series-${props.colorRole})`)
const values = computed(() => props.values.map((value) => (
  typeof value === 'number' && Number.isFinite(value) ? value : null
)))
const numericValues = computed(() => values.value.filter((value): value is number => value !== null))
const bounds = computed(() => ({
  min: numericValues.value.length ? Math.min(...numericValues.value) : 0,
  max: numericValues.value.length ? Math.max(...numericValues.value) : 0,
}))
const dateRangeBounds = computed(() => {
  if (!props.dateRange) return null
  const start = Date.parse(`${props.dateRange.start.slice(0, 10)}T00:00:00Z`)
  const end = Date.parse(`${props.dateRange.end.slice(0, 10)}T00:00:00Z`)
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? { start, end } : null
})
function xPosition(index: number): number {
  const date = props.dates?.[index]
  const bounds = dateRangeBounds.value
  if (date && bounds) {
    const timestamp = Date.parse(`${date.slice(0, 10)}T00:00:00Z`)
    if (Number.isFinite(timestamp)) {
      const span = bounds.end - bounds.start
      return span === 0 ? 60 : Math.max(0, Math.min(120, ((timestamp - bounds.start) / span) * 120))
    }
  }
  return values.value.length === 1 ? 60 : (index * 120) / (values.value.length - 1)
}
const lineGeometry = computed(() => {
  const { min, max } = bounds.value
  const span = max - min || 1
  const segments: string[][] = []
  const markers: { x: number; y: number }[] = []
  let segment: string[] = []
  let singleton: { x: number; y: number; value: number } | null = null
  const finishSegment = () => {
    if (segment.length > 1) segments.push(segment)
    else if (singleton && singleton.value !== 0) markers.push(singleton)
    segment = []
    singleton = null
  }
  values.value.forEach((value, index) => {
    if (value === null) {
      finishSegment()
      return
    }
    const x = xPosition(index)
    const y = 32 - ((value - min) / span) * 26
    segment.push(`${x},${y}`)
    if (segment.length === 1) singleton = { x, y, value }
    if (value === 0) markers.push({ x, y })
  })
  finishSegment()
  return { segments, markers }
})
const bars = computed(() => {
  const { min, max } = bounds.value
  const span = Math.max(Math.abs(min), Math.abs(max), 1)
  const range = dateRangeBounds.value
  const slots = range ? Math.floor((range.end - range.start) / 86_400_000) + 1 : values.value.length
  const slotWidth = 120 / Math.max(slots, 1)
  const width = Math.max(2, slotWidth * 0.65)
  return values.value.flatMap((value, index) => {
    if (value === null) return []
    const height = Math.abs(value) / span * 28
    const date = props.dates?.[index]
    const timestamp = date ? Date.parse(`${date.slice(0, 10)}T00:00:00Z`) : Number.NaN
    const offset = range && Number.isFinite(timestamp) ? (timestamp - range.start) / 86_400_000 : index
    const center = (offset + 0.5) * slotWidth
    const x = Math.max(0, Math.min(120 - width, center - width / 2))
    return [{ x, y: 34 - height, width, height }]
  })
})
const stackedBars = computed(() => {
  const series = props.series ?? []
  const length = Math.max(props.values.length, ...series.map(({ values }) => values.length), 0)
  const totals = Array.from({ length }, (_, index) => series.reduce((total, item) => {
    const value = item.values[index]
    return total + (typeof value === 'number' && Number.isFinite(value) ? value : 0)
  }, 0))
  const maximum = Math.max(1, ...totals)
  const range = dateRangeBounds.value
  const slots = range ? Math.floor((range.end - range.start) / 86_400_000) + 1 : length
  const slotWidth = 120 / Math.max(slots, 1)
  const width = slotWidth * 0.65
  const rects: { x: number; y: number; width: number; height: number; color: string }[] = []
  const zeros: { x: number; y: number; rx: number; color: string }[] = []
  for (let index = 0; index < length; index++) {
    const date = props.dates?.[index]
    const timestamp = date ? Date.parse(`${date.slice(0, 10)}T00:00:00Z`) : Number.NaN
    const offset = range && Number.isFinite(timestamp) ? (timestamp - range.start) / 86_400_000 : index
    const x = Math.max(0, Math.min(120 - width, (offset + 0.5) * slotWidth - width / 2))
    let stackedHeight = 0
    for (const item of series) {
      const value = item.values[index]
      if (typeof value !== 'number' || !Number.isFinite(value)) continue
      const color = `var(--chart-series-${item.colorRole})`
      if (value === 0) {
        zeros.push({ x: x + width / 2, y: 34 - stackedHeight, rx: Math.min(1.5, width / 2), color })
      } else {
        const height = value / maximum * 28
        rects.push({ x, y: 34 - stackedHeight - height, width, height, color })
        stackedHeight += height
      }
    }
  }
  return { rects, zeros }
})
const hasChartValues = computed(() => props.variant === 'stacked-bars'
  ? (props.series ?? []).some(({ values }) => values.some((value) => typeof value === 'number' && Number.isFinite(value)))
  : numericValues.value.length > 0)
</script>

<template>
  <svg
    v-if="hasChartValues"
    class="analysis-mini-chart"
    :class="`analysis-mini-chart--${colorRole}`"
    :style="{ '--analysis-mini-chart-color': chartColor }"
    viewBox="0 0 120 38"
    preserveAspectRatio="none"
    role="img"
    :aria-label="label"
  >
    <template v-if="variant === 'bars'">
      <rect
        v-for="(bar, index) in bars.filter((item) => item.height > 0)"
        :key="index"
        :x="bar.x"
        :y="bar.y"
        :width="bar.width"
        :height="bar.height"
        rx="1.5"
      />
      <circle
        v-for="(bar, index) in bars.filter((item) => item.height === 0)"
        :key="index"
        :cx="bar.x + bar.width / 2"
        cy="34"
        r="1.5"
      />
    </template>
    <template v-else-if="variant === 'stacked-bars'">
      <rect
        v-for="(bar, index) in stackedBars.rects"
        :key="index"
        :x="bar.x"
        :y="bar.y"
        :width="bar.width"
        :height="bar.height"
        :fill="bar.color"
        rx="1.5"
      />
      <ellipse
        v-for="(point, index) in stackedBars.zeros"
        :key="index"
        :cx="point.x"
        :cy="point.y"
        :fill="point.color"
        :rx="point.rx"
        ry="1.5"
      />
    </template>
    <template v-else>
      <polyline
        v-for="(segment, index) in lineGeometry.segments"
        :key="index"
        :points="segment.join(' ')"
      />
      <circle
        v-for="(point, index) in lineGeometry.markers"
        :key="index"
        :cx="point.x"
        :cy="point.y"
        r="2.5"
      />
    </template>
  </svg>
</template>

<style scoped>
.analysis-mini-chart { display: block; width: 100%; height: 6rem; overflow: visible; }
.analysis-mini-chart polyline { fill: none; stroke: var(--analysis-mini-chart-color); stroke-width: 2.4; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.analysis-mini-chart circle:not([fill]) { fill: var(--analysis-mini-chart-color); }
.analysis-mini-chart rect:not([fill]) { fill: color-mix(in srgb, var(--analysis-mini-chart-color) 74%, var(--surface)); }
</style>

