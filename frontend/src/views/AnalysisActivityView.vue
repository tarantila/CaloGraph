<script setup lang="ts">
import { MarkPointComponent } from 'echarts/components'
import { use } from 'echarts/core'
import type { EChartsOption } from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisRangeWithinDays, periodFromQuery, resolveAllDataRange, type AppliedPeriod, type PeriodLimits, type PeriodRange } from '../analytics-period'
import { analysisHubQuery, analysisRangeDayCount } from '../analysis-v2'
import PeriodPicker from '../components/PeriodPicker.vue'
import ChartPanel from '../components/ChartPanel.vue'
import { chartColors } from '../chart-theme'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { VerificationActivityResponse } from '../types'

use([MarkPointComponent])
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const number = createNumberFormatter({ maximumFractionDigits: 0 })

const maxDays = 3661
const limits = computed<PeriodLimits>(() => ({ minDays: 1, maxDays, customRange: true, maxDate: today.value }))
const response = ref<VerificationActivityResponse | null>(null)
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'
const initialSelection = periodFromQuery(route.query, limits.value, today.value, null)
const period = ref(initialSelection.period)
const customRange = ref<PeriodRange | null>({ start: initialSelection.start, end: initialSelection.end })
const normalizedSelection = computed(() => periodFromQuery(
  { ...(customRange.value ?? {}), period: period.value }, limits.value, today.value,
  period.value === 'all' ? committedAllRange.value : allRange.value,
))
const range = computed<PeriodRange>(() => ({
  start: normalizedSelection.value.start, end: normalizedSelection.value.end,
}))
const periodDays = computed(() => analysisRangeDayCount(range.value))
const loading = ref(true)
const error = ref('')
let latestLoadId = 0

const readings = computed(() => (response.value?.days ?? []).flatMap((day) => {
  const canonical = day.canonical
  if (canonical?.status !== 'available' || typeof canonical.active_energy_kcal !== 'number'
    || !Number.isFinite(canonical.active_energy_kcal)) return []
  return [{ date: day.date, value: canonical.active_energy_kcal }]
}).sort((left, right) => left.date.localeCompare(right.date)))
const average = computed(() => readings.value.length
  ? readings.value.reduce((sum, reading) => sum + reading.value, 0) / readings.value.length
  : null)
const chartDates = computed(() => Array.from(
  { length: periodDays.value },
  (_, index) => shiftIsoDate(range.value.start, index),
))
const readingsByDate = computed(() => new Map(readings.value.map((reading) => [reading.date, reading.value])))
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  return {
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
      valueFormatter: (value) => typeof value === 'number' && Number.isFinite(value)
        ? `${number.format(value)} kcal` : '–',
    },
    xAxis: {
      type: 'category',
      data: chartDates.value,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, hideOverlap: true, formatter: (value: string | number) => formatGermanDate(String(value).slice(0, 10)) },
    },
    yAxis: {
      type: 'value',
      min: 0,
      scale: false,
      axisLine: { lineStyle: { color: palette.axis } },
      name: 'kcal',
      nameTextStyle: { color: palette.label },
      axisLabel: { color: palette.label, formatter: (value: string | number) => number.format(Number(value)) },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: t('analysisV2.activeEnergyTitle'),
      type: 'bar',
      barMaxWidth: 32,
      data: chartDates.value.map((date) => readingsByDate.value.get(date) ?? null),
      itemStyle: { color: palette.amber },
      markPoint: {
        symbol: 'circle',
        symbolSize: 9,
        itemStyle: { color: palette.amber },
        data: readings.value.filter(({ value }) => value === 0).map(({ date }) => ({
          name: t('analysisV2.activeEnergyTitle'),
          coord: [date, 0],
        })),
      },
    }],
  }
})

async function syncQuery(): Promise<void> {
  const query = analysisHubQuery(period.value, range.value)
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
    error.value = ''
    loading.value = false
    return
  }
  if (!analysisRangeWithinDays(range.value.start, range.value.end, maxDays) || range.value.end > today.value) {
    response.value = null
    error.value = t('analysisV2.invalidRange')
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  response.value = null
  await syncQuery()
  if (requestId !== latestLoadId || auth.user?.id !== userId) return
  const params = new URLSearchParams({
    start: range.value.start,
    end: range.value.end,
    view: 'canonical',
  })
  try {
    const result = await api<VerificationActivityResponse>(
      `/analytics/verification/activity?${params.toString()}`,
    )
    if (requestId !== latestLoadId || auth.user?.id !== userId) return
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
  const changed = selected.start !== range.value.start || selected.end !== range.value.end
    || selected.period !== period.value
  committedAllRange.value = selected.period === 'all'
    ? { start: selected.start, end: selected.end } : null
  period.value = selected.period
  customRange.value = selected.period === 'all' ? null : selected
  if (changed) void load()
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const currentQuery = analysisHubQuery(period.value, range.value)
    if (nextPeriod === currentQuery.period && nextStart === currentQuery.start && nextEnd === currentQuery.end) return
    requestedInitialAll = false
    const selected = periodFromQuery(
      route.query, limits.value, today.value,
      period.value === 'all' ? committedAllRange.value : allRange.value,
    )
    const changed = selected.period !== period.value
      || selected.start !== range.value.start || selected.end !== range.value.end
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    period.value = selected.period
    customRange.value = selected.period === 'all' ? null : selected
    if (changed) void load()
    else void syncQuery()
  },
)

watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], [previousUserId, previousTimezone]) => {
    if (userId === previousUserId && timezone === previousTimezone) return
    const accountChanged = userId !== previousUserId
    latestLoadId += 1
    response.value = null
    error.value = ''
    if (accountChanged) committedAllRange.value = null
    requestedInitialAll = route.query.period === 'all'
    if (!userId) {
      loading.value = false
      return
    }
    const selected = periodFromQuery(
      route.query, limits.value, today.value,
      accountChanged ? null : committedAllRange.value,
    )
    period.value = selected.period
    customRange.value = selected.period === 'all' ? null : selected
    loading.value = true
    void load()
  },
  { flush: 'sync' },
)

onMounted(() => { void load() })
onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, range) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/aktivitaet', query: analysisHubQuery(period, range) }">{{ t('analysisV2.categories.activity') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ t('analysisV2.activeEnergyTitle') }}</span>
    </nav>
    <div class="page-heading page-heading--period">
      <div>
        <h1 class="analysis-detail-title"><span class="analysis-detail-icon analysis-detail-icon--activity" aria-hidden="true">↗</span>{{ t('analysisV2.activeEnergyTitle') }}</h1>
        <p>{{ t('analysisV2.activeEnergyDescription') }}</p>
      </div>
      <PeriodPicker
        :start="range.start"
        :end="range.end"
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
    <template v-else-if="response">
      <section class="card activity-summary">
        <div class="activity-summary-primary">
          <h2>{{ t('analysisV2.activeEnergyTitle') }}</h2>
          <strong>{{ average === null ? '–' : `${number.format(average)} kcal` }}</strong>
        </div>
        <span class="activity-range">{{ formatGermanDate(range.start) }} – {{ formatGermanDate(range.end) }}</span>
      </section>
      <p v-if="!readings.length" class="card empty" role="status">{{ t('analysisV2.activeEnergyEmpty') }}</p>
      <template v-else>
        <ChartPanel :title="t('analysisV2.activeEnergyChart')" :option="chartOption" :empty="false" />
        <section class="card activity-reading-list">
          <h2>{{ t('analysisV2.activeEnergyDaily') }}</h2>
          <ul :aria-label="t('analysisV2.activeEnergyDaily')">
            <li v-for="reading in readings" :key="reading.date">
              <time :datetime="reading.date">{{ formatGermanDate(reading.date) }}</time>
              <strong>{{ number.format(reading.value) }} kcal</strong>
            </li>
          </ul>
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis-detail-page { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; }
.analysis-detail-page > .page-heading { margin-bottom: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.activity-summary { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 1rem; }
.activity-summary, .activity-reading-list { padding: .9rem; }
.activity-summary-primary { display: grid; gap: .3rem; }
.activity-summary h2 { margin: 0; color: var(--muted); font-size: .95rem; }
.activity-summary strong { font-size: 1.5rem; }
.activity-range { color: var(--muted); }
.activity-reading-list h2 { margin-top: 0; font-size: 1.05rem; }
.activity-reading-list ul { list-style: none; margin: 0; padding: 0; }
.activity-reading-list li { display: flex; justify-content: space-between; gap: .8rem; padding: .65rem 0; border-top: 1px solid var(--border); }
</style>
