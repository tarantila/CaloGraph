<script setup lang="ts">
import { graphic, type EChartsOption } from 'echarts'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { AnalysisIcon, analysisHubQuery, analysisRangeDayCount, isNutritionAnalysisKey, localizedAnalysisPresentation } from '../analysis-v2'
import { analysisRangeWithinDays, periodFromQuery, resolveAllDataRange, type AppliedPeriod, type PeriodLimits, type PeriodRange } from '../analytics-period'
import { chartColors } from '../chart-theme'
import AnalysisReadingsDisclosure from '../components/AnalysisReadingsDisclosure.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisNutritionMetricKey, AnalysisNutritionResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const kcal = createNumberFormatter({ maximumFractionDigits: 0 })
const maxDays = 365
const limits = computed<PeriodLimits>(() => ({ minDays: 1, maxDays, customRange: true, maxDate: today.value }))
const response = ref<AnalysisNutritionResponse | null>(null)
const loading = ref(true)
const error = ref('')
const heading = ref<HTMLHeadingElement | null>(null)
let latestLoadId = 0
let requestedRange = ''
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'
let syncingQuery: ReturnType<typeof analysisHubQuery> | null = null

const metric = computed<AnalysisNutritionMetricKey | null>(() => {
  const key = route.params.metric
  return typeof key === 'string' && isNutritionAnalysisKey(key) ? key : null
})
const presentation = computed(() => metric.value ? localizedAnalysisPresentation(metric.value) : null)
const unit = computed(() => metric.value === 'dietary_energy_kcal' ? 'kcal' : 'g')
const nutritionMetricColorRoles: Record<AnalysisNutritionMetricKey, 'violet' | 'teal' | 'blue' | 'amber' | 'lilac'> = {
  dietary_energy_kcal: 'violet',
  protein_g: 'teal',
  carbohydrates_g: 'blue',
  fat_g: 'amber',
  fiber_g: 'lilac',
  sugar_g: 'lilac',
  saturated_fat_g: 'lilac',
}
const metricColor = computed(() => chartColors.value[
  metric.value ? nutritionMetricColorRoles[metric.value] : 'lilac'
])
const subtitle = computed(() => {
  const description = presentation.value?.description
  if (!description) return t('analysisV2.nutritionDetailDescription')
  return `${description.replace(/[.!?]+$/, '')}${t('analysisV2.nutritionDescriptionPeriodSuffix')}`
})

const selection = computed(() => periodFromQuery(
  route.query, limits.value, today.value,
  route.query.period === 'all' ? committedAllRange.value : allRange.value,
))
const start = computed(() => selection.value.start)
const end = computed(() => selection.value.end)
const period = computed(() => selection.value.period)
const dailyGroups = computed(() => [...(response.value?.days ?? [])]
  .sort((left, right) => left.date.localeCompare(right.date)))
const metricReadings = computed(() => {
  const key = metric.value
  if (!key) return []
  return dailyGroups.value.filter((day) => {
    const value = day[key]
    return typeof value === 'number' && Number.isFinite(value)
  })
})
const metricStats = computed(() => {
  const key = metric.value
  if (!key) return { average: null, minimum: null, maximum: null }
  let count = 0
  let total = 0
  let minimum = Number.POSITIVE_INFINITY
  let maximum = Number.NEGATIVE_INFINITY
  for (const day of metricReadings.value) {
    const value = day[key]
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    count += 1
    total += value
    minimum = Math.min(minimum, value)
    maximum = Math.max(maximum, value)
  }
  return {
    average: count ? total / count : null,
    minimum: count ? minimum : null,
    maximum: count ? maximum : null,
  }
})
const hasMetricData = computed(() => metricReadings.value.length > 0)
const chartDates = computed(() => response.value ? Array.from(
  { length: analysisRangeDayCount({ start: start.value, end: end.value }) },
  (_, index) => shiftIsoDate(start.value, index),
) : [])
const chartValues = computed(() => {
  const key = metric.value
  if (!key) return []
  const byDate = new Map(response.value?.days.map((day) => [day.date, day[key]]) ?? [])
  return chartDates.value.map((date) => byDate.get(date) ?? null)
})
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const color = metricColor.value
  const valueFormatter = metric.value === 'dietary_energy_kcal' ? kcal : number
  return {
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
      valueFormatter: (value) => typeof value === 'number' && Number.isFinite(value)
        ? `${valueFormatter.format(value)} ${unit.value}` : '–',
    },
    grid: { top: 30, left: 12, right: 18, bottom: 12, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: chartDates.value,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: {
        color: palette.label,
        hideOverlap: true,
        formatter: (value: string | number) => formatGermanDate(String(value)),
      },
    },
    yAxis: {
      type: 'value',
      min: 0,
      name: unit.value,
      nameTextStyle: { color: palette.label },
      axisLabel: {
        color: palette.label,
        ...(metric.value === 'dietary_energy_kcal'
          ? { formatter: (value: string | number) => kcal.format(Number(value)) }
          : {}),
      },
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: presentation.value?.label ?? t('analysisV2.nutritionDetailTitle'),
      type: 'line',
      smooth: false,
      connectNulls: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 6,
      data: chartValues.value,
      lineStyle: { color, width: 2.5 },
      itemStyle: { color, borderColor: palette.background, borderWidth: 1.5 },
      areaStyle: {
        origin: 'start',
        color: new graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(color, 0.26) },
          { offset: 0.45, color: withAlpha(color, 0.12) },
          { offset: 0.75, color: withAlpha(color, 0.035) },
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

function formatMetricValue(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '–'
  const formatter = metric.value === 'dietary_energy_kcal' ? kcal : number
  return `${formatter.format(value)} ${unit.value}`
}

async function syncQuery(): Promise<void> {
  const query = analysisHubQuery(period.value, selection.value)
  if (route.query.period === query.period
    && route.query.start === query.start && route.query.end === query.end) return
  syncingQuery = query
  await router.replace({
    query: { ...route.query, start: query.start, end: query.end, period: query.period },
    hash: route.hash,
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  const userId = auth.user?.id
  if (!userId) {
    requestedRange = ''
    response.value = null
    error.value = ''
    loading.value = false
    return
  }
  if (!metric.value) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.nutritionMetricNotFound')
    loading.value = false
    return
  }
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today.value) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.nutritionRangeInvalid')
    loading.value = false
    return
  }
  const range = { start: start.value, end: end.value }
  requestedRange = `${period.value}/${range.start}/${range.end}`
  response.value = null
  error.value = ''
  loading.value = true
  await syncQuery()
  if (requestId !== latestLoadId || auth.user?.id !== userId) return
  const params = new URLSearchParams(range)
  try {
    const result = await api<AnalysisNutritionResponse>(`/analytics/nutrition/analysis?${params.toString()}`)
    if (requestId !== latestLoadId || auth.user?.id !== userId) return
    response.value = result
    const admitted = allRange.value
    if (requestedInitialAll) {
      requestedInitialAll = false
      if (admitted) {
        committedAllRange.value = admitted
        await router.replace({
          path: route.path,
          query: { ...route.query, ...analysisHubQuery('all', admitted), start: undefined, end: undefined },
          hash: route.hash,
        })
        return
      }
    }
    if (period.value === 'all' && !admitted) {
      committedAllRange.value = null
      await load()
    }
  } catch (cause) {
    if (requestId === latestLoadId && auth.user?.id === userId) {
      requestedInitialAll = false
      error.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (requestId === latestLoadId && auth.user?.id === userId) loading.value = false
  }
}

function applyPeriod(value: AppliedPeriod): void {
  requestedInitialAll = false
  const selected = periodFromQuery(value, limits.value, today.value, allRange.value)
  committedAllRange.value = selected.period === 'all'
    ? { start: selected.start, end: selected.end } : null
  void router.push({
    path: route.path,
    query: {
      ...route.query,
      ...analysisHubQuery(selected.period, selected),
      start: selected.period === 'custom' ? selected.start : undefined,
      end: selected.period === 'custom' ? selected.end : undefined,
    },
    hash: route.hash,
  })
}

watch(
  () => [route.params.metric, route.query.start, route.query.end, route.query.period] as const,
  ([nextMetric, nextStart, nextEnd, nextPeriod], [previousMetric, previousStart, previousEnd, previousPeriod]) => {
    // Valid nutrients share one aggregate reader; changing its presentation does not change scope.
    const presentationOnly = typeof nextMetric === 'string' && isNutritionAnalysisKey(nextMetric)
      && typeof previousMetric === 'string' && isNutritionAnalysisKey(previousMetric)
    const identityChanged = nextMetric !== previousMetric && !presentationOnly
    if (identityChanged) {
      latestLoadId += 1
      requestedRange = ''
      response.value = null
      committedAllRange.value = null
    }
    const ownNormalization = !identityChanged && syncingQuery
      && nextPeriod === syncingQuery.period && nextStart === syncingQuery.start && nextEnd === syncingQuery.end
    syncingQuery = null
    if (ownNormalization) return
    const selectionChanged = nextStart !== previousStart || nextEnd !== previousEnd || nextPeriod !== previousPeriod
    if (identityChanged || selectionChanged) requestedInitialAll = false
    const selected = periodFromQuery(
      route.query, limits.value, today.value, identityChanged ? null : committedAllRange.value ?? allRange.value,
    )
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    if (requestedRange === `${period.value}/${start.value}/${end.value}`) void syncQuery()
  },
  { flush: 'sync' },
)
watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], [previousUserId, previousTimezone]) => {
    if (userId === previousUserId && timezone === previousTimezone) return
    const accountChanged = userId !== previousUserId
    latestLoadId += 1
    requestedRange = ''
    response.value = null
    error.value = ''
    if (accountChanged) committedAllRange.value = null
    requestedInitialAll = route.query.period === 'all'
    loading.value = Boolean(userId)
  },
  { flush: 'sync' },
)


watch(
  () => [
    route.params.metric, start.value, end.value, period.value,
    auth.user?.id ?? null, auth.user?.timezone ?? 'UTC',
  ] as const,
  () => {
    const rangeKey = `${period.value}/${start.value}/${end.value}`
    if (!metric.value || !analysisRangeWithinDays(start.value, end.value, maxDays)
      || requestedRange !== rangeKey) void load()
    else void syncQuery()
  },
  { immediate: true },
)
watch(() => route.params.metric, (next, previous) => {
  if (next !== previous) void nextTick(() => heading.value?.focus())
})
onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="analysis-detail-page analysis-nutrition-detail" :style="{ '--nutrition-metric-color': metricColor }">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: { ...route.query, ...analysisHubQuery(period, { start, end }) }, hash: route.hash }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/ernaehrung', query: { ...route.query, ...analysisHubQuery(period, { start, end }) }, hash: route.hash }">{{ t('analysisV2.categories.nutrition') }}</RouterLink>
      <span v-if="metric" aria-hidden="true">›</span>
      <span v-if="metric" aria-current="page">{{ presentation?.label }}</span>
    </nav>
    <div class="page-heading page-heading--period">
      <div>
        <h1 ref="heading" class="analysis-detail-title" tabindex="-1">
          <span class="analysis-detail-icon nutrition-metric-icon" aria-hidden="true">
            <AnalysisIcon :icon="presentation?.icon ?? 'nutrition'" />
          </span>{{ presentation?.label ?? t('analysisV2.nutritionDetailTitle') }}
        </h1>
        <p>{{ subtitle }}</p>
      </div>
      <PeriodPicker
        v-if="metric"
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
    <section v-if="!metric" class="card error" role="alert">
      <p>{{ t('analysisV2.nutritionMetricNotFound') }}</p>
      <RouterLink :to="{ path: '/analyse/katalog', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.nutritionBackToCatalog') }}</RouterLink>
    </section>
    <template v-else>
      <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
      <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
        <p>{{ error }}</p>
        <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
      </section>
      <p v-else-if="response && !hasMetricData" class="card empty nutrition-empty" role="status">{{ t('analysisV2.nutritionEmpty') }}</p>
      <template v-else-if="response">
        <section class="card nutrition-summary">
          <h2>{{ t('analysisV2.average') }}</h2>
          <strong>{{ formatMetricValue(metricStats.average) }}</strong>
        </section>
        <ChartPanel
          :title="presentation?.label ?? t('analysisV2.nutritionDetailTitle')"
          :option="chartOption"
          :empty="!hasMetricData"
        />
        <section class="nutrition-statistics">
          <article class="card">
            <h2>{{ t('analysisV2.minimum') }}</h2>
            <strong>{{ formatMetricValue(metricStats.minimum) }}</strong>
          </article>
          <article class="card">
            <h2>{{ t('analysisV2.maximum') }}</h2>
            <strong>{{ formatMetricValue(metricStats.maximum) }}</strong>
          </article>
        </section>
        <AnalysisReadingsDisclosure
          :key="route.path"
          class="card nutrition-reading-list"
          heading-id="nutrition-readings-title"
          :title="t('analysisV2.metricReadingsCount', { count: number.format(metricReadings.length) })"
        >
          <div class="table-scroll">
            <table aria-labelledby="nutrition-readings-title">
              <thead class="sr-only">
                <tr>
                  <th scope="col">{{ t('common.date') }}</th>
                  <th scope="col" class="number">{{ t('analysisV2.nutritionMetricValue') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="day in metricReadings" :key="day.date">
                  <td><time :datetime="day.date">{{ formatGermanDate(day.date) }}</time></td>
                  <td class="number">{{ formatMetricValue(day[metric]) }}</td>
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
.analysis-nutrition-detail { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; min-width: 0; }
.analysis-nutrition-detail > .page-heading { margin-bottom: 0; }
.analysis-nutrition-detail > .chart-card { min-height: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.nutrition-metric-icon { color: var(--nutrition-metric-color); background: color-mix(in srgb, var(--nutrition-metric-color) 14%, transparent); }
.analysis-nutrition-detail h1:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 3px; }
.nutrition-summary { display: grid; width: min(100%, 24rem); gap: .35rem; padding: .9rem; }
.nutrition-summary h2, .nutrition-statistics h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.nutrition-summary strong { color: var(--nutrition-metric-color); font-size: 1.2rem; overflow-wrap: anywhere; }
.nutrition-statistics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.nutrition-statistics article { display: grid; align-content: start; gap: .35rem; min-width: 0; padding: .9rem; }
.nutrition-statistics strong { font-size: 1.15rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.nutrition-reading-list { min-width: 0; padding: .9rem; }
.nutrition-empty { margin: 0; padding: 1rem; }
.error { padding: 1rem; }
.analysis-breadcrumbs a:focus-visible, .error a:focus-visible, .error button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
@media (max-width: 600px) {
  .analysis-nutrition-detail > .page-heading { min-width: 0; }
  .page-heading > .analysis-period-picker { width: 100%; }
}
</style>
