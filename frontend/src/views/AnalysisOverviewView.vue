<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import { analysisHref, analysisHubQuery, analysisIconSymbol, localizedAnalysisPresentation, analysisRangeForDays, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type {
  AnalysisMicronutrientResponse,
  AnalysisRegistryResponse,
  VerificationActivityResponse,
  WeightResponse,
} from '../types'

type ReaderState = 'idle' | 'loading' | 'ready' | 'error'
type PreviewReader = 'weight' | 'activity' | 'micronutrients'

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
const workspaceReady = ref(false)
const weightResponse = ref<WeightResponse | null>(null)
const activityResponse = ref<VerificationActivityResponse | null>(null)
const micronutrientResponse = ref<AnalysisMicronutrientResponse | null>(null)
const readerStates = reactive<Record<PreviewReader, ReaderState>>({
  weight: 'idle', activity: 'idle', micronutrients: 'idle',
})
const readerErrors = reactive<Record<PreviewReader, string>>({
  weight: '', activity: '', micronutrients: '',
})
const pointerDragKey = ref<string | null>(null)
const range = computed(() => analysisRangeForDays(
  isoDateInTimeZone(auth.user?.timezone ?? 'UTC'),
  Number(period.value),
))
const cardEntries = computed(() => selectedKeys.value.flatMap((key) => {
  const entry = registry.value.find((item) => item.analysis_key === key)
  const presentation = localizedAnalysisPresentation(key)
  return entry && entry.status === 'available' && entry.card_capable && presentation
    ? [{ entry, presentation }]
    : []
}))
const periodDays = computed(() => Number(period.value))
const readingsByActivity = computed(() => (activityResponse.value?.days ?? []).flatMap((day) => {
  const record = day.canonical
  if (record?.status !== 'available' || typeof record.active_energy_kcal !== 'number'
    || !Number.isFinite(record.active_energy_kcal)) return []
  return [{ date: day.date, value: record.active_energy_kcal }]
}).sort((left, right) => left.date.localeCompare(right.date)))

let previewLoadId = 0
let lastPreviewSignature = ''

async function syncPeriodQuery(): Promise<void> {
  if (route.query.period === period.value) return
  await router.replace({ query: { ...route.query, period: period.value } })
}

async function loadWorkspace(): Promise<void> {
  workspaceLoading.value = true
  lastPreviewSignature = ''
  workspaceError.value = ''
  try {
    const [registryResponse] = await Promise.all([
      api<AnalysisRegistryResponse>('/analytics/registry'),
      preferences.load(),
    ])
    registry.value = registryResponse.analyses
    workspaceReady.value = true
  } catch (cause) {
    workspaceReady.value = false
    workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    workspaceLoading.value = false
  }
  if (workspaceReady.value) await loadPreviews()
}

async function loadPreviews(): Promise<void> {
  if (!workspaceReady.value) return
  const keys = new Set(cardEntries.value.map(({ entry }) => entry.analysis_key))
  const start = range.value.start
  const end = range.value.end
  const params = new URLSearchParams({ start, end }).toString()
  const requestedReaders: PreviewReader[] = []
  if (keys.has('weight_kg')) requestedReaders.push('weight')
  if (keys.has('active_energy_kcal')) requestedReaders.push('activity')
  if (cardEntries.value.some(({ entry }) => entry.category_key === 'micronutrients')) {
    requestedReaders.push('micronutrients')
  }
  const signature = `${start}|${end}|${requestedReaders.join(',')}`
  if (signature === lastPreviewSignature) return
  lastPreviewSignature = signature
  const loadId = ++previewLoadId
  weightResponse.value = null
  activityResponse.value = null
  micronutrientResponse.value = null
  for (const reader of ['weight', 'activity', 'micronutrients'] as const) {
    readerStates[reader] = requestedReaders.includes(reader) ? 'loading' : 'idle'
    readerErrors[reader] = ''
  }

  const requests = requestedReaders.map(async (reader) => {
    try {
      if (reader === 'weight') {
        const response = await api<WeightResponse>(`/analytics/weight?${params}`)
        if (loadId === previewLoadId) weightResponse.value = response
      } else if (reader === 'activity') {
        const response = await api<VerificationActivityResponse>(
          `/analytics/verification/activity?${params}&view=canonical`,
        )
        if (loadId === previewLoadId) activityResponse.value = response
      } else {
        const response = await api<AnalysisMicronutrientResponse>(
          `/analytics/nutrition/micronutrients?${params}`,
        )
        if (loadId === previewLoadId) micronutrientResponse.value = response
      }
      if (loadId === previewLoadId) readerStates[reader] = 'ready'
    } catch (cause) {
      if (loadId === previewLoadId) {
        readerErrors[reader] = localizeApiError(cause, 'analysisV2.loadFailed')
        readerStates[reader] = 'error'
        lastPreviewSignature = ''
      }
    }
  })
  await Promise.all(requests)
}

function moveKey(key: string, offset: -1 | 1): void {
  const currentIndex = selectedKeys.value.indexOf(key)
  const destination = currentIndex + offset
  if (currentIndex < 0 || destination < 0 || destination >= selectedKeys.value.length || saving.value) return
  const next = [...selectedKeys.value]
  next.splice(currentIndex, 1)
  next.splice(destination, 0, key)
  void preferences.replace(next)
}

function removeKey(key: string): void {
  if (saving.value) return
  void preferences.replace(selectedKeys.value.filter((item) => item !== key))
}

function pointerDown(key: string, event: PointerEvent): void {
  if (!event.isPrimary || saving.value) return
  pointerDragKey.value = key
}

function finishPointerDrag(event: PointerEvent): void {
  const key = pointerDragKey.value
  pointerDragKey.value = null
  if (!key || saving.value || typeof document.elementFromPoint !== 'function') return
  const target = document.elementFromPoint(event.clientX, event.clientY)
    ?.closest<HTMLElement>('[data-analysis-index]')
  if (!target) return
  const destination = Number(target.dataset.analysisIndex)
  const source = selectedKeys.value.indexOf(key)
  if (Number.isInteger(destination) && source >= 0 && destination !== source) {
    const next = [...selectedKeys.value]
    next.splice(source, 1)
    next.splice(destination, 0, key)
    void preferences.replace(next)
  }
}

function cancelPointerDrag(): void {
  pointerDragKey.value = null
}


function entryRoute(key: string) {
  return { path: analysisHref(key), query: analysisHubQuery(period.value) }
}

function cardReader(key: string): PreviewReader {
  if (key === 'weight_kg') return 'weight'
  if (key === 'active_energy_kcal') return 'activity'
  return 'micronutrients'
}

function cardMetric(key: string) {
  return micronutrientResponse.value?.metrics.find((metric) => metric.metric_key === key) ?? null
}

function cardCoverage(key: string): string {
  if (key === 'weight_kg') {
    const count = weightResponse.value?.summary.measurement_count ?? 0
    return t('analysisV2.measurementCount', { count: number.format(count) })
  }
  if (key === 'active_energy_kcal') {
    return t('analysisV2.daysCoverage', {
      count: number.format(readingsByActivity.value.length),
      total: number.format(periodDays.value),
    })
  }
  const metric = cardMetric(key)
  return metric
    ? t('analysisV2.daysCoverage', { count: number.format(metric.days_with_data), total: number.format(periodDays.value) })
    : t('analysisV2.noReadings')
}

function cardValue(key: string): string | null {
  if (key === 'weight_kg') {
    const latest = weightResponse.value?.summary.latest
    return latest ? `${number.format(latest.weight_kg)} kg` : null
  }
  if (key === 'active_energy_kcal') {
    const latest = readingsByActivity.value.at(-1)
    return latest ? `${number.format(latest.value)} kcal` : null
  }
  const metric = cardMetric(key)
  if (!metric || metric.days_with_data === 0 || metric.total === null) return null
  return `${number.format(metric.total)} ${metric.unit}`
}

function cardHasReadings(key: string): boolean {
  if (key === 'weight_kg') return (weightResponse.value?.summary.measurement_count ?? 0) > 0
  if (key === 'active_energy_kcal') return readingsByActivity.value.length > 0
  return (cardMetric(key)?.days_with_data ?? 0) > 0
}

watch(
  () => route.query.period,
  (value) => {
    const nextPeriod = parseAnalysisHubPeriod(value)
    if (nextPeriod !== period.value) period.value = nextPeriod
  },
)

watch(
  [period, () => selectedKeys.value.join('|')],
  async () => {
    await syncPeriodQuery()
    await loadPreviews()
  },
)

onMounted(async () => {
  window.addEventListener('pointerup', finishPointerDrag)
  window.addEventListener('pointercancel', cancelPointerDrag)
  await syncPeriodQuery()
  await loadWorkspace()
  await loadPreviews()
})

onBeforeUnmount(() => {
  window.removeEventListener('pointerup', finishPointerDrag)
  window.removeEventListener('pointercancel', cancelPointerDrag)
})
</script>

<template>
  <div class="analysis-hub">
    <div class="page-heading">
      <div>
        <h1>{{ t('analysisV2.title') }}</h1>
        <p>{{ t('analysisV2.description') }}</p>
      </div>
      <RouterLink class="button secondary" :to="{ path: '/analyse/katalog', query: analysisHubQuery(period) }">
        {{ t('analysisV2.openCatalog') }}
      </RouterLink>
    </div>

    <nav class="card analysis-legacy-links" aria-labelledby="analysis-existing-views-title">
      <h2 id="analysis-existing-views-title">{{ t('analysisV2.existingViews') }}</h2>
      <ul>
        <li><RouterLink to="/ernaehrung">{{ t('analysisV2.legacyNutrition') }}</RouterLink></li>
        <li><RouterLink to="/aktivitaet">{{ t('analysisV2.legacyActivity') }}</RouterLink></li>
        <li><RouterLink to="/gewicht">{{ t('analysisV2.legacyWeight') }}</RouterLink></li>
        <li><RouterLink to="/trends">{{ t('analysisV2.legacyTrends') }}</RouterLink></li>
        <li><RouterLink to="/mikronaehrstoffe">{{ t('analysisV2.legacyMicronutrients') }}</RouterLink></li>
      </ul>
    </nav>

    <section class="analysis-period card" :aria-label="t('analysisV2.period')">
      <span>{{ t('analysisV2.period') }}</span>
      <div role="group" :aria-label="t('analysisV2.period')">
        <button
          v-for="choice in hubPeriods"
          :key="choice"
          class="button secondary"
          type="button"
          :aria-pressed="period === choice"
          @click="period = choice"
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
      <section v-if="saveError" class="card error analysis-save-error" role="alert" aria-live="assertive">
        {{ saveError }}
      </section>

      <section v-if="!cardEntries.length" class="card empty analysis-empty-state" role="status">
        <h2>{{ t('analysisV2.emptySelection') }}</h2>
        <p>{{ t('analysisV2.emptySelectionHelp') }}</p>
        <RouterLink class="button primary" :to="{ path: '/analyse/katalog', query: analysisHubQuery(period) }">
          {{ t('analysisV2.openCatalog') }}
        </RouterLink>
      </section>

      <ol v-else class="analysis-card-list" :aria-label="t('analysisV2.title')">
        <li
          v-for="({ entry, presentation }, index) in cardEntries"
          :key="entry.analysis_key"
          class="card analysis-card"
          :data-analysis-key="entry.analysis_key"
          :data-analysis-index="index"
        >
          <div class="analysis-card-tools">
            <button
              class="analysis-drag-handle"
              type="button"
              :aria-label="t('analysisV2.dragHandle', { label: presentation.label })"
              :disabled="saving"
              @pointerdown="pointerDown(entry.analysis_key, $event)"
            >
              ⠿
            </button>
            <button
              class="analysis-move-control"
              type="button"
              :aria-label="t('analysisV2.moveForward', { label: presentation.label })"
              :disabled="saving || index === 0"
              @click="moveKey(entry.analysis_key, -1)"
            >
              ↑
            </button>
            <button
              class="analysis-move-control"
              type="button"
              :aria-label="t('analysisV2.moveBackward', { label: presentation.label })"
              :disabled="saving || index === cardEntries.length - 1"
              @click="moveKey(entry.analysis_key, 1)"
            >
              ↓
            </button>
            <details class="analysis-overflow">
              <summary :aria-label="t('analysisV2.cardActions', { label: presentation.label })">…</summary>
              <button
                type="button"
                :aria-label="t('analysisV2.removeCard', { label: presentation.label })"
                :disabled="saving"
                @click="removeKey(entry.analysis_key)"
              >
                {{ t('analysisV2.remove') }}
              </button>
            </details>
          </div>

          <RouterLink class="analysis-card-content" :to="entryRoute(entry.analysis_key)">
            <span class="analysis-card-icon" aria-hidden="true">
              {{ analysisIconSymbol(presentation.icon) }}
            </span>
            <span class="analysis-card-copy">
              <h2 class="analysis-card-heading">{{ presentation.label }}</h2>
              <span class="analysis-card-description">{{ presentation.description }}</span>
              <span v-if="readerStates[cardReader(entry.analysis_key)] === 'loading'" class="analysis-card-state" role="status">
                {{ t('common.loading') }}
              </span>
              <span v-else-if="readerStates[cardReader(entry.analysis_key)] === 'error'" class="analysis-card-state analysis-card-error" role="alert">
                {{ readerErrors[cardReader(entry.analysis_key)] }}
              </span>
              <span v-else-if="readerStates[cardReader(entry.analysis_key)] === 'ready' && !cardHasReadings(entry.analysis_key)" class="analysis-card-state">
                {{ entry.category_key === 'micronutrients' ? t('analysisV2.noDataPeriod') : t('analysisV2.noReadings') }}
              </span>
              <span v-else-if="readerStates[cardReader(entry.analysis_key)] === 'ready'" class="analysis-card-state">
                <strong>{{ cardValue(entry.analysis_key) ?? '–' }}</strong> · {{ cardCoverage(entry.analysis_key) }}
                <time v-if="entry.analysis_key === 'weight_kg' && weightResponse?.summary.latest" :datetime="weightResponse.summary.latest.date">
                  {{ formatGermanDate(weightResponse.summary.latest.date) }}
                </time>
              </span>
            </span>
          </RouterLink>
        </li>
      </ol>
    </template>
  </div>
</template>

<style scoped>
.analysis-hub { display: grid; gap: 1rem; }
.analysis-period { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: .75rem; }
.analysis-legacy-links { display: grid; gap: .5rem; }
.analysis-legacy-links h2 { font-size: 1rem; margin: 0; }
.analysis-legacy-links ul { display: flex; flex-wrap: wrap; gap: .5rem 1rem; list-style: none; margin: 0; padding: 0; }
.analysis-legacy-links a { min-height: 2.75rem; display: inline-flex; align-items: center; }
.analysis-period [role="group"] { display: flex; flex-wrap: wrap; gap: .5rem; }
.analysis-period button[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-empty-state { display: grid; justify-items: start; gap: .45rem; }
.analysis-empty-state h2, .analysis-empty-state p { margin: 0; }
.analysis-card-list { display: grid; gap: .8rem; list-style: none; margin: 0; padding: 0; }
.analysis-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: .8rem; min-width: 0; }
.analysis-card-tools { display: flex; flex-direction: column; align-items: center; gap: .3rem; }
.analysis-card-tools button, .analysis-overflow summary { min-width: 2.4rem; min-height: 2.4rem; }
.analysis-drag-handle { cursor: grab; touch-action: none; user-select: none; }
.analysis-drag-handle:active { cursor: grabbing; }
.analysis-overflow { position: relative; }
.analysis-overflow summary { display: grid; place-items: center; cursor: pointer; list-style: none; border: 1px solid var(--border); border-radius: .5rem; }
.analysis-overflow summary::-webkit-details-marker { display: none; }
.analysis-overflow > button { position: absolute; z-index: 2; top: 100%; right: 0; white-space: nowrap; }
.analysis-card-content { display: grid; grid-template-columns: 2.8rem minmax(0, 1fr); align-items: start; gap: .75rem; color: inherit; text-decoration: none; min-width: 0; }
.analysis-card-content:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; border-radius: .5rem; }
.analysis-card-icon { display: grid; place-items: center; width: 2.8rem; height: 2.8rem; border-radius: .8rem; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); font-size: 1.35rem; }
.analysis-card-copy { display: grid; gap: .25rem; min-width: 0; }
.analysis-card-heading { margin: 0; font-weight: 700; font-size: 1.05rem; }
.analysis-card-description, .analysis-card-state { color: var(--muted); overflow-wrap: anywhere; }
.analysis-card-state strong { color: var(--text); }
.analysis-card-state time { display: block; margin-top: .15rem; font-size: .85rem; }
.analysis-card-error { color: var(--danger, #a12622); }
.analysis-save-error { margin: 0; }
@media (max-width: 520px) {
  .analysis-card { grid-template-columns: minmax(0, 1fr); }
  .analysis-card-tools { flex-direction: row; justify-content: flex-end; }
}
</style>
