<script setup lang="ts">
import { graphic, type EChartsOption } from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { AnalysisIcon, analysisHubQuery, analysisRangeDayCount, localizedAnalysisPresentation } from '../analysis-v2'
import { periodFromQuery, resolveAllDataRange, validPeriodRange, type AppliedPeriod, type PeriodLimits, type PeriodRange } from '../analytics-period'
import AnalysisReadingsDisclosure from '../components/AnalysisReadingsDisclosure.vue'
import ChartPanel from '../components/ChartPanel.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import { chartColors } from '../chart-theme'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { micronutrientIconFor, resolveMicronutrientIdentityColor } from '../micronutrient-identity'
import { effectiveTheme } from '../theme'
import { useAuthStore } from '../stores/auth'
import type { AnalysisMicronutrientMetric, AnalysisMicronutrientResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const number = createNumberFormatter({ maximumSignificantDigits: 4 })

const maxDays = 365
const limits = computed<PeriodLimits>(() => ({
  minDays: 1, maxDays, customRange: true, maxDate: today.value,
}))
const initialSelection = periodFromQuery(route.query, limits.value, today.value, null)
const start = ref(initialSelection.start)
const end = ref(initialSelection.end)
const period = ref(initialSelection.period)
const metricKey = ref(Array.isArray(route.params.metric) ? route.params.metric[0] ?? '' : String(route.params.metric ?? ''))
const response = ref<AnalysisMicronutrientResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'

const breadcrumbQuery = computed(() => {
  const query = analysisHubQuery(period.value, { start: start.value, end: end.value })
  return { ...route.query, start: query.start, end: query.end, period: query.period }
})

const metric = computed<AnalysisMicronutrientMetric | null>(() => (
  response.value?.metrics.find((item) => item.metric_key === metricKey.value) ?? null
))
const detailPresentation = computed(() => localizedAnalysisPresentation(metricKey.value))
const detailLabel = computed(() => detailPresentation.value?.label ?? t('analysisV2.micronutrientDetailTitle'))
const detailSubtitle = computed(() => t('analysisV2.micronutrientMetricDescription', { metric: detailLabel.value }))
const detailIcon = computed(() => micronutrientIconFor(metricKey.value))
const identityColor = computed(() => {
  void effectiveTheme.value
  return resolveMicronutrientIdentityColor(metricKey.value, chartColors.value.emerald || '#047857')
})
const unit = computed(() => metric.value?.unit === 'ug' ? 'µg' : metric.value?.unit ?? '')
const averageValue = computed(() => {
  const currentMetric = metric.value
  if (!currentMetric || currentMetric.total === null || currentMetric.days_with_data <= 0) return null
  return currentMetric.total / currentMetric.days_with_data
})
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
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const color = identityColor.value
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
      name: detailLabel.value,
      type: 'line',
      smooth: false,
      connectNulls: false,
      showSymbol: true,
      symbolSize: 6,
      data: chartValues.value,
      lineStyle: { color, width: 2 },
      itemStyle: { color, borderColor: palette.background, borderWidth: 1.25 },
      areaStyle: {
        origin: 'start',
        color: new graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(color, 0.24) },
          { offset: 0.45, color: withAlpha(color, 0.1) },
          { offset: 0.75, color: withAlpha(color, 0.03) },
          { offset: 1, color: withAlpha(color, 0) },
        ], false),
      },
    }],
  }
})

function withAlpha(color: string, alpha: number): string {
  const hex = color.match(/^#([\da-f]{6})$/i)
  if (hex) {
    const channels = [0, 2, 4].map((offset) => Number.parseInt(hex[1]!.slice(offset, offset + 2), 16))
    return `rgba(${channels.join(', ')}, ${alpha})`
  }
  const rgb = color.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/i)
  return rgb ? `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, ${alpha})` : color
}

function formatValue(value: number | null): string {
  return value === null ? '–' : `${number.format(value)} ${unit.value}`.trim()
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
  const userId = auth.user?.id
  if (!userId) {
    response.value = null
    loading.value = false
    error.value = ''
    return
  }
  if (!validPeriodRange({ start: start.value, end: end.value }, limits.value)) {
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
  if (requestId !== latestLoadId || auth.user?.id !== userId) return
  const params = new URLSearchParams({
    start: start.value,
    end: end.value,
    metric_key: requestedMetricKey,
  })
  try {
    const result = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (requestId !== latestLoadId || auth.user?.id !== userId) return
    if (result.metrics[0]?.metric_key !== requestedMetricKey) {
      error.value = t('analysisV2.metricNotFound')
      return
    }
    response.value = result
    const admitted = allRange.value
    if (requestedInitialAll) {
      requestedInitialAll = false
      if (admitted) {
        applyPeriod({ ...admitted, period: 'all' })
        return
      }
    }
    if (period.value === 'all' && !admitted) {
      committedAllRange.value = null
      applyPeriod(periodFromQuery({ period: '30' }, limits.value, today.value, null))
    }
  } catch (cause) {
    if (requestId === latestLoadId) {
      requestedInitialAll = false
      error.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (requestId === latestLoadId) loading.value = false
  }
}

function applyPeriod(value: AppliedPeriod): void {
  if (!validPeriodRange(value, limits.value)) return
  requestedInitialAll = false
  const selected = periodFromQuery(value, limits.value, today.value, allRange.value)
  committedAllRange.value = selected.period === 'all'
    ? { start: selected.start, end: selected.end } : null
  period.value = selected.period
  start.value = selected.start
  end.value = selected.end
  void load()
}

watch(
  () => [route.params.metric, route.query.start, route.query.end, route.query.period] as const,
  (current) => {
    const [nextMetric, nextStart, nextEnd, nextPeriod] = current
    const nextKey = Array.isArray(nextMetric) ? nextMetric[0] ?? '' : String(nextMetric ?? '')
    const identityChanged = nextKey !== metricKey.value
    const currentQuery = analysisHubQuery(period.value, { start: start.value, end: end.value })
    if (!identityChanged && nextPeriod === currentQuery.period
      && nextStart === currentQuery.start && nextEnd === currentQuery.end) return
    requestedInitialAll = false
    if (identityChanged) {
      response.value = null
      committedAllRange.value = null
    }
    const selected = periodFromQuery(
      route.query, limits.value, today.value,
      period.value === 'all' ? committedAllRange.value : allRange.value,
    )
    const changed = identityChanged || selected.start !== start.value
      || selected.end !== end.value || selected.period !== period.value
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    metricKey.value = nextKey
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    if (changed) void load()
    else void syncQuery()
  },
)

watch(
  () => [auth.user?.id, auth.user?.timezone] as const,
  (current, previous) => {
    if (previous === undefined || (current[0] === previous[0] && current[1] === previous[1])) return
    latestLoadId += 1
    response.value = null
    loading.value = false
    error.value = ''
    requestedInitialAll = false
    committedAllRange.value = null
    if (!auth.user) return
    const nextMetric = route.params.metric
    const nextKey = Array.isArray(nextMetric) ? nextMetric[0] ?? '' : String(nextMetric ?? '')
    const selected = periodFromQuery(route.query, limits.value, today.value, null)
    metricKey.value = nextKey
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    void load()
  },
  { flush: 'sync' },
)

onMounted(() => { void load() })
onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="analysis-detail-page" :style="{ '--micronutrient-identity-color': identityColor }">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: breadcrumbQuery, hash: route.hash }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/mikronaehrstoffe', query: breadcrumbQuery, hash: route.hash }">{{ t('analysisV2.categories.micronutrients') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ detailLabel }}</span>
    </nav>
    <div class="page-heading page-heading--period">
      <div>
        <h1 class="analysis-detail-title">
          <span class="analysis-detail-icon analysis-detail-icon--micronutrient" aria-hidden="true">
            <component :is="detailIcon" v-if="detailIcon" weight="bold" />
            <AnalysisIcon v-else :icon="detailPresentation?.icon ?? 'leaf'" />
          </span>
          {{ detailLabel }}
        </h1>
        <p>{{ detailSubtitle }}</p>
      </div>
      <PeriodPicker
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :max-date="today"
        :period-capabilities="capability"
        :all-range="allRange"
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
      </p>
      <template v-else>
        <section class="micronutrient-summary">
          <article class="card">
            <h2>{{ t('analysisV2.average') }}</h2>
            <strong>{{ averageValue === null ? '–' : formatValue(averageValue) }}</strong>
          </article>
        </section>
        <ChartPanel :title="detailLabel" :option="chartOption" :empty="false" />
        <AnalysisReadingsDisclosure
          :key="route.path"
          class="card micronutrient-daily-card"
          heading-id="micronutrient-readings-title"
          :title="t('analysisV2.micronutrientReadingsCount', { count: number.format(actualReadings.length) })"
        >
          <div class="table-scroll">
            <table aria-labelledby="micronutrient-readings-title">
              <thead class="sr-only">
                <tr>
                  <th scope="col">{{ t('common.date') }}</th>
                  <th scope="col" class="number">{{ t('analysisV2.micronutrientValue') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in actualReadings" :key="`${point.date}-${point.provider_key}`">
                  <td><time :datetime="point.date">{{ formatGermanDate(point.date) }}</time></td>
                  <td class="number">{{ formatValue(point.value) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </AnalysisReadingsDisclosure>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis-detail-page { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; }
.analysis-detail-page > .page-heading { margin-bottom: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-detail-icon--micronutrient {
  color: var(--micronutrient-identity-color);
  background: color-mix(in srgb, var(--micronutrient-identity-color) 14%, transparent);
}
.micronutrient-summary { display: grid; grid-template-columns: minmax(0, 1fr); gap: .8rem; }
.micronutrient-summary article { display: grid; gap: .35rem; min-width: 0; padding: .9rem; }
.micronutrient-summary h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.micronutrient-summary strong { color: var(--micronutrient-identity-color); font-size: 1.2rem; overflow-wrap: anywhere; }
.micronutrient-empty { display: grid; gap: .4rem; margin: 0; padding: .75rem 1rem; }
.micronutrient-daily-card { padding: .9rem; }
</style>
