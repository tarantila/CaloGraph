<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisCategories, analysisHref, analysisHubQuery, analysisIconSymbol, localizedAnalysisPresentation, analysisRangeForDays, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { AnalysisMicronutrientResponse, AnalysisRegistryResponse } from '../types'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const number = createNumberFormatter({ maximumFractionDigits: 1 })
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
const groupedEntries = computed(() => analysisCategories.map((category) => ({
  ...category,
  entries: registry.value.flatMap((entry) => {
    if (entry.category_key !== category.key) return []
    const presentation = localizedAnalysisPresentation(entry.analysis_key)
    return presentation ? [{ entry, presentation }] : []
  }),
})))

async function syncPeriodQuery(): Promise<void> {
  if (route.query.period === period.value) return
  await router.replace({ query: { ...route.query, period: period.value } })
}

async function loadMicronutrients(): Promise<void> {
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
  if (!workspaceLoading.value) await loadMicronutrients()
}

async function toggleAnalysis(key: string): Promise<void> {
  if (saving.value) return
  const nextKeys = selectedKeys.value.includes(key)
    ? selectedKeys.value.filter((item) => item !== key)
    : [...selectedKeys.value, key]
  await preferences.replace(nextKeys)
}

function isSelected(key: string): boolean {
  return selectedKeys.value.includes(key)
}

function micronutrientMetric(key: string) {
  return micronutrients.value?.metrics.find((metric) => metric.metric_key === key) ?? null
}

function metricTotal(key: string): string | null {
  const metric = micronutrientMetric(key)
  if (!metric || metric.days_with_data === 0 || metric.total === null) return null
  return `${number.format(metric.total)} ${metric.unit}`
}

async function scrollToCategoryHash(): Promise<void> {
  const id = route.hash.slice(1)
  if (!id.startsWith('analysis-category-')) return
  await nextTick()
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

watch(() => route.query.period, (value) => {
  const next = parseAnalysisHubPeriod(value)
  if (next !== period.value) void changePeriod(next)
})
watch(() => route.hash, () => { void scrollToCategoryHash() })


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
      <span aria-current="page">{{ t('analysisV2.breadcrumbCatalog') }}</span>
    </nav>
    <div class="page-heading">
      <div>
        <h1>{{ t('analysisV2.catalog') }}</h1>
        <p>{{ t('analysisV2.catalogDescription') }}</p>
      </div>
      <RouterLink class="button secondary" :to="{ path: '/analyse', query: analysisHubQuery(period) }">
        {{ t('analysisV2.returnHub') }}
      </RouterLink>
    </div>

    <section class="analysis-period card" :aria-label="t('analysisV2.period')">
      <span>{{ t('analysisV2.period') }}</span>
      <div role="group" :aria-label="t('analysisV2.period')">
        <button
          v-for="choice in hubPeriods"
          :key="choice"
          class="button secondary"
          type="button"
          :aria-pressed="period === choice"
          @click="changePeriod(choice)"
        >
          {{ t(`analysisV2.period${choice}`) }}
        </button>
      </div>
    </section>

    <div v-if="workspaceLoading" class="dashboard-loading" role="status" aria-live="polite">
      {{ t('analysisV2.loading') }}
    </div>
    <section v-else-if="workspaceError" class="card error" role="alert">
      <p>{{ workspaceError }}</p>
      <button class="button secondary" type="button" @click="loadWorkspace">{{ t('analysisV2.retry') }}</button>
    </section>
    <template v-else>
      <section v-if="saveError" class="card error" role="alert" aria-live="assertive">{{ saveError }}</section>
      <section v-if="micronutrientLoading" class="card" role="status" aria-live="polite">
        {{ t('analysisV2.loading') }}
      </section>
      <section v-else-if="micronutrientError" class="card error" role="alert">
        <p>{{ micronutrientError }}</p>
        <button class="button secondary" type="button" @click="loadMicronutrients">{{ t('analysisV2.retry') }}</button>
      </section>

      <section
        v-for="group in groupedEntries"
        :id="`analysis-category-${group.key}`"
        :key="group.key"
        class="analysis-catalog-category"
      >
        <h2>{{ t(group.labelKey) }}</h2>
        <p v-if="!group.entries.length" class="card empty">{{ t('analysisV2.catalogEmpty') }}</p>
        <div v-else class="analysis-catalog-grid">
          <article
            v-for="({ entry, presentation }) in group.entries"
            :key="entry.analysis_key"
            class="card analysis-catalog-entry"
            :data-analysis-key="entry.analysis_key"
            :aria-disabled="entry.status === 'coming_soon' ? 'true' : undefined"
          >
            <span class="analysis-catalog-icon" aria-hidden="true">{{ analysisIconSymbol(presentation.icon) }}</span>
            <div class="analysis-catalog-copy">
              <h3 class="analysis-catalog-title">
                <RouterLink
                  v-if="entry.status === 'available' && entry.detail_capable"
                  :to="{ path: analysisHref(entry.analysis_key), query: analysisHubQuery(period) }"
                >
                  {{ presentation.label }}
                </RouterLink>
                <span v-else>{{ presentation.label }}</span>
              </h3>
              <p>{{ presentation.description }}</p>
              <p v-if="entry.status === 'coming_soon'" class="analysis-catalog-status">
                {{ t('analysisV2.comingSoon') }}
              </p>
              <p
                v-else-if="entry.category_key === 'micronutrients' && micronutrients && (micronutrientMetric(entry.analysis_key)?.days_with_data ?? 0) === 0"
                class="analysis-catalog-status"
              >
                {{ t('analysisV2.noDataPeriod') }}
              </p>
              <p
                v-else-if="entry.category_key === 'micronutrients' && micronutrients && micronutrientMetric(entry.analysis_key)?.days_with_data"
                class="analysis-catalog-status"
              >
                {{ t('analysisV2.daysCoverage', { count: micronutrientMetric(entry.analysis_key)?.days_with_data, total: Number(period) }) }}
                <span v-if="metricTotal(entry.analysis_key)"> · {{ metricTotal(entry.analysis_key) }}</span>
              </p>
            </div>
            <button
              v-if="entry.status === 'available' && entry.card_capable"
              class="button secondary analysis-catalog-action"
              type="button"
              :data-analysis-add="entry.analysis_key"
              :aria-label="t(isSelected(entry.analysis_key) ? 'analysisV2.removeCard' : 'analysisV2.addCard', { label: presentation.label })"
              :disabled="saving"
              @click="toggleAnalysis(entry.analysis_key)"
            >
              {{ isSelected(entry.analysis_key) ? t('analysisV2.remove') : t('analysisV2.add') }}
            </button>
          </article>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.analysis-catalog-page { display: grid; gap: 1rem; }
.analysis-breadcrumbs { display: flex; flex-wrap: wrap; gap: .5rem; color: var(--muted); font-size: .9rem; }
.analysis-breadcrumbs a { color: inherit; }
.analysis-period { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: .75rem; }
.analysis-period [role="group"] { display: flex; flex-wrap: wrap; gap: .5rem; }
.analysis-period button[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-catalog-category { display: grid; gap: .7rem; scroll-margin-top: 1rem; }
.analysis-catalog-category h2 { margin: 0; font-size: 1.2rem; }
.analysis-catalog-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .8rem; }
.analysis-catalog-entry { display: grid; grid-template-columns: 2.5rem minmax(0, 1fr) auto; align-items: start; gap: .7rem; min-width: 0; }
.analysis-catalog-entry[aria-disabled="true"] { opacity: .72; }
.analysis-catalog-icon { display: grid; place-items: center; width: 2.5rem; height: 2.5rem; border-radius: .75rem; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
.analysis-catalog-copy { min-width: 0; }
.analysis-catalog-title { margin: 0; color: inherit; font-size: 1rem; font-weight: 700; overflow-wrap: anywhere; }
.analysis-catalog-title a:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
.analysis-catalog-copy p { margin: .25rem 0 0; color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }
.analysis-catalog-copy .analysis-catalog-status { color: var(--muted-strong); font-weight: 600; }
.analysis-catalog-action { grid-column: 2 / -1; justify-self: start; }
@media (max-width: 720px) { .analysis-catalog-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) {
  .analysis-catalog-entry { grid-template-columns: 2.5rem minmax(0, 1fr); }
  .analysis-catalog-action { grid-column: 2; }
}
</style>
