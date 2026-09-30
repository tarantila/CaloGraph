<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisRangeWithinDays } from '../analytics-period'
import { analysisHubQuery, analysisRangeDayCount, normalizeAnalysisPeriodForCapability, parseAnalysisPeriodSelection, supportedAnalysisPeriods, type AnalysisPeriodSelection } from '../analysis-v2'
import AnalysisPeriodPicker from '../components/AnalysisPeriodPicker.vue'
import WeightHistoryChart from '../components/WeightHistoryChart.vue'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { WeightResponse } from '../types'

type WeightPreset = AnalysisPeriodSelection
const maxDays = 3661
const presets = supportedAnalysisPeriods(maxDays, true)
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const response = ref<WeightResponse | null>(null)
const loading = ref(true)
const error = ref('')
const loadId = ref(0)

function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const initialSelection = normalizeAnalysisPeriodForCapability(
  parseAnalysisPeriodSelection(queryString(route.query.period)),
  queryString(route.query.start) && queryString(route.query.end)
    ? { start: queryString(route.query.start)!, end: queryString(route.query.end)! } : null,
  maxDays, today,
)
const start = ref(initialSelection.range.start)
const end = ref(initialSelection.range.end)
const period = ref<WeightPreset>(initialSelection.period)
const points = computed(() => [...(response.value?.points ?? [])].sort((left, right) => left.date.localeCompare(right.date)))
const summary = computed(() => response.value?.summary ?? null)
const change = computed(() => points.value.length >= 2 ? summary.value?.change_kg ?? null : null)
const periodDays = computed(() => analysisRangeDayCount({ start: start.value, end: end.value }))
const measuredDays = computed(() => new Set(points.value
  .filter((point) => point.date >= start.value && point.date <= end.value)
  .map((point) => point.date)).size)

function formatWeight(value: number | null | undefined): string {
  return value === null || value === undefined ? '–' : `${number.format(value)} kg`
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
  const requestId = ++loadId.value
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today) {
    response.value = null
    error.value = t('analysisV2.invalidRange')
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  response.value = null
  await syncQuery()
  if (requestId !== loadId.value) return
  const params = new URLSearchParams({ start: start.value, end: end.value })
  try {
    const result = await api<WeightResponse>(`/analytics/weight?${params.toString()}`)
    if (requestId === loadId.value) response.value = result
  } catch (cause) {
    if (requestId === loadId.value) error.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    if (requestId === loadId.value) loading.value = false
  }
}

function applyPeriod(value: { start: string; end: string; period: WeightPreset }): void {
  const normalized = normalizeAnalysisPeriodForCapability(
    value.period, value.period === 'custom' ? value : null, maxDays, today,
  )
  period.value = normalized.period
  start.value = normalized.range.start
  end.value = normalized.range.end
  void load()
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const requested = parseAnalysisPeriodSelection(nextPeriod)
    const queryStart = queryString(nextStart)
    const queryEnd = queryString(nextEnd)
    const normalized = normalizeAnalysisPeriodForCapability(
      requested, requested === 'custom' && queryStart && queryEnd
        ? { start: queryStart, end: queryEnd } : null, maxDays, today,
    )
    const nextPreset = normalized.period
    const nextRange = normalized.range
    const rangeChanged = nextRange.start !== start.value || nextRange.end !== end.value
    const presetChanged = nextPreset !== period.value
    start.value = nextRange.start
    end.value = nextRange.end
    period.value = nextPreset
    if (rangeChanged || presetChanged) void load()
    else void syncQuery()
  },
)

onMounted(() => { void load() })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/koerper', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.categories.body') }}</RouterLink>
    </nav>
    <div class="page-heading">
      <div>
        <h1 class="analysis-detail-title"><span class="analysis-detail-icon analysis-detail-icon--weight" aria-hidden="true">◉</span>{{ t('analysisV2.weightTitle') }}</h1>
        <p>{{ t('analysisV2.weightDescription') }}</p>
      </div>
      <AnalysisPeriodPicker
        :start="start"
        :end="end"
        :today="today"
        :period="period"
        :presets="presets"
        :custom-range="true"
        :max-days="3661"
        @apply="applyPeriod"
      />
    </div>

    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response && summary">
      <section class="weight-summary-grid" :aria-label="t('analysisV2.weightHistory')">
        <article class="card weight-summary-primary">
          <h2>{{ t('analysisV2.latestMeasurement') }}</h2>
          <strong>{{ formatWeight(summary.latest?.weight_kg) }}</strong>
          <time v-if="summary.latest" :datetime="summary.latest.date">{{ formatGermanDate(summary.latest.date) }}</time>
          <div class="weight-summary-meta">
            <span>
              <span class="weight-summary-meta-label">{{ t('analysisV2.change') }}</span>
              <strong>{{ change === null ? '–' : formatWeight(change) }}</strong>
              <span v-if="points.length < 2">{{ t('analysisV2.changeUnavailable') }}</span>
            </span>
            <span>
              <span class="weight-summary-meta-label">{{ t('analysisV2.measurementCount', { count: number.format(summary.measurement_count) }) }}</span>
              <span class="weight-period-range">{{ t('analysisV2.daysCoverage', {
                count: number.format(measuredDays), total: number.format(periodDays),
              }) }}</span>
              <span class="weight-period-range">{{ formatGermanDate(start) }} – {{ formatGermanDate(end) }}</span>
            </span>
          </div>
        </article>
        <div class="weight-summary-secondary">
          <article class="card">
            <h2>{{ t('analysisV2.average') }}</h2>
            <strong>{{ formatWeight(summary.average_kg) }}</strong>
          </article>
          <article class="card">
            <h2>{{ t('analysisV2.minimum') }}</h2>
            <strong>{{ formatWeight(summary.minimum_kg) }}</strong>
          </article>
          <article class="card">
            <h2>{{ t('analysisV2.maximum') }}</h2>
            <strong>{{ formatWeight(summary.maximum_kg) }}</strong>
          </article>
        </div>
      </section>
      <p v-if="!points.length" class="card empty" role="status">{{ t('analysisV2.weightEmpty') }}</p>
      <template v-else>
        <WeightHistoryChart :points="points" />
        <section class="card weight-reading-list">
          <h2>{{ t('analysisV2.weightReadings') }}</h2>
          <ul :aria-label="t('analysisV2.weightReadings')">
            <li v-for="point in points" :key="point.date">
              <time :datetime="point.date">{{ formatGermanDate(point.date) }}</time>
              <strong>{{ formatWeight(point.weight_kg) }}</strong>
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
.weight-summary-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.5fr); gap: .8rem; }
.weight-summary-grid article { display: grid; align-content: start; gap: .35rem; min-width: 0; }
.weight-summary-primary, .weight-reading-list { padding: .9rem; }
.weight-summary-grid h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.weight-summary-primary > strong { color: var(--chart-series-violet); font-size: 1.5rem; overflow-wrap: anywhere; }
.weight-summary-grid time, .weight-summary-grid article > span, .weight-summary-meta-label, .weight-period-range { color: var(--muted); font-size: .9rem; }
.weight-summary-meta { display: flex; flex-wrap: wrap; gap: 1rem; margin-top: .35rem; padding-top: .65rem; border-top: 1px solid var(--border); }
.weight-summary-meta > span { display: grid; gap: .15rem; min-width: 0; }
.weight-summary-meta strong { font-size: 1.05rem; }
.weight-summary-secondary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.weight-summary-secondary article { padding: .9rem; }
.weight-summary-secondary strong { font-size: 1.15rem; overflow-wrap: anywhere; }
.weight-reading-list h2 { margin-top: 0; font-size: 1.05rem; }
.weight-reading-list ul { list-style: none; margin: 0; padding: 0; }
.weight-reading-list li { display: flex; justify-content: space-between; gap: .8rem; padding: .65rem 0; border-top: 1px solid var(--border); }
@media (max-width: 900px) {
  .weight-summary-grid { grid-template-columns: 1fr; }
}
@media (max-width: 680px) {
  .weight-summary-secondary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 440px) {
  .weight-summary-secondary { grid-template-columns: 1fr; }
}
</style>
