<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHubQuery, analysisRangeDayCount, isNutritionAnalysisKey, localizedAnalysisPresentation, normalizeAnalysisPeriodForCapability, nutritionAnalysisKeys, parseAnalysisPeriodSelection, supportedAnalysisPeriods, type AnalysisPeriodSelection } from '../analysis-v2'
import { analysisRangeWithinDays } from '../analytics-period'
import { chartColors } from '../chart-theme'
import AnalysisPeriodPicker from '../components/AnalysisPeriodPicker.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisNutritionMetricKey, AnalysisNutritionResponse, AnalysisNutritionValues } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const number = createNumberFormatter({ maximumFractionDigits: 2 })
const maxDays = 365
const presets = supportedAnalysisPeriods(maxDays, false)
const response = ref<AnalysisNutritionResponse | null>(null)
const loading = ref(true)
const error = ref('')
const heading = ref<HTMLHeadingElement | null>(null)
let latestLoadId = 0
let requestedRange = ''

const metric = computed<AnalysisNutritionMetricKey | null>(() => {
  const key = route.params.metric
  return typeof key === 'string' && isNutritionAnalysisKey(key) ? key : null
})
const presentation = computed(() => metric.value ? localizedAnalysisPresentation(metric.value) : null)
const unit = computed(() => metric.value === 'dietary_energy_kcal' ? 'kcal' : 'g')

function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const selection = computed(() => {
  const requested = parseAnalysisPeriodSelection(queryString(route.query.period))
  const start = queryString(route.query.start)
  const end = queryString(route.query.end)
  return normalizeAnalysisPeriodForCapability(
    requested, requested === 'custom' && start && end ? { start, end } : null,
    maxDays, today,
  )
})
const start = computed(() => selection.value.range.start)
const end = computed(() => selection.value.range.end)
const period = computed(() => selection.value.period)
const dailyGroups = computed(() => [...(response.value?.days ?? [])]
  .sort((left, right) => left.date.localeCompare(right.date))
  .map((day) => ({
    day,
    namedMeals: day.meals.filter((meal) => Boolean(meal.display_name?.trim())),
  })))
const hasNutritionData = computed(() => dailyGroups.value.some(({ day, namedMeals }) => (
  nutritionAnalysisKeys.some((key) => day[key] !== null && Number.isFinite(day[key]))
    || namedMeals.length > 0
)))
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
const measuredDays = computed(() => chartValues.value.filter(
  (value) => value !== null && Number.isFinite(value),
).length)
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const energySelected = metric.value === 'dietary_energy_kcal'
  const macroDefinitions = [
    { key: 'protein_g', color: palette.teal },
    { key: 'carbohydrates_g', color: palette.blue },
    { key: 'fat_g', color: palette.amber },
  ] as const
  const daysByDate = energySelected
    ? new Map(response.value?.days.map((day) => [day.date, day]) ?? [])
    : null
  const macroSeries = energySelected ? macroDefinitions.map(({ key, color }) => ({
    name: key === 'protein_g' && i18n.global.locale.value === 'de'
      ? 'Eiweiß' : localizedAnalysisPresentation(key)?.label ?? key,
    type: 'bar' as const,
    stack: 'macros',
    barMaxWidth: 32,
    yAxisIndex: 1,
    data: chartDates.value.map((date) => daysByDate?.get(date)?.[key] ?? null),
    itemStyle: { color },
  })) : []
  const valueAxis = (name: string) => ({
    type: 'value' as const,
    min: 0,
    name,
    nameTextStyle: { color: palette.label },
    axisLabel: { color: palette.label },
    axisLine: { lineStyle: { color: palette.axis } },
    splitLine: { lineStyle: { color: palette.grid, type: 'dashed' as const } },
  })
  return {
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
    },
    ...(energySelected ? { legend: { top: 4, textStyle: { color: palette.label } }, grid: { top: 70 } } : {}),
    xAxis: {
      type: 'category',
      data: chartDates.value,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, hideOverlap: true, formatter: (value: string | number) => formatGermanDate(String(value)) },
    },
    yAxis: energySelected
      ? [valueAxis('kcal'), { ...valueAxis('g'), position: 'right', splitLine: { show: false } }]
      : valueAxis(unit.value),
    series: [{
      name: presentation.value?.label ?? t('analysisV2.nutritionDetailTitle'),
      type: 'line',
      connectNulls: false,
      showSymbol: true,
      data: chartValues.value,
      lineStyle: { color: energySelected ? palette.violet : palette.emerald, width: 2 },
      itemStyle: { color: energySelected ? palette.violet : palette.emerald },
    }, ...macroSeries],
  }
})

function formatValue(values: AnalysisNutritionValues): string {
  const value = metric.value ? values[metric.value] : null
  return value === null ? '–' : `${number.format(value)} ${unit.value}`
}

async function syncQuery(): Promise<void> {
  const query = analysisHubQuery(period.value, selection.value.range)
  if (route.query.period === query.period
    && route.query.start === query.start && route.query.end === query.end) return
  await router.replace({
    query: { ...route.query, start: query.start, end: query.end, period: query.period },
    hash: route.hash,
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  if (!metric.value) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.nutritionMetricNotFound')
    loading.value = false
    return
  }
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.nutritionRangeInvalid')
    loading.value = false
    return
  }
  const range = { start: start.value, end: end.value }
  requestedRange = `${range.start}/${range.end}`
  response.value = null
  error.value = ''
  loading.value = true
  await syncQuery()
  if (requestId !== latestLoadId) return
  const params = new URLSearchParams(range)
  try {
    const result = await api<AnalysisNutritionResponse>(`/analytics/nutrition/analysis?${params.toString()}`)
    if (requestId === latestLoadId) response.value = result
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
  void router.push({
    path: route.path,
    query: {
      ...route.query,
      ...analysisHubQuery(normalized.period, normalized.range),
      start: normalized.period === 'custom' ? normalized.range.start : undefined,
      end: normalized.period === 'custom' ? normalized.range.end : undefined,
    },
    hash: route.hash,
  })
}

watch(
  () => [route.params.metric, start.value, end.value, period.value] as const,
  () => {
    const rangeKey = `${start.value}/${end.value}`
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
  <div class="analysis-nutrition-detail">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/ernaehrung', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.categories.nutrition') }}</RouterLink>
      <span v-if="metric" aria-hidden="true">›</span>
      <span v-if="metric" aria-current="page">{{ presentation?.label }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1 ref="heading" tabindex="-1">{{ presentation?.label ?? t('analysisV2.nutritionDetailTitle') }}</h1>
        <p>{{ presentation?.description ?? t('analysisV2.nutritionDetailDescription') }}</p>
      </div>
      <AnalysisPeriodPicker
        v-if="metric"
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :presets="presets"
        :custom-range="true"
        :max-days="365"
        @apply="applyPeriod"
      />
    </div>
    <section v-if="!metric" class="card error" role="alert">
      <p>{{ t('analysisV2.nutritionMetricNotFound') }}</p>
      <RouterLink :to="{ path: '/analyse/ernaehrung', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.nutritionBackToCatalog') }}</RouterLink>
    </section>
    <template v-else>
      <nav class="nutrition-metric-list" :aria-label="t('analysisV2.nutritionMetrics')">
        <RouterLink
          v-for="key in nutritionAnalysisKeys"
          :key="key"
          :to="{ path: `/analyse/ernaehrung/${key}`, query: route.query }"
          :aria-current="metric === key ? 'page' : undefined"
        >{{ localizedAnalysisPresentation(key)?.label }}</RouterLink>
      </nav>
      <p v-if="response" class="nutrition-coverage">
        {{ t('analysisV2.daysCoverage', { count: measuredDays, total: analysisRangeDayCount({ start, end }) }) }}
      </p>
      <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
      <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
        <p>{{ error }}</p>
        <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
      </section>
      <p v-else-if="response && !hasNutritionData" class="card empty nutrition-empty" role="status">{{ t('analysisV2.nutritionEmpty') }}</p>
      <template v-else-if="response">
        <ChartPanel :title="presentation?.label ?? t('analysisV2.nutritionDetailTitle')" :option="chartOption" />
        <section class="card nutrition-daily">
          <h2>{{ t('analysisV2.nutritionDaily') }}</h2>
          <ul class="nutrition-days">
            <li v-for="{ day, namedMeals } in dailyGroups" :key="day.date" class="nutrition-day">
              <div class="nutrition-day-heading">
                <time :datetime="day.date">{{ formatGermanDate(day.date) }}</time>
                <strong>{{ formatValue(day) }}</strong>
              </div>
              <div class="nutrition-meals">
                <h3>{{ t('analysisV2.nutritionNamedMeals') }}</h3>
                <ul v-if="namedMeals.length">
                  <li v-for="(meal, index) in namedMeals" :key="`${day.date}-${index}`">
                    <span>{{ meal.display_name }}</span>
                    <span>{{ formatValue(meal) }}</span>
                  </li>
                </ul>
                <p v-else>{{ t('analysisV2.nutritionNoNamedMeals') }}</p>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis-nutrition-detail { display: grid; gap: 1rem; min-width: 0; }
.analysis-nutrition-detail > .page-heading { margin-bottom: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-nutrition-detail h1:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 3px; }
.nutrition-metric-list { display: flex; flex-wrap: wrap; gap: .5rem; min-width: 0; }
.nutrition-metric-list a { display: inline-flex; align-items: center; min-height: 2.75rem; padding: .55rem .8rem; border: 1px solid var(--border); border-radius: .6rem; background: var(--surface); color: var(--ink); font-size: .9rem; text-decoration: none; overflow-wrap: anywhere; }
.nutrition-metric-list a:hover { border-color: var(--accent); }
.nutrition-metric-list a[aria-current="page"] { border-color: var(--accent); color: var(--accent-hover); font-weight: 700; background: color-mix(in srgb, var(--accent) 10%, var(--surface)); }
.nutrition-metric-list a:focus-visible, .analysis-breadcrumbs a:focus-visible, .error a:focus-visible, .error button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.nutrition-empty { margin: 0; padding: 1rem; }
.nutrition-coverage { margin: 0; color: var(--muted-strong); }
.nutrition-daily { min-width: 0; padding: 1rem; }
.nutrition-daily h2 { margin: 0 0 .75rem; font-size: 1.08rem; }
.nutrition-days, .nutrition-meals ul { list-style: none; margin: 0; padding: 0; }
.nutrition-day { padding: 1rem 0; border-top: 1px solid var(--border); min-width: 0; }
.nutrition-day-heading, .nutrition-meals li { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .35rem 1rem; min-width: 0; }
.nutrition-day-heading strong { font-size: 1.07rem; }
.nutrition-meals { margin-top: .75rem; padding-left: .9rem; border-left: 2px solid var(--border); min-width: 0; }
.nutrition-meals h3 { margin: 0 0 .35rem; color: var(--muted-strong); font-size: .9rem; }
.nutrition-meals li { padding: .3rem 0; overflow-wrap: anywhere; }
.nutrition-meals p { margin: 0; color: var(--muted); font-size: .9rem; }
@media (max-width: 600px) {
  .page-heading { min-width: 0; }
  .page-heading > .analysis-period-picker { width: 100%; }
  .nutrition-metric-list a { flex: 1 1 auto; justify-content: center; }
}
</style>
