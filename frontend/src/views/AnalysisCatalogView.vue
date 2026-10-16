<script setup lang="ts">
import {
  computed, nextTick, onBeforeUnmount, onMounted, ref, watch,
} from 'vue'
import { PhCaretRight } from '@phosphor-icons/vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisCatalogEntryGrid from '../components/AnalysisCatalogEntryGrid.vue'
import {
  AnalysisIcon, addAnalysisPreference, analysisCategories, analysisHref, analysisHubQuery,
  canonicalAnalysisCardId, localizedAnalysisPresentation, analysisRangeDayCount,
  hubMiniChartVariant, HUB_MINI_CHART_BAR_WIDTH_RATIO, HUB_MINI_CHART_MAX_BAR_WIDTH,
} from '../analysis-v2'
import { commonAllDataRange, periodFromQuery, validPeriodRange, type AppliedPeriod, type PeriodLimits, type PeriodRange, type PeriodSelection } from '../analytics-period'
import PeriodPicker from '../components/PeriodPicker.vue'
import AnalysisMiniChart from '../components/AnalysisMiniChart.vue'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import { micronutrientColorTokenFor, micronutrientIconFor } from '../micronutrient-identity'
import type { AnalysisCategoryKey, AnalysisMicronutrientMetric, AnalysisMicronutrientResponse, AnalysisRegistryResponse } from '../types'


const t = i18n.global.t.bind(i18n.global)
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const MICRONUTRIENT_MIN_BAR_WIDTH = 2.5
const props = defineProps<{ categoryKey?: AnalysisCategoryKey }>()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const preferences = useAnalysisPreferences()
const activeCardIds = preferences.activeCardIds
const saving = preferences.saving
const saveError = preferences.saveError
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const maxDays = computed(() => (
  props.categoryKey === 'nutrition' || props.categoryKey === 'micronutrients' ? 365
    : props.categoryKey === 'body' || props.categoryKey === 'activity' ? 3661 : 31
))
const limits = computed<PeriodLimits>(() => ({
  minDays: 1, maxDays: maxDays.value, customRange: true, maxDate: today.value,
}))
const initialSelection = periodFromQuery(route.query, limits.value, today.value, null)
const period = ref<PeriodSelection>(initialSelection.period)
const customRange = ref<PeriodRange | null>(initialSelection.period === 'custom'
  ? { start: initialSelection.start, end: initialSelection.end } : null)
const committedAllRange = ref<PeriodRange | null>(null)
const normalizedSelection = computed(() => periodFromQuery(
  { period: period.value, ...(customRange.value ?? {}) }, limits.value, today.value,
  period.value === 'all' ? committedAllRange.value : allRange.value,
))
const range = computed(() => ({
  start: normalizedSelection.value.start, end: normalizedSelection.value.end,
}))
const periodDays = computed(() => analysisRangeDayCount(range.value))
const registry = ref<AnalysisRegistryResponse['analyses']>([])
const workspaceLoading = ref(true)
const workspaceError = ref('')
const micronutrientLoading = ref(false)
const micronutrientError = ref('')
const micronutrients = ref<AnalysisMicronutrientResponse | null>(null)
const micronutrientMode = ref<'with-data' | 'all'>('with-data')
const micronutrientTrends = ref<Record<string, AnalysisMicronutrientResponse>>({})
const micronutrientTrendErrors = ref<Record<string, string>>({})
let micronutrientLoadId = 0
let workspaceLoadId = 0
let lastMicronutrientSignature = ''
const displaysMicronutrients = computed(() => props.categoryKey === undefined || props.categoryKey === 'micronutrients')
const micronutrientSourceIdentity = computed(() => JSON.stringify([
  props.categoryKey ?? 'catalog',
  registry.value.filter((entry) => entry.category_key === 'micronutrients'
    && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients')
    .map((entry) => entry.analysis_key).sort(),
]))
const capability = computed(() => displaysMicronutrients.value
  && !micronutrientLoading.value && !micronutrientError.value
  ? micronutrients.value?.period_capabilities : undefined)
const allRange = computed(() => commonAllDataRange([
  { limits: limits.value, capability: capability.value },
], today.value))

function invalidateMicronutrientResponses(): void {
  micronutrientLoadId += 1
  lastMicronutrientSignature = ''
  micronutrientLoading.value = false
  micronutrientError.value = ''
  micronutrients.value = null
  micronutrientTrends.value = {}
  micronutrientTrendErrors.value = {}
}
watch(micronutrientSourceIdentity, () => {
  invalidateMicronutrientResponses()
  committedAllRange.value = null
}, { flush: 'sync' })
const search = ref('')
const searchTerm = computed(() => search.value.trim().toLocaleLowerCase(i18n.global.locale.value))
const groupedEntries = computed(() => analysisCategories
  .filter((category) => !props.categoryKey || category.key === props.categoryKey)
  .map((category) => ({
    ...category,
    entries: registry.value.flatMap((entry) => {
      if (entry.category_key !== category.key) return []
      const presentation = localizedAnalysisPresentation(entry.analysis_key)
      if (!presentation) return []
      if (searchTerm.value && ![presentation.label, presentation.description].some(
        (text) => text.toLocaleLowerCase(i18n.global.locale.value).includes(searchTerm.value),
      )) return []
      return [{ entry, presentation }]
    }),
  }))
  .filter((group) => !searchTerm.value || group.entries.length > 0))
const activeCategory = computed(() => analysisCategories.find((category) => category.key === props.categoryKey) ?? null)
const pageTitle = computed(() => activeCategory.value ? t(activeCategory.value.labelKey) : t('analysisV2.catalog'))
const categoryPath: Record<AnalysisCategoryKey, string> = {
  body: '/analyse/koerper',
  activity: '/analyse/aktivitaet',
  nutrition: '/analyse/ernaehrung',
  micronutrients: '/analyse/mikronaehrstoffe',
  fluid: '/analyse',
}
const micronutrientTypeGroups = computed(() => ([
  { key: 'vitamin' as const, labelKey: 'analysisV2.micronutrientTypes.vitamins' },
  { key: 'mineral' as const, labelKey: 'analysisV2.micronutrientTypes.minerals' },
]).map((type) => ({
  ...type,
  entries: groupedEntries.value.flatMap((group) => group.entries.filter(({ entry }) => (
    group.key === 'micronutrients'
      && micronutrients.value?.metrics.find((metric) => metric.metric_key === entry.analysis_key)?.category === type.key
  ))),
})))
const summaryNutrients = computed(() => [...new Map(
  (micronutrients.value?.metrics ?? [])
    .filter((metric) => props.categoryKey === 'micronutrients' || registry.value.some((entry) => (
      entry.analysis_key === metric.metric_key && entry.category_key === 'micronutrients'
        && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients'
    )))
    .map((metric) => [metric.metric_key, metric]),
).values()])
function nutrientHasData(metric: AnalysisMicronutrientMetric): metric is AnalysisMicronutrientMetric & { total: number } {
  return metric.days_with_data > 0 && metric.total !== null
}
const dataBearingNutrients = computed(() => summaryNutrients.value.filter(nutrientHasData))
const micronutrientOverviewGroups = computed(() => micronutrientTypeGroups.value.map((type) => ({
  ...type,
  availableMetrics: (micronutrientMode.value === 'all' ? summaryNutrients.value : dataBearingNutrients.value)
    .filter((metric) => metric.category === type.key),
})))
const presentNutrientCount = computed(() => summaryNutrients.value.filter(
  (metric) => metric.days_with_data > 0 && metric.total !== null,
).length)
const trendNutrients = computed(() => props.categoryKey === 'micronutrients'
  ? dataBearingNutrients.value
  : summaryNutrients.value.filter((metric) => metric.days_with_data > 0).slice(0, 3))
const trendDates = computed(() => Array.from(
  { length: periodDays.value }, (_, index) => shiftIsoDate(range.value.start, index),
))
const trendCharts = computed(() => trendNutrients.value.map((metric) => {
  const response = micronutrientTrends.value[metric.metric_key]
  const byDate = new Map((response?.series ?? []).map(({ date, value }) => [date, value]))
  const values = trendDates.value.map((date) => byDate.get(date) ?? null)
  let firstDataIndex = -1
  let lastDataIndex = -1
  for (let index = 0; index < values.length; index += 1) {
    if (typeof values[index] !== 'number' || !Number.isFinite(values[index])) continue
    if (firstDataIndex < 0) firstDataIndex = index
    lastDataIndex = index
  }
  const variant = hubMiniChartVariant(values, trendDates.value)
  return {
    metric,
    loaded: response !== undefined,
    values,
    variant,
    hasValues: firstDataIndex >= 0,
    dateRange: variant === 'bars' && firstDataIndex >= 0
      ? { start: trendDates.value[firstDataIndex]!, end: trendDates.value[lastDataIndex]! }
      : range.value,
  }
}))
const trendChartsByKey = computed(() => new Map(
  trendCharts.value.map((chart) => [chart.metric.metric_key, chart] as const),
))

const handoffQuery = computed(() => ({
  ...route.query, ...analysisHubQuery(normalizedSelection.value.period, range.value),
  start: normalizedSelection.value.period === 'custom' ? range.value.start : undefined,
  end: normalizedSelection.value.period === 'custom' ? range.value.end : undefined,
}))

async function syncPeriodQuery(): Promise<void> {
  const query = { ...route.query }
  delete query.start
  delete query.end
  Object.assign(query, analysisHubQuery(normalizedSelection.value.period, range.value))
  if (route.query.period === query.period && route.query.start === query.start && route.query.end === query.end) return
  await router.replace({ query, hash: route.hash })
}

async function loadMicronutrients(): Promise<void> {
  const userId = auth.user?.id
  if (!displaysMicronutrients.value || !userId) return
  const hasCanonicalMicronutrients = registry.value.some((entry) => (
    entry.category_key === 'micronutrients' && entry.status === 'available'
      && entry.reader_key === 'analytics.nutrition.micronutrients'
  ))
  if (!hasCanonicalMicronutrients) return
  const { start, end } = range.value
  const signature = `${micronutrientSourceIdentity.value}|${start}|${end}`
  if (signature === lastMicronutrientSignature) return
  lastMicronutrientSignature = signature
  const loadId = ++micronutrientLoadId
  let allDataUnsupported = false
  micronutrientLoading.value = true
  micronutrientError.value = ''
  micronutrients.value = null
  micronutrientTrends.value = {}
  micronutrientTrendErrors.value = {}
  const params = new URLSearchParams({ start, end })
  try {
    const response = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (loadId !== micronutrientLoadId || auth.user?.id !== userId) return
    micronutrients.value = response
    allDataUnsupported = response.period_capabilities?.supports_all_data === false
    await Promise.all(trendNutrients.value.map(async ({ metric_key }) => {
      const metricParams = new URLSearchParams({ start, end, metric_key })
      try {
        const trend = await api<AnalysisMicronutrientResponse>(
          `/analytics/nutrition/micronutrients?${metricParams.toString()}`,
        )
        if (loadId === micronutrientLoadId && auth.user?.id === userId) {
          micronutrientTrends.value[metric_key] = trend
        }
      } catch (cause) {
        if (loadId === micronutrientLoadId && auth.user?.id === userId) {
          micronutrientTrendErrors.value[metric_key] = localizeApiError(cause, 'analysisV2.loadFailed')
          lastMicronutrientSignature = ''
        }
      }
    }))
  } catch (cause) {
    if (loadId === micronutrientLoadId && auth.user?.id === userId) {
      micronutrientError.value = localizeApiError(cause, 'analysisV2.loadFailed')
      lastMicronutrientSignature = ''
    }
  } finally {
    if (loadId === micronutrientLoadId && auth.user?.id === userId) micronutrientLoading.value = false
  }
  if (loadId !== micronutrientLoadId || auth.user?.id !== userId || period.value !== 'all') return
  if (allDataUnsupported) {
    committedAllRange.value = null
    period.value = '30'
    customRange.value = null
    await syncPeriodQuery()
    await loadMicronutrients()
    return
  }
  const admitted = allRange.value
  if (!admitted) return
  committedAllRange.value = admitted
  if (admitted.start !== start || admitted.end !== end) await loadMicronutrients()
}

async function loadWorkspace(): Promise<void> {
  const userId = auth.user?.id
  if (!userId) {
    workspaceLoadId += 1
    registry.value = []
    workspaceLoading.value = false
    workspaceError.value = ''
    return
  }
  const loadId = ++workspaceLoadId
  workspaceLoading.value = true
  workspaceError.value = ''
  try {
    const [response] = await Promise.all([
      api<AnalysisRegistryResponse>('/analytics/registry'),
      preferences.load(),
    ])
    if (loadId !== workspaceLoadId || auth.user?.id !== userId) return
    registry.value = response.analyses
    await syncPeriodQuery()
    if (loadId !== workspaceLoadId || auth.user?.id !== userId) return
    workspaceLoading.value = false
    await loadMicronutrients()
  } catch (cause) {
    if (loadId === workspaceLoadId && auth.user?.id === userId) {
      workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (loadId === workspaceLoadId && auth.user?.id === userId) workspaceLoading.value = false
  }
}

async function applyPeriod(value: AppliedPeriod): Promise<void> {
  if (props.categoryKey === 'micronutrients' && !validPeriodRange(value, limits.value)) return
  const normalized = periodFromQuery(
    { period: value.period, ...(value.period === 'custom' ? { start: value.start, end: value.end } : {}) },
    limits.value, today.value, allRange.value,
  )
  committedAllRange.value = normalized.period === 'all'
    ? { start: normalized.start, end: normalized.end } : null
  period.value = normalized.period
  customRange.value = normalized.period === 'custom'
    ? { start: normalized.start, end: normalized.end } : null
  invalidateMicronutrientResponses()
  await syncPeriodQuery()
  await loadMicronutrients()
}

async function addAnalysis(key: string): Promise<void> {
  const canonicalKey = canonicalAnalysisCardId(key)
  if (saving.value || activeCardIds.value.includes(canonicalKey)) return
  await preferences.replaceActiveCardIds(addAnalysisPreference(activeCardIds.value, canonicalKey))
}

function removeAnalysis(key: string): void {
  const canonicalKey = canonicalAnalysisCardId(key)
  if (saving.value) return
  void preferences.replaceActiveCardIds(activeCardIds.value.filter((item) => item !== canonicalKey))
}


async function scrollToCategoryHash(): Promise<void> {
  const id = route.hash.slice(1)
  if (!id.startsWith('analysis-category-')) return
  await nextTick()
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

watch(
  () => [route.query.period, route.query.start, route.query.end, props.categoryKey] as const,
  (current, previous) => {
    const [queryPeriod, queryStartValue, queryEndValue, categoryKey] = current
    const categoryChanged = previous !== undefined && previous[3] !== categoryKey
    const normalized = periodFromQuery(
      { period: queryPeriod,
        ...(queryStartValue !== undefined ? { start: queryStartValue } : {}),
        ...(queryEndValue !== undefined ? { end: queryEndValue } : {}) },
      limits.value, today.value, period.value === 'all' ? committedAllRange.value : allRange.value,
    )
    const nextPeriod = normalized.period
    const nextCustom = nextPeriod === 'custom' ? { start: normalized.start, end: normalized.end } : null
    const changed = period.value !== nextPeriod || customRange.value?.start !== nextCustom?.start
      || customRange.value?.end !== nextCustom?.end
    committedAllRange.value = nextPeriod === 'all'
      ? { start: normalized.start, end: normalized.end } : null
    period.value = nextPeriod
    if (changed) customRange.value = nextCustom
    if (changed) invalidateMicronutrientResponses()
    if (categoryKey === undefined || categoryKey === 'micronutrients') {
      if ((changed || categoryChanged) && !workspaceLoading.value && registry.value.length) {
        void syncPeriodQuery().then(() => loadMicronutrients())
      } else if (!workspaceLoading.value) void syncPeriodQuery()
      return
    }
    // Source invalidation runs synchronously before this route normalization.
    if (!workspaceLoading.value) void syncPeriodQuery()
  },
)
watch(
  () => [auth.user?.id ?? null, auth.user?.timezone ?? 'UTC'] as const,
  ([userId, timezone], [previousUserId, previousTimezone]) => {
    if (userId === previousUserId && timezone === previousTimezone) return
    const accountChanged = userId !== previousUserId
    invalidateMicronutrientResponses()
    if (accountChanged && route.query.period !== 'all') committedAllRange.value = null
    if (accountChanged) {
      workspaceLoadId += 1
      registry.value = []
      workspaceError.value = ''
      workspaceLoading.value = Boolean(userId)
    }
    if (!userId) {
      registry.value = []
      workspaceLoading.value = false
      workspaceError.value = ''
      return
    }
    const normalized = periodFromQuery(
      route.query, limits.value, today.value, committedAllRange.value,
    )
    period.value = normalized.period
    committedAllRange.value = normalized.period === 'all'
      ? { start: normalized.start, end: normalized.end } : null
    customRange.value = normalized.period === 'custom'
      ? { start: normalized.start, end: normalized.end } : null
    if (accountChanged) {
      void loadWorkspace()
    } else if (displaysMicronutrients.value) {
      void syncPeriodQuery().then(() => loadMicronutrients())
    } else {
      void syncPeriodQuery()
    }
  },
  { flush: 'sync' },
)


onMounted(async () => {
  // loadWorkspace advances this generation; unmount/account changes invalidate its continuation.
  const loadId = workspaceLoadId + 1
  await loadWorkspace()
  if (loadId !== workspaceLoadId) return
  await syncPeriodQuery()
  if (loadId !== workspaceLoadId) return
  await scrollToCategoryHash()
})
onBeforeUnmount(() => {
  workspaceLoadId += 1
  micronutrientLoadId += 1
})
</script>

<template>
  <div class="analysis-catalog-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: handoffQuery, hash: route.hash }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ pageTitle }}</span>
    </nav>

    <div class="page-heading analysis-heading page-heading--period">
      <div>
        <h1>{{ pageTitle }}</h1>
        <p v-if="!props.categoryKey">{{ t('analysisV2.catalogDescription') }}</p>
      </div>
      <PeriodPicker
        v-if="!workspaceLoading && !workspaceError"
        :start="range.start"
        :end="range.end"
        :today="today"
        :period="normalizedSelection.period"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :max-date="limits.maxDate"
        :period-capabilities="capability"
        :all-range="allRange"
        @apply="applyPeriod"
      />
    </div>

    <div v-if="workspaceLoading" class="dashboard-loading" role="status" aria-live="polite">
      {{ t('analysisV2.loading') }}
    </div>
    <section v-else-if="workspaceError" class="card error" role="alert">
      <p>{{ workspaceError }}</p>
      <button class="button secondary" type="button" @click="loadWorkspace">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else>
      <section v-if="saveError" class="card error" role="alert" aria-live="assertive">{{ saveError }}</section>
      <label v-if="props.categoryKey !== 'micronutrients'" class="analysis-catalog-search">
        <span>{{ t('analysisV2.catalogSearchLabel') }}</span>
        <input v-model="search" type="search" :placeholder="t('analysisV2.catalogSearchPlaceholder')">
      </label>
      <p v-if="props.categoryKey !== 'micronutrients' && searchTerm && !groupedEntries.some((group) => group.entries.length)" class="card empty" role="status">
        {{ t('analysisV2.catalogSearchNoResults') }}
      </p>
      <section v-if="(!props.categoryKey || props.categoryKey === 'micronutrients') && micronutrientLoading" class="card" role="status" aria-live="polite">
        {{ t('analysisV2.loading') }}
      </section>
      <section v-else-if="(!props.categoryKey || props.categoryKey === 'micronutrients') && micronutrientError" class="card error" role="alert">
        <p>{{ micronutrientError }}</p>
        <button class="button secondary" type="button" @click="loadMicronutrients">{{ t('analysisV2.retry') }}</button>
      </section>

      <div
        v-if="props.categoryKey === 'micronutrients' && micronutrients"
        id="analysis-category-micronutrients"
        class="analysis-micronutrient-overview"
      >
        <div class="analysis-micronutrient-toolbar">
          <div
            class="verification-view-toggle"
            role="group"
            :aria-label="t('analysisV2.micronutrientDisplayMode')"
          >
            <button
              type="button"
              :aria-pressed="micronutrientMode === 'with-data'"
              @click="micronutrientMode = 'with-data'"
            >{{ t('analysisV2.micronutrientWithData') }}</button>
            <button
              type="button"
              :aria-pressed="micronutrientMode === 'all'"
              @click="micronutrientMode = 'all'"
            >{{ t('analysisV2.micronutrientAll') }}</button>
          </div>
          <span
            class="analysis-micronutrient-average-mark"
            :title="t('analysisV2.micronutrientAverageNote')"
            :aria-label="t('analysisV2.micronutrientAverageNote')"
          >Ø</span>
        </div>
        <div class="analysis-micronutrient-groups">
          <section
            v-for="group in micronutrientOverviewGroups"
            :key="group.key"
            class="card analysis-micronutrient-group"
            :data-nutrient-group="group.key"
          >
            <header class="section-card-header analysis-micronutrient-group-heading">
              <h2 class="analysis-catalog-category-heading font-medium">{{ t(group.labelKey) }}</h2>
            </header>
            <p
              v-if="group.availableMetrics.length === 0"
              class="analysis-nutrient-extra analysis-micronutrient-empty"
            >
              {{ t('analysisV2.noDataPeriod') }}
            </p>
            <ul v-else class="analysis-micronutrient-list">
              <li
                v-for="metric in group.availableMetrics"
                :key="metric.metric_key"
                class="analysis-micronutrient-row"
                :data-nutrient-key="metric.metric_key"
                :style="{ '--micronutrient-color': `var(${micronutrientColorTokenFor(metric.metric_key) ?? '--chart-series-emerald'})` }"
              >
                <RouterLink
                  :to="{ path: analysisHref(metric.metric_key), query: handoffQuery, hash: route.hash }"
                  class="analysis-micronutrient-link"
                >
                  <span class="analysis-micronutrient-heading">
                    <span class="analysis-micronutrient-icon" aria-hidden="true">
                      <component
                        :is="micronutrientIconFor(metric.metric_key)"
                        v-if="micronutrientIconFor(metric.metric_key)"
                        size="1em"
                        weight="bold"
                        color="currentColor"
                        aria-hidden="true"
                      />
                      <AnalysisIcon
                        v-else
                        :icon="localizedAnalysisPresentation(metric.metric_key)?.icon ?? 'leaf'"
                      />
                    </span>
                    <span class="analysis-micronutrient-copy">
                      <span class="analysis-micronutrient-name text-base font-medium">
                        {{ localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label }}
                      </span>
                      <span v-if="nutrientHasData(metric)" class="analysis-micronutrient-average">
                        {{ number.format(metric.total / metric.days_with_data) }}
                        {{ metric.unit === 'ug' ? 'µg' : metric.unit }}
                      </span>
                      <span v-else class="analysis-micronutrient-no-data">{{ t('analysisV2.micronutrientNoData') }}</span>
                    </span>
                  </span>
                  <AnalysisMiniChart
                    v-if="nutrientHasData(metric) && trendChartsByKey.get(metric.metric_key)?.hasValues"
                    class="analysis-micronutrient-trend"
                    :values="trendChartsByKey.get(metric.metric_key)?.values ?? []"
                    :dates="trendDates"
                    :date-range="trendChartsByKey.get(metric.metric_key)?.dateRange ?? range"
                    :label="t('analysisV2.micronutrientChartDescription', {
                      label: localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label,
                    })"
                    :variant="trendChartsByKey.get(metric.metric_key)?.variant ?? 'bars'"
                    :area="trendChartsByKey.get(metric.metric_key)?.variant === 'line'"
                    color-role="emerald"
                    series-color="var(--micronutrient-color)"
                    zero-as-bar
                    :min-bar-width="MICRONUTRIENT_MIN_BAR_WIDTH"
                    :bar-width-ratio="HUB_MINI_CHART_BAR_WIDTH_RATIO"
                    :max-bar-width="HUB_MINI_CHART_MAX_BAR_WIDTH"
                  />
                  <PhCaretRight class="analysis-micronutrient-chevron" size="1em" aria-hidden="true" />
                </RouterLink>
                <div
                  v-if="micronutrientTrendErrors[metric.metric_key]"
                  class="analysis-micronutrient-trend-error"
                  role="alert"
                >
                  <span>{{ micronutrientTrendErrors[metric.metric_key] }}</span>
                  <button class="button secondary" type="button" @click="loadMicronutrients">
                    {{ t('analysisV2.retry') }}
                  </button>
                </div>
              </li>
            </ul>
          </section>
        </div>
      </div>
      <section
        v-else-if="props.categoryKey === 'micronutrients' && !micronutrients && !micronutrientLoading && !micronutrientError"
        class="card empty"
        role="status"
      >
        {{ t('analysisV2.noDataPeriod') }}
      </section>

      <template v-if="props.categoryKey !== 'micronutrients'">
        <section
          v-for="group in groupedEntries"
          :id="`analysis-category-${group.key}`"
          :key="group.key"
          class="card analysis-catalog-category"
        >
          <h2 v-if="!props.categoryKey" class="analysis-catalog-category-heading">
            <RouterLink :to="{ path: categoryPath[group.key], query: handoffQuery, hash: group.key === 'fluid' ? '#analysis-category-fluid' : route.hash }">{{ t(group.labelKey) }}</RouterLink>
          </h2>
          <p v-if="!group.entries.length && !searchTerm" class="card empty">{{ t('analysisV2.catalogEmpty') }}</p>
          <template v-else-if="group.key === 'micronutrients' && micronutrients && !searchTerm">
            <p class="analysis-nutrient-count">
              {{ number.format(presentNutrientCount) }} {{ i18n.global.locale.value === 'de' ? 'von' : 'of' }} {{ number.format(summaryNutrients.length) }}
            </p>
            <p v-if="!presentNutrientCount" class="analysis-nutrient-extra">{{ t('analysisV2.noDataPeriod') }}</p>
            <ul v-if="trendCharts.length" class="analysis-nutrient-trends">
              <li v-for="{ metric, loaded, values } in trendCharts" :key="metric.metric_key">
                <RouterLink :to="{ path: `/analyse/mikronaehrstoffe/${encodeURIComponent(metric.metric_key)}`, query: handoffQuery, hash: route.hash }">{{ localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label }}</RouterLink>
                <span class="analysis-nutrient-extra">{{ t('analysisV2.daysCoverage', {
                  count: number.format(metric.days_with_data), total: number.format(periodDays),
                }) }}</span>
                <span v-if="micronutrientTrendErrors[metric.metric_key]" class="analysis-nutrient-error" role="alert">
                  {{ micronutrientTrendErrors[metric.metric_key] }}
                  <button class="button secondary" type="button" @click="loadMicronutrients">{{ t('analysisV2.retry') }}</button>
                </span>
                <span v-else-if="!loaded" role="status">{{ t('common.loading') }}</span>
                <AnalysisMiniChart
                  v-else
                  :values="values"
                  :dates="trendDates"
                  :date-range="range"
                  :label="`${localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label} · ${t('analysisV2.micronutrientDaily')}`"
                  variant="line"
                  color-role="emerald"
                />
              </li>
            </ul>
            <section v-for="type in micronutrientTypeGroups" :key="type.key" class="analysis-catalog-subcategory">
              <h3>{{ t(type.labelKey) }}</h3>
              <AnalysisCatalogEntryGrid
                :entries="type.entries"
                :selected-keys="activeCardIds"
                :saving="saving"
                :period="normalizedSelection.period"
                :period-range="range"
                :period-days="periodDays"
                :micronutrients="micronutrients"
                @add="addAnalysis"
                @remove="removeAnalysis"
              />
            </section>
          </template>
          <AnalysisCatalogEntryGrid
            v-else
            :entries="group.entries"
            :selected-keys="activeCardIds"
            :saving="saving"
            :period="normalizedSelection.period"
            :period-range="range"
            :period-days="periodDays"
            :micronutrients="micronutrients"
            @add="addAnalysis"
            @remove="removeAnalysis"
          />
        </section>
      </template>
    </template>
  </div>
</template>

<style scoped>
.analysis-catalog-page { display: grid; gap: 1.15rem; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; min-width: 0; color: var(--muted); font-size: .86rem; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-heading { margin: 0; }
.analysis-heading > div:first-child { min-width: 0; }
.analysis-heading p { max-width: 42rem; }
.analysis-catalog-search { display: grid; gap: .4rem; max-width: 32rem; color: var(--muted-strong); }
.analysis-catalog-search input { min-width: 0; width: 100%; }
.analysis-catalog-category { display: grid; gap: .8rem; min-width: 0; padding: .95rem; scroll-margin-top: 1rem; }
.analysis-catalog-category-heading { margin: 0; font-size: 1.1rem; }
.analysis-catalog-category-heading a { color: inherit; text-decoration: none; }
.analysis-catalog-category-heading a:hover { color: var(--accent-hover); }
.analysis-catalog-subcategory { display: grid; gap: .65rem; }
.analysis-catalog-subcategory h3 { margin: 0; color: var(--muted-strong); font-size: .92rem; }
.analysis-nutrient-count { margin: 0; font-weight: 700; }
.analysis-nutrient-extra { margin: 0; color: var(--muted); font-size: .88rem; }
.analysis-nutrient-trends { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr)); gap: 1rem; margin: 0; padding: 0; list-style: none; }
.analysis-nutrient-trends li { display: grid; align-content: start; gap: .45rem; min-width: 0; }
.analysis-nutrient-trends a { width: fit-content; padding-block: .4rem; color: var(--ink); font-weight: 700; overflow-wrap: anywhere; }
.analysis-nutrient-trends a:hover { color: var(--accent-hover); }
.analysis-nutrient-trends a:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.analysis-nutrient-error { display: grid; gap: .5rem; color: var(--danger); }
.analysis-micronutrient-overview {
  display: grid;
  gap: .65rem;
  min-width: 0;
  --analysis-micronutrient-lime: #a3e635;
  --analysis-micronutrient-orange: var(--chart-series-budget);
  --analysis-micronutrient-yellow: var(--chart-series-amber);
  --analysis-micronutrient-rust: #ef6c57;
}
:global(:root[data-theme='light'] .analysis-micronutrient-overview) {
  --analysis-micronutrient-lime: #4d7c0f;
  --analysis-micronutrient-orange: color-mix(in srgb, var(--chart-series-budget) 95%, var(--ink));
  --analysis-micronutrient-yellow: #a16207;
  --analysis-micronutrient-rust: #b9381f;
}
@media (prefers-color-scheme: light) {
  :global(:root:not([data-theme]) .analysis-micronutrient-overview) {
    --analysis-micronutrient-lime: #4d7c0f;
    --analysis-micronutrient-orange: color-mix(in srgb, var(--chart-series-budget) 95%, var(--ink));
    --analysis-micronutrient-yellow: #a16207;
    --analysis-micronutrient-rust: #b9381f;
  }
}
.analysis-micronutrient-toolbar { display: flex; align-items: center; justify-content: space-between; gap: .75rem; }
.analysis-micronutrient-toolbar .verification-view-toggle { width: fit-content; }
.analysis-micronutrient-average-mark { color: var(--muted); font-size: .9rem; }
.analysis-micronutrient-groups { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 27rem), 1fr)); align-items: start; gap: 1rem; }
.analysis-micronutrient-group { min-width: 0; align-content: start; }
.analysis-micronutrient-group-heading { padding: .85rem .8rem .7rem; }
.analysis-micronutrient-list { display: grid; min-width: 0; margin: 0; padding: 0; list-style: none; }
.analysis-micronutrient-row { min-width: 0; border-bottom: 1px solid var(--border); container-type: inline-size; container-name: micronutrient-row; }
.analysis-micronutrient-row:last-child { border-bottom: 0; }
.analysis-micronutrient-link { display: grid; grid-template-columns: minmax(0, 1fr) clamp(5rem, 22cqi, 7.5rem) 1rem; align-items: center; gap: .6rem; min-width: 0; padding: .55rem .8rem; border-radius: .35rem; color: var(--ink); text-decoration: none; }
.analysis-micronutrient-link:hover { background: color-mix(in srgb, var(--micronutrient-color) 5%, var(--surface)); }
.analysis-micronutrient-link:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.analysis-micronutrient-heading { display: grid; grid-template-columns: 2rem minmax(0, 1fr); align-items: center; gap: .55rem; min-width: 0; }
.analysis-micronutrient-icon { display: grid; place-items: center; width: 2rem; height: 2rem; border-radius: .62rem; color: var(--micronutrient-color); background: color-mix(in srgb, var(--micronutrient-color) 14%, transparent); }
.analysis-micronutrient-copy { display: grid; gap: .15rem; min-width: 0; }
.analysis-micronutrient-name { overflow-wrap: normal; hyphens: auto; }
.analysis-micronutrient-average, .analysis-micronutrient-no-data { color: var(--muted-strong); font-weight: 400; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.analysis-micronutrient-no-data { color: var(--muted); font-size: .88rem; }
.analysis-micronutrient-trend { display: block; width: 100%; min-width: 0; height: 3rem; }
.analysis-micronutrient-chevron { grid-column: 3; color: var(--muted); justify-self: end; }
.analysis-micronutrient-empty { padding: .8rem; }
.analysis-micronutrient-trend-error { display: grid; gap: .45rem; padding: .1rem .8rem .6rem; color: var(--danger); }


@media (max-width: 560px) {
  .analysis-heading { align-items: flex-start; flex-wrap: wrap; gap: .7rem; }
  .analysis-catalog-category { gap: .65rem; }
}
</style>
