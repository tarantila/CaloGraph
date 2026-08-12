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
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError } from '../api'
import ChartPanel from '../components/ChartPanel.vue'
import {
  formatGermanDate,
  formatGermanDateTime,
  formatGermanDayMonth,
  isoWeekday,
  shiftIsoDate,
} from '../date-format'
import type { DailyPoint, ImportBatch, ImportSummary, Target, YazioStatus } from '../types'

interface Summary {
  today: DailyPoint
  week: {
    consumed_kcal: number
    budget_kcal: number | null
    deviation_kcal: number | null
    remaining_kcal: number | null
  }
  protein_7d_average_g: number | null
  last_import_at: string | null
  data_start_date: string | null
  data_end_date: string | null
  data_day_count: number
}

type PeriodValue = 7 | 30 | 60 | 'all'

const periods: ReadonlyArray<{ value: PeriodValue; label: string }> = [
  { value: 7, label: '7 Tage' },
  { value: 30, label: '30 Tage' },
  { value: 60, label: '60 Tage' },
  { value: 'all', label: 'Alle' },
]
const summary = ref<Summary | null>(null)
const trends = ref<DailyPoint[]>([])
const targets = ref<Target[]>([])
const imports = ref<ImportBatch[]>([])
const yazioStatus = ref<YazioStatus | null>(null)
const periodDays = ref<PeriodValue>(30)
const error = ref('')
const loading = ref(true)
const syncingYazio = ref(false)
const syncFeedback = ref('')
const syncFailed = ref(false)
const highlightOverBudget = ref(false)

const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 })
const percentage = new Intl.NumberFormat('de-DE', { style: 'percent', maximumFractionDigits: 0 })

function addDays(value: string, amount: number) {
  return shiftIsoDate(value, amount)
}


function hasNutritionData(point: DailyPoint) {
  return [point.calories_kcal, point.protein_g, point.carbs_g, point.fat_g].some(
    (value) => value != null,
  )
}

async function loadTrends() {
  if (!summary.value) return
  const end = summary.value.today.date
  const start =
    periodDays.value === 'all'
      ? (summary.value.data_start_date ?? end)
      : addDays(end, -(periodDays.value - 1))
  const result = await api<{ points: DailyPoint[] }>(
    `/analytics/trends?start=${start}&end=${end}&include_incomplete=true`,
  )
  trends.value = result.points
}

async function selectPeriod(value: PeriodValue) {
  periodDays.value = value
  try {
    await loadTrends()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Zeitraum konnte nicht geladen werden.'
  }
}

async function loadDashboard() {
  const [summaryResult, targetResult, importResult, yazioResult] = await Promise.all([
    api<Summary>('/dashboard/summary'),
    api<Target[]>('/settings/targets'),
    api<ImportBatch[]>('/imports'),
    api<YazioStatus>('/yazio/status'),
  ])
  summary.value = summaryResult
  targets.value = Array.isArray(targetResult) ? targetResult : []
  imports.value = Array.isArray(importResult) ? importResult : []
  yazioStatus.value = yazioResult
  await loadTrends()
}

onMounted(async () => {
  try {
    await loadDashboard()
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Dashboard konnte nicht geladen werden.'
  } finally {
    loading.value = false
  }
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
    syncFeedback.value = `${integer.format(result.inserted)} neu · ${integer.format(result.updated)} aktualisiert · ${integer.format(result.skipped)} unverändert`
    const [summaryResult, importResult, yazioResult] = await Promise.all([
      api<Summary>('/dashboard/summary'),
      api<ImportBatch[]>('/imports'),
      api<YazioStatus>('/yazio/status'),
    ])
    summary.value = summaryResult
    imports.value = Array.isArray(importResult) ? importResult : []
    yazioStatus.value = yazioResult
    await loadTrends()
  } catch (cause) {
    syncFailed.value = true
    syncFeedback.value =
      cause instanceof ApiError ? cause.message : 'YAZIO konnte nicht synchronisiert werden.'
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

const calorieTarget = computed(
  () => summary.value?.today.target_kcal ?? currentTarget.value?.calories_kcal ?? null,
)
const proteinTarget = computed(() => currentTarget.value?.protein_g ?? null)
const todayCalories = computed(() => summary.value?.today.calories_kcal ?? null)
const todayProtein = computed(() => summary.value?.today.protein_g ?? null)
const caloriesRemaining = computed(() => {
  if (todayCalories.value == null || calorieTarget.value == null) return null
  return calorieTarget.value - todayCalories.value
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
  if (todayCalories.value == null || sevenDayCalories.value == null) return 'Noch kein 7-Tage-Schnitt'
  const difference = Math.round(todayCalories.value - sevenDayCalories.value)
  if (difference === 0) return 'Genau im 7-Tage-Schnitt'
  return `${integer.format(Math.abs(difference))} kcal ${difference > 0 ? 'über' : 'unter'} 7-Tage-Schnitt`
})

const dateHeading = computed(() =>
  summary.value ? formatGermanDate(summary.value.today.date) : '',
)

const rangeLabel = computed(() => {
  if (!trends.value.length) return 'Keine erfassten Tage'
  const first = formatGermanDayMonth(trends.value[0].date)
  const last = formatGermanDate(trends.value.at(-1)!.date)
  const recordedDays = trends.value.filter(hasNutritionData).length
  return `${first} – ${last} · ${recordedDays}/${trends.value.length} mit Daten`
})

const chartLabelInterval = computed(() =>
  Math.max(0, Math.ceil(trends.value.length / 9) - 1),
)

const latestImport = computed(() => imports.value[0] ?? null)
const sourceLabel = computed(() => {
  if (yazioStatus.value?.configured) return 'YAZIO'
  if (!latestImport.value) return 'Keine Datenquelle'
  return latestImport.value.source_type.startsWith('yazio') ? 'YAZIO' : 'Import'
})
const lastImportLabel = computed(() => {
  const value =
    yazioStatus.value?.last_success_at ??
    summary.value?.last_import_at ??
    latestImport.value?.finished_at
  return value ? formatGermanDateTime(value) : 'noch nicht synchronisiert'
})
const sourceDescription = computed(() =>
  yazioStatus.value?.last_success_at ||
  summary.value?.last_import_at ||
  latestImport.value?.finished_at
    ? `Zuletzt synchronisiert: ${lastImportLabel.value}`
    : 'Noch kein Import vorhanden',
)
const syncScheduleLabel = computed(() => {
  if (yazioStatus.value?.available === false) return 'YAZIO ist auf diesem Server deaktiviert'
  if (!yazioStatus.value?.configured) return 'Keine persönliche YAZIO-Verbindung eingerichtet'
  if (!yazioStatus.value.sync_enabled) return 'Automatik pausiert · Zugangsdaten aktualisieren'
  if (['pending', 'running', 'failed'].includes(yazioStatus.value.historical_sync?.state ?? '')) {
    return 'Historische Synchronisierung läuft im Hintergrund · Details unter Importe'
  }
  const minutes = yazioStatus.value.sync_interval_minutes
  const interval =
    minutes == null
      ? 'Automatik eingerichtet'
      : minutes % 60 === 0
        ? `Automatisch alle ${integer.format(minutes / 60)} Std.`
        : `Automatisch alle ${integer.format(minutes)} Min.`
  const days = yazioStatus.value.sync_days
  return days == null ? interval : `${interval} · letzte ${integer.format(days)} Tage`
})

const currentWeekPoints = computed(() => {
  if (!summary.value) return []
  const weekday = isoWeekday(summary.value.today.date)
  if (weekday == null) return []
  const mondayBasedWeekday = (weekday + 6) % 7
  const start = addDays(summary.value.today.date, -mondayBasedWeekday)
  return trends.value.filter((point) => point.date >= start && point.date <= summary.value!.today.date)
})

const recordedWeekPoints = computed(() =>
  currentWeekPoints.value.filter((point) => point.calories_kcal != null),
)
const weekAverageCalories = computed(() =>
  average(recordedWeekPoints.value.map((point) => point.calories_kcal)),
)
const weekAverageProtein = computed(() =>
  average(recordedWeekPoints.value.map((point) => point.protein_g)),
)
const weekBudgetComparisons = computed(() =>
  recordedWeekPoints.value.flatMap((point) => {
    if (point.calories_kcal == null) return []
    const rawBudgetKcal = point.target_kcal
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
  return `${integer.format(weekWithinBudgetCount.value)} von ${integer.format(comparedDays)} ${comparedDays === 1 ? 'Tag' : 'Tagen'}`
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
    ? `${integer.format(recordedVisibleDays.value)} von ${integer.format(trends.value.length)} Tagen · ${percentage.format(coverageRatio.value)}`
    : 'Noch keine Tage im Zeitraum verfügbar',
)
const gapLabel = computed(() => {
  if (!trends.value.length) return 'Noch kein Zeitraum geladen'
  if (!missingVisibleDays.value) return 'Keine Datenlücken'
  return `${integer.format(missingVisibleDays.value)} ${missingVisibleDays.value === 1 ? 'Tag' : 'Tage'} ohne Daten`
})
const gapDescription = computed(() =>
  !trends.value.length
    ? 'Nach dem ersten Import werden Datenlücken hier sichtbar'
    : missingVisibleDays.value
      ? 'Für diese Kalendertage fehlen Ernährungseinträge'
      : 'Alle Kalendertage im Zeitraum sind erfasst',
)

const chartText = '#98a5b9'
const chartGrid = '#263449'
const tooltip = {
  backgroundColor: '#111d30',
  borderColor: '#324157',
  textStyle: { color: '#f3f6fb' },
}

const calorieBarData = computed(() =>
  trends.value.map((item) => {
    if (item.calories_kcal == null) return null

    const calories = Number(item.calories_kcal)
    const budget = Number(item.target_kcal)
    const isOverBudget =
      item.target_kcal != null && Number.isFinite(budget) && calories > budget

    if (highlightOverBudget.value && isOverBudget) {
      return {
        value: calories,
        itemStyle: { color: '#fb7185', borderRadius: [5, 5, 0, 0] },
      }
    }

    return calories
  }),
)

const calorieChart = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: { ...tooltip, trigger: 'axis', valueFormatter: (value) => `${integer.format(Number(value))} kcal` },
  legend: {
    top: 0,
    right: 0,
    data: ['Aufnahme', 'Tagesbudget'],
    textStyle: { color: chartText, fontFamily: 'Inter' },
    itemWidth: 14,
    itemHeight: 8,
  },
  grid: { left: 54, right: 18, top: 46, bottom: 40 },
  xAxis: {
    type: 'category',
    data: trends.value.map((item) => formatGermanDayMonth(item.date)),
    axisLine: { lineStyle: { color: chartGrid } },
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
    name: 'kcal',
    nameTextStyle: { color: chartText, fontFamily: 'Inter', padding: [0, 0, 0, -34] },
    axisLabel: { color: chartText, fontFamily: 'Inter' },
    splitLine: { lineStyle: { color: chartGrid, type: 'dashed' } },
  },
  series: [
    {
      name: 'Aufnahme',
      type: 'bar',
      barMaxWidth: 26,
      data: calorieBarData.value,
      itemStyle: { color: '#8b5cf6', borderRadius: [5, 5, 0, 0] },
      emphasis: { itemStyle: { color: '#a78bfa' } },
    },
    {
      name: 'Tagesbudget',
      type: 'line',
      data: trends.value.map((item) => item.target_kcal),
      step: 'middle',
      connectNulls: false,
      showSymbol: false,
      lineStyle: { color: '#fb923c', width: 2, type: 'dashed' },
      itemStyle: { color: '#fb923c' },
    },
  ],
}))

const macroChart = computed<EChartsOption>(() => ({
  animationDuration: 500,
  tooltip: {
    ...tooltip,
    trigger: 'axis',
    order: 'seriesDesc',
    valueFormatter: (value) => `${decimal.format(Number(value))} g`,
  },
  legend: {
    bottom: 0,
    data: ['Protein', 'Kohlenhydrate', 'Fett'],
    textStyle: { color: chartText, fontFamily: 'Inter' },
    itemWidth: 12,
    itemHeight: 8,
  },
  grid: { left: 48, right: 16, top: 20, bottom: 50 },
  xAxis: {
    type: 'category',
    data: trends.value.map((item) => formatGermanDayMonth(item.date)),
    axisLine: { lineStyle: { color: chartGrid } },
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
    name: 'g',
    nameTextStyle: { color: chartText, fontFamily: 'Inter' },
    axisLabel: { color: chartText, fontFamily: 'Inter' },
    splitLine: { lineStyle: { color: chartGrid, type: 'dashed' } },
  },
  series: [
    {
      name: 'Protein',
      type: 'bar',
      stack: 'macros',
      barMaxWidth: 26,
      data: trends.value.map((item) => item.protein_g),
      itemStyle: { color: '#2dd4bf' },
    },
    {
      name: 'Kohlenhydrate',
      type: 'bar',
      stack: 'macros',
      barMaxWidth: 26,
      data: trends.value.map((item) => item.carbs_g),
      itemStyle: { color: '#4f8cff' },
    },
    {
      name: 'Fett',
      type: 'bar',
      stack: 'macros',
      barMaxWidth: 26,
      data: trends.value.map((item) => item.fat_g),
      itemStyle: { color: '#f7b928', borderRadius: [4, 4, 0, 0] },
    },
  ],
}))
</script>

<template>
  <div class="dashboard-heading">
    <div>
      <h1>Ernährungsüberblick</h1>
      <p>{{ dateHeading }}</p>
    </div>
    <div class="dashboard-heading-actions">
      <div class="period-control" aria-label="Zeitraum auswählen">
        <button
          v-for="period in periods"
          :key="period.value"
          type="button"
          :class="{ active: periodDays === period.value }"
          :aria-pressed="periodDays === period.value"
          @click="selectPeriod(period.value)"
        >
          {{ period.label }}
        </button>
      </div>
      <span class="dashboard-period-range" aria-live="polite">{{ rangeLabel }}</span>
    </div>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading" aria-live="polite">
    <PhArrowsClockwise :size="22" class="spin" aria-hidden="true" />
    Dashboard wird geladen …
  </div>
  <template v-else-if="summary">
    <section class="dashboard-stats" aria-label="Kennzahlen">
      <article class="card metric-card">
        <div class="metric-card-label"><PhFire :size="22" weight="duotone" aria-hidden="true" /><span>Heute</span></div>
        <div class="metric-card-value">
          {{ todayCalories == null ? '–' : integer.format(todayCalories) }}
          <small>kcal</small>
        </div>
        <p :class="{ warning: todayCalories != null }">{{ todayComparison }}</p>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label purple"><PhTarget :size="22" weight="duotone" aria-hidden="true" /><span>Verbleibend</span></div>
        <div class="metric-card-value">
          {{ caloriesRemaining == null ? '–' : integer.format(Math.max(caloriesRemaining, 0)) }}
          <small>kcal</small>
        </div>
        <p>{{ caloriesRemaining != null && caloriesRemaining < 0 ? `${integer.format(Math.abs(caloriesRemaining))} kcal über Tagesbudget` : 'Für heute verfügbar' }}</p>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label teal"><PhBarbell :size="22" weight="duotone" aria-hidden="true" /><span>Protein</span></div>
        <div class="metric-card-value compact">
          {{ todayProtein == null ? '–' : decimal.format(todayProtein) }}
          <small v-if="proteinTarget">/ {{ integer.format(proteinTarget) }} g</small>
          <small v-else>g</small>
        </div>
        <div class="metric-card-progress-label">
          <span>{{ proteinTarget && todayProtein != null ? `${integer.format(Math.max(proteinTarget - todayProtein, 0))} g bis zum Ziel` : 'Kein Proteinziel' }}</span>
          <strong v-if="proteinTarget">{{ percentage.format(proteinProgress) }}</strong>
        </div>
        <progress class="metric-progress protein" :value="proteinProgress" max="1">
          {{ percentage.format(proteinProgress) }}
        </progress>
      </article>

      <article class="card metric-card">
        <div class="metric-card-label blue"><PhChartPieSlice :size="22" weight="duotone" aria-hidden="true" /><span>Wochenrest</span></div>
        <div class="metric-card-value">
          {{ summary.week.remaining_kcal == null ? '–' : integer.format(Math.max(summary.week.remaining_kcal, 0)) }}
          <small v-if="summary.week.remaining_kcal != null">kcal</small>
        </div>
        <p v-if="summary.week.remaining_kcal == null">Noch kein vollständiges Wochenbudget</p>
        <p v-else-if="summary.week.remaining_kcal < 0">
          {{ integer.format(Math.abs(summary.week.remaining_kcal)) }} kcal über Wochenbudget
        </p>
        <p v-else>
          Mo–So · {{ integer.format(summary.week.consumed_kcal) }} von
          {{ integer.format(summary.week.budget_kcal!) }} kcal
        </p>
      </article>
    </section>

    <section class="dashboard-charts" aria-label="Ernährungsverlauf">
      <ChartPanel
        title="Kalorienaufnahme"
        :option="calorieChart"
        :empty="!trends.some((item) => item.calories_kcal != null)"
        :height="318"
      >
        <template #header-actions>
          <div class="chart-header-actions">
            <label class="chart-highlight-toggle">
              <input
                v-model="highlightOverBudget"
                type="checkbox"
                role="switch"
              />
              <span>Über Budget hervorheben</span>
            </label>
          </div>
        </template>
      </ChartPanel>
      <ChartPanel
        title="Makronährstoff-Verteilung"
        :option="macroChart"
        :empty="!trends.some((item) => item.protein_g != null || item.carbs_g != null || item.fat_g != null)"
        :height="318"
      >
        <template #header-actions><span class="chart-range">Gramm pro Tag</span></template>
      </ChartPanel>
    </section>

    <section class="dashboard-bottom-grid" aria-label="Zusammenfassungen">
      <article class="card summary-card">
        <div class="summary-card-title">
          <div><h2>7-Tage-Schnitt</h2><p>{{ sevenRecordedDays.length }} erfasste Tage</p></div>
          <PhTrendUp :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="average-grid">
          <div><span>Kalorien</span><strong>{{ sevenDayCalories == null ? '–' : `${integer.format(sevenDayCalories)} kcal` }}</strong></div>
          <div><span>Protein</span><strong>{{ sevenDayProtein == null ? '–' : `${integer.format(sevenDayProtein)} g` }}</strong></div>
          <div><span>Kohlenhydrate</span><strong>{{ sevenDayCarbs == null ? '–' : `${integer.format(sevenDayCarbs)} g` }}</strong></div>
          <div><span>Fett</span><strong>{{ sevenDayFat == null ? '–' : `${integer.format(sevenDayFat)} g` }}</strong></div>
        </div>
      </article>

      <article class="card summary-card weekly-summary-card">
        <div class="summary-card-title">
          <div><h2>Wochenzusammenfassung</h2><p>Laufende Woche</p></div>
          <PhChartPieSlice :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="weekly-summary-list">
          <div>
            <span class="weekly-summary-icon calories">
              <PhChartBar :size="18" weight="fill" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">Wochenschnitt</span>
            <strong>{{ weekAverageCalories == null ? '–' : `${integer.format(weekAverageCalories)} kcal` }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon protein">
              <PhBarbell :size="18" weight="fill" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">Protein im Schnitt</span>
            <strong>{{ weekAverageProtein == null ? '–' : `${integer.format(weekAverageProtein)} g` }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon target">
              <PhTarget :size="18" weight="duotone" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">Kalorienbudget eingehalten</span>
            <strong>{{ weekBudgetResultLabel }}</strong>
          </div>
          <div>
            <span class="weekly-summary-icon recorded">
              <PhCheck :size="19" weight="bold" aria-hidden="true" />
            </span>
            <span class="weekly-summary-label">Daten erfasst</span>
            <strong>{{ recordedWeekPoints.length }} von {{ elapsedWeekDays }} Tagen</strong>
          </div>
        </div>
      </article>

      <article class="card summary-card quality-card">
        <div class="summary-card-title">
          <div><h2>Datenstatus</h2><p>Gewählter Zeitraum</p></div>
          <PhDatabase :size="22" weight="duotone" aria-hidden="true" />
        </div>
        <div class="quality-list">
          <div>
            <span :class="['quality-icon', coverageIsComplete ? 'success' : 'warning']">
              <PhCheckCircle v-if="coverageIsComplete" :size="19" weight="fill" aria-hidden="true" />
              <PhWarningCircle v-else :size="19" weight="fill" aria-hidden="true" />
            </span>
            <span>
              <strong>Datenabdeckung</strong>
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
            {{ syncingYazio ? 'Synchronisiere …' : 'Jetzt synchronisieren' }}
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
