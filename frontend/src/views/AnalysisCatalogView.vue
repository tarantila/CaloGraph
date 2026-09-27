<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisCatalogEntryGrid from '../components/AnalysisCatalogEntryGrid.vue'
import { analysisCategories, analysisHubQuery, localizedAnalysisPresentation, analysisRangeForDays, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisCategoryKey, AnalysisMicronutrientResponse, AnalysisRegistryResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const props = defineProps<{ categoryKey?: AnalysisCategoryKey }>()
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const preferences = useAnalysisPreferences()
const selectedKeys = preferences.selectedKeys
const saving = preferences.saving
const saveError = preferences.saveError
const period = ref<AnalysisHubPeriod>(parseAnalysisHubPeriod(route.query.period))
const hubPeriods: readonly AnalysisHubPeriod[] = ['7', '30']
const registry = ref<AnalysisRegistryResponse['analyses']>([])
const workspaceLoading = ref(true)
const workspaceError = ref('')
const micronutrientLoading = ref(false)
const micronutrientError = ref('')
const micronutrients = ref<AnalysisMicronutrientResponse | null>(null)
let micronutrientLoadId = 0
const today = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
const range = computed(() => analysisRangeForDays(today, Number(period.value)))
const groupedEntries = computed(() => analysisCategories
  .filter((category) => !props.categoryKey || category.key === props.categoryKey)
  .map((category) => ({
    ...category,
    entries: registry.value.flatMap((entry) => {
      if (entry.category_key !== category.key) return []
      const presentation = localizedAnalysisPresentation(entry.analysis_key)
      return presentation ? [{ entry, presentation }] : []
    }),
  })))
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

async function syncPeriodQuery(): Promise<void> {
  if (route.query.period === period.value) return
  await router.replace({ query: { ...route.query, period: period.value }, hash: route.hash })
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
  const params = new URLSearchParams({ start, end })
  try {
    const response = await api<AnalysisMicronutrientResponse>(
      `/analytics/nutrition/micronutrients?${params.toString()}`,
    )
    if (loadId === micronutrientLoadId) micronutrients.value = response
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
    await loadMicronutrients()
  } catch (cause) {
    workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    workspaceLoading.value = false
  }
}

async function changePeriod(value: AnalysisHubPeriod): Promise<void> {
  period.value = value
  await syncPeriodQuery()
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
  () => [route.query.period, props.categoryKey] as const,
  ([queryPeriod, categoryKey]) => {
    period.value = parseAnalysisHubPeriod(queryPeriod)
    if (categoryKey === undefined || categoryKey === 'micronutrients') {
      if (!workspaceLoading.value && registry.value.length) void loadMicronutrients()
      return
    }
    micronutrientLoadId += 1
    micronutrientLoading.value = false
    micronutrientError.value = ''
    micronutrients.value = null
  },
)


onMounted(async () => {
  await syncPeriodQuery()
  await loadWorkspace()
  await scrollToCategoryHash()
})
</script>

<template>
  <div class="analysis-catalog-page">
    <nav class="analysis-breadcrumbs" :aria-label="t('analysisV2.breadcrumbs')">
      <RouterLink :to="{ path: '/analyse', query: analysisHubQuery(period) }">{{ t('analysisV2.breadcrumbHub') }}</RouterLink>
      <span aria-hidden="true">›</span>
      <span aria-current="page">{{ pageTitle }}</span>
    </nav>

    <div class="page-heading analysis-heading">
      <div>
        <h1>{{ pageTitle }}</h1>
        <p v-if="!props.categoryKey">{{ t('analysisV2.catalogDescription') }}</p>
      </div>
      <div class="period-control" role="group" :aria-label="t('analysisV2.period')">
        <button
          v-for="choice in hubPeriods"
          :key="choice"
          type="button"
          :class="{ active: period === choice }"
          :aria-pressed="period === choice"
          @click="changePeriod(choice)"
        >
          {{ t(`analysisV2.period${choice}`) }}
        </button>
      </div>
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
        class="analysis-catalog-category"
      >
        <h2 v-if="!props.categoryKey" class="analysis-catalog-category-heading">
          <RouterLink :to="{ path: categoryPath[group.key], query: analysisHubQuery(period), hash: group.key === 'fluid' ? '#analysis-category-fluid' : undefined }">{{ t(group.labelKey) }}</RouterLink>
        </h2>
        <p v-if="!group.entries.length" class="card empty">{{ t('analysisV2.catalogEmpty') }}</p>
        <template v-else-if="group.key === 'micronutrients' && micronutrients">
          <section v-for="type in micronutrientTypeGroups" :key="type.key" class="analysis-catalog-subcategory">
            <h3>{{ t(type.labelKey) }}</h3>
            <AnalysisCatalogEntryGrid
              :entries="type.entries"
              :selected-keys="selectedKeys"
              :saving="saving"
              :period="period"
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
.analysis-catalog-category { display: grid; gap: .8rem; min-width: 0; scroll-margin-top: 1rem; }
.analysis-catalog-category-heading { margin: 0; font-size: 1.1rem; }
.analysis-catalog-category-heading a { color: inherit; text-decoration: none; }
.analysis-catalog-category-heading a:hover { color: var(--accent-hover); }
.analysis-catalog-subcategory { display: grid; gap: .65rem; }
.analysis-catalog-subcategory h3 { margin: 0; color: var(--muted-strong); font-size: .92rem; }
@media (max-width: 560px) {
  .analysis-heading { align-items: flex-start; flex-wrap: wrap; gap: .7rem; }
  .analysis-heading .period-control { max-width: 100%; }
  .analysis-catalog-category { gap: .65rem; }
}
</style>
