<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHubQuery, localizedAnalysisPresentation, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import { analysisRangeWithinDays, validAnalyticsRange } from '../analytics-period'
import AnalyticsPeriodFilter from '../components/AnalyticsPeriodFilter.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { chartColors } from '../chart-theme'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisMicronutrientMetric, AnalysisMicronutrientResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const number = createNumberFormatter({ maximumSignificantDigits: 4 })
const compactPresets: AnalysisHubPeriod[] = ['7', '30']
const desktopPresets = ['custom', '7', '30']

function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const initialPeriod = parseAnalysisHubPeriod(route.query.period)
const defaultRange = { start: shiftIsoDate(today, -(Number(initialPeriod) - 1)), end: today }
const queryStart = queryString(route.query.start)
const queryEnd = queryString(route.query.end)
const initialRangeIsValid = validAnalyticsRange(queryStart, queryEnd)
const initialRange = initialRangeIsValid
  ? { start: queryStart as string, end: queryEnd as string }
  : defaultRange
const start = ref(initialRange.start)
const end = ref(initialRange.end)
const period = ref<AnalysisHubPeriod | 'custom'>(
  initialRangeIsValid
    ? (initialRange.start === shiftIsoDate(initialRange.end, -6) ? '7'
      : initialRange.start === shiftIsoDate(initialRange.end, -29) ? '30' : 'custom')
    : initialPeriod,
)
const metricKey = ref(Array.isArray(route.params.metric) ? route.params.metric[0] ?? '' : String(route.params.metric ?? ''))
const response = ref<AnalysisMicronutrientResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0

const metric = computed<AnalysisMicronutrientMetric | null>(() => (
  response.value?.metrics.find((item) => item.metric_key === metricKey.value) ?? null
))
const detailPresentation = computed(() => localizedAnalysisPresentation(metricKey.value))
const actualReadings = computed(() => (response.value?.series ?? []).filter(
  (point) => point.value !== null && Number.isFinite(point.value),
))
const hasSeriesValues = computed(() => actualReadings.value.length > 0)
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  return {
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
    },
    xAxis: {
      type: 'category',
      data: response.value?.series.map((point) => point.date) ?? [],
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: {
        color: palette.label,
        formatter: (value: string | number) => formatGermanDate(String(value).slice(0, 10)),
      },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: detailPresentation.value?.label ?? t('analysisV2.micronutrientDetailTitle'),
      type: 'line',
      connectNulls: false,
      showSymbol: true,
      data: response.value?.series.map((point) => point.value) ?? [],
      lineStyle: { color: palette.lilac, width: 2 },
      itemStyle: { color: palette.lilac },
    }],
  }
})

function contributors(): string {
  return metric.value?.contributors.map((item) => t(
    item.days_with_data === 1 ? 'analysisV2.sourceDay' : 'analysisV2.sourceDays',
    { provider: providerLabel(item.provider_key), count: item.days_with_data },
  )).join(' · ') ?? ''
}

function providerLabel(providerKey: string): string {
  const key = `providerPreferencesUi.providers.${providerKey}`
  const label = t(key)
  return label === key ? providerKey : label
}

function formatValue(value: number | null): string {
  return value === null ? '–' : `${number.format(value)} ${metric.value?.unit ?? ''}`.trim()
}

async function syncQuery(): Promise<void> {
  await router.replace({
    query: { ...route.query, start: start.value, end: end.value, period: period.value },
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, 31)) {
    response.value = null
    error.value = t('analysisV2.micronutrientRangeInvalid')
    loading.value = false
    return
  }
  const requestedMetricKey = metricKey.value
  loading.value = true
  error.value = ''
  response.value = null
  await syncQuery()
  if (requestId !== latestLoadId) return
  const params = new URLSearchParams({
    start: start.value,
    end: end.value,
    metric_key: requestedMetricKey,
  })
  try {
    const result = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (requestId !== latestLoadId) return
    if (result.metrics[0]?.metric_key !== requestedMetricKey) {
      error.value = t('analysisV2.metricNotFound')
      return
    }
    response.value = result
  } catch (cause) {
    if (requestId === latestLoadId) error.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    if (requestId === latestLoadId) loading.value = false
  }
}

function selectPeriod(value: string): void {
  if (value === 'custom') {
    period.value = 'custom'
    return
  }
  const nextPeriod = parseAnalysisHubPeriod(value)
  period.value = nextPeriod
  start.value = shiftIsoDate(today, -(Number(nextPeriod) - 1))
  end.value = today
}

watch(
  () => [route.params.metric, route.query.start, route.query.end, route.query.period] as const,
  ([nextMetric, nextStart, nextEnd, nextPeriod]) => {
    const nextKey = Array.isArray(nextMetric) ? nextMetric[0] ?? '' : String(nextMetric ?? '')
    const nextQueryStart = queryString(nextStart)
    const nextQueryEnd = queryString(nextEnd)
    const hasRange = validAnalyticsRange(nextQueryStart, nextQueryEnd)
    const defaultPeriod = parseAnalysisHubPeriod(nextPeriod)
    const nextRange = hasRange
      ? { start: nextQueryStart as string, end: nextQueryEnd as string }
      : { start: shiftIsoDate(today, -(Number(defaultPeriod) - 1)), end: today }
    const nextPreset: AnalysisHubPeriod | 'custom' = hasRange
      ? nextRange.start === shiftIsoDate(nextRange.end, -6) ? '7'
        : nextRange.start === shiftIsoDate(nextRange.end, -29) ? '30' : 'custom'
      : defaultPeriod
    const changed = nextKey !== metricKey.value || nextRange.start !== start.value
      || nextRange.end !== end.value || nextPreset !== period.value
    metricKey.value = nextKey
    start.value = nextRange.start
    end.value = nextRange.end
    period.value = nextPreset
    if (changed) void load()
  },
)

onMounted(() => { void load() })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/mikronaehrstoffe', query: analysisHubQuery(period) }">{{ t('analysisV2.categories.micronutrients') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ detailPresentation?.label ?? t('analysisV2.micronutrientDetailTitle') }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1>{{ detailPresentation?.label ?? t('analysisV2.micronutrientDetailTitle') }}</h1>
        <p>{{ detailPresentation?.description ?? t('analysisV2.micronutrientDescription') }}</p>
      </div>
    </div>

    <AnalyticsPeriodFilter
      :start="start"
      :end="end"
      :initial-preset="period"
      :compact-preset="period === 'custom' ? 'custom' : period"
      :compact-presets="compactPresets"
      :desktop-presets="desktopPresets"
      @update:start="start = $event"
      @update:end="end = $event"
      @preset="selectPeriod"
      @apply="load"
    />

    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response && metric">
      <p v-if="!hasSeriesValues" class="card empty" role="status">{{ t('analysisV2.micronutrientEmpty') }}</p>
      <template v-else>
        <section class="micronutrient-summary">
          <article class="card">
            <h2>{{ t('analysisV2.micronutrientTotal') }}</h2>
            <strong>{{ formatValue(metric.total) }}</strong>
          </article>
          <article class="card">
            <h2>{{ t('analysisV2.micronutrientCoverage') }}</h2>
            <strong>{{ t('analysisV2.daysCoverage', { count: metric.days_with_data, total: response.period_days }) }}</strong>
          </article>
        </section>
        <p v-if="contributors()" class="micronutrient-provenance">
          <span>{{ t('analysisV2.contributors') }}:</span> {{ contributors() }}
        </p>
        <ChartPanel :title="detailPresentation?.label ?? t('analysisV2.micronutrientDetailTitle')" :option="chartOption" :empty="false" />
        <section class="card micronutrient-daily-card">
          <h2>{{ t('analysisV2.micronutrientDaily') }}</h2>
          <ul :aria-label="t('analysisV2.micronutrientDaily')">
            <li v-for="point in actualReadings" :key="`${point.date}-${point.provider_key}`">
              <time :datetime="point.date">{{ formatGermanDate(point.date) }}</time>
              <span class="micronutrient-day-provider">{{ providerLabel(point.provider_key) }}</span>
              <strong>{{ formatValue(point.value) }}</strong>
            </li>
          </ul>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis-detail-page { display: grid; gap: 1rem; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.micronutrient-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.micronutrient-summary article { display: grid; gap: .35rem; min-width: 0; }
.micronutrient-summary h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.micronutrient-summary strong { font-size: 1.2rem; overflow-wrap: anywhere; }
.micronutrient-provenance { margin: 0; color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }
.micronutrient-daily-card h2 { margin-top: 0; font-size: 1.05rem; }
.micronutrient-daily-card ul { list-style: none; margin: 0; padding: 0; }
.micronutrient-daily-card li { display: grid; grid-template-columns: minmax(8rem, 1fr) minmax(7rem, 1fr) minmax(5rem, auto); gap: .5rem; padding: .65rem 0; border-top: 1px solid var(--border); }
.micronutrient-day-provider { color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; }
.micronutrient-daily-card li strong { text-align: right; }
@media (max-width: 600px) {
  .micronutrient-summary { grid-template-columns: 1fr; }
  .micronutrient-daily-card li { grid-template-columns: minmax(0, 1fr) minmax(5rem, auto); }
  .micronutrient-daily-card li time, .micronutrient-daily-card li .micronutrient-day-provider { grid-column: 1; }
  .micronutrient-daily-card li strong { grid-column: 2; grid-row: 1 / span 2; align-self: center; }
}
</style>
