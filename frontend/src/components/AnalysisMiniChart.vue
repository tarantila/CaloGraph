<script setup lang="ts">
import { computed } from 'vue'

type AnalysisMiniChartColorRole = 'violet' | 'teal' | 'blue' | 'amber' | 'emerald' | 'lilac'

const props = defineProps<{
  values: readonly (number | null)[]
  dates?: readonly (string | null)[]
  dateRange?: { start: string; end: string }
  label: string
  variant: 'line' | 'bars'
  colorRole: AnalysisMiniChartColorRole
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
const lineSegments = computed(() => {
  const { min, max } = bounds.value
  const span = max - min || 1
  const segments: string[][] = []
  let segment: string[] = []
  values.value.forEach((value, index) => {
    if (value === null) {
      if (segment.length) segments.push(segment)
      segment = []
      return
    }
    const x = xPosition(index)
    const y = 32 - ((value - min) / span) * 26
    segment.push(`${x},${y}`)
  })
  if (segment.length) segments.push(segment)
  return segments
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
</script>

<template>
  <svg
    v-if="numericValues.length"
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
    <template v-else>
      <polyline
        v-for="(segment, index) in lineSegments.filter((item) => item.length > 1)"
        :key="index"
        :points="segment.join(' ')"
      />
      <circle
        v-for="(point, index) in lineSegments.flatMap((segment) => segment.length === 1 ? segment : [])"
        :key="index"
        :cx="point.split(',')[0]"
        :cy="point.split(',')[1]"
        r="2.5"
      />
    </template>
  </svg>
</template>

<style scoped>
.analysis-mini-chart { display: block; width: 100%; height: 3rem; overflow: visible; }
.analysis-mini-chart polyline { fill: none; stroke: var(--analysis-mini-chart-color); stroke-width: 2.4; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.analysis-mini-chart circle { fill: var(--analysis-mini-chart-color); }
.analysis-mini-chart rect { fill: color-mix(in srgb, var(--analysis-mini-chart-color) 74%, var(--surface)); }
</style>

