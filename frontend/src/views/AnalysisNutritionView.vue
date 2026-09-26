<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import {
  analysisRangeWithinDays,
  analyticsPresetMatchesRange,
  parseAnalyticsCompactPreset,
  resolveAnalyticsRange,
  validAnalyticsRange,
  type AnalyticsCompactPreset,
} from '../analytics-period'
import AnalyticsPeriodFilter from '../components/AnalyticsPeriodFilter.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { createNumberFormatter, i18n } from '../i18n'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { useAuthStore } from '../stores/auth'
import type { AnalysisMicronutrientResponse, DailyPoint, DecimalTransport } from '../types'

type AnalysisNutritionPoint = Omit<
  DailyPoint,
  'calories_kcal' | 'protein_g' | 'carbs_g' | 'fat_g'
> & {
  calories_kcal: DecimalTransport
  protein_g: DecimalTransport
  carbs_g: DecimalTransport
  fat_g: DecimalTransport
}

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const defaultRange = { start: shiftIsoDate(today, -29), end: today }
const compactPresets: AnalyticsCompactPreset[] = ['7', '30', '60', '90']
const desktopPresets = ['custom', '7', '30', '60', '90']
const initialRange = resolveAnalyticsRange(route.query.start, route.query.end, defaultRange)


function periodForRange(start: string, end: string): AnalyticsCompactPreset {
  if (start === shiftIsoDate(end, -6)) return '7'
  if (start === shiftIsoDate(end, -29)) return '30'
  if (start === shiftIsoDate(end, -59)) return '60'
  if (start === shiftIsoDate(end, -89)) return '90'
  return 'custom'
}

const initialQueryRangeIsValid = validAnalyticsRange(route.query.start, route.query.end)
const queryPeriod = parseAnalyticsCompactPreset(route.query.period, [...compactPresets, 'custom'])
const start = ref(initialRange.start)
const end = ref(initialRange.end)
const period = ref<AnalyticsCompactPreset>(
  initialQueryRangeIsValid && analyticsPresetMatchesRange(queryPeriod, route.query.start, route.query.end)
    ? queryPeriod ?? periodForRange(initialRange.start, initialRange.end)
    : periodForRange(initialRange.start, initialRange.end),
)
const points = ref<AnalysisNutritionPoint[]>([])
const loading = ref(true)
const error = ref('')
const number = createNumberFormatter({ maximumFractionDigits: 1 })
let latestLoadId = 0

function selectPeriod(value: AnalyticsCompactPreset): void {
  period.value = value
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const hasQueryRange = validAnalyticsRange(nextStart, nextEnd)
    const nextRange = resolveAnalyticsRange(nextStart, nextEnd, defaultRange)
    const candidate = parseAnalyticsCompactPreset(nextPeriod, [...compactPresets, 'custom'])
    const nextPreset = hasQueryRange && analyticsPresetMatchesRange(candidate, nextStart, nextEnd)
      ? candidate ?? periodForRange(nextRange.start, nextRange.end)
      : periodForRange(nextRange.start, nextRange.end)
    const rangeChanged = nextRange.start !== start.value || nextRange.end !== end.value
    start.value = nextRange.start
    end.value = nextRange.end
    period.value = nextPreset
    if (rangeChanged) void load()
  },
)

async function load(): Promise<void> {
  const loadId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, 90)) {
    points.value = []
    error.value = t('analysisNutrition.invalidRange')
    loading.value = false
    return
  }

  loading.value = true
  error.value = ''
  await router.replace({
    query: { ...route.query, start: start.value, end: end.value, period: period.value },
  })
  if (loadId !== latestLoadId) return

  try {
    await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?start=${today}&end=${today}`,
    )
    if (loadId !== latestLoadId) return
    const params = new URLSearchParams({ start: start.value, end: end.value })
    const result = await api<{ points: AnalysisNutritionPoint[] }>(`/analytics/trends?${params.toString()}`)
    if (loadId === latestLoadId) points.value = result.points
  } catch (cause) {
    if (loadId === latestLoadId) {
      points.value = []
      error.value = localizeApiError(cause, 'analysisNutrition.loadFailed')
    }
  } finally {
    if (loadId === latestLoadId) loading.value = false
  }
}

onMounted(() => { void load() })

const metricDefinitions = [
  { key: 'calories_kcal', label: 'charts.calories', unit: 'kcal' },
  { key: 'protein_g', label: 'nutritionVerification.protein', unit: 'g' },
  { key: 'carbs_g', label: 'charts.carbs', unit: 'g' },
  { key: 'fat_g', label: 'charts.fat', unit: 'g' },
] as const

function finiteNumber(value: DecimalTransport): number | null {
  if (value === null || (typeof value === 'string' && value.trim() === '')) return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

function metricValues(key: typeof metricDefinitions[number]['key']): (number | null)[] {
  return points.value.map((point) => finiteNumber(point[key]))
}

function metricTotal(key: typeof metricDefinitions[number]['key']): number | null {
  const values = metricValues(key).filter((value): value is number => value !== null)
  return values.length ? values.reduce((sum, value) => sum + value, 0) : null
}

function metricCoverage(key: typeof metricDefinitions[number]['key']): number {
  return metricValues(key).filter((value) => value !== null).length
}

function formatTotal(key: typeof metricDefinitions[number]['key'], unit: string): string {
  const total = metricTotal(key)
  return total === null ? '–' : `${number.format(total)} ${unit}`
}

const hasNutritionData = computed(() => metricDefinitions.some((metric) =>
  metricCoverage(metric.key) > 0,
))
const chartOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  legend: { data: metricDefinitions.map((metric) => t(metric.label)) },
  xAxis: { type: 'category', data: points.value.map((point) => point.date) },
  yAxis: { type: 'value' },
  series: metricDefinitions.map((metric) => ({
    name: t(metric.label),
    type: 'line',
    connectNulls: false,
    data: metricValues(metric.key),
  })),
}))
const rangeLabel = computed(() => `${formatGermanDate(start.value)} – ${formatGermanDate(end.value)}`)
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('analysisNutrition.title') }}</h1>
      <p>{{ t('analysisNutrition.description') }}</p>
    </div>
  </div>

  <AnalyticsPeriodFilter
    :start="start"
    :end="end"
    :initial-preset="period"
    :compact-preset="period"
    :compact-presets="compactPresets"
    :desktop-presets="desktopPresets"
    @update:start="start = $event"
    @update:end="end = $event"
    @preset="selectPeriod"
    @apply="load"
  />

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('common.loading') }}
  </div>
  <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
    <p>{{ error }}</p>
    <button class="button secondary analysis-retry" type="button" @click="load">
      {{ t('common.tryAgain') }}
    </button>
  </section>
  <template v-else>
    <section class="analysis-summary-grid" :aria-label="t('analysisNutrition.summary')">
      <article v-for="metric in metricDefinitions" :key="metric.key" class="card analysis-summary-card">
        <h2>{{ t(metric.label) }}</h2>
        <strong>{{ formatTotal(metric.key, metric.unit) }}</strong>
        <p>{{ t('analysisNutrition.coverage', { count: metricCoverage(metric.key), total: points.length }) }}</p>
      </article>
    </section>
    <ChartPanel
      :title="`${t('analysisNutrition.chartTitle')} · ${rangeLabel}`"
      :option="chartOption"
      :empty="!hasNutritionData"
      :height="320"
    />
  </template>
</template>

<style scoped>
.analysis-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: .8rem;
  margin: 1rem 0;
}
.analysis-summary-card { min-width: 0; }
.analysis-summary-card h2 { margin: 0 0 .65rem; color: var(--muted-strong); font-size: .92rem; }
.analysis-summary-card strong { display: block; overflow-wrap: anywhere; font-size: 1.35rem; }
.analysis-summary-card p { margin: .5rem 0 0; color: var(--muted); font-size: .85rem; }
@media (max-width: 880px) {
  .analysis-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 520px) {
  .analysis-summary-grid { grid-template-columns: 1fr; }
}
</style>
