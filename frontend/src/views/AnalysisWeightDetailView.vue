<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisRangeWithinDays, periodFromQuery, resolveAllDataRange, type AppliedPeriod, type PeriodLimits, type PeriodRange } from '../analytics-period'
import { AnalysisIcon, analysisHubQuery } from '../analysis-v2'
import AnalysisReadingsDisclosure from '../components/AnalysisReadingsDisclosure.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import WeightHistoryChart from '../components/WeightHistoryChart.vue'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { WeightResponse } from '../types'

const maxDays = 3661
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const response = ref<WeightResponse | null>(null)
const loading = ref(true)
const error = ref('')
const loadId = ref(0)

const limits = computed<PeriodLimits>(() => ({ minDays: 1, maxDays, customRange: true, maxDate: today.value }))
const weightPeriodPresets = ['3661'] as const
const capability = computed(() => response.value?.period_capabilities)
const allRange = computed(() => resolveAllDataRange(capability.value, response.value?.end_date ?? today.value, limits.value))
const committedAllRange = ref<PeriodRange | null>(null)
let requestedInitialAll = route.query.period === 'all'
const initialSelection = periodFromQuery(route.query, limits.value, today.value, null, weightPeriodPresets)
const start = ref(initialSelection.start)
const end = ref(initialSelection.end)
const period = ref(initialSelection.period)
const points = computed(() => [...(response.value?.points ?? [])].sort((left, right) => left.date.localeCompare(right.date)))
const summary = computed(() => response.value?.summary ?? null)
const change = computed(() => points.value.length >= 2 ? summary.value?.change_kg ?? null : null)

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
  const userId = auth.user?.id
  if (!userId) {
    response.value = null
    error.value = ''
    loading.value = false
    return
  }
  if (!analysisRangeWithinDays(start.value, end.value, maxDays) || end.value > today.value) {
    response.value = null
    error.value = t('analysisV2.invalidRange')
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  response.value = null
  await syncQuery()
  if (requestId !== loadId.value || auth.user?.id !== userId) return
  const params = new URLSearchParams({ start: start.value, end: end.value })
  try {
    const result = await api<WeightResponse>(`/analytics/weight?${params.toString()}`)
    if (requestId !== loadId.value || auth.user?.id !== userId) return
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
    if (requestId === loadId.value && auth.user?.id === userId) {
      requestedInitialAll = false
      error.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (requestId === loadId.value && auth.user?.id === userId) loading.value = false
  }
}

function applyPeriod(value: AppliedPeriod): void {
  requestedInitialAll = false
  const selected = periodFromQuery(value, limits.value, today.value, allRange.value, weightPeriodPresets)
  committedAllRange.value = selected.period === 'all'
    ? { start: selected.start, end: selected.end } : null
  period.value = selected.period
  start.value = selected.start
  end.value = selected.end
  void load()
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const currentQuery = analysisHubQuery(period.value, { start: start.value, end: end.value })
    if (nextPeriod === currentQuery.period && nextStart === currentQuery.start && nextEnd === currentQuery.end) return
    requestedInitialAll = false
    const selected = periodFromQuery(
      route.query,
      limits.value,
      today.value,
      period.value === 'all' ? committedAllRange.value : allRange.value,
      weightPeriodPresets,
    )
    const changed = selected.start !== start.value || selected.end !== end.value || selected.period !== period.value
    committedAllRange.value = selected.period === 'all'
      ? { start: selected.start, end: selected.end } : null
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    if (changed) void load()
    else void syncQuery()
  },
)

watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], [previousUserId, previousTimezone]) => {
    if (userId === previousUserId && timezone === previousTimezone) return
    const accountChanged = userId !== previousUserId
    loadId.value += 1
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
      weightPeriodPresets,
    )
    start.value = selected.start
    end.value = selected.end
    period.value = selected.period
    loading.value = true
    void load()
  },
  { flush: 'sync' },
)

onMounted(() => { void load() })
onBeforeUnmount(() => { loadId.value += 1 })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/koerper', query: analysisHubQuery(period, { start, end }) }">{{ t('analysisV2.categories.body') }}</RouterLink>
    </nav>
    <div class="page-heading page-heading--period">
      <div>
        <h1 class="analysis-detail-title"><span class="analysis-detail-icon analysis-detail-icon--weight" aria-hidden="true"><AnalysisIcon icon="scale" /></span>{{ t('analysisV2.weightTitle') }}</h1>
        <p>{{ t('analysisV2.weightDescription') }}</p>
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
        :additional-presets="weightPeriodPresets"
        @apply="applyPeriod"
      />
    </div>

    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response && summary">
      <section class="card weight-summary-primary">
        <div class="weight-summary-current">
          <h2>{{ t('analysisV2.latestMeasurement') }}</h2>
          <strong>{{ formatWeight(summary.latest?.weight_kg) }}</strong>
          <time v-if="summary.latest" :datetime="summary.latest.date">{{ formatGermanDate(summary.latest.date) }}</time>
        </div>
        <div class="weight-summary-meta">
          <span>
            <span class="weight-summary-meta-label">{{ t('analysisV2.change') }}</span>
            <strong>{{ change === null ? '–' : formatWeight(change) }}</strong>
            <span v-if="points.length < 2">{{ t('analysisV2.changeUnavailable') }}</span>
          </span>
        </div>
      </section>
      <p v-if="!points.length" class="card empty" role="status">{{ t('analysisV2.weightEmpty') }}</p>
      <WeightHistoryChart v-else :points="points" />
      <section class="weight-summary-secondary">
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
      </section>
      <AnalysisReadingsDisclosure
        v-if="points.length"
        :key="route.path"
        class="card weight-reading-list"
        heading-id="weight-readings-title"
        :title="t('analysisV2.weightReadingsCount', { count: number.format(points.length) })"
      >
        <div class="table-scroll">
          <table aria-labelledby="weight-readings-title">
            <thead class="sr-only"><tr><th scope="col">{{ t('common.date') }}</th><th scope="col" class="number">{{ t('analysisV2.weightTitle') }}</th></tr></thead>
            <tbody>
              <tr v-for="point in points" :key="point.date">
                <td><time :datetime="point.date">{{ formatGermanDate(point.date) }}</time></td>
                <td class="number">{{ formatWeight(point.weight_kg) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </AnalysisReadingsDisclosure>
    </template>
  </div>
</template>

<style scoped>
.analysis-detail-page { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; }
.analysis-detail-page > .page-heading { margin-bottom: 0; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.weight-summary-primary { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); gap: 0; min-width: 0; padding: 0; }
.weight-summary-primary h2, .weight-summary-secondary h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.weight-summary-current, .weight-summary-meta { display: grid; align-content: start; gap: .35rem; min-width: 0; padding: .9rem; }
.weight-summary-primary > .weight-summary-meta { border-left: 1px solid var(--border); }
.weight-reading-list { padding: .9rem; }
.weight-summary-current > strong { color: var(--chart-series-violet); font-size: 1.2rem; overflow-wrap: anywhere; }
.weight-summary-current time { color: var(--muted); font-size: .9rem; }
.weight-summary-meta-label { color: var(--muted); font-size: .92rem; }
.weight-summary-meta > span { display: grid; gap: .35rem; min-width: 0; }
.weight-summary-meta strong { font-size: 1.2rem; overflow-wrap: anywhere; }
.weight-summary-secondary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.weight-summary-secondary article { display: grid; align-content: start; gap: .35rem; min-width: 0; padding: .9rem; }
.weight-summary-secondary strong { font-size: 1.15rem; overflow-wrap: anywhere; }
@media (max-width: 680px) {
  .weight-summary-primary { grid-template-columns: minmax(0, 1fr); }
  .weight-summary-current { border-bottom: 1px solid var(--border); }
  .weight-summary-primary > .weight-summary-meta { border-left: 0; }
  .weight-summary-secondary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 440px) {
  .weight-summary-secondary { grid-template-columns: 1fr; }
}
</style>
