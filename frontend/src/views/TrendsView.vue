<script setup lang="ts">
import {
  PhBarbell,
  PhCalendarBlank,
  PhChartBar,
  PhCheckCircle,
  PhTrendUp,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import type { EChartsOption, LineSeriesOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

import { hasActivityCredit } from '../activity'
import { api, localizeApiError } from '../api'
import { parseAnalyticsCompactPreset, type AnalyticsCompactPreset } from '../analytics-period'
import AnalyticsPeriodFilter from '../components/AnalyticsPeriodFilter.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, formatGermanDayMonth, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { DailyPoint } from '../types'
interface BudgetBalance {
  tracked_days: number
  within_budget_days: number
  over_budget_days: number
  over_maintenance_days: number
  unclassified_budget_days: number
}

const t = i18n.global.t.bind(i18n.global)
const route = useRoute() as RouteLocationNormalizedLoaded | undefined
const router = useRouter() as Router | undefined
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const defaultStart = shiftIsoDate(today, -89)

function queryValue(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function validIsoDate(value: string | null): value is string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/u.test(value)) return false
  const parsed = new Date(`${value}T00:00:00Z`)
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value
}

const queryStart = queryValue(route?.query.start)
const queryEnd = queryValue(route?.query.end)
const hasValidQueryRange = validIsoDate(queryStart) && validIsoDate(queryEnd) && queryStart <= queryEnd
const start = ref(hasValidQueryRange ? queryStart : defaultStart)
const end = ref(hasValidQueryRange ? queryEnd : today)
const compactPresets: AnalyticsCompactPreset[] = ['30', '90', '180', 'year', 'all']
const queryPeriod = parseAnalyticsCompactPreset(route?.query.period, compactPresets)

function periodMatchesRange(value: AnalyticsCompactPreset | undefined, rangeStart: string, rangeEnd: string, hasRange: boolean) {
  if (!value || !hasRange) return undefined
  if (value === 'custom' || value === 'all') return value
  const expectedStart = value === 'year'
    ? `${rangeEnd.slice(0, 4)}-01-01`
    : shiftIsoDate(rangeEnd, -(Number(value) - 1))
  return rangeStart === expectedStart ? value : undefined
}

const period = ref<AnalyticsCompactPreset | undefined>(
  periodMatchesRange(queryPeriod, start.value, end.value, hasValidQueryRange),
)
const points = ref<DailyPoint[]>([])
const budgetBalance = ref<BudgetBalance | null>(null)
const loading = ref(true)
const error = ref('')
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const decimal = createNumberFormatter({ maximumFractionDigits: 1 })

function selectPeriod(value: AnalyticsCompactPreset) {
  period.value = value
}

const rangePreset = computed(() => {
  if (start.value === shiftIsoDate(end.value, -29)) return '30'
  if (start.value === shiftIsoDate(end.value, -89)) return '90'
  if (start.value === shiftIsoDate(end.value, -179)) return '180'
  if (start.value === `${end.value.slice(0, 4)}-01-01`) return 'year'
  return 'custom'
})

watch(
  () => [
    queryValue(route?.query.start),
    queryValue(route?.query.end),
    queryValue(route?.query.period),
  ] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const hasValidRange =
      validIsoDate(nextStart) && validIsoDate(nextEnd) && nextStart <= nextEnd
    const resolvedStart = hasValidRange ? nextStart : defaultStart
    const resolvedEnd = hasValidRange ? nextEnd : today
    const resolvedPeriod = periodMatchesRange(
      parseAnalyticsCompactPreset(nextPeriod, compactPresets),
      resolvedStart,
      resolvedEnd,
      hasValidRange,
    )
    if (resolvedPeriod !== period.value) period.value = resolvedPeriod
    if (resolvedStart === start.value && resolvedEnd === end.value) return
    start.value = resolvedStart
    end.value = resolvedEnd
    void load()
  },
)


async function load() {
  if (!validIsoDate(start.value) || !validIsoDate(end.value) || start.value > end.value) {
    error.value = t('settingsUi.invalidRange')
    points.value = []
    budgetBalance.value = null
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  await router?.replace({
    query: { ...route?.query, start: start.value, end: end.value, period: period.value || undefined },
  })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (period.value === 'all') params.set('period', 'all')
    const response = await api<{ points: DailyPoint[]; budget_balance: BudgetBalance }>(
      `/analytics/trends?${params.toString()}`,
    )
    points.value = response.points
    budgetBalance.value = response.budget_balance ?? null
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}
onMounted(() => { void load() })

const latestNutrition = computed(() =>
  [...points.value].reverse().find((item) => item.calories_kcal != null),
)
const recordedDays = computed(() => points.value.filter((item) => item.calories_kcal != null).length)
const latestSevenDayAverage = computed(() =>
  [...points.value].reverse().find((item) => item.average_7d != null)?.average_7d ?? null,
)
const activityRelevant = computed(() => points.value.some(hasActivityCredit))
const activityCreditData = computed(() =>
  points.value.map((item) =>
    hasActivityCredit(item) ? item.activity_credit_kcal : null,
  ),
)
const rangeLabel = computed(() =>
  `${formatGermanDayMonth(start.value)} – ${formatGermanDate(end.value)}`,
)

type CalorieTooltipEntry = {
  axisValueLabel: string
  dataIndex: number
  marker: string
  seriesName: string
  value: number | null
}

function formatCalorieTooltip(params: unknown) {
  const entries = Array.isArray(params) ? params as CalorieTooltipEntry[] : []
  if (!entries.length) return ''
  const day = points.value[entries[0].dataIndex]
  if (!day) return ''
  const marker = (seriesName: string) => entries.find((entry) => entry.seriesName === seriesName)?.marker ?? ''
  const formatRow = (label: string, value: number | null, prefix = '', seriesName = label) =>
    `${marker(seriesName)}${label}: ${value == null ? '–' : `${prefix}${integer.format(Number(value))} kcal`}`
  const rows = [
    formatRow(t('charts.calories'), day.calories_kcal, '', t('charts.calories')),
  ]
  if (hasActivityCredit(day)) {
    rows.push(formatRow(
      t('activity.activityCredit'),
      day.activity_credit_kcal,
      '+',
      t('activity.activityCredit'),
    ))
    if (day.target_kcal != null) {
      rows.push(formatRow(t('activity.baseBudget'), day.target_kcal, '', t('activity.baseBudget')))
    }
    if (day.effective_budget_kcal != null) {
      rows.push(formatRow(
        t('charts.effectiveDailyBudget'),
        day.effective_budget_kcal,
        '',
        t('charts.effectiveDailyBudget'),
      ))
    }
  } else if (day.target_kcal != null) {
    rows.push(formatRow(t('charts.dailyBudget'), day.target_kcal, '', t('charts.dailyBudget')))
  }
  if (day.average_7d != null) rows.push(formatRow(t('trends.average7'), day.average_7d))
  if (day.average_14d != null) rows.push(formatRow(t('trends.average14'), day.average_14d))
  if (day.average_28d != null) rows.push(formatRow(t('trends.average28'), day.average_28d))
  return [`<strong>${entries[0].axisValueLabel}</strong>`, ...rows].join('<br/>')
}

const tooltip = {
  trigger: 'axis' as const,
  backgroundColor: '#111d30',
  borderColor: '#324157',
  textStyle: { color: '#f3f6fb' },
}
const legendText = { color: '#98a5b9', fontFamily: 'Inter' }
const axisLine = { lineStyle: { color: '#263449' } }
const axisLabel = { color: '#98a5b9', fontFamily: 'Inter' }
const splitLine = { lineStyle: { color: '#263449', type: 'dashed' as const } }

const calorieOption = computed<EChartsOption>(() => {
  const budgetSeries: LineSeriesOption[] = activityRelevant.value
    ? [
        {
          name: t('activity.baseBudget'),
          type: 'line' as const,
          showSymbol: false,
          data: points.value.map((item) => item.target_kcal),
          lineStyle: { color: '#64748b', type: 'dashed', width: 2 },
          itemStyle: { color: '#64748b' },
        },
        {
          name: t('charts.effectiveDailyBudget'),
          type: 'line' as const,
          showSymbol: false,
          data: points.value.map((item) =>
            hasActivityCredit(item) ? item.effective_budget_kcal : null,
          ),
          lineStyle: { color: '#fb923c', type: 'dashed', width: 2 },
          itemStyle: { color: '#fb923c' },
        },
      ]
    : [
        {
          name: t('charts.dailyBudget'),
          type: 'line' as const,
          showSymbol: false,
          data: points.value.map((item) => item.target_kcal),
          lineStyle: { color: '#64748b', type: 'dashed', width: 2 },
          itemStyle: { color: '#64748b' },
        },
      ]
  return {
    animationDuration: 500,
    tooltip: { ...tooltip, formatter: formatCalorieTooltip },
    legend: {
      top: 0,
      right: 0,
      data: [
        t('charts.calories'),
        ...(activityRelevant.value ? [t('activity.activityCredit')] : []),
        t('trends.average7'),
        t('trends.average14'),
        t('trends.average28'),
        ...budgetSeries.map((series) => String(series.name)),
      ],
      textStyle: legendText,
      itemWidth: 14,
      itemHeight: 8,
    },
    grid: { left: 58, right: 18, top: 54, bottom: 38 },
    xAxis: {
      type: 'category',
      data: points.value.map((item) => formatGermanDayMonth(item.date)),
      axisLine,
      axisTick: { show: false },
      axisLabel: { ...axisLabel, hideOverlap: true },
    },
    yAxis: { type: 'value', name: t('common.kcal'), nameTextStyle: axisLabel, axisLabel, splitLine },
    series: [
      {
        name: t('charts.calories'),
        type: 'bar',
        stack: 'calories',
        barMaxWidth: 24,
        data: points.value.map((item) => item.calories_kcal),
        itemStyle: { color: '#8b5cf6', borderRadius: [4, 4, 0, 0] },
      },
      ...(activityRelevant.value
        ? [{
            name: t('activity.activityCredit'),
            type: 'bar' as const,
            stack: 'calories',
            barMaxWidth: 24,
            data: activityCreditData.value,
            itemStyle: {
              color: '#f6c445',
              borderColor: '#ffe08a',
              borderWidth: 1,
              borderRadius: [3, 3, 0, 0],
            },
          }]
        : []),
      {
        name: t('trends.average7'),
        type: 'line',
        showSymbol: false,
        data: points.value.map((item) => item.average_7d),
        lineStyle: { color: '#2dd4bf', width: 3 },
      },
      {
        name: t('trends.average14'),
        type: 'line',
        showSymbol: false,
        data: points.value.map((item) => item.average_14d),
        lineStyle: { color: '#5b8ff9', width: 2 },
      },
      {
        name: t('trends.average28'),
        type: 'line',
        showSymbol: false,
        data: points.value.map((item) => item.average_28d),
        lineStyle: { color: '#d19bff', width: 2 },
      },
      ...budgetSeries,
    ],
  }
})

const nutritionOption = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: {
    ...tooltip,
    trigger: 'axis',
    valueFormatter: (value) => `${decimal.format(Number(value))} ${t('common.grams')}`,
  },
  legend: {
    top: 0,
    right: 0,
    data: [t('charts.protein'), t('charts.carbs'), t('charts.fat')],
    textStyle: legendText,
  },
  grid: { left: 58, right: 18, top: 48, bottom: 38 },
  xAxis: {
    type: 'category',
    data: points.value.map((item) => formatGermanDayMonth(item.date)),
    axisLine,
    axisTick: { show: false },
    axisLabel: { ...axisLabel, hideOverlap: true },
  },
  yAxis: { type: 'value', name: t('common.grams'), nameTextStyle: axisLabel, axisLabel, splitLine },
  series: [
    {
      name: t('charts.protein'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.protein_g),
      lineStyle: { color: '#2dd4bf', width: 2 },
    },
    {
      name: t('charts.carbs'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.carbs_g),
      lineStyle: { color: '#f6ad35', width: 2 },
    },
    {
      name: t('charts.fat'),
      type: 'line',
      showSymbol: false,
      data: points.value.map((item) => item.fat_g),
      lineStyle: { color: '#a78bfa', width: 2 },
    },
  ],
}))
</script>

<template>
  <div class="page-heading analytics-page-heading">
    <div class="analytics-page-heading-content">
      <div><h1>{{ t('trends.title') }}</h1><p>{{ t('trends.description') }}</p></div>
      <AnalyticsPeriodFilter
        v-model:start="start"
        v-model:end="end"
        :initial-preset="rangePreset"
        :compact-presets="compactPresets"
        :compact-preset="period"
        @preset="selectPeriod"
        @apply="load"
      />
    </div>
  </div>
  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('trends.loading') }}</div>
  <template v-else>
    <section class="insight-strip" :aria-label="t('trends.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhTrendUp :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.average7') }}</small><strong>{{ latestSevenDayAverage == null ? '–' : `${integer.format(latestSevenDayAverage)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhChartBar :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.latestDay') }}</small><strong>{{ latestNutrition?.calories_kcal == null ? '–' : `${integer.format(latestNutrition.calories_kcal)} kcal` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhBarbell :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.latestProtein') }}</small><strong>{{ latestNutrition?.protein_g == null ? '–' : `${decimal.format(latestNutrition.protein_g)} g` }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhCalendarBlank :size="20" weight="duotone" /></span>
        <span><small>{{ t('trends.dataBasis') }}</small><strong>{{ t('trends.recordedDays', { recorded: recordedDays, total: points.length }) }}</strong></span>
      </article>
    </section>
    <section class="card budget-balance-section" aria-labelledby="budget-balance-title">
      <div class="section-card-header">
        <div>
          <h2 id="budget-balance-title">{{ t('budgetBalance.title') }}</h2>
          <p>{{ t('budgetBalance.description') }}</p>
        </div>
      </div>
      <div class="budget-balance-grid">
        <article class="card insight-card">
          <span class="insight-icon purple"><PhCalendarBlank :size="20" weight="duotone" aria-hidden="true" /></span>
          <span>
            <small>{{ t('budgetBalance.tracked') }}</small>
            <strong>{{ budgetBalance?.tracked_days ?? '–' }}</strong>
            <small
              v-if="budgetBalance?.unclassified_budget_days"
              class="budget-balance-secondary"
            >{{ t('budgetBalance.unclassifiedCount', { count: budgetBalance.unclassified_budget_days }) }}</small>
          </span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" aria-hidden="true" /></span>
          <span><small>{{ t('budgetBalance.within') }}</small><strong>{{ budgetBalance?.within_budget_days ?? '–' }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" aria-hidden="true" /></span>
          <span><small>{{ t('budgetBalance.over') }}</small><strong>{{ budgetBalance?.over_budget_days ?? '–' }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon red"><PhWarningCircle :size="20" weight="duotone" aria-hidden="true" /></span>
          <span><small>{{ t('budgetBalance.maintenance') }}</small><strong>{{ budgetBalance?.over_maintenance_days ?? '–' }}</strong></span>
        </article>
      </div>
    </section>
    <div class="trend-chart-grid">
      <ChartPanel
        class="trend-chart-wide"
        :title="t('trends.caloriesChart')"
        :option="calorieOption"
        :empty="!points.some((item) => item.calories_kcal != null)"
        :height="350"
      >
        <template #header-actions><span class="chart-range">{{ rangeLabel }}</span></template>
      </ChartPanel>
      <ChartPanel
        class="trend-chart-wide"
        :title="t('trends.macroChart')"
        :option="nutritionOption"
        :empty="!points.some((item) => item.protein_g != null || item.carbs_g != null || item.fat_g != null)"
        :height="310"
      />
    </div>
  </template>
</template>
