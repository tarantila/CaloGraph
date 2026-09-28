<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHubQuery, analysisRangeForDays, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import ChartPanel from '../components/ChartPanel.vue'
import { chartColors } from '../chart-theme'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { VerificationActivityResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const period = ref<AnalysisHubPeriod>(route.query.period === '30' ? '30' : '7')
const hubPeriods: readonly AnalysisHubPeriod[] = ['7', '30']
const range = computed(() => analysisRangeForDays(today, Number(period.value)))
const response = ref<VerificationActivityResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0

const readings = computed(() => (response.value?.days ?? []).flatMap((day) => {
  const canonical = day.canonical
  if (canonical?.status !== 'available' || typeof canonical.active_energy_kcal !== 'number'
    || !Number.isFinite(canonical.active_energy_kcal)) return []
  return [{ date: day.date, value: canonical.active_energy_kcal }]
}).sort((left, right) => left.date.localeCompare(right.date)))
const coverage = computed(() => t('analysisV2.activityCoverage', {
  count: number.format(readings.value.length),
  total: number.format(Number(period.value)),
}))
const average = computed(() => readings.value.length
  ? readings.value.reduce((sum, reading) => sum + reading.value, 0) / readings.value.length
  : null)
const coverageIsComplete = computed(() => readings.value.length === Number(period.value))
const chartDates = computed(() => Array.from(
  { length: Number(period.value) },
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
      axisLabel: { color: palette.label },
      splitLine: { lineStyle: { color: palette.grid, type: 'dashed' } },
    },
    series: [{
      name: t('analysisV2.activeEnergyTitle'),
      type: 'bar',
      barMaxWidth: 32,
      data: chartDates.value.map((date) => readingsByDate.value.get(date) ?? null),
      itemStyle: { color: palette.violet },
    }],
  }
})

async function syncQuery(): Promise<void> {
  await router.replace({
    query: {
      ...route.query,
      start: range.value.start,
      end: range.value.end,
      period: period.value,
    },
  })
}

async function load(): Promise<void> {
  const requestId = ++latestLoadId
  loading.value = true
  error.value = ''
  response.value = null
  await syncQuery()
  if (requestId !== latestLoadId) return
  const params = new URLSearchParams({
    start: range.value.start,
    end: range.value.end,
    view: 'canonical',
  })
  try {
    const result = await api<VerificationActivityResponse>(
      `/analytics/verification/activity?${params.toString()}`,
    )
    if (requestId === latestLoadId) response.value = result
  } catch (cause) {
    if (requestId === latestLoadId) error.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    if (requestId === latestLoadId) loading.value = false
  }
}

function choosePeriod(value: AnalysisHubPeriod): void {
  if (period.value === value) return
  period.value = value
  void load()
}

watch(
  () => route.query.period,
  (value) => {
    const next = parseAnalysisHubPeriod(value)
    if (next !== period.value) {
      period.value = next
      void load()
    }
  },
)

onMounted(() => { void load() })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/aktivitaet', query: analysisHubQuery(period) }">{{ t('analysisV2.categories.activity') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ t('analysisV2.activeEnergyTitle') }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1>{{ t('analysisV2.activeEnergyTitle') }}</h1>
        <p>{{ t('analysisV2.activeEnergyDescription') }}</p>
      </div>
      <div class="period-control activity-period" role="group" :aria-label="t('analysisV2.period')">
        <button
          v-for="choice in hubPeriods"
          :key="choice"
          type="button"
          :class="{ active: period === choice }"
          :aria-pressed="period === choice"
          @click="choosePeriod(choice)"
        >
          {{ t(`analysisV2.period${choice}`) }}
        </button>
      </div>
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
          <span
            class="activity-coverage"
            :class="coverageIsComplete ? 'activity-coverage--complete' : 'activity-coverage--incomplete'"
            :data-coverage-status="coverageIsComplete ? 'complete' : 'incomplete'"
          >
            <span class="activity-coverage-icon" aria-hidden="true">{{ coverageIsComplete ? '✓' : '!' }}</span>
            {{ coverage }}
          </span>
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
.analysis-detail-page { display: grid; gap: 1rem; }
.analysis-detail-page > .page-heading { margin-bottom: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.activity-summary { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: end; gap: 1rem; }
.activity-summary, .activity-reading-list { padding: .9rem; }
.activity-summary-primary { display: grid; gap: .3rem; }
.activity-summary h2 { margin: 0; color: var(--muted); font-size: .95rem; }
.activity-summary strong { font-size: 1.5rem; }
.activity-coverage { display: inline-flex; align-items: center; gap: .35rem; font-size: .9rem; }
.activity-coverage--complete { color: var(--success); }
.activity-coverage--incomplete { color: var(--warning); }
.activity-coverage-icon { font-weight: 750; }
.activity-range { color: var(--muted); }
.activity-reading-list h2 { margin-top: 0; font-size: 1.05rem; }
.activity-reading-list ul { list-style: none; margin: 0; padding: 0; }
.activity-reading-list li { display: flex; justify-content: space-between; gap: .8rem; padding: .65rem 0; border-top: 1px solid var(--border); }
</style>
