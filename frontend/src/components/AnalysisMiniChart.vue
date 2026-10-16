<script setup lang="ts">
import { computed, useId } from 'vue'

type AnalysisMiniChartColorRole = 'violet' | 'teal' | 'blue' | 'amber' | 'emerald' | 'lilac'
type MacroKey = 'protein_g' | 'carbohydrates_g' | 'fat_g'
type MacroSeries = { key: MacroKey; values: readonly (number | null)[]; colorRole: 'teal' | 'blue' | 'amber' }

type AnalysisMiniChartAreaStop = { offset: string; opacity: number }
const DEFAULT_AREA_STOPS: readonly AnalysisMiniChartAreaStop[] = [
  { offset: '0', opacity: 0.2 },
  { offset: '0.6', opacity: 0.12 },
  { offset: '1', opacity: 0.06 },
]
const props = withDefaults(defineProps<{
  values: readonly (number | null)[]
  dates?: readonly (string | null)[]
  dateRange?: { start: string; end: string } | null
  label: string
  variant: 'line' | 'bars' | 'stacked-bars'
  colorRole: AnalysisMiniChartColorRole
  series?: readonly MacroSeries[]
  showPoints?: boolean
  pointRadius?: number
  area?: boolean
  stableAspectRatio?: boolean
  padLineDomain?: boolean
  hubLineDomainPaddingStep?: number | null
  seriesColor?: string | null
  zeroAsBar?: boolean
  barWidthRatio?: number | null
  minBarWidth?: number | null
  maxBarWidth?: number | null
  trendOverlay?: boolean
  smoothLine?: boolean
  // Reuse the shared immutable fallback rather than allocate per chart.
  // eslint-disable-next-line vue/require-default-prop
  areaStops?: readonly AnalysisMiniChartAreaStop[]
}>(), {
  dates: () => [],
  dateRange: null,
  series: () => [],
  showPoints: false,
  pointRadius: 2.5,
  area: false,
  stableAspectRatio: false,
  padLineDomain: false,
  hubLineDomainPaddingStep: null,
  seriesColor: null,
  zeroAsBar: false,
  barWidthRatio: null,
  minBarWidth: null,
  maxBarWidth: null,
  trendOverlay: false,
  smoothLine: false,
})
const areaStops = computed(() => props.areaStops ?? DEFAULT_AREA_STOPS)

const chartColor = computed(() => props.seriesColor ?? `var(--chart-series-${props.colorRole})`)
const lineBaselineY = 38
const areaGradientId = `analysis-mini-area-${useId()}`
const micronutrientTrendBaselineY = 32
const micronutrientTrendGap = 4.75
const micronutrientTrendAmplitude = 16
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
function monotoneCurvePath(points: readonly { x: number; y: number }[]): string {
  const secants: number[] = []
  const widths: number[] = []
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index]!
    const next = points[index + 1]!
    const width = next.x - current.x
    widths.push(width)
    secants.push((next.y - current.y) / width)
  }
  const tangents = new Array<number>(points.length)
  tangents[0] = secants[0]!
  tangents[tangents.length - 1] = secants.at(-1)!
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = secants[index - 1]!
    const next = secants[index]!
    if (previous === 0 || next === 0 || Math.sign(previous) !== Math.sign(next)) {
      tangents[index] = 0
      continue
    }
    const previousWidth = widths[index - 1]!
    const nextWidth = widths[index]!
    const previousWeight = 2 * nextWidth + previousWidth
    const nextWeight = nextWidth + 2 * previousWidth
    tangents[index] = (previousWeight + nextWeight) / (previousWeight / previous + nextWeight / next)
  }
  for (let index = 0; index < secants.length; index += 1) {
    const secant = secants[index]!
    if (secant === 0) {
      tangents[index] = 0
      tangents[index + 1] = 0
      continue
    }
    const alpha = tangents[index]! / secant
    const beta = tangents[index + 1]! / secant
    const magnitude = alpha * alpha + beta * beta
    if (magnitude > 9) {
      const factor = 3 / Math.sqrt(magnitude)
      tangents[index] = factor * alpha * secant
      tangents[index + 1] = factor * beta * secant
    }
  }
  const first = points[0]!
  const path = [`M ${first.x} ${first.y}`]
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index]!
    const next = points[index + 1]!
    const width = widths[index]!
    path.push(
      `C ${current.x + width / 3} ${current.y + tangents[index]! * width / 3},`
      + ` ${next.x - width / 3} ${next.y - tangents[index + 1]! * width / 3},`
      + ` ${next.x} ${next.y}`,
    )
  }
  return path.join(' ')
}
const lineGeometry = computed(() => {
  const { min, max } = bounds.value
  const range = max - min
  const magnitude = Math.max(Math.abs(min), Math.abs(max), 1)
  const usesHubLineDomainPadding = props.hubLineDomainPaddingStep !== null && !props.padLineDomain
  const minimumHubRange = usesHubLineDomainPadding
    ? Math.max(Math.max(Math.abs(min), Math.abs(max)) * 0.005, props.hubLineDomainPaddingStep!)
    : 0
  const domainRange = usesHubLineDomainPadding ? Math.max(range, minimumHubRange) : range
  const dataDomainMin = usesHubLineDomainPadding && domainRange > range
    ? min + (range - domainRange) / 2
    : min
  const lowerPadding = props.padLineDomain
    ? Math.max(range * 0.6, magnitude * 0.0025)
    : usesHubLineDomainPadding ? domainRange * 0.1 : 0
  const upperPadding = props.padLineDomain
    ? Math.max(range * 0.2, magnitude * 0.0025)
    : usesHubLineDomainPadding ? domainRange * 0.2 : 0
  const domainMin = dataDomainMin - lowerPadding
  const span = domainRange + lowerPadding + upperPadding || 1
  const segments: string[][] = []
  const smoothSegments: string[] = []
  const markers: { x: number; y: number }[] = []
  const areaPolygons: string[] = []
  const smoothAreaPaths: string[] = []
  let segment: { x: number; y: number }[] = []
  let singleton: { x: number; y: number; value: number } | null = null
  const finishSegment = () => {
    if (segment.length > 1) {
      const points = segment.map(({ x, y }) => `${x},${y}`)
      if (props.smoothLine) {
        const path = monotoneCurvePath(segment)
        smoothSegments.push(path)
        if (props.area) {
          const first = segment[0]!
          const last = segment[segment.length - 1]!
          smoothAreaPaths.push(`${path} L ${last.x} ${lineBaselineY} L ${first.x} ${lineBaselineY} Z`)
        }
      } else {
        segments.push(points)
        if (props.area) {
          const first = segment[0]!
          const last = segment[segment.length - 1]!
          areaPolygons.push([
            `${first.x},${lineBaselineY}`,
            ...points,
            `${last.x},${lineBaselineY}`,
          ].join(' '))
        }
      }
    } else if (singleton && singleton.value !== 0 && !props.showPoints) {
      markers.push(singleton)
    }
    segment = []
    singleton = null
  }
  values.value.forEach((value, index) => {
    if (value === null) {
      finishSegment()
      return
    }
    const x = xPosition(index)
    const y = 32 - ((value - domainMin) / span) * 26
    segment.push({ x, y })
    if (segment.length === 1) singleton = { x, y, value }
    if (props.showPoints || value === 0) markers.push({ x, y })
  })
  finishSegment()
  return { segments, smoothSegments, markers, areaPolygons, smoothAreaPaths }
})
const bars = computed(() => {
  const { min, max } = bounds.value
  const span = Math.max(Math.abs(min), Math.abs(max), 1)
  const range = dateRangeBounds.value
  const slots = range ? Math.floor((range.end - range.start) / 86_400_000) + 1 : values.value.length
  const slotWidth = 120 / Math.max(slots, 1)
  const minBarWidth = typeof props.minBarWidth === 'number'
    && Number.isFinite(props.minBarWidth) && props.minBarWidth > 0 ? props.minBarWidth : 0
  const requestedWidth = Math.max(minBarWidth, props.barWidthRatio === null
    ? Math.max(2, slotWidth * 0.65) : slotWidth * props.barWidthRatio)
  const width = typeof props.maxBarWidth === 'number' && Number.isFinite(props.maxBarWidth) && props.maxBarWidth > 0
    ? Math.min(requestedWidth, props.maxBarWidth)
    : requestedWidth
  return values.value.flatMap((value, index) => {
    if (value === null) return []
    const zero = value === 0
    const height = zero && props.zeroAsBar ? 2 : Math.abs(value) / span * (props.trendOverlay ? micronutrientTrendAmplitude : 28)
    const baselineY = props.trendOverlay ? micronutrientTrendBaselineY : 34
    const y = zero && props.zeroAsBar ? 32 : baselineY - height
    const date = props.dates?.[index]
    const timestamp = date ? Date.parse(`${date.slice(0, 10)}T00:00:00Z`) : Number.NaN
    const offset = range && Number.isFinite(timestamp) ? (timestamp - range.start) / 86_400_000 : index
    const slotPosition = range && Number.isFinite(timestamp) ? Math.round(offset) : index
    const center = (offset + 0.5) * slotWidth
    const x = Math.max(0, Math.min(120 - width, center - width / 2))
    return [{ index, x, y, width, height, zero, fillOpacity: slotPosition % 2 === 0 ? 0.66 : 0.36 }]
  })
})
const barTrendGeometry = computed(() => {
  const span = Math.max(Math.abs(bounds.value.min), Math.abs(bounds.value.max), 1)
  const segments: string[][] = []
  const markers: { x: number; y: number }[] = []
  const hasDates = props.dates.length > 0
  let segment: { x: number; y: number }[] = []
  let previousIndex: number | null = null
  let previousTimestamp: number | null = null
  const finishSegment = () => {
    if (segment.length > 1) {
      segments.push(segment.map(({ x, y }) => `${x},${y}`))
      markers.push(...segment)
    } else if (segment.length === 1) {
      markers.push(segment[0]!)
    }
    segment = []
  }

  for (const bar of bars.value) {
    const value = values.value[bar.index]!
    const date = props.dates[bar.index]
    const timestamp = date ? Date.parse(`${date.slice(0, 10)}T00:00:00Z`) : Number.NaN
    const hasValidDate = Number.isFinite(timestamp)
    const consecutive = previousIndex === null || (
      bar.index === previousIndex + 1
      && (!hasDates || (
        previousTimestamp !== null
        && hasValidDate
        && timestamp - previousTimestamp === 86_400_000
      ))
    )
    if (!consecutive) finishSegment()
    segment.push({
      x: bar.x + bar.width / 2,
      y: micronutrientTrendBaselineY
        - micronutrientTrendGap
        - (Math.abs(value) / span) * micronutrientTrendAmplitude,
    })
    previousIndex = bar.index
    previousTimestamp = hasValidDate ? timestamp : null
  }
  finishSegment()
  return { segments, markers }
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
    :class="[
      `analysis-mini-chart--${colorRole}`,
      { 'analysis-mini-chart--bar-trend': trendOverlay && variant === 'bars' },
    ]"
    :style="{ '--analysis-mini-chart-color': chartColor }"
    viewBox="0 0 120 38"
    :preserveAspectRatio="stableAspectRatio ? 'xMidYMid meet' : 'none'"
    role="img"
    :aria-label="label"
  >
    <defs v-if="area && variant === 'line'">
      <linearGradient
        :id="areaGradientId"
        x1="0" y1="0" x2="0"
        :y2="lineBaselineY"
        gradientUnits="userSpaceOnUse"
      >
        <stop v-for="stop in areaStops" :key="stop.offset" :offset="stop.offset" :stop-color="chartColor" :stop-opacity="stop.opacity" />
      </linearGradient>
    </defs>
    <template v-if="variant === 'bars'">
      <rect
        v-for="(bar, index) in bars.filter((item) => item.height > 0)"
        :key="index"
        :x="bar.x"
        :y="bar.y"
        :width="bar.width"
        :height="bar.height"
        :rx="bar.zero ? 0 : 1.5"
        :fill-opacity="trendOverlay ? bar.fillOpacity : undefined"
        :fill="seriesColor ? chartColor : undefined"
      />
      <circle
        v-for="(bar, index) in bars.filter((item) => item.height === 0)"
        :key="index"
        :cx="bar.x + bar.width / 2"
        cy="34"
        r="1.5"
      />
      <template v-if="trendOverlay">
        <polyline
          v-for="(segment, index) in barTrendGeometry.segments"
          :key="`trend-${index}`"
          :points="segment.join(' ')"
        />
        <circle
          v-for="(point, index) in barTrendGeometry.markers"
          :key="`halo-${index}`"
          class="analysis-mini-chart__trend-point-halo"
          :cx="point.x"
          :cy="point.y"
          r="0.01"
          fill="var(--surface)"
          stroke="var(--surface)"
          stroke-width="5"
          vector-effect="non-scaling-stroke"
        />
        <circle
          v-for="(point, index) in barTrendGeometry.markers"
          :key="`point-${index}`"
          class="analysis-mini-chart__trend-point"
          :cx="point.x"
          :cy="point.y"
          r="0.01"
          :fill="chartColor"
          :stroke="chartColor"
          stroke-width="3"
          vector-effect="non-scaling-stroke"
        />
      </template>
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
      <path
        v-for="(smoothAreaPath, index) in lineGeometry.smoothAreaPaths"
        :key="`smooth-area-${index}`"
        class="analysis-mini-chart__area"
        :d="smoothAreaPath"
        :fill="`url(#${areaGradientId})`"
        stroke="none"
      />
      <polygon
        v-for="(points, index) in lineGeometry.areaPolygons"
        :key="`area-${index}`"
        class="analysis-mini-chart__area"
        :points="points"
        :fill="`url(#${areaGradientId})`"
        stroke="none"
      />
      <path
        v-for="(path, index) in lineGeometry.smoothSegments"
        :key="`smooth-line-${index}`"
        class="analysis-mini-chart__line"
        :d="path"
      />
      <polyline
        v-for="(segment, index) in lineGeometry.segments"
        :key="index"
        class="analysis-mini-chart__line"
        :points="segment.join(' ')"
      />
      <circle
        v-for="(point, index) in lineGeometry.markers"
        :key="index"
        class="analysis-mini-chart__line-point"
        :cx="point.x"
        :cy="point.y"
        :r="pointRadius"
      />
    </template>
  </svg>
</template>

<style scoped>
.analysis-mini-chart { display: block; width: 100%; height: 6rem; overflow: visible; }
 .analysis-mini-chart__line,
 .analysis-mini-chart polyline {
  fill: none;
  stroke: var(--analysis-mini-chart-color);
  stroke-width: 2.4;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}
.analysis-mini-chart circle:not([fill]) { fill: var(--analysis-mini-chart-color); }
.analysis-mini-chart rect:not([fill]) { fill: color-mix(in srgb, var(--analysis-mini-chart-color) 74%, var(--surface)); }
.analysis-mini-chart--bar-trend polyline { stroke-width: 2; stroke-opacity: 1; }
.analysis-mini-chart__trend-point-halo {
  fill: var(--surface);
  stroke: var(--surface);
  stroke-width: 5;
  vector-effect: non-scaling-stroke;
}
.analysis-mini-chart__trend-point {
  fill: var(--analysis-mini-chart-color);
  stroke: var(--analysis-mini-chart-color);
  stroke-width: 3;
  vector-effect: non-scaling-stroke;
}
</style>

