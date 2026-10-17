<script setup lang="ts">
import { MarkPointComponent } from 'echarts/components'
import { use } from 'echarts/core'
import { graphic, type EChartsOption } from 'echarts'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { AnalysisIcon, analysisHubQuery, analysisRangeDayCount } from '../analysis-v2'
import { analysisRangeWithinDays, periodFromQuery, resolveAllDataRange } from '../analytics-period'
import type { AppliedPeriod, PeriodLimits, PeriodRange } from '../analytics-period'
import { chartColors } from '../chart-theme'
import ChartPanel from '../components/ChartPanel.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { VerificationActivityResponse } from '../types'

use([MarkPointComponent])
const t = i18n.global.t.bind(i18n.global)
function withAlpha(color: string, alpha: number): string {
  const match = /^#([0-9a-f]{6})$/i.exec(color)
  if (!match) return color
  const value = Number.parseInt(match[1]!, 16)
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`
}
const route = useRoute()
const pagePath = route.path
const router = useRouter()
const auth = useAuthStore()
type ActivityChartView = 'bar' | 'line'
const activityChartView = ref<ActivityChartView>('bar')
const activityChartViewStorageKey = (userId: string) =>
  `calograph.analysis.activity.chart-view.v1.${encodeURIComponent(userId)}`
function storedActivityChartView(userId: string | null): ActivityChartView {
  if (!userId || typeof window === 'undefined') return 'bar'
  try {
    return window.localStorage.getItem(activityChartViewStorageKey(userId)) === 'line' ? 'line' : 'bar'
  } catch {
    return 'bar'
  }
}
function selectActivityChartView(view: ActivityChartView): void {
  activityChartView.value = view
  const userId = auth.user?.id
  if (!userId || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(activityChartViewStorageKey(userId), view)
  } catch {
    // Keep the chart usable when browser storage is unavailable.
  }
}
watch(
  () => auth.user?.id ?? null,
  (userId) => { activityChartView.value = storedActivityChartView(userId) },
  { immediate: true },
)
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const kcal = createNumberFormatter({ maximumFractionDigits: 0 })
const maxDays = 3661
const limits = computed<PeriodLimits>(() => ({ minDays: 1, maxDays, customRange: true, maxDate: today.value }))
const response = ref<VerificationActivityResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0
let requestedRange = ''
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'
let syncingQuery: ReturnType<typeof analysisHubQuery> | null = null

const selection = computed(() => periodFromQuery(
  route.query, limits.value, today.value,
  route.query.period === 'all' ? committedAllRange.value : allRange.value,
))
const start = computed(() => selection.value.start)
const end = computed(() => selection.value.end)
const period = computed(() => selection.value.period)
const chartDates = computed(() => response.value ? Array.from(
  { length: analysisRangeDayCount({ start: start.value, end: end.value }) },
  (_, index) => shiftIsoDate(start.value, index),
) : [])
const values = computed(() => {
  const byDate = new Map(response.value?.days.map((day) => [day.date, day.canonical]) ?? [])
  return chartDates.value.map((date) => {
    const canonical = byDate.get(date)
    const value = canonical?.active_energy_kcal
    return canonical?.status === 'available' && typeof value === 'number' && Number.isFinite(value)
      ? value : null
  })
})
const summary = computed(() => {
  let total = 0
  let count = 0
  let minimum: number | null = null
  let maximum: number | null = null
  for (const value of values.value) {
    if (value === null) continue
    total += value
    count += 1
    minimum = minimum === null ? value : Math.min(minimum, value)
    maximum = maximum === null ? value : Math.max(maximum, value)
  }
  return { total, average: count ? total / count : null, minimum, maximum }
})
const chartOption = computed<EChartsOption>(() => {
  const palette = chartColors.value
  const series = activityChartView.value === 'line'
    ? {
      name: t('analysisV2.activeEnergyTitle'),
      type: 'line' as const,
      data: values.value,
      smooth: 0.4,
      smoothMonotone: 'x' as const,
      connectNulls: true,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 8,
      lineStyle: { color: palette.amber, width: 2.5 },
      itemStyle: { color: palette.amber, borderColor: palette.background, borderWidth: 2 },
      areaStyle: {
        origin: 'start' as const,
        color: new graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: withAlpha(palette.amber, 0.2) },
          { offset: 0.45, color: withAlpha(palette.amber, 0.1) },
          { offset: 0.75, color: withAlpha(palette.amber, 0.035) },
          { offset: 1, color: withAlpha(palette.amber, 0) },
        ], false),
      },
    }
    : {
      name: t('analysisV2.activeEnergyTitle'),
      type: 'bar' as const,
      barMaxWidth: 20,
      data: values.value,
      itemStyle: { color: palette.amber, borderRadius: [4, 4, 0, 0] },
      markPoint: {
        symbol: 'circle',
        symbolSize: 9,
        itemStyle: { color: palette.amber },
        label: { show: false },
        data: chartDates.value.flatMap((date, index) => values.value[index] === 0
          ? [{ name: t('analysisV2.activeEnergyTitle'), coord: [date, 0], value: 0 }] : []),
      },
    }
  return {
    backgroundColor: palette.background,
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBackground,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipInk },
      valueFormatter: (value) => typeof value === 'number' && Number.isFinite(value)
        ? `${kcal.format(value)} kcal` : '–',
    },
    grid: { top: 30, left: 12, right: 18, bottom: 12, containLabel: true },
    xAxis: {
      type: 'category',
      data: chartDates.value,
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, hideOverlap: true, formatter: (value: string | number) => formatGermanDate(String(value)) },
    },
    yAxis: {
      type: 'value',
      min: 0,
      scale: false,
      minInterval: 1,
      name: 'kcal',
      nameTextStyle: { color: palette.label },
      axisLabel: { color: palette.label, formatter: (value: string | number) => kcal.format(Number(value)) },
      axisLine: { lineStyle: { color: palette.axis } },
      splitLine: { lineStyle: { color: palette.grid, opacity: .65 } },
    },
    series: [series],
  }
})

async function syncQuery(): Promise<void> {
  if (route.path !== pagePath) return
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
  if (route.path !== pagePath) return
  const requestId = ++latestLoadId
  const userId = auth.user?.id
  if (!userId) {
    requestedRange = ''
    response.value = null
    error.value = ''
    loading.value = false
    return
  }
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today.value) {
    requestedRange = ''
    response.value = null
    error.value = t('analysisV2.invalidRange')
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
  const params = new URLSearchParams({ ...range, view: 'canonical' })
  try {
    const result = await api<VerificationActivityResponse>(`/analytics/verification/activity?${params.toString()}`)
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

watch(() => route.path, (path) => {
  if (path !== pagePath) {
    latestLoadId += 1
    requestedInitialAll = false
  }
}, { flush: 'sync' })
watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], [previousUserId, previousTimezone]) => {
    if (route.path !== pagePath || (userId === previousUserId && timezone === previousTimezone)) return
    const accountChanged = userId !== previousUserId
    latestLoadId += 1
    requestedRange = ''
    response.value = null
    error.value = ''
    if (accountChanged) committedAllRange.value = null
    requestedInitialAll = route.query.period === 'all'
    if (!userId) {
      loading.value = false
      return
    }
    loading.value = true
  },
  { flush: 'sync' },
)

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod], [previousStart, previousEnd, previousPeriod]) => {
    if (route.path !== pagePath) return
    const ownNormalization = syncingQuery
      && nextPeriod === syncingQuery.period && nextStart === syncingQuery.start && nextEnd === syncingQuery.end
    syncingQuery = null
    if (ownNormalization) return
    if (nextStart !== previousStart || nextEnd !== previousEnd || nextPeriod !== previousPeriod) requestedInitialAll = false
    const selected = periodFromQuery(route.query, limits.value, today.value, committedAllRange.value ?? allRange.value)
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    if (requestedRange === `${period.value}/${start.value}/${end.value}`) void syncQuery()
  },
  { flush: 'sync' },
)
watch(
  () => [start.value, end.value, period.value, auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  () => {
    if (route.path !== pagePath) return
    if (requestedRange !== `${period.value}/${start.value}/${end.value}`) void load()
    else void syncQuery()
  },
  { immediate: true },
)
onBeforeUnmount(() => { latestLoadId += 1 })
</script>

<template>
  <div class="analysis-activity-overview">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: { ...route.query, ...analysisHubQuery(period, { start, end }) }, hash: route.hash }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ t('analysisV2.categories.activity') }}</span>
    </nav>
    <div class="page-heading page-heading--period">
      <h1><AnalysisIcon icon="activity" />{{ t('analysisV2.categories.activity') }}</h1>
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
    <p v-else-if="response && summary.average === null" class="card empty activity-empty" role="status">{{ t('analysisV2.activeEnergyEmpty') }}</p>
    <template v-else-if="response && summary.average !== null">
      <section class="card activity-overview-summary" :aria-label="t('analysisV2.activeEnergyTitle')">
        <RouterLink
          class="activity-overview-kpi"
          :to="{ path: '/analyse/aktivitaet/aktive-energie', query: { ...route.query, ...analysisHubQuery(period, { start, end }) }, hash: route.hash }"
        >
          <h2>{{ t('analysisV2.activityAveragePerValueDay') }}</h2>
          <strong class="activity-overview-kpi-value">{{ kcal.format(summary.average) }} kcal</strong>
        </RouterLink>
        <div class="activity-overview-total">
          <h2>{{ t('analysisV2.activityRecordedTotal') }}</h2>
          <strong>{{ kcal.format(summary.total) }} kcal</strong>
        </div>
      </section>
      <ChartPanel :title="t('analysisV2.activeEnergyChart')" :option="chartOption" :empty="false">
        <template #header-actions>
          <label class="chart-highlight-toggle activity-chart-view-switch">
            <span :class="{ 'is-active': activityChartView === 'bar' }">{{ t('analysisV2.activityChartDisplay') }}</span>
            <input
              type="checkbox"
              role="switch"
              :checked="activityChartView === 'line'"
              :aria-label="t('analysisV2.activityChartViewLabel')"
              @change="selectActivityChartView(($event.target as HTMLInputElement).checked ? 'line' : 'bar')"
            />
            <span :class="{ 'is-active': activityChartView === 'line' }">{{ t('analysisV2.activityChartLine') }}</span>
          </label>
        </template>
      </ChartPanel>
      <section class="activity-overview-statistics">
        <article class="card">
          <h2>{{ t('analysisV2.minimum') }}</h2>
          <strong>{{ summary.minimum === null ? '–' : `${kcal.format(summary.minimum)} kcal` }}</strong>
        </article>
        <article class="card">
          <h2>{{ t('analysisV2.maximum') }}</h2>
          <strong>{{ summary.maximum === null ? '–' : `${kcal.format(summary.maximum)} kcal` }}</strong>
        </article>
      </section>
    </template>
  </div>
</template>

<style scoped>
.analysis-activity-overview { display: grid; gap: 1rem; min-width: 0; }
.analysis-activity-overview > .page-heading { margin-bottom: 0; }
.analysis-activity-overview > .chart-card { min-height: 0; }
.page-heading h1 { display: flex; align-items: center; gap: .65rem; margin: 0; }
.page-heading h1 svg { flex-shrink: 0; color: var(--chart-series-amber); }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.activity-overview-summary { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 0; min-width: 0; padding: 0; }
.activity-overview-summary h2, .activity-overview-statistics h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.activity-overview-kpi, .activity-overview-total { display: grid; align-content: start; gap: .35rem; min-width: 0; padding: .9rem; }
.activity-overview-kpi { color: var(--ink); text-decoration: none; border-radius: inherit; }
.activity-overview-kpi:hover { background: color-mix(in srgb, var(--chart-series-amber) 6%, var(--surface)); }
.activity-overview-kpi-value { color: var(--chart-series-amber); font-size: 1.5rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.activity-overview-total { border-left: 1px solid var(--border); }
.activity-overview-total strong { font-size: 1.05rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.activity-overview-statistics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.activity-overview-statistics article { display: grid; align-content: start; gap: .35rem; min-width: 0; padding: .9rem; }
.activity-overview-statistics strong { font-size: 1.15rem; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.activity-overview-kpi:focus-visible, .analysis-breadcrumbs a:focus-visible, .error button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.activity-empty { margin: 0; padding: 1rem; }
.error { padding: 1rem; }
@media (max-width: 680px) {
  .activity-overview-summary { grid-template-columns: minmax(0, 1fr); }
  .activity-overview-kpi { border-bottom: 1px solid var(--border); }
  .activity-overview-total { border-left: 0; }
  .activity-overview-statistics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .analysis-activity-overview > .page-heading { min-width: 0; align-items: stretch; }
  .page-heading > .analysis-period-picker { width: 100%; }
}
.analysis-activity-overview .activity-chart-view-switch {
  width: auto;
  margin-left: auto;
  white-space: nowrap;
}
.analysis-activity-overview .activity-chart-view-switch > span {
  color: var(--muted);
  font-weight: 400;
  transition: color .18s ease;
}
.analysis-activity-overview .activity-chart-view-switch > span.is-active {
  color: var(--accent);
  font-weight: 600;
}
.analysis-activity-overview .activity-chart-view-switch input,
.analysis-activity-overview .activity-chart-view-switch input:checked {
  border-color: var(--border-strong);
  background: var(--surface-soft);
}
.analysis-activity-overview .activity-chart-view-switch input::after,
.analysis-activity-overview .activity-chart-view-switch input:checked::after {
  background: var(--accent);
}

</style>
