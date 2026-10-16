<script setup lang="ts">
import {
  PhArrowsClockwise,
  PhBarbell,
  PhChartBar,
  PhChartPieSlice,
  PhCheck,
  PhCheckCircle,
  PhDatabase,
  PhFire,
  PhTarget,
  PhTrendUp,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import type { EChartsOption, LineSeriesOption } from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { RouteLocationNormalizedLoaded, Router } from 'vue-router'

import { hasActivityCredit, hasActivityCreditAmount } from '../activity'
import { api, localizeApiError } from '../api'
import {
  periodFromQuery, rangeForDays, resolveAllDataRange,
  type AppliedPeriod, type PeriodLimits,
} from '../analytics-period'
import ChartPanel from '../components/ChartPanel.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import { chartColors } from '../chart-theme'
import { useAuthStore } from '../stores/auth'
import {
  formatGermanDate,
  formatGermanDateTime,
  formatGermanDayMonth,
  isoDateInTimeZone,
  isoWeekday,
  shiftIsoDate,
} from '../date-format'
import { createKcalFormatter, createNumberFormatter, i18n, roundHalfUp } from '../i18n'
import type {
  DailyPoint, ImportBatch, ImportSummary, ReaderPeriodCapabilities, Target, User, YazioStatus,
} from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const route = useRoute() as RouteLocationNormalizedLoaded | undefined
const router = useRouter() as Router | undefined

interface Summary {
  today: DailyPoint
  week: {
    consumed_kcal: number
    budget_kcal: number | null
    deviation_kcal: number | null
    remaining_kcal: number | null
    activity_credit_kcal: number
    effective_budget_kcal: number | null
    effective_deviation_kcal: number | null
    effective_remaining_kcal: number | null
  }
  protein_7d_average_g: number | null
  last_import_at: string | null
  data_start_date: string | null
  data_end_date: string | null
  data_day_count: number
}

const summary = ref<Summary | null>(null)
const localToday = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const today = computed(() => summary.value?.today.date ?? localToday)
const limits: PeriodLimits = { minDays: 1, maxDays: 3661, customRange: true }
const capability = ref<ReaderPeriodCapabilities | undefined>()
const allRange = computed(() => resolveAllDataRange(capability.value, today.value, limits))
const initial = periodFromQuery(route?.query ?? {}, limits, today.value, null)
const start = ref(initial.start)
const end = ref(initial.end)
const period = ref(initial.period)
let requestedInitialAll = route?.query.period === 'all'
let periodLoadId = 0
let periodDisposed = false
const trends = ref<DailyPoint[]>([])
const currentWeekTrends = ref<DailyPoint[]>([])
const currentWeekReady = ref(false)
let summaryGeneration = 0
let currentWeekGeneration: number | null = null
let currentWeekLoadId = 0
let currentWeekLoadGeneration: number | null = null
let currentWeekLoad: Promise<void> | null = null
const targets = ref<Target[]>([])
const imports = ref<ImportBatch[]>([])
const yazioStatus = ref<YazioStatus | null>(null)
const error = ref('')
const loading = ref(true)
const syncingYazio = ref(false)
const syncFeedback = ref('')
const syncFailed = ref(false)
const highlightOverBudget = ref(false)
const highlightSaving = ref(false)
const highlightError = ref(false)
const highlightRetryValue = ref(false)

const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const kcal = createKcalFormatter()
const decimal = createNumberFormatter({ maximumFractionDigits: 1 })
const percentage = createNumberFormatter({ style: 'percent', maximumFractionDigits: 0 })

function addDays(value: string, amount: number) {
  return shiftIsoDate(value, amount)
}

function weekStartFor(date: string): string | null {
  const weekday = isoWeekday(date)
  return weekday == null ? null : addDays(date, -((weekday + 6) % 7))
}

function acceptSummarySnapshot(value: Summary): void {
  summaryGeneration += 1
  currentWeekLoadId += 1
  currentWeekLoadGeneration = null
  currentWeekLoad = null
  currentWeekGeneration = null
  currentWeekReady.value = false
  summary.value = value
}

function rememberCurrentWeekFromTrends(
  generation: number,
  requested: AppliedPeriod,
  points: DailyPoint[],
  currentDate: string,
): boolean {
  const weekStart = weekStartFor(currentDate)
  if (weekStart && (requested.start > weekStart || requested.end < currentDate)) return false
  if (generation !== summaryGeneration || periodDisposed) return false
  if (currentWeekGeneration === generation) return true

  currentWeekLoadId += 1
  currentWeekLoadGeneration = null
  currentWeekLoad = null
  currentWeekTrends.value = weekStart
    ? points.filter((point) => point.date >= weekStart && point.date <= currentDate)
    : []
  currentWeekGeneration = generation
  currentWeekReady.value = true
  return true
}

function loadCurrentWeekTrends(generation: number, currentDate: string): Promise<void> {
  if (
    periodDisposed
    || generation !== summaryGeneration
    || currentWeekGeneration === generation
  ) return Promise.resolve()
  if (currentWeekLoad && currentWeekLoadGeneration === generation) return currentWeekLoad

  const weekStart = weekStartFor(currentDate)
  if (!weekStart) {
    rememberCurrentWeekFromTrends(
      generation,
      { start: currentDate, end: currentDate, period: 'custom' },
      [],
      currentDate,
    )
    return Promise.resolve()
  }

  const requestId = ++currentWeekLoadId
  const params = new URLSearchParams({
    start: weekStart,
    end: currentDate,
    include_incomplete: 'true',
  })
  let pending: Promise<void>
  pending = api<{ points: DailyPoint[] }>(`/analytics/trends?${params.toString()}`)
    .then((result) => {
      if (
        periodDisposed
        || generation !== summaryGeneration
        || requestId !== currentWeekLoadId
        || currentWeekGeneration === generation
      ) return
      currentWeekTrends.value = result.points.filter(
        (point) => point.date >= weekStart && point.date <= currentDate,
      )
      currentWeekGeneration = generation
      currentWeekReady.value = true
    })
    .catch((cause: unknown) => {
      if (
        periodDisposed
        || generation !== summaryGeneration
        || requestId !== currentWeekLoadId
        || currentWeekGeneration === generation
      ) return
      throw cause
    })
    .finally(() => {
      if (currentWeekLoad === pending) {
        currentWeekLoad = null
        currentWeekLoadGeneration = null
      }
    })
  currentWeekLoadGeneration = generation
  currentWeekLoad = pending
  return pending
}

function hasNutritionData(point: DailyPoint) {
  return [point.calories_kcal, point.protein_g, point.carbs_g, point.fat_g].some(
    (value) => value != null,
  )
}

function applyPeriod(value: AppliedPeriod): void {
  requestedInitialAll = false
  const selected = periodFromQuery(value, limits, today.value, allRange.value)
  start.value = selected.start
  end.value = selected.end
  period.value = selected.period
  void loadTrends()
}

watch(
  () => [route?.query.start, route?.query.end, route?.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    // Our own replacement must not reinterpret active All while its request is pending.
    if (nextStart === start.value && nextEnd === end.value && nextPeriod === period.value) return
    const selected = periodFromQuery(route?.query ?? {}, limits, today.value, allRange.value)
    if (selected.start === start.value && selected.end === end.value && selected.period === period.value) return
    requestedInitialAll = false
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    void loadTrends()
  },
)

async function loadTrends(errorTarget: 'period' | 'dashboard' | 'sync' = 'period') {
  if (periodDisposed) return
  const requestId = ++periodLoadId
  const generation = summaryGeneration
  const requested: AppliedPeriod = { start: start.value, end: end.value, period: period.value }
  const updatesDashboardError = errorTarget !== 'period' || summary.value !== null
  let trendResponseLoaded = false
  capability.value = undefined
  if (updatesDashboardError && currentWeekReady.value) error.value = ''
  try {
    await router?.replace({
      query: { ...route?.query, ...requested },
      hash: route?.hash,
    })
    if (requestId !== periodLoadId || generation !== summaryGeneration) return
    const params = new URLSearchParams({
      start: requested.start, end: requested.end, include_incomplete: 'true',
    })
    if (requested.period === 'all') params.set('period', 'all')
    const result = await api<{
      points: DailyPoint[]
      period_capabilities?: ReaderPeriodCapabilities
    }>(`/analytics/trends?${params.toString()}`)
    if (requestId !== periodLoadId || generation !== summaryGeneration) return
    capability.value = result.period_capabilities
    const admitted = resolveAllDataRange(capability.value, today.value, limits)
    if (requestedInitialAll) {
      requestedInitialAll = false
      if (admitted) {
        start.value = admitted.start
        end.value = admitted.end
        period.value = 'all'
        await loadTrends(errorTarget)
        return
      }
    }
    if (requested.period === 'all' && !admitted) {
      const fallback = rangeForDays(today.value, 30)
      start.value = fallback.start
      end.value = fallback.end
      period.value = '30'
      await loadTrends(errorTarget)
      return
    }
    trends.value = result.points
    trendResponseLoaded = true

    const currentDate = summary.value?.today.date
    if (currentDate && currentWeekGeneration !== generation) {
      const weekStart = weekStartFor(currentDate)
      if (!weekStart || (requested.start <= weekStart && requested.end >= currentDate)) {
        rememberCurrentWeekFromTrends(generation, requested, result.points, currentDate)
      } else {
        await loadCurrentWeekTrends(generation, currentDate)
      }
    }
    if (requestId !== periodLoadId || generation !== summaryGeneration) return
    if (updatesDashboardError && currentWeekGeneration === generation) error.value = ''
  } catch (cause) {
    if (requestId !== periodLoadId || generation !== summaryGeneration) return
    requestedInitialAll = false
    if (!trendResponseLoaded) capability.value = undefined
    if (errorTarget === 'sync') {
      syncFailed.value = true
      syncFeedback.value = localizeApiError(cause, 'overviewUi.syncFailed')
    } else if (updatesDashboardError) {
      error.value = localizeApiError(
        cause,
        errorTarget === 'dashboard' ? 'overviewUi.dashboardLoadFailed' : 'overviewUi.periodLoadFailed',
      )
    }
  } finally {
    if (
      requestId === periodLoadId
      && generation === summaryGeneration
      && summary.value
      && (
        currentWeekGeneration === generation
        || error.value !== ''
        || (errorTarget === 'sync' && syncFailed.value)
      )
    ) loading.value = false
  }
}

async function loadDashboard() {
  const [summaryResult, targetResult, importResult, yazioResult] = await Promise.all([
    api<Summary>('/dashboard/summary'),
    api<Target[]>('/settings/targets'),
    api<ImportBatch[]>('/imports'),
    api<YazioStatus>('/yazio/status'),
  ])
  acceptSummarySnapshot(summaryResult)
  targets.value = Array.isArray(targetResult) ? targetResult : []
  imports.value = Array.isArray(importResult) ? importResult : []
  yazioStatus.value = yazioResult
  await loadTrends('dashboard')
}

async function reloadDashboard() {
  error.value = ''
  loading.value = true
  try {
    await loadDashboard()
  } catch (cause) {
    error.value = localizeApiError(cause, 'overviewUi.dashboardLoadFailed')
    loading.value = false
  }
}


async function saveHighlightOverBudget(value: boolean): Promise<void> {
  if (highlightSaving.value) return
  const confirmed = auth.user?.highlight_over_budget ?? false
  const generation = auth.beginProfileUpdate()
  highlightOverBudget.value = value
  highlightRetryValue.value = value
  highlightSaving.value = true
  highlightError.value = false
  try {
    await auth.enqueueProfileUpdate(
      generation,
      () => api<User>('/settings/profile', {
        method: 'PUT',
        body: JSON.stringify({ highlight_over_budget: value }),
      }),
      (updated) => {
        auth.commitProfileUpdate(generation, updated)
      },
    )
  } catch {
    if (auth.isCurrentProfileUpdate(generation)) {
      highlightOverBudget.value = confirmed
      highlightError.value = true
    }
  } finally {
    highlightSaving.value = false
  }
}

function retryHighlightOverBudget(): void {
  void saveHighlightOverBudget(highlightRetryValue.value)
}
onMounted(() => {
  highlightOverBudget.value = auth.user?.highlight_over_budget ?? false
  highlightRetryValue.value = highlightOverBudget.value
  void reloadDashboard()
})
onBeforeUnmount(() => {
  periodDisposed = true
  periodLoadId += 1
  currentWeekLoadId += 1
  currentWeekLoadGeneration = null
  currentWeekLoad = null
})

async function syncYazioNow() {
  if (
    syncingYazio.value ||
    !yazioStatus.value?.available ||
    !yazioStatus.value.configured ||
    !yazioStatus.value.sync_enabled
  ) return
  syncingYazio.value = true
  syncFeedback.value = ''
  syncFailed.value = false
  try {
    const result = await api<ImportSummary>('/yazio/sync', { method: 'POST' })
    syncFeedback.value = t('overviewUi.syncCounts', { new: integer.format(result.inserted), updated: integer.format(result.updated), unchanged: integer.format(result.skipped) })
    const [summaryResult, importResult, yazioResult] = await Promise.all([
      api<Summary>('/dashboard/summary'),
      api<ImportBatch[]>('/imports'),
      api<YazioStatus>('/yazio/status'),
    ])
    acceptSummarySnapshot(summaryResult)
    imports.value = Array.isArray(importResult) ? importResult : []
    yazioStatus.value = yazioResult
    await loadTrends('sync')
  } catch (cause) {
    syncFailed.value = true
    syncFeedback.value = localizeApiError(cause, 'overviewUi.syncFailed')
  } finally {
    syncingYazio.value = false
  }
}

const currentTarget = computed(() => {
  if (!summary.value) return null
  const today = summary.value.today.date
  return (
    targets.value.find(
      (target) =>
        target.valid_from <= today && (target.valid_to == null || today < target.valid_to),
    ) ?? null
  )
})

const todayActivityRelevant = computed(() =>
  summary.value != null && hasActivityCredit(summary.value.today),
)
const calorieTarget = computed(() => {
  const today = summary.value?.today
  if (!today) return currentTarget.value?.calories_kcal ?? null
  return todayActivityRelevant.value
    ? today.effective_budget_kcal
    : today.target_kcal ?? currentTarget.value?.calories_kcal ?? null
})
const proteinTarget = computed(() => currentTarget.value?.protein_g ?? null)
const todayCalories = computed(() => summary.value?.today.calories_kcal ?? null)
const todayProtein = computed(() => summary.value?.today.protein_g ?? null)
const caloriesRemaining = computed(() => {
  if (todayCalories.value == null || calorieTarget.value == null) return null
  return calorieTarget.value - todayCalories.value
})
const todayActivityNote = computed(() => {
  const today = summary.value?.today
  if (!today || !hasActivityCredit(today)) return null
  return t('activity.creditForToday', {
    value: kcal.format(Number(today.activity_credit_kcal)),
  })
})
const weekActivityRelevant = computed(() =>
  summary.value != null && hasActivityCreditAmount(summary.value.week.activity_credit_kcal),
)
const weekRemaining = computed(() => {
  const week = summary.value?.week
  if (!week) return null
  return weekActivityRelevant.value ? week.effective_remaining_kcal : week.remaining_kcal
})

const proteinProgress = computed(() => {
  if (todayProtein.value == null || !proteinTarget.value) return 0
  return Math.min(todayProtein.value / proteinTarget.value, 1)
})

const sevenRecordedDays = computed(() =>
  trends.value.filter((point) => point.calories_kcal != null).slice(-7),
)

function average(values: Array<number | null | undefined>) {
  const present = values.filter((value): value is number => value != null)
  return present.length
    ? present.reduce((total, value) => total + Number(value), 0) / present.length
    : null
}

const sevenDayCalories = computed(() =>
  average(sevenRecordedDays.value.map((point) => point.calories_kcal)),
)
const sevenDayProtein = computed(() =>
  average(sevenRecordedDays.value.map((point) => point.protein_g)),
)
const sevenDayCarbs = computed(() => average(sevenRecordedDays.value.map((point) => point.carbs_g)))
const sevenDayFat = computed(() => average(sevenRecordedDays.value.map((point) => point.fat_g)))

const todayComparison = computed(() => {
  if (todayCalories.value == null || sevenDayCalories.value == null) return t('overview.noSevenDayAverage')
  const difference = roundHalfUp(todayCalories.value - sevenDayCalories.value)
  if (difference === 0) return t('overview.exactSevenDayAverage')
  return `${kcal.format(Math.abs(difference))} kcal ${difference > 0 ? t('common.above') : t('common.below')} ${t('overview.sevenDayAverage')}`
})

const dateHeading = computed(() =>
  summary.value ? formatGermanDate(summary.value.today.date) : '',
)

const rangeLabel = computed(() => {
  if (!trends.value.length) return t('overviewUi.rangeNoDays')
  const first = formatGermanDayMonth(trends.value[0].date)
  const last = formatGermanDate(trends.value.at(-1)!.date)
  const recordedDays = trends.value.filter(hasNutritionData).length
  return t('overviewUi.rangeWithData', { first, last, recorded: recordedDays, total: trends.value.length })
})

const chartLabelInterval = computed(() =>
  Math.max(0, Math.ceil(trends.value.length / 9) - 1),
)

const latestImport = computed(() => imports.value[0] ?? null)
const sourceLabel = computed(() => {
  if (yazioStatus.value?.configured) return 'YAZIO'
  if (!latestImport.value) return t('overview.noSource')
  return latestImport.value.source_type.startsWith('yazio') ? 'YAZIO' : t('overview.imported')
})
const lastImportLabel = computed(() => {
  const value =
    yazioStatus.value?.last_success_at ??
    summary.value?.last_import_at ??
    latestImport.value?.finished_at
  return value ? formatGermanDateTime(value) : t('overviewUi.notSynced')
})
const sourceDescription = computed(() =>
  yazioStatus.value?.last_success_at ||
  summary.value?.last_import_at ||
  latestImport.value?.finished_at
    ? t('overview.lastSync', { value: lastImportLabel.value })
    : t('overviewUi.noImport'),
)
const syncScheduleLabel = computed(() => {
  if (yazioStatus.value?.available === false) return t('overviewUi.serverDisabled')
  if (!yazioStatus.value?.configured) return t('overviewUi.noConnection')
  if (yazioStatus.value.scheduler_enabled === false || !yazioStatus.value.sync_enabled) return t('overviewUi.paused')
  if (['pending', 'running', 'failed'].includes(yazioStatus.value.historical_sync?.state ?? '')) {
    return t('overviewUi.historicalRunning')
  }
  const minutes = yazioStatus.value.sync_interval_minutes
  const interval =
    minutes == null
      ? t('overviewUi.automatic')
      : minutes % 60 === 0
        ? t('overviewUi.automaticHours', { value: integer.format(minutes / 60) })
        : t('overviewUi.automaticMinutes', { value: integer.format(minutes) })
  const days = yazioStatus.value.sync_days
  return days == null ? interval : t('overviewUi.lastDays', { interval, days: integer.format(days) })
})

const currentWeekPoints = computed(() =>
  currentWeekReady.value ? currentWeekTrends.value : [],
)

const recordedWeekPoints = computed(() =>
  currentWeekPoints.value.filter((point) => point.calories_kcal != null),
)
const weekAverageCalories = computed(() =>
  average(recordedWeekPoints.value.map((point) => point.calories_kcal)),
)
const weekAverageProtein = computed(() =>
  average(recordedWeekPoints.value.map((point) => point.protein_g)),
)
const budgetForPoint = (point: DailyPoint) =>
  hasActivityCredit(point) ? point.effective_budget_kcal : point.target_kcal
const weekBudgetComparisons = computed(() =>
  recordedWeekPoints.value.flatMap((point) => {
    if (point.calories_kcal == null) return []
    const rawBudgetKcal = budgetForPoint(point)
    if (rawBudgetKcal == null) return []
    const caloriesKcal = Number(point.calories_kcal)
    const budgetKcal = Number(rawBudgetKcal)
    if (!Number.isFinite(caloriesKcal) || !Number.isFinite(budgetKcal)) return []
    return [
      {
        caloriesKcal,
        budgetKcal,
      },
    ]
  }),
)
const weekWithinBudgetCount = computed(
  () =>
    weekBudgetComparisons.value.filter(
      ({ caloriesKcal, budgetKcal }) => caloriesKcal <= budgetKcal,
    ).length,
)
const weekBudgetResultLabel = computed(() => {
  const comparedDays = weekBudgetComparisons.value.length
  if (!comparedDays) return '–'
  return t('overviewUi.weekResult', {
    count: integer.format(weekWithinBudgetCount.value),
    days: integer.format(comparedDays),
    unit: comparedDays === 1 ? t('overviewUi.day') : t('overviewUi.days'),
  })
})
const elapsedWeekDays = computed(() => {
  if (!summary.value) return 0
  const weekday = isoWeekday(summary.value.today.date)
  if (weekday == null) return 0
  const mondayBasedWeekday = (weekday + 6) % 7
  return mondayBasedWeekday + 1
})

const recordedVisibleDays = computed(
  () => trends.value.filter(hasNutritionData).length,
)
const missingVisibleDays = computed(() => trends.value.length - recordedVisibleDays.value)
const coverageRatio = computed(() =>
  trends.value.length ? recordedVisibleDays.value / trends.value.length : 0,
)
const coverageIsComplete = computed(
  () => trends.value.length > 0 && missingVisibleDays.value === 0,
)
const coverageLabel = computed(() =>
  trends.value.length
    ? `${integer.format(recordedVisibleDays.value)} ${t('common.of')} ${integer.format(trends.value.length)} ${t('common.daysDative')} · ${percentage.format(coverageRatio.value)}`
    : t('overviewUi.noPeriod'),
)
const gapLabel = computed(() => {
  if (!trends.value.length) return t('overviewUi.noRange')
  if (!missingVisibleDays.value) return t('overviewUi.noGaps')
  return t('overviewUi.gapDays', {
    count: integer.format(missingVisibleDays.value),
    unit: missingVisibleDays.value === 1 ? t('overviewUi.day') : t('common.days'),
  })
})
const gapDescription = computed(() =>
  !trends.value.length
    ? t('overviewUi.importFirst')
    : missingVisibleDays.value
      ? t('overviewUi.gapsDescription')
      : t('overviewUi.allRecorded'),
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
  const day = trends.value[entries[0].dataIndex]
  const rows = entries.map(
    (entry) =>
      `${entry.marker}${entry.seriesName}: ${
        entry.value == null ? '–' : `${kcal.format(Number(entry.value))} kcal`
      }`,
  )
  if (day && hasActivityCredit(day)) {
    rows.push(
      `${t('activity.activityCredit')}: +${kcal.format(Number(day.activity_credit_kcal))} kcal`,
    )
  }
  return [`<strong>${entries[0].axisValueLabel}</strong>`, ...rows].join('<br/>')
}


const visibleActivityCredit = computed(() => trends.value.some(hasActivityCredit))
const calorieBarData = computed(() =>
  trends.value.map((item) => {
    if (item.calories_kcal == null) return null

    const calories = Number(item.calories_kcal)
    const rawBudget = budgetForPoint(item)
    const budget = Number(rawBudget)
    const isOverBudget =
      rawBudget != null && Number.isFinite(budget) && calories > budget

    if (highlightOverBudget.value && isOverBudget) {
      return {
        value: calories,
        itemStyle: { color: '#fb7185', borderRadius: [5, 5, 0, 0] },
      }
    }

    return calories
  }),
)

const calorieChart = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const chartText = palette.label
  const chartGrid = palette.grid
  const tooltip = {
    backgroundColor: palette.tooltipBackground,
    borderColor: palette.tooltipBorder,
    textStyle: { color: palette.tooltipInk },
  }
  const budgetSeries: LineSeriesOption[] = visibleActivityCredit.value
    ? [
        {
          name: t('activity.baseBudget'),
          type: 'line' as const,
          data: trends.value.map((item) => item.target_kcal),
          step: 'middle' as const,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: palette.baseline, width: 2, type: 'dashed' },
          itemStyle: { color: palette.baseline },
        },
        {
          name: t('activity.effectiveBudget'),
          type: 'line' as const,
          data: trends.value.map((item) =>
            hasActivityCredit(item) ? item.effective_budget_kcal : null,
          ),
          step: 'middle' as const,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: palette.budget, width: 2, type: 'dashed' },
          itemStyle: { color: palette.budget },
        },
      ]
    : [
        {
          name: t('charts.dailyBudget'),
          type: 'line' as const,
          data: trends.value.map((item) => item.target_kcal),
          step: 'middle' as const,
          connectNulls: false,
          showSymbol: false,
          lineStyle: { color: palette.budget, width: 2, type: 'dashed' },
          itemStyle: { color: palette.budget },
        },
      ]
  return {
    backgroundColor: palette.background,
    tooltip: { ...tooltip, trigger: 'axis', formatter: formatCalorieTooltip },
    legend: {
      top: 0,
      right: 0,
      data: [t('overviewUi.intake'), ...budgetSeries.map((series) => String(series.name))],
      itemWidth: 14,
      itemHeight: 8,
      textStyle: { color: chartText, fontFamily: 'Inter' },
    },
    grid: { left: 54, right: 18, top: 46, bottom: 40 },
    xAxis: {
      type: 'category',
      data: trends.value.map((item) => formatGermanDayMonth(item.date)),
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { show: false },
      axisLabel: {
        color: chartText,
        fontFamily: 'Inter',
        fontSize: 9,
        interval: chartLabelInterval.value,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: palette.axis } },
      name: 'kcal',
      nameTextStyle: { color: chartText, fontFamily: 'Inter', padding: [0, 0, 0, -34] },
      axisLabel: { color: chartText, fontFamily: 'Inter', formatter: (value: number) => kcal.format(value) },
      splitLine: { lineStyle: { color: chartGrid, type: 'dashed' } },
    },
    series: [
      {
        name: t('overviewUi.intake'),
        type: 'bar',
        barMaxWidth: 26,
        data: calorieBarData.value,
        itemStyle: { color: palette.violet, borderRadius: [5, 5, 0, 0] },
        emphasis: { itemStyle: { color: palette.violet } },
      },
      ...budgetSeries,
    ],
  }
})

const macroChart = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const chartText = palette.label
  const chartGrid = palette.grid
  const tooltip = {
    backgroundColor: palette.tooltipBackground,
    borderColor: palette.tooltipBorder,
    textStyle: { color: palette.tooltipInk },
  }
  return {
    animationDuration: 500,
    backgroundColor: palette.background,
    tooltip: {
      ...tooltip,
      trigger: 'axis',
      order: 'seriesDesc',
      valueFormatter: (value) => `${decimal.format(Number(value))} g`,
    },
    legend: {
      bottom: 0,
      data: [t('overviewUi.protein'), t('overviewUi.carbs'), t('overviewUi.fat')],
      textStyle: { color: chartText, fontFamily: 'Inter' },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: 48, right: 16, top: 20, bottom: 50 },
    xAxis: {
      type: 'category',
      data: trends.value.map((item) => formatGermanDayMonth(item.date)),
      axisLine: { lineStyle: { color: palette.axis } },
      axisTick: { show: false },
      axisLabel: {
        color: chartText,
        fontFamily: 'Inter',
        fontSize: 9,
        interval: chartLabelInterval.value,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: palette.axis } },
      name: 'g',
      nameTextStyle: { color: chartText, fontFamily: 'Inter' },
      axisLabel: { color: chartText, fontFamily: 'Inter' },
      splitLine: { lineStyle: { color: chartGrid, type: 'dashed' } },
    },
    series: [
      {
        name: t('overviewUi.protein'),
        type: 'bar',
        stack: 'macros',
        barMaxWidth: 26,
        data: trends.value.map((item) => item.protein_g),
        itemStyle: { color: palette.teal },
      },
      {
        name: t('overviewUi.carbs'),
        type: 'bar',
        stack: 'macros',
        barMaxWidth: 26,
        data: trends.value.map((item) => item.carbs_g),
        itemStyle: { color: palette.blue },
      },
      {
        name: t('overviewUi.fat'),
        type: 'bar',
        stack: 'macros',
        barMaxWidth: 26,
        data: trends.value.map((item) => item.fat_g),
        itemStyle: { color: palette.amber, borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <div class="dashboard-heading">
    <div>
      <h1>{{ t('overview.title') }}</h1>
      <p>{{ dateHeading }}</p>
    </div>
    <div class="dashboard-heading-actions">
      <PeriodPicker
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :period-capabilities="capability"
        :all-range="allRange"
        @apply="applyPeriod"
      />
      <span class="dashboard-period-range" aria-live="polite">{{ rangeLabel }}</span>
    </div>
  </div>

  <div v-if="error" class="card error" role="alert">
    <span>{{ error }}</span>
    <button class="button" type="button" @click="reloadDashboard">{{ t('common.tryAgain') }}</button>
  </div>
  <div v-else-if="loading" class="dashboard-loading" aria-live="polite">
    <PhArrowsClockwise :size="22" class="spin" aria-hidden="true" />
    {{ t('overviewUi.dashboardLoading') }}
  </div>
  <template v-else-if="summary">
    <section class="dashboard-stats" :aria-label="t('overview.stats')">
      <article class="card metric-card">
        <div class="metric-card-label violet"><PhFire :size="22" weight="duotone" aria-hidden="true" /><span>{{ t('overview.today') }}</span></div>
        <div class="metric-card-value">
          {{ todayCalories == null ? '–' : kcal.format(todayCalories) }}
          <small>{{ t('common.kcal') }}</small>
        </div>
        <p :class="{ warning: todayCalories != null }">{{ todayComparison }}</p>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label purple"><PhTarget :size="22" weight="duotone" aria-hidden="true" /><span>{{ todayActivityRelevant ? t('activity.effectiveRemaining') : t('overview.remaining') }}</span></div>
        <div class="metric-card-value">
          {{ caloriesRemaining == null ? '–' : kcal.format(Math.max(caloriesRemaining, 0)) }}
          <small>{{ t('common.kcal') }}</small>
        </div>
        <p>{{ caloriesRemaining != null && caloriesRemaining < 0 ? t('overviewUi.dailyOverBudget', { value: kcal.format(Math.abs(caloriesRemaining)) }) : t('overviewUi.availableToday') }}</p>
        <small v-if="todayActivityNote" class="activity-credit-note">{{ todayActivityNote }}</small>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label teal"><PhBarbell :size="22" weight="duotone" aria-hidden="true" /><span>{{ t('overview.protein') }}</span></div>
        <div class="metric-card-value compact">
          {{ todayProtein == null ? '–' : decimal.format(todayProtein) }}
          <small v-if="proteinTarget">/ {{ integer.format(proteinTarget) }} {{ t('common.grams') }}</small>
          <small v-else>{{ t('common.grams') }}</small>
        </div>
        <div class="metric-card-progress-label">
          <span>{{ proteinTarget && todayProtein != null ? t('overviewUi.proteinToTarget', { value: integer.format(Math.max(proteinTarget - todayProtein, 0)) }) : t('overviewUi.noProteinTarget') }}</span>
          <strong v-if="proteinTarget">{{ percentage.format(proteinProgress) }}</strong>
        </div>
        <progress class="metric-progress protein" :value="proteinProgress" max="1">
          {{ percentage.format(proteinProgress) }}
        </progress>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label blue"><PhChartPieSlice :size="22" weight="duotone" aria-hidden="true" /><span>{{ weekActivityRelevant ? t('activity.effectiveRemaining') : t('overview.weekRemaining') }}</span></div>
        <div class="metric-card-value">
          {{ weekRemaining == null ? '–' : kcal.format(Math.max(weekRemaining, 0)) }}
          <small v-if="weekRemaining != null">{{ t('common.kcal') }}</small>
        </div>
        <p v-if="weekRemaining == null">{{ t('overviewUi.weekBudgetMissing') }}</p>
        <p v-else-if="weekRemaining < 0">
          {{ t('overviewUi.weeklyOverBudget', { value: kcal.format(Math.abs(weekRemaining)) }) }}
        </p>
        <p v-else>
          {{ weekActivityRelevant ? t('activity.baseBudget') : t('weekly.budgetTable') }} {{ summary.week.budget_kcal == null ? '–' : `${kcal.format(summary.week.budget_kcal)} kcal` }}
          <template v-if="weekActivityRelevant">
            · {{ t('activity.activityCredit') }} +{{ kcal.format(summary.week.activity_credit_kcal) }} kcal
          </template>
        </p>
      </article>
    </section>

    <section class="dashboard-charts" :aria-label="t('overviewUi.nutritionTrend')">
      <ChartPanel
        :title="t('overviewUi.chartCalories')"
        :option="calorieChart"
        :empty="!trends.some((item) => item.calories_kcal != null)"
        :height="318"
      >
        <template #header-actions>
          <div class="chart-header-actions">
            <label class="chart-highlight-toggle" :aria-busy="highlightSaving">
              <input
                :checked="highlightOverBudget"
                type="checkbox"
                role="switch"
                :disabled="highlightSaving"
                :aria-describedby="highlightSaving || highlightError ? 'highlight-over-budget-status' : undefined"
                @change="saveHighlightOverBudget(($event.target as HTMLInputElement).checked)"
              />
              <span>{{ t('overviewUi.highlightBudget') }}</span>
            </label>
            <span
              v-if="highlightSaving"
              id="highlight-over-budget-status"
              class="chart-preference-status"
              role="status"
              aria-live="polite"
            >{{ t('overviewUi.highlightSaving') }}</span>
            <span v-else-if="highlightError" id="highlight-over-budget-status" class="chart-preference-error" role="alert">
              {{ t('overviewUi.highlightSaveFailed') }}
              <button type="button" class="chart-preference-retry" @click="retryHighlightOverBudget">{{ t('common.tryAgain') }}</button>
            </span>
          </div>
        </template>
      </ChartPanel>
      <ChartPanel
        :title="t('overviewUi.chartMacros')"
        :option="macroChart"
        :empty="!trends.some((item) => item.protein_g != null || item.carbs_g != null || item.fat_g != null)"
        :height="318"
      >
        <template #header-actions><span class="chart-range">{{ t('overviewUi.macroRange') }}</span></template>
      </ChartPanel>
    </section>
    <section class="dashboard-bottom-grid" :aria-label="t('overviewUi.summaries')">
      <article class="card summary-card">
        <div class="summary-card-title">
          <div><h2>{{ t('overviewUi.sevenDayAverage') }}</h2><p>{{ t('overviewUi.sevenRecorded', { count: sevenRecordedDays.length }) }}</p></div>
          <PhTrendUp :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="average-grid">
          <div><span>{{ t('overviewUi.calories') }}</span><strong>{{ sevenDayCalories == null ? '–' : `${kcal.format(sevenDayCalories)} ${t('common.kcal')}` }}</strong></div>
          <div><span>{{ t('overviewUi.protein') }}</span><strong>{{ sevenDayProtein == null ? '–' : `${integer.format(sevenDayProtein)} ${t('common.grams')}` }}</strong></div>
          <div><span>{{ t('overviewUi.carbs') }}</span><strong>{{ sevenDayCarbs == null ? '–' : `${integer.format(sevenDayCarbs)} ${t('common.grams')}` }}</strong></div>
          <div><span>{{ t('overviewUi.fat') }}</span><strong>{{ sevenDayFat == null ? '–' : `${integer.format(sevenDayFat)} ${t('common.grams')}` }}</strong></div>
        </div>
      </article>
      <article class="card summary-card weekly-summary-card" :aria-busy="!currentWeekReady">
        <div class="summary-card-title">
          <div><h2>{{ t('overviewUi.weeklySummary') }}</h2><p>{{ t('overviewUi.runningWeek') }}</p></div>
          <PhChartPieSlice :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="weekly-summary-list">
          <div>
            <span class="weekly-summary-icon calories">
              <PhChartBar :size="18" weight="fill" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">{{ t('overviewUi.weekAverage') }}</span>
            <strong>{{ weekAverageCalories == null ? '–' : `${kcal.format(weekAverageCalories)} ${t('common.kcal')}` }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon protein">
              <PhBarbell :size="18" weight="fill" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">{{ t('overviewUi.proteinAverage') }}</span>
            <strong>{{ weekAverageProtein == null ? '–' : `${integer.format(weekAverageProtein)} ${t('common.grams')}` }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon target">
              <PhTarget :size="18" weight="duotone" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">{{ t('overviewUi.budgetKept') }}</span>
            <strong>{{ weekBudgetResultLabel }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon recorded">
              <PhCheck :size="19" weight="bold" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">{{ t('overviewUi.recorded') }}</span>
            <strong>{{ currentWeekReady ? recordedWeekPoints.length : '–' }} {{ t('common.of') }} {{ elapsedWeekDays }} {{ t('common.daysDative') }}</strong>
          </div>
        </div>
      </article>
      <article class="card summary-card quality-card">
        <div class="summary-card-title">
          <div><h2>{{ t('overviewUi.dataStatus') }}</h2><p>{{ t('overviewUi.selectedPeriod') }}</p></div>
          <PhDatabase :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="quality-list">
          <div>
            <span :class="['quality-icon', coverageIsComplete ? 'success' : 'warning']">
              <PhCheckCircle v-if="coverageIsComplete" :size="19" weight="fill" aria-hidden="true" />
              <PhWarningCircle v-else :size="19" weight="fill" aria-hidden="true" />
            </span>
            <span>
              <strong>{{ t('overviewUi.coverage') }}</strong>
              <small>{{ coverageLabel }}</small>
              <progress class="metric-progress data-coverage" :value="coverageRatio" max="1">
                {{ percentage.format(coverageRatio) }}
              </progress>
            </span>
          </div>
          <div>
            <span :class="['quality-icon', missingVisibleDays ? 'warning' : 'success']">
              <PhWarningCircle v-if="missingVisibleDays" :size="19" weight="fill" aria-hidden="true" />
              <PhCheckCircle v-else :size="19" weight="fill" aria-hidden="true" />
            </span>
            <span><strong>{{ gapLabel }}</strong><small>{{ gapDescription }}</small></span>
          </div>
          <div>
            <span class="quality-icon source"><PhArrowsClockwise :size="19" weight="bold" aria-hidden="true" /></span>
            <span><strong>{{ sourceLabel }}</strong><small>{{ sourceDescription }}</small></span>
          </div>
        </div>
        <div class="yazio-sync-panel">
          <button
            type="button"
            class="yazio-sync-button"
            :disabled="syncingYazio || !yazioStatus?.available || !yazioStatus?.configured || !yazioStatus?.sync_enabled"
            :aria-busy="syncingYazio"
            @click="syncYazioNow"
          >
            <PhArrowsClockwise
              :size="17"
              weight="bold"
              :class="{ spin: syncingYazio }"
              aria-hidden="true"
            />
            {{ syncingYazio ? t('overviewUi.syncing') : t('overviewUi.syncNow') }}
          </button>
          <small>{{ syncScheduleLabel }}</small>
          <p
            v-if="syncFeedback || yazioStatus?.last_error"
            :class="['sync-feedback', { error: syncFailed || yazioStatus?.last_error }]"
            :role="syncFailed || yazioStatus?.last_error ? 'alert' : 'status'"
          >
            {{ syncFeedback || yazioStatus?.last_error }}
          </p>
        </div>
      </article>
    </section>
  </template>
</template>
