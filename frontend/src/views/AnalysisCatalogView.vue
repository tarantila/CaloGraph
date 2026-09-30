<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisCatalogEntryGrid from '../components/AnalysisCatalogEntryGrid.vue'
import { analysisCategories, analysisHubQuery, localizedAnalysisPresentation, analysisRangeDayCount, normalizeAnalysisPeriodForCapability, parseAnalysisPeriodSelection, supportedAnalysisPeriods, type AnalysisPeriodSelection } from '../analysis-v2'
import AnalysisPeriodPicker from '../components/AnalysisPeriodPicker.vue'
import AnalysisMiniChart from '../components/AnalysisMiniChart.vue'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisCategoryKey, AnalysisMicronutrientResponse, AnalysisRegistryResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const props = defineProps<{ categoryKey?: AnalysisCategoryKey }>()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const preferences = useAnalysisPreferences()
const selectedKeys = preferences.selectedKeys
const saving = preferences.saving
const saveError = preferences.saveError
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const requestedPeriod = parseAnalysisPeriodSelection(route.query.period)
const queryStart = typeof route.query.start === 'string' ? route.query.start : null
const queryEnd = typeof route.query.end === 'string' ? route.query.end : null
const maxDays = computed(() => (
  props.categoryKey === 'nutrition' ? 365
    : props.categoryKey === 'body' || props.categoryKey === 'activity' ? 3661 : 31
))
const presets = computed(() => supportedAnalysisPeriods(maxDays.value, maxDays.value >= 3661))
const initialSelection = normalizeAnalysisPeriodForCapability(
  requestedPeriod, requestedPeriod === 'custom' && queryStart && queryEnd
    ? { start: queryStart, end: queryEnd } : null, maxDays.value, today,
)
const period = ref<AnalysisPeriodSelection>(initialSelection.period)
const customRange = ref(initialSelection.period === 'custom' ? initialSelection.range : null)
const normalizedSelection = computed(() => normalizeAnalysisPeriodForCapability(
  period.value, customRange.value, maxDays.value, today,
))
const range = computed(() => normalizedSelection.value.range)
const periodDays = computed(() => analysisRangeDayCount(range.value))
const registry = ref<AnalysisRegistryResponse['analyses']>([])
const workspaceLoading = ref(true)
const workspaceError = ref('')
const micronutrientLoading = ref(false)
const micronutrientError = ref('')
const micronutrients = ref<AnalysisMicronutrientResponse | null>(null)
const micronutrientTrends = ref<Record<string, AnalysisMicronutrientResponse>>({})
const micronutrientTrendErrors = ref<Record<string, string>>({})
let micronutrientLoadId = 0
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
    .filter((metric) => registry.value.some((entry) => (
      entry.analysis_key === metric.metric_key && entry.category_key === 'micronutrients'
        && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients'
    )))
    .map((metric) => [metric.metric_key, metric]),
).values()])
const presentNutrientCount = computed(() => summaryNutrients.value.filter(
  (metric) => metric.days_with_data > 0 && metric.total !== null,
).length)
const trendNutrients = computed(() => summaryNutrients.value
  .filter((metric) => metric.days_with_data > 0).slice(0, 3))
const trendDates = computed(() => Array.from(
  { length: periodDays.value }, (_, index) => shiftIsoDate(range.value.start, index),
))
const trendCharts = computed(() => trendNutrients.value.map((metric) => {
  const response = micronutrientTrends.value[metric.metric_key]
  const byDate = new Map((response?.series ?? []).map(({ date, value }) => [date, value]))
  return {
    metric,
    loaded: response !== undefined,
    values: trendDates.value.map((date) => byDate.get(date) ?? null),
  }
}))

async function syncPeriodQuery(): Promise<void> {
  const query = { ...route.query }
  delete query.start
  delete query.end
  Object.assign(query, analysisHubQuery(period.value, range.value))
  if (route.query.period === query.period && route.query.start === query.start && route.query.end === query.end) return
  await router.replace({ query, hash: route.hash })
}

async function loadMicronutrients(): Promise<void> {
  const displaysMicronutrients = props.categoryKey === undefined || props.categoryKey === 'micronutrients'
  if (!displaysMicronutrients) return
  const hasCanonicalMicronutrients = registry.value.some((entry) => (
    entry.category_key === 'micronutrients' && entry.status === 'available'
      && entry.reader_key === 'analytics.nutrition.micronutrients'
  ))
  if (!hasCanonicalMicronutrients) return
  const loadId = ++micronutrientLoadId
  const { start, end } = range.value
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
    if (loadId !== micronutrientLoadId) return
    micronutrients.value = response
    await Promise.all(trendNutrients.value.map(async ({ metric_key }) => {
      const metricParams = new URLSearchParams({ start, end, metric_key })
      try {
        const trend = await api<AnalysisMicronutrientResponse>(
          `/analytics/nutrition/micronutrients?${metricParams.toString()}`,
        )
        if (loadId === micronutrientLoadId) micronutrientTrends.value[metric_key] = trend
      } catch (cause) {
        if (loadId === micronutrientLoadId) {
          micronutrientTrendErrors.value[metric_key] = localizeApiError(cause, 'analysisV2.loadFailed')
        }
      }
    }))
  } catch (cause) {
    if (loadId === micronutrientLoadId) {
      micronutrientError.value = localizeApiError(cause, 'analysisV2.loadFailed')
    }
  } finally {
    if (loadId === micronutrientLoadId) micronutrientLoading.value = false
  }
}

async function loadWorkspace(): Promise<void> {
  workspaceLoading.value = true
  workspaceError.value = ''
  try {
    const [response] = await Promise.all([
      api<AnalysisRegistryResponse>('/analytics/registry'),
      preferences.load(),
    ])
    registry.value = response.analyses
    await syncPeriodQuery()
    workspaceLoading.value = false
    await loadMicronutrients()
  } catch (cause) {
    workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    workspaceLoading.value = false
  }
}

async function applyPeriod(value: { start: string; end: string; period: AnalysisPeriodSelection }): Promise<void> {
  const normalized = normalizeAnalysisPeriodForCapability(
    value.period, value.period === 'custom' ? value : null, maxDays.value, today,
  )
  period.value = normalized.period
  customRange.value = normalized.period === 'custom' ? normalized.range : null
  await syncPeriodQuery()
  await loadMicronutrients()
}

async function addAnalysis(key: string): Promise<void> {
  if (saving.value || selectedKeys.value.includes(key)) return
  await preferences.replace([...selectedKeys.value, key])
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
    const requested = parseAnalysisPeriodSelection(queryPeriod)
    const start = typeof queryStartValue === 'string' ? queryStartValue : null
    const end = typeof queryEndValue === 'string' ? queryEndValue : null
    const normalized = normalizeAnalysisPeriodForCapability(
      requested, requested === 'custom' && start && end ? { start, end } : null,
      maxDays.value, today,
    )
    const nextPeriod = normalized.period
    const nextCustom = nextPeriod === 'custom' ? normalized.range : null
    const changed = period.value !== nextPeriod || customRange.value?.start !== nextCustom?.start
      || customRange.value?.end !== nextCustom?.end
    period.value = nextPeriod
    customRange.value = nextCustom
    if (categoryKey === undefined || categoryKey === 'micronutrients') {
      if (changed || categoryChanged) micronutrientLoadId += 1
      if ((changed || categoryChanged) && !workspaceLoading.value && registry.value.length) {
        void syncPeriodQuery().then(() => loadMicronutrients())
      } else if (!workspaceLoading.value) void syncPeriodQuery()
      return
    }
    micronutrientLoadId += 1
    micronutrientLoading.value = false
    micronutrientError.value = ''
    micronutrients.value = null
    micronutrientTrends.value = {}
    micronutrientTrendErrors.value = {}
    if (!workspaceLoading.value) void syncPeriodQuery()
  },
)


onMounted(async () => {
  await loadWorkspace()
  await syncPeriodQuery()
  await scrollToCategoryHash()
})
onBeforeUnmount(() => { micronutrientLoadId += 1 })
</script>

<template>
  <div class="analysis-catalog-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period, range) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ pageTitle }}</span>
    </nav>

    <div class="page-heading analysis-heading">
      <div>
        <h1>{{ pageTitle }}</h1>
        <p v-if="!props.categoryKey">{{ t('analysisV2.catalogDescription') }}</p>
      </div>
      <AnalysisPeriodPicker
        v-if="!workspaceLoading && !workspaceError"
        :start="range.start"
        :end="range.end"
        :today="today"
        :period="period"
        :presets="presets"
        :custom-range="true"
        :max-days="maxDays"
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
      <label class="analysis-catalog-search">
        <span>{{ t('analysisV2.catalogSearchLabel') }}</span>
        <input v-model="search" type="search" :placeholder="t('analysisV2.catalogSearchPlaceholder')">
      </label>
      <p v-if="searchTerm && !groupedEntries.some((group) => group.entries.length)" class="card empty" role="status">
        {{ t('analysisV2.catalogSearchNoResults') }}
      </p>
      <section v-if="(!props.categoryKey || props.categoryKey === 'micronutrients') && micronutrientLoading" class="card" role="status" aria-live="polite">
        {{ t('analysisV2.loading') }}
      </section>
      <section v-else-if="(!props.categoryKey || props.categoryKey === 'micronutrients') && micronutrientError" class="card error" role="alert">
        <p>{{ micronutrientError }}</p>
        <button class="button secondary" type="button" @click="loadMicronutrients">{{ t('analysisV2.retry') }}</button>
      </section>

      <section
        v-for="group in groupedEntries"
        :id="`analysis-category-${group.key}`"
        :key="group.key"
        class="card analysis-catalog-category"
      >
        <h2 v-if="!props.categoryKey" class="analysis-catalog-category-heading">
          <RouterLink :to="{ path: categoryPath[group.key], query: analysisHubQuery(period, range), hash: group.key === 'fluid' ? '#analysis-category-fluid' : undefined }">{{ t(group.labelKey) }}</RouterLink>
        </h2>
        <p v-if="!group.entries.length && !searchTerm" class="card empty">{{ t('analysisV2.catalogEmpty') }}</p>
        <template v-else-if="group.key === 'micronutrients' && micronutrients && !searchTerm">
          <p class="analysis-nutrient-count">
            {{ number.format(presentNutrientCount) }} {{ i18n.global.locale.value === 'de' ? 'von' : 'of' }} {{ number.format(summaryNutrients.length) }}
          </p>
          <p v-if="!presentNutrientCount" class="analysis-nutrient-extra">{{ t('analysisV2.noDataPeriod') }}</p>
          <ul v-if="trendCharts.length" class="analysis-nutrient-trends">
            <li v-for="{ metric, loaded, values } in trendCharts" :key="metric.metric_key">
              <RouterLink :to="{ path: `/analyse/mikronaehrstoffe/${encodeURIComponent(metric.metric_key)}`, query: analysisHubQuery(period, range) }">{{ localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label }}</RouterLink>
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
              :selected-keys="selectedKeys"
              :saving="saving"
              :period="period"
              :period-range="range"
              :period-days="periodDays"
              :micronutrients="micronutrients"
              @add="addAnalysis"
            />
          </section>
        </template>
        <AnalysisCatalogEntryGrid
          v-else
          :entries="group.entries"
          :selected-keys="selectedKeys"
          :saving="saving"
          :period="period"
          :period-range="range"
          :period-days="periodDays"
          :micronutrients="micronutrients"
          @add="addAnalysis"
        />
      </section>
    </template>
  </div>
</template>

<style scoped>
.analysis-catalog-page { display: grid; gap: 1.15rem; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; min-width: 0; color: var(--muted); font-size: .86rem; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-heading { align-items: center; margin: 0; }
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
@media (max-width: 560px) {
  .analysis-heading { align-items: flex-start; flex-wrap: wrap; gap: .7rem; }
  .analysis-heading .period-control { max-width: 100%; }
  .analysis-catalog-category { gap: .65rem; }
}
</style>
