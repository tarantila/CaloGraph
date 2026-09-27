<script setup lang="ts">
import type { EChartsOption } from 'echarts'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHubQuery, analysisRangeForDays, type AnalysisHubPeriod } from '../analysis-v2'
import { analysisRangeWithinDays, validAnalyticsRange } from '../analytics-period'
import ChartPanel from '../components/ChartPanel.vue'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { WeightResponse } from '../types'

type WeightPreset = AnalysisHubPeriod | '90' | '180' | '365' | 'custom'
const presets: readonly WeightPreset[] = ['7', '30', '90', '180', '365', 'custom']
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

function rangeForPreset(value: Exclude<WeightPreset, 'custom'>): { start: string; end: string } {
  return analysisRangeForDays(today, Number(value))
}

const queryStart = queryString(route.query.start)
const queryEnd = queryString(route.query.end)
const queryRangeIsValid = validAnalyticsRange(queryStart, queryEnd)
const queryPeriod = queryString(route.query.period)
const initialPreset = presets.find((value): value is Exclude<WeightPreset, 'custom'> => value !== 'custom' && value === queryPeriod)
const initialRange = queryRangeIsValid
  ? { start: queryStart as string, end: queryEnd as string }
  : rangeForPreset(initialPreset ?? '30')
const start = ref(initialRange.start)
const end = ref(initialRange.end)
const period = ref<WeightPreset>(queryRangeIsValid
  ? (presets.find((value) => value !== 'custom' && start.value === rangeForPreset(value).start && end.value === rangeForPreset(value).end) ?? 'custom')
  : initialPreset ?? '30')
const points = computed(() => [...(response.value?.points ?? [])].sort((left, right) => left.date.localeCompare(right.date)))
const summary = computed(() => response.value?.summary ?? null)
const change = computed(() => points.value.length >= 2 ? summary.value?.change_kg ?? null : null)
const chartOption = computed<EChartsOption>(() => ({
  xAxis: {
    type: 'time',
    axisLabel: {
      hideOverlap: true,
      formatter: (value: string | number) => formatGermanDate(new Date(value).toISOString().slice(0, 10)),
    },
  },
  yAxis: { type: 'value', name: 'kg', scale: true },
  series: [{
    name: t('analysisV2.weightTitle'),
    type: 'line',
    showSymbol: true,
    symbol: 'circle',
    symbolSize: 8,
    connectNulls: false,
    data: points.value.map((point) => [Date.parse(`${point.date}T00:00:00Z`), point.weight_kg]),
  }],
}))

function formatWeight(value: number | null | undefined): string {
  return value === null || value === undefined ? '–' : `${number.format(value)} kg`
}

async function syncQuery(): Promise<void> {
  await router.replace({
    query: { ...route.query, start: start.value, end: end.value, period: period.value },
  })
}

async function load(): Promise<void> {
  const requestId = ++loadId.value
  if (!analysisRangeWithinDays(start.value, end.value, 3661)) {
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

async function choosePreset(value: WeightPreset): Promise<void> {
  period.value = value
  if (value === 'custom') return
  const range = rangeForPreset(value)
  start.value = range.start
  end.value = range.end
  await load()
}

async function applyCustomRange(): Promise<void> {
  period.value = 'custom'
  await load()
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const queryStart = queryString(nextStart)
    const queryEnd = queryString(nextEnd)
    const hasRange = validAnalyticsRange(queryStart, queryEnd)
    const requestedPreset = presets.find((value) => value === queryString(nextPeriod))
    const nextPreset = requestedPreset ?? (hasRange ? 'custom' : '30')
    const nextRange = hasRange
      ? { start: queryStart as string, end: queryEnd as string }
      : nextPreset === 'custom'
        ? { start: start.value, end: end.value }
        : rangeForPreset(nextPreset)
    const rangeChanged = nextRange.start !== start.value || nextRange.end !== end.value
    const presetChanged = nextPreset !== period.value
    start.value = nextRange.start
    end.value = nextRange.end
    period.value = nextPreset
    if (rangeChanged || presetChanged) void load()
  },
)

onMounted(() => { void load() })
</script>

<template>
  <div class="analysis-detail-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <RouterLink :to="{ path: '/analyse/koerper', query: analysisHubQuery(period) }">{{ t('analysisV2.categories.body') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ t('analysisV2.weightTitle') }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1>{{ t('analysisV2.weightTitle') }}</h1>
        <p>{{ t('analysisV2.weightDescription') }}</p>
      </div>
    </div>

    <section class="weight-period card" :aria-label="t('analysisV2.range')">
      <div class="weight-period-presets" role="group" :aria-label="t('analysisV2.range')">
        <button
          v-for="choice in presets"
          :key="choice"
          class="button secondary"
          type="button"
          :aria-pressed="period === choice"
          @click="choosePreset(choice)"
        >
          {{ t(choice === 'custom' ? 'analysisV2.customRange' : `analysisV2.range${choice}`) }}
        </button>
      </div>
      <form v-if="period === 'custom'" class="weight-custom-range" @submit.prevent="applyCustomRange">
        <label>
          {{ t('analysisV2.startDate') }}
          <input v-model="start" type="date" :max="today" required>
        </label>
        <label>
          {{ t('analysisV2.endDate') }}
          <input v-model="end" type="date" :max="today" required>
        </label>
        <button class="button secondary" type="submit">{{ t('analysisV2.applyRange') }}</button>
      </form>
    </section>

    <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">{{ t('common.loading') }}</div>
    <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button secondary" type="button" @click="load">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else-if="response && summary">
      <section class="weight-summary-grid" :aria-label="t('analysisV2.weightHistory')">
        <article class="card">
          <h2>{{ t('analysisV2.measurementCount', { count: number.format(summary.measurement_count) }) }}</h2>
          <span>{{ formatGermanDate(start) }} – {{ formatGermanDate(end) }}</span>
        </article>
        <article class="card">
          <h2>{{ t('analysisV2.latestMeasurement') }}</h2>
          <strong>{{ formatWeight(summary.latest?.weight_kg) }}</strong>
          <time v-if="summary.latest" :datetime="summary.latest.date">{{ formatGermanDate(summary.latest.date) }}</time>
        </article>
        <article class="card">
          <h2>{{ t('analysisV2.change') }}</h2>
          <strong>{{ change === null ? '–' : formatWeight(change) }}</strong>
          <span v-if="points.length < 2">{{ t('analysisV2.changeUnavailable') }}</span>
        </article>
        <article class="card"><h2>{{ t('analysisV2.average') }}</h2><strong>{{ formatWeight(summary.average_kg) }}</strong></article>
        <article class="card"><h2>{{ t('analysisV2.minimum') }}</h2><strong>{{ formatWeight(summary.minimum_kg) }}</strong></article>
        <article class="card"><h2>{{ t('analysisV2.maximum') }}</h2><strong>{{ formatWeight(summary.maximum_kg) }}</strong></article>
      </section>
      <p v-if="!points.length" class="card empty" role="status">{{ t('analysisV2.weightEmpty') }}</p>
      <template v-else>
        <ChartPanel :title="t('analysisV2.weightHistory')" :option="chartOption" :empty="false" />
        <section class="card weight-reading-list">
          <h2>{{ t('analysisV2.weightHistory') }}</h2>
          <ul :aria-label="t('analysisV2.weightHistory')">
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
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.weight-period { display: grid; gap: .8rem; }
.weight-period-presets { display: flex; flex-wrap: wrap; gap: .5rem; }
.weight-period button[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.weight-custom-range { display: flex; flex-wrap: wrap; align-items: end; gap: .75rem; }
.weight-custom-range label { display: grid; gap: .3rem; }
.weight-custom-range input { min-height: 2.5rem; }
.weight-summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.weight-summary-grid article { display: grid; align-content: start; gap: .3rem; min-width: 0; }
.weight-summary-grid h2 { margin: 0; color: var(--muted); font-size: .92rem; }
.weight-summary-grid strong { font-size: 1.25rem; overflow-wrap: anywhere; }
.weight-summary-grid time, .weight-summary-grid span { color: var(--muted); font-size: .9rem; }
.weight-reading-list h2 { margin-top: 0; font-size: 1.05rem; }
.weight-reading-list ul { list-style: none; margin: 0; padding: 0; }
.weight-reading-list li { display: flex; justify-content: space-between; gap: .8rem; padding: .65rem 0; border-top: 1px solid var(--border); }
@media (max-width: 680px) { .weight-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 440px) { .weight-summary-grid { grid-template-columns: 1fr; } }
</style>
