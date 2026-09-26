<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import {
  analysisRangeWithinDays,
  analyticsPresetMatchesRange,
  parseAnalyticsCompactPreset,
  resolveAnalyticsRange,
  validAnalyticsRange,
  type AnalyticsCompactPreset,
} from '../analytics-period'
import AnalyticsPeriodFilter from '../components/AnalyticsPeriodFilter.vue'
import { createNumberFormatter, i18n } from '../i18n'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { useAuthStore } from '../stores/auth'
import type { AnalysisMicronutrientMetric, AnalysisMicronutrientResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const defaultRange = { start: shiftIsoDate(today, -29), end: today }
const compactPresets: AnalyticsCompactPreset[] = ['7', '30']
const desktopPresets = ['custom', '7', '30']
const initialRange = resolveAnalyticsRange(route.query.start, route.query.end, defaultRange)

function periodForRange(start: string, end: string): AnalyticsCompactPreset {
  if (start === shiftIsoDate(end, -6)) return '7'
  if (start === shiftIsoDate(end, -29)) return '30'
  return 'custom'
}

const initialRangeIsValid = validAnalyticsRange(route.query.start, route.query.end)
const queryPeriod = parseAnalyticsCompactPreset(route.query.period, [...compactPresets, 'custom'])
const start = ref(initialRange.start)
const end = ref(initialRange.end)
const period = ref<AnalyticsCompactPreset>(
  initialRangeIsValid && analyticsPresetMatchesRange(queryPeriod, route.query.start, route.query.end)
    ? queryPeriod ?? periodForRange(initialRange.start, initialRange.end)
    : periodForRange(initialRange.start, initialRange.end),
)
const response = ref<AnalysisMicronutrientResponse | null>(null)
const loading = ref(true)
const error = ref('')
let latestLoadId = 0
const number = createNumberFormatter({ maximumSignificantDigits: 4 })

function selectPeriod(value: AnalyticsCompactPreset): void {
  period.value = value
}

watch(
  () => [route.query.start, route.query.end, route.query.period] as const,
  ([nextStart, nextEnd, nextPeriod]) => {
    const hasRange = validAnalyticsRange(nextStart, nextEnd)
    const range = resolveAnalyticsRange(nextStart, nextEnd, defaultRange)
    const candidate = parseAnalyticsCompactPreset(nextPeriod, [...compactPresets, 'custom'])
    const nextPreset = hasRange && analyticsPresetMatchesRange(candidate, nextStart, nextEnd)
      ? candidate ?? periodForRange(range.start, range.end)
      : periodForRange(range.start, range.end)
    const rangeChanged = range.start !== start.value || range.end !== end.value
    start.value = range.start
    end.value = range.end
    period.value = nextPreset
    if (rangeChanged) void load()
  },
)

async function load(): Promise<void> {
  const loadId = ++latestLoadId
  if (!analysisRangeWithinDays(start.value, end.value, 31)) {
    response.value = null
    error.value = t('analysisMicronutrients.invalidRange')
    loading.value = false
    return
  }

  loading.value = true
  error.value = ''
  response.value = null
  await router.replace({
    query: { ...route.query, start: start.value, end: end.value, period: period.value },
  })
  if (loadId !== latestLoadId) return

  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    const result = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (loadId === latestLoadId) response.value = result
  } catch (cause) {
    if (loadId === latestLoadId) error.value = localizeApiError(cause, 'analysisMicronutrients.loadFailed')
  } finally {
    if (loadId === latestLoadId) loading.value = false
  }
}

onMounted(() => { void load() })

const vitamins = computed(() => response.value?.metrics.filter((metric) =>
  metric.category === 'vitamin' && metric.days_with_data > 0,
) ?? [])
const minerals = computed(() => response.value?.metrics.filter((metric) =>
  metric.category === 'mineral' && metric.days_with_data > 0,
) ?? [])
const hasValues = computed(() => vitamins.value.length > 0 || minerals.value.length > 0)

function providerLabel(providerKey: string): string {
  const key = `providerPreferencesUi.providers.${providerKey}`
  const label = t(key)
  return label === key ? providerKey : label
}

function formatTotal(metric: AnalysisMicronutrientMetric): string {
  return metric.total === null ? '–' : `${number.format(metric.total)} ${metric.unit}`
}

function reference(metric: AnalysisMicronutrientMetric): string | null {
  return metric.eu_nrv === null
    ? null
    : t('analysisMicronutrients.reference', { value: number.format(metric.eu_nrv), unit: metric.unit })
}

const metricGroups = computed(() => [
  { key: 'vitamin', label: t('analysisMicronutrients.vitamins'), metrics: vitamins.value },
  { key: 'mineral', label: t('analysisMicronutrients.minerals'), metrics: minerals.value },
])

function contributors(metric: AnalysisMicronutrientMetric): string {
  return metric.contributors.map((item) => t(
    item.days_with_data === 1 ? 'analysisMicronutrients.sourceDay' : 'analysisMicronutrients.sourceDays',
    {
      provider: providerLabel(item.provider_key),
      count: item.days_with_data,
    },
  )).join(' · ')
}

function detailLocation(metric: AnalysisMicronutrientMetric) {
  return {
    name: 'analysis-micronutrient-detail',
    params: { metric: metric.metric_key },
    query: { start: start.value, end: end.value },
  }
}
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('analysisMicronutrients.title') }}</h1>
      <p>{{ t('analysisMicronutrients.description') }}</p>
    </div>
  </div>

  <AnalyticsPeriodFilter
    :start="start"
    :end="end"
    :initial-preset="period"
    :compact-preset="period"
    :compact-presets="compactPresets"
    :desktop-presets="desktopPresets"
    @update:start="start = $event"
    @update:end="end = $event"
    @preset="selectPeriod"
    @apply="load"
  />

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('common.loading') }}
  </div>
  <section v-else-if="error" class="card error" role="alert" aria-live="assertive">
    <p>{{ error }}</p>
    <button class="button secondary analysis-retry" type="button" @click="load">
      {{ t('common.tryAgain') }}
    </button>
  </section>
  <p v-else-if="response && !hasValues" class="card empty" role="status">
    {{ t('analysisMicronutrients.empty') }}
  </p>
  <div v-else-if="response" class="analysis-micronutrient-groups">
    <section v-for="group in metricGroups" :key="group.key" class="micronutrient-group">
      <h2>{{ group.label }}</h2>
      <div v-if="group.metrics.length" class="micronutrient-cards">
        <article v-for="metric in group.metrics" :key="metric.metric_key" class="card micronutrient-card">
          <RouterLink class="micronutrient-detail-link" :to="detailLocation(metric)">
            <h3>{{ metric.label }}</h3>
            <span class="micronutrient-total">{{ formatTotal(metric) }}</span>
          </RouterLink>
          <p>{{ t('analysisMicronutrients.coverage', { count: metric.days_with_data, period: response.period_days }) }}</p>
          <p v-if="reference(metric)" class="micronutrient-reference">{{ reference(metric) }}</p>
          <p v-if="metric.contributors.length" class="micronutrient-contributors">
            <span class="micronutrient-secondary-label">{{ t('analysisMicronutrients.contributors') }}:</span>
            {{ contributors(metric) }}
          </p>
        </article>
      </div>
      <p v-else class="card empty micronutrient-group-empty" role="status">
        {{ t('analysisMicronutrients.emptyCategory') }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.analysis-micronutrient-groups { display: grid; gap: 1.4rem; }
.micronutrient-group h2 { margin: 0 0 .75rem; font-size: 1.2rem; }
.micronutrient-cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.micronutrient-card { min-width: 0; }
.micronutrient-detail-link { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: .35rem .8rem; color: inherit; text-decoration: none; }
.micronutrient-detail-link:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
.micronutrient-detail-link h3 { margin: 0; min-width: 0; overflow-wrap: anywhere; font-size: 1rem; }
.micronutrient-total { font-size: 1.2rem; font-weight: 700; white-space: nowrap; }
.micronutrient-card p { margin: .45rem 0 0; color: var(--muted); font-size: .88rem; overflow-wrap: anywhere; }
.micronutrient-reference { font-size: .8rem !important; }
.micronutrient-secondary-label { font-size: .78rem; }
@media (max-width: 640px) { .micronutrient-cards { grid-template-columns: 1fr; } }
</style>
