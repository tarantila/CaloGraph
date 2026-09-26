<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

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
import type { AnalysisMicronutrientMetric, AnalysisMicronutrientResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const defaultRange = { start: shiftIsoDate(today, -29), end: today }
const compactPresets: AnalyticsCompactPreset[] = ['7', '30']
const desktopPresets = ['custom', '7', '30']
const initialRange = resolveAnalyticsRange(route.query.start, route.query.end, defaultRange)
const number = createNumberFormatter({ maximumSignificantDigits: 4 })

function routeMetric(): string {
  const param = route.params.metric
  return Array.isArray(param) ? param[0] ?? '' : String(param ?? '')
}

function periodForRange(start: string, end: string): AnalyticsCompactPreset {
  if (start === shiftIsoDate(end, -6)) return '7'
  if (start === shiftIsoDate(end, -29)) return '30'
  return 'custom'
}

const initialRangeIsValid = validAnalyticsRange(route.query.start, route.query.end)
const queryPeriod = parseAnalyticsCompactPreset(route.query.period, [...compactPresets, 'custom'])
const start = ref(initialRange.start)
const end = ref(initialRange.end)
const period = ref<AnalyticsCompactPreset>(
  initialRangeIsValid && analyticsPresetMatchesRange(queryPeriod, route.query.start, route.query.end)
    ? queryPeriod ?? periodForRange(initialRange.start, initialRange.end)
    : periodForRange(initialRange.start, initialRange.end),
)
const metricKey = ref(routeMetric())
const response = ref<AnalysisMicronutrientResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0

function selectPeriod(value: AnalyticsCompactPreset): void {
  period.value = value
}

watch(
  () => [route.params.metric, route.query.start, route.query.end, route.query.period] as const,
  ([nextMetric, nextStart, nextEnd, nextPeriod]) => {
    const nextKey = Array.isArray(nextMetric) ? nextMetric[0] ?? '' : String(nextMetric ?? '')
    const hasRange = validAnalyticsRange(nextStart, nextEnd)
    const range = resolveAnalyticsRange(nextStart, nextEnd, defaultRange)
    const candidate = parseAnalyticsCompactPreset(nextPeriod, [...compactPresets, 'custom'])
    const nextPreset = hasRange && analyticsPresetMatchesRange(candidate, nextStart, nextEnd)
      ? candidate ?? periodForRange(range.start, range.end)
      : periodForRange(range.start, range.end)
    const changed = nextKey !== metricKey.value || range.start !== start.value || range.end !== end.value
    metricKey.value = nextKey
    start.value = range.start
    end.value = range.end
    period.value = nextPreset
    if (changed) void load()
  },
)

async function load(): Promise<void> {
  const loadId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, 31)) {
    response.value = null
    error.value = t('analysisMicronutrients.invalidRange')
    loading.value = false
    return
  }

  const requestedMetricKey = metricKey.value
  loading.value = true
  error.value = ''
  response.value = null
  await router.replace({
    query: { ...route.query, start: start.value, end: end.value, period: period.value },
  })
  if (loadId !== latestLoadId) return

  try {
    const params = new URLSearchParams({
      start: start.value,
      end: end.value,
      metric_key: requestedMetricKey,
    })
    const result = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (loadId !== latestLoadId) return
    if (result.metrics[0]?.metric_key !== requestedMetricKey) {
      error.value = t('analysisMicronutrients.metricNotFound')
      return
    }
    response.value = result
  } catch (cause) {
    if (loadId === latestLoadId) error.value = localizeApiError(cause, 'analysisMicronutrients.loadFailed')
  } finally {
    if (loadId === latestLoadId) loading.value = false
  }
}

onMounted(() => { void load() })

const metric = computed<AnalysisMicronutrientMetric | null>(() => response.value?.metrics[0] ?? null)
const hasSeriesValues = computed(() => response.value?.series.some((point) => point.value !== null) ?? false)
const chartOption = computed<EChartsOption>(() => ({
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: response.value?.series.map((point) => point.date) ?? [] },
  yAxis: { type: 'value' },
  series: [{
    name: metric.value?.label ?? '',
    type: 'line',
    connectNulls: false,
    data: response.value?.series.map((point) => point.value) ?? [],
  }],
}))

function providerLabel(providerKey: string): string {
  const key = `providerPreferencesUi.providers.${providerKey}`
  const label = t(key)
  return label === key ? providerKey : label
}

function formatValue(value: number | null): string {
  return value === null ? '–' : `${number.format(value)} ${metric.value?.unit ?? ''}`.trim()
}

function formatTotal(value: number | null): string {
  return value === null ? '–' : `${number.format(value)} ${metric.value?.unit ?? ''}`.trim()
}

function contributors(): string {
  return metric.value?.contributors.map((item) => t(
    item.days_with_data === 1 ? 'analysisMicronutrients.sourceDay' : 'analysisMicronutrients.sourceDays',
    {
      provider: providerLabel(item.provider_key),
      count: item.days_with_data,
    },
  )).join(' · ') ?? ''
}
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ metric?.label ?? t('analysisMicronutrientDetail.title') }}</h1>
      <p v-if="metric">{{ t('analysisMicronutrients.coverage', { count: metric.days_with_data, period: response?.period_days ?? 0 }) }}</p>
    </div>
    <RouterLink
      class="button secondary micronutrient-back-link"
      :to="{ name: 'analysis-micronutrients', query: { start, end } }"
    >
      {{ t('analysisMicronutrientDetail.back') }}
    </RouterLink>
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
  <template v-else-if="response && metric">
    <section class="card micronutrient-detail-summary">
      <div>
        <span>{{ t('analysisMicronutrients.total') }}</span>
        <strong>{{ formatTotal(metric.total) }}</strong>
      </div>
      <div>
        <span>{{ t('analysisMicronutrients.coverage', { count: metric.days_with_data, period: response.period_days }) }}</span>
        <strong>{{ metric.days_with_data }} / {{ response.period_days }}</strong>
      </div>
    </section>
    <p v-if="metric.contributors.length" class="micronutrient-detail-contributors">
      <span>{{ t('analysisMicronutrients.contributors') }}:</span> {{ contributors() }}
    </p>
    <ChartPanel
      :title="metric.label"
      :option="chartOption"
      :empty="!hasSeriesValues"
      :height="300"
    />
    <section class="card micronutrient-daily-card">
      <h2>{{ t('analysisMicronutrientDetail.dailyValues') }}</h2>
      <ul :aria-label="t('analysisMicronutrientDetail.dailyValues')">
        <li v-for="point in response.series" :key="point.date">
          <time :datetime="point.date">{{ formatGermanDate(point.date) }}</time>
          <span class="micronutrient-day-provider">{{ providerLabel(point.provider_key) }}</span>
          <strong>{{ formatValue(point.value) }}</strong>
        </li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
.micronutrient-back-link { align-self: start; white-space: normal; }
.micronutrient-detail-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; margin-bottom: .8rem; }
.micronutrient-detail-summary div { min-width: 0; display: grid; gap: .35rem; }
.micronutrient-detail-summary span, .micronutrient-detail-contributors { color: var(--muted); font-size: .88rem; }
.micronutrient-detail-summary strong { overflow-wrap: anywhere; font-size: 1.2rem; }
.micronutrient-detail-contributors { margin: .5rem 0 1rem; }
.micronutrient-daily-card { margin-top: 1rem; }
.micronutrient-daily-card h2 { margin-top: 0; font-size: 1.05rem; }
.micronutrient-daily-card ul { list-style: none; margin: 0; padding: 0; }
.micronutrient-daily-card li { display: grid; grid-template-columns: minmax(8rem, 1fr) minmax(7rem, 1fr) minmax(5rem, auto); gap: .5rem; padding: .65rem 0; border-top: 1px solid var(--border); }
.micronutrient-day-provider { color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; }
.micronutrient-daily-card li strong { text-align: right; }
@media (max-width: 600px) {
  .micronutrient-detail-summary { grid-template-columns: 1fr; }
  .micronutrient-daily-card li { grid-template-columns: minmax(0, 1fr) minmax(5rem, auto); }
  .micronutrient-daily-card li time { grid-column: 1; }
  .micronutrient-daily-card li .micronutrient-day-provider { grid-column: 1; }
  .micronutrient-daily-card li strong { grid-column: 2; grid-row: 1 / span 2; align-self: center; }
}
</style>
