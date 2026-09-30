<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHubQuery, analysisRangeDayCount, localizedAnalysisPresentation, normalizeAnalysisPeriodForCapability, parseAnalysisPeriodSelection, supportedAnalysisPeriods, type AnalysisPeriodSelection } from '../analysis-v2'
import { analysisRangeWithinDays } from '../analytics-period'
import AnalysisPeriodPicker from '../components/AnalysisPeriodPicker.vue'
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

function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const maxDays = 31
const presets = supportedAnalysisPeriods(maxDays, false)
const requestedPeriod = parseAnalysisPeriodSelection(route.query.period)
const queryStart = queryString(route.query.start)
const queryEnd = queryString(route.query.end)
const initialSelection = normalizeAnalysisPeriodForCapability(
  requestedPeriod, requestedPeriod === 'custom' && queryStart && queryEnd
    ? { start: queryStart, end: queryEnd } : null, maxDays, today,
)
const start = ref(initialSelection.range.start)
const end = ref(initialSelection.range.end)
const period = ref<AnalysisPeriodSelection>(initialSelection.period)
const metricKey = ref(Array.isArray(route.params.metric) ? route.params.metric[0] ?? '' : String(route.params.metric ?? ''))
const response = ref<AnalysisMicronutrientResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0

const metric = computed<AnalysisMicronutrientMetric | null>(() => (
  response.value?.metrics.find((item) => item.metric_key === metricKey.value) ?? null
))
const detailPresentation = computed(() => localizedAnalysisPresentation(metricKey.value))
const chartDates = computed(() => response.value ? Array.from(
  { length: analysisRangeDayCount({ start: start.value, end: end.value }) },
  (_, index) => shiftIsoDate(start.value, index),
) : [])
const chartValues = computed(() => {
  const valuesByDate = new Map((response.value?.series ?? []).map(({ date, value }) => [date, value]))
  return chartDates.value.map((date) => valuesByDate.get(date) ?? null)
})
const actualReadings = computed(() => (response.value?.series ?? []).filter(
  (point) => point.value !== null && Number.isFinite(point.value),
))
const hasSeriesValues = computed(() => actualReadings.value.length > 0)
const coverageIsComplete = computed(() => metric.value !== null
  && response.value !== null
  && metric.value.days_with_data === response.value.period_days)
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
      data: chartDates.value,
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
      data: chartValues.value,
      lineStyle: { color: palette.emerald, width: 2 },
      itemStyle: { color: palette.emerald },
      areaStyle: { color: palette.emerald, opacity: 0.12 },
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
  const query = analysisHubQuery(period.value, { start: start.value, end: end.value })
  if (route.query.period === query.period
    && route.query.start === query.start && route.query.end === query.end) return
  await router.replace({
    query: { ...route.query, start: query.start, end: query.end, period: query.period },
    hash: route.hash,
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today) {
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

function applyPeriod(value: { start: string; end: string; period: AnalysisPeriodSelection }): void {
  const normalized = normalizeAnalysisPeriodForCapability(
    value.period, value.period === 'custom' ? value : null, maxDays, today,
  )
  period.value = normalized.period
  start.value = normalized.range.start
  end.value = normalized.range.end
  void load()
}

watch(
  () => [route.params.metric, route.query.start, route.query.end, route.query.period] as const,
  ([nextMetric, nextStart, nextEnd, nextPeriod]) => {
    const nextKey = Array.isArray(nextMetric) ? nextMetric[0] ?? '' : String(nextMetric ?? '')
    const nextQueryStart = queryString(nextStart)
    const nextQueryEnd = queryString(nextEnd)
    const parsed = parseAnalysisPeriodSelection(nextPeriod)
    const normalized = normalizeAnalysisPeriodForCapability(
      parsed, parsed === 'custom' && nextQueryStart && nextQueryEnd
        ? { start: nextQueryStart, end: nextQueryEnd } : null, maxDays, today,
    )
    const nextRange = normalized.range
    const nextPreset = normalized.period
    const changed = nextKey !== metricKey.value || nextRange.start !== start.value
      || nextRange.end !== end.value || nextPreset !== period.value
    metricKey.value = nextKey
    start.value = nextRange.start
    end.value = nextRange.end
    period.value = nextPreset
    if (changed) void load()
    else void syncQuery()
  },
)

onMounted(() => { void load() })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, { start: start, end: end }) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/mikronaehrstoffe', query: analysisHubQuery(period, { start: start, end: end }) }">{{ t('analysisV2.categories.micronutrients') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ detailPresentation?.label ?? t('analysisV2.micronutrientDetailTitle') }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1>{{ detailPresentation?.label ?? t('analysisV2.micronutrientDetailTitle') }}</h1>
        <p>{{ detailPresentation?.description ?? t('analysisV2.micronutrientDescription') }}</p>
      </div>
      <AnalysisPeriodPicker
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :presets="presets"
        :custom-range="true"
        :max-days="maxDays"
        @apply="applyPeriod"
      />
    </div>
    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response && metric">
      <p v-if="!hasSeriesValues" class="card empty micronutrient-empty" role="status">
        {{ t('analysisV2.micronutrientEmpty') }}
        <span>{{ t('analysisV2.daysCoverage', { count: metric.days_with_data, total: response.period_days }) }}</span>
      </p>
      <template v-else>
        <section class="micronutrient-summary">
          <article class="card">
            <h2>{{ t('analysisV2.micronutrientTotal') }}</h2>
            <strong>{{ formatValue(metric.total) }}</strong>
          </article>
          <article class="card">
            <h2>{{ t('analysisV2.micronutrientCoverage') }}</h2>
            <strong
              class="micronutrient-coverage"
              :class="coverageIsComplete ? 'micronutrient-coverage--complete' : 'micronutrient-coverage--incomplete'"
              :data-coverage-status="coverageIsComplete ? 'complete' : 'incomplete'"
            >
              <span class="micronutrient-coverage-icon" aria-hidden="true">{{ coverageIsComplete ? '✓' : '!' }}</span>
              {{ t('analysisV2.daysCoverage', { count: metric.days_with_data, total: response.period_days }) }}
            </strong>
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
.analysis-detail-page > .page-heading { margin-bottom: 0; }
.page-heading > .analytics-period-filter { flex: 0 1 820px; width: min(100%, 820px); }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.micronutrient-summary { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.micronutrient-summary article { display: grid; gap: .35rem; min-width: 0; padding: .9rem; }
.micronutrient-summary h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.micronutrient-summary strong { font-size: 1.2rem; overflow-wrap: anywhere; }
.micronutrient-coverage { display: flex; align-items: baseline; gap: .4rem; }
.micronutrient-coverage--complete { color: var(--success); }
.micronutrient-coverage--incomplete { color: var(--warning); }
.micronutrient-coverage-icon { flex: 0 0 auto; font-size: 1rem; }
.micronutrient-empty { display: grid; gap: .4rem; margin: 0; padding: .75rem 1rem; }
.micronutrient-provenance { margin: 0; color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }
.micronutrient-daily-card { padding: .9rem; }
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
