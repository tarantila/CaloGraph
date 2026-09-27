<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisCatalogEntryGrid from '../components/AnalysisCatalogEntryGrid.vue'
import AnalysisMiniChart from '../components/AnalysisMiniChart.vue'
import { analysisCategories, analysisHref, analysisHubQuery, analysisIconSymbol, localizedAnalysisPresentation, analysisRangeForDays, parseAnalysisHubPeriod, type AnalysisHubPeriod } from '../analysis-v2'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { formatGermanDate, isoDateInTimeZone } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type {
  AnalysisCategoryKey,
  AnalysisMicronutrientResponse,
  AnalysisRegistryResponse,
  VerificationActivityResponse,
  WeightResponse,
} from '../types'

type CatalogEntry = {
  entry: AnalysisRegistryResponse['analyses'][number]
  presentation: NonNullable<ReturnType<typeof localizedAnalysisPresentation>>
}
type ReaderState = 'idle' | 'loading' | 'ready' | 'error'
type PreviewReader = 'weight' | 'activity'

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
const micronutrientResponses = reactive<Record<string, AnalysisMicronutrientResponse | null>>({})
const readerStates = reactive<Record<PreviewReader, ReaderState>>({
  weight: 'idle', activity: 'idle',
})
const readerErrors = reactive<Record<PreviewReader, string>>({
  weight: '', activity: '',
})
const micronutrientStates = reactive<Record<string, ReaderState>>({})
const micronutrientErrors = reactive<Record<string, string>>({})
const pointerDragKey = ref<string | null>(null)
const pointerDragId = ref<number | null>(null)
const previewOrder = ref<string[] | null>(null)
const insertionTarget = ref<{ key: string; side: 'before' | 'after' } | null>(null)
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
const displayedCardEntries = computed(() => {
  if (!previewOrder.value) return cardEntries.value
  const positions = new Map(previewOrder.value.map((key, index) => [key, index]))
  return [...cardEntries.value].sort((left, right) =>
    (positions.get(left.entry.analysis_key) ?? Number.MAX_SAFE_INTEGER)
      - (positions.get(right.entry.analysis_key) ?? Number.MAX_SAFE_INTEGER))
})
const periodDays = computed(() => Number(period.value))
const orderedActivityDays = computed(() => [...(activityResponse.value?.days ?? [])]
  .sort((left, right) => left.date.localeCompare(right.date)))
const readingsByActivity = computed(() => orderedActivityDays.value.flatMap((day) => {
  const record = day.canonical
  if (record?.status !== 'available' || typeof record.active_energy_kcal !== 'number'
    || !Number.isFinite(record.active_energy_kcal)) return []
  return [{ date: day.date, value: record.active_energy_kcal }]
}))
const activityChartValues = computed(() => orderedActivityDays.value.map(({ canonical }) =>
  canonical?.status === 'available'
    && typeof canonical.active_energy_kcal === 'number'
    && Number.isFinite(canonical.active_energy_kcal)
    ? canonical.active_energy_kcal
    : null))
const activityChartDates = computed(() => orderedActivityDays.value.map(({ date }) => date))
const weightPoints = computed(() => [...(weightResponse.value?.points ?? [])]
  .filter((point) => Number.isFinite(point.weight_kg))
  .sort((left, right) => left.date.localeCompare(right.date)))
const activityAverage = computed(() => {
  const readings = readingsByActivity.value
  return readings.length
    ? readings.reduce((sum, reading) => sum + reading.value, 0) / readings.length
    : null
})
const categorySections = computed(() => analysisCategories.map((category) => ({
  ...category,
  entries: registry.value.flatMap((entry) => {
    if (entry.category_key !== category.key) return []
    const presentation = localizedAnalysisPresentation(entry.analysis_key)
    return presentation ? [{ entry, presentation }] : []
  }),
})))

function categoryExamples(categoryKey: AnalysisCategoryKey, entries: readonly CatalogEntry[]): CatalogEntry[] {
  if (categoryKey !== 'micronutrients') return entries.slice(0, 3)
  const chosen = new Set<string>()
  const vitamin = entries.find(({ entry, presentation }) =>
    entry.analysis_key.startsWith('vitamin_') || presentation.label.startsWith('Vitamin'))
  const calcium = entries.find(({ entry }) => entry.analysis_key === 'calcium_mg')
  if (vitamin) chosen.add(vitamin.entry.analysis_key)
  if (calcium) chosen.add(calcium.entry.analysis_key)
  for (const { entry, presentation } of entries) {
    if (chosen.size >= 3) break
    if (entry.analysis_key !== 'calcium_mg' && !presentation.label.startsWith('Vitamin')) {
      chosen.add(entry.analysis_key)
    }
  }
  if (chosen.size < 3) {
    for (const { entry } of entries) {
      if (chosen.size >= 3) break
      chosen.add(entry.analysis_key)
    }
  }
  return entries.filter(({ entry }) => chosen.has(entry.analysis_key))
}

let previewLoadId = 0
let lastPreviewSignature = ''

async function syncPeriodQuery(): Promise<void> {
  if (route.query.period === period.value) return
  await router.replace({ query: { ...route.query, period: period.value }, hash: route.hash })
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
  const micronutrientKeys = cardEntries.value
    .filter(({ entry }) => entry.category_key === 'micronutrients')
    .map(({ entry }) => entry.analysis_key)
  const signature = `${start}|${end}|${requestedReaders.join(',')}|${[...micronutrientKeys].sort().join(',')}`
  if (signature === lastPreviewSignature) return
  lastPreviewSignature = signature
  const loadId = ++previewLoadId
  weightResponse.value = null
  activityResponse.value = null
  for (const reader of ['weight', 'activity'] as const) {
    readerStates[reader] = requestedReaders.includes(reader) ? 'loading' : 'idle'
    readerErrors[reader] = ''
  }
  const requestedMicronutrients = new Set(micronutrientKeys)
  for (const key of Object.keys(micronutrientResponses)) {
    if (requestedMicronutrients.has(key)) continue
    delete micronutrientResponses[key]
    delete micronutrientStates[key]
    delete micronutrientErrors[key]
  }
  for (const key of micronutrientKeys) {
    micronutrientResponses[key] = null
    micronutrientStates[key] = 'loading'
    micronutrientErrors[key] = ''
  }

  const requests = requestedReaders.map(async (reader) => {
    try {
      if (reader === 'weight') {
        const response = await api<WeightResponse>(`/analytics/weight?${params}`)
        if (loadId === previewLoadId) weightResponse.value = response
      } else {
        const response = await api<VerificationActivityResponse>(
          `/analytics/verification/activity?${params}&view=canonical`,
        )
        if (loadId === previewLoadId) activityResponse.value = response
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
  for (const key of micronutrientKeys) {
    requests.push((async () => {
      try {
        const metricParams = new URLSearchParams({ start, end, metric_key: key }).toString()
        const response = await api<AnalysisMicronutrientResponse>(
          `/analytics/nutrition/micronutrients?${metricParams}`,
        )
        if (loadId === previewLoadId) micronutrientResponses[key] = response
        if (loadId === previewLoadId) micronutrientStates[key] = 'ready'
      } catch (cause) {
        if (loadId === previewLoadId) {
          micronutrientErrors[key] = localizeApiError(cause, 'analysisV2.loadFailed')
          micronutrientStates[key] = 'error'
          lastPreviewSignature = ''
        }
      }
    })())
  }
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

function addKey(key: string): void {
  if (saving.value || selectedKeys.value.includes(key)) return
  void preferences.replace([...selectedKeys.value, key])
}

function pointerDown(key: string, event: PointerEvent): void {
  if (!event.isPrimary || saving.value) return
  pointerDragKey.value = key
  pointerDragId.value = event.pointerId
  previewOrder.value = [...selectedKeys.value]
  insertionTarget.value = null
}

function updatePointerPreview(event: PointerEvent): void {
  if (pointerDragKey.value === null || pointerDragId.value !== event.pointerId
    || typeof document.elementFromPoint !== 'function') return
  const target = document.elementFromPoint(event.clientX, event.clientY)
    ?.closest<HTMLElement>('.analysis-card[data-analysis-key]')
  const key = target?.dataset.analysisKey
  if (!previewOrder.value) return
  if (!target || !key) {
    insertionTarget.value = null
    return
  }
  if (key === pointerDragKey.value) return
  const rect = target.getBoundingClientRect()
  const mobile = window.matchMedia?.('(max-width: 600px)').matches ?? window.innerWidth <= 600
  const after = mobile
    ? event.clientY >= rect.top + rect.height / 2
    : event.clientX >= rect.left + rect.width / 2
  const side = after ? 'after' : 'before'
  const next = [...previewOrder.value]
  const source = next.indexOf(pointerDragKey.value)
  if (source < 0) return
  next.splice(source, 1)
  const destination = next.indexOf(key)
  if (destination < 0) return
  next.splice(destination + (side === 'after' ? 1 : 0), 0, pointerDragKey.value)
  previewOrder.value = next
  insertionTarget.value = { key, side }
}

function finishPointerDrag(event: PointerEvent): void {
  if (pointerDragKey.value === null || pointerDragId.value !== event.pointerId) return
  updatePointerPreview(event)
  const next = previewOrder.value
  const shouldSave = insertionTarget.value !== null
    && next !== null
    && next.some((key, index) => key !== selectedKeys.value[index])
    && !saving.value
  pointerDragKey.value = null
  pointerDragId.value = null
  previewOrder.value = null
  insertionTarget.value = null
  if (shouldSave && next) void preferences.replace(next)
}

function cancelPointerDrag(event: PointerEvent): void {
  if (pointerDragId.value !== null && pointerDragId.value !== event.pointerId) return
  pointerDragKey.value = null
  pointerDragId.value = null
  previewOrder.value = null
  insertionTarget.value = null
}

function entryRoute(key: string) {
  return { path: analysisHref(key), query: analysisHubQuery(period.value) }
}

const categoryPaths: Record<AnalysisCategoryKey, string> = {
  body: '/analyse/koerper',
  activity: '/analyse/aktivitaet',
  nutrition: '/analyse/ernaehrung',
  micronutrients: '/analyse/mikronaehrstoffe',
  fluid: '/analyse',
}

function categoryRoute(key: AnalysisCategoryKey) {
  return {
    path: categoryPaths[key],
    hash: key === 'fluid' ? '#analysis-category-fluid' : undefined,
    query: analysisHubQuery(period.value),
  }
}

function cardState(key: string): ReaderState {
  if (key === 'weight_kg') return readerStates.weight
  if (key === 'active_energy_kcal') return readerStates.activity
  return micronutrientStates[key] ?? 'idle'
}

function cardError(key: string): string {
  if (key === 'weight_kg') return readerErrors.weight
  if (key === 'active_energy_kcal') return readerErrors.activity
  return micronutrientErrors[key] ?? ''
}

function cardMetric(key: string) {
  return micronutrientResponses[key]?.metrics.find((metric) => metric.metric_key === key) ?? null
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
    return activityAverage.value === null ? null : `${number.format(activityAverage.value)} kcal`
  }
  const metric = cardMetric(key)
  if (!metric || metric.days_with_data === 0 || metric.total === null) return null
  return `${number.format(metric.total / metric.days_with_data)} ${metric.unit}`
}

function weightChange(): string | null {
  const change = weightResponse.value?.summary.change_kg
  if (change === undefined || change === null) return null
  return `${change > 0 ? '+' : ''}${number.format(change)} kg`
}

function cardHasReadings(key: string): boolean {
  if (key === 'weight_kg') return (weightResponse.value?.summary.measurement_count ?? 0) > 0
  if (key === 'active_energy_kcal') return readingsByActivity.value.length > 0
  return (cardMetric(key)?.days_with_data ?? 0) > 0
}

function cardChartValues(key: string): Array<number | null> {
  if (key === 'weight_kg') return weightPoints.value.map((point) => point.weight_kg)
  if (key === 'active_energy_kcal') return activityChartValues.value
  return (micronutrientResponses[key]?.series ?? []).map((point) => point.value)
}

function cardChartDates(key: string): Array<string | null> {
  if (key === 'weight_kg') return weightPoints.value.map((point) => point.date)
  if (key === 'active_energy_kcal') return activityChartDates.value
  return (micronutrientResponses[key]?.series ?? []).map((point) => point.date)
}

function cardHasChartData(key: string): boolean {
  return cardChartValues(key).some((value) => typeof value === 'number' && Number.isFinite(value))
}

function chartLabel(key: string, label: string): string {
  if (key === 'weight_kg') return `${label} · ${t('analysisV2.weightHistory')}`
  if (key === 'active_energy_kcal') return `${label} · ${t('analysisV2.activeEnergyChart')}`
  return `${label} · ${t('analysisV2.micronutrientDaily')}`
}

function insertionClass(key: string): string | null {
  if (pointerDragKey.value === key) return 'is-dragging'
  if (insertionTarget.value?.key === key) return `is-drop-${insertionTarget.value.side}`
  return null
}
async function scrollToCategoryHash(): Promise<void> {
  const id = route.hash.slice(1)
  if (!id.startsWith('analysis-category-')) return
  await nextTick()
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

watch(
  () => route.query.period,
  (value) => {
    const nextPeriod = parseAnalysisHubPeriod(value)
    if (nextPeriod !== period.value) period.value = nextPeriod
  },
)
watch(() => route.hash, () => { void scrollToCategoryHash() })

watch(
  [period, () => selectedKeys.value.join('|')],
  async () => {
    await syncPeriodQuery()
    await loadPreviews()
  },
)

onMounted(async () => {
  window.addEventListener('pointermove', updatePointerPreview)
  window.addEventListener('pointerup', finishPointerDrag)
  window.addEventListener('pointercancel', cancelPointerDrag)
  await syncPeriodQuery()
  await loadWorkspace()
  await scrollToCategoryHash()
})

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', updatePointerPreview)
  window.removeEventListener('pointerup', finishPointerDrag)
  window.removeEventListener('pointercancel', cancelPointerDrag)
})
</script>

<template>
  <div class="analysis-hub">
    <section class="card analysis-header">
      <div class="analysis-heading-copy">
        <h1>{{ t('analysisV2.title') }}</h1>
        <p>{{ t('analysisV2.description') }}</p>
      </div>
      <div class="analysis-period" :aria-label="t('analysisV2.period')">
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

      <section class="analysis-section" aria-labelledby="analysis-selected-title">
        <h2 id="analysis-selected-title">{{ t('analysisV2.myAnalyses') }}</h2>
        <div v-if="!displayedCardEntries.length" class="card empty analysis-empty-state" role="status">
          <h3>{{ t('analysisV2.emptySelection') }}</h3>
          <p>{{ t('analysisV2.emptySelectionHelp') }}</p>
        </div>

        <ol v-else class="analysis-card-list" :aria-label="t('analysisV2.myAnalyses')">
          <li
            v-for="({ entry, presentation }) in displayedCardEntries"
            :key="entry.analysis_key"
            class="card analysis-card"
            :class="insertionClass(entry.analysis_key)"
            :data-analysis-key="entry.analysis_key"
            :data-analysis-card="entry.analysis_key"
          >
            <RouterLink class="analysis-card-content" :to="entryRoute(entry.analysis_key)">
              <span class="analysis-card-icon" aria-hidden="true">
                {{ analysisIconSymbol(presentation.icon) }}
              </span>
              <span class="analysis-card-copy">
                <h3 class="analysis-card-heading">{{ presentation.label }}</h3>
                <span class="analysis-card-description">{{ presentation.description }}</span>
                <span v-if="cardState(entry.analysis_key) === 'loading'" class="analysis-card-state" role="status">
                  {{ t('common.loading') }}
                </span>
                <span v-else-if="cardState(entry.analysis_key) === 'error'" class="analysis-card-state analysis-card-error" role="alert">
                  {{ cardError(entry.analysis_key) }}
                </span>
                <span v-else-if="cardState(entry.analysis_key) === 'ready' && !cardHasReadings(entry.analysis_key)" class="analysis-card-state">
                  {{ entry.category_key === 'micronutrients' ? t('analysisV2.noDataPeriod') : t('analysisV2.noReadings') }}
                </span>
                <span v-else-if="cardState(entry.analysis_key) === 'ready'" class="analysis-card-state">
                  <span class="analysis-card-kpi">
                    <span class="analysis-card-kpi-label">
                      {{ entry.analysis_key === 'weight_kg' ? t('analysisV2.latestMeasurement') : t('analysisV2.averagePerDay') }}
                    </span>
                    <strong class="analysis-metric-value">{{ cardValue(entry.analysis_key) ?? '–' }}</strong>
                  </span>
                  <span v-if="entry.analysis_key === 'weight_kg'" class="analysis-card-extra">
                    {{ t('analysisV2.change') }}:
                    {{ weightChange() ?? t('analysisV2.changeUnavailable') }}
                  </span>
                  <span class="analysis-card-extra">{{ cardCoverage(entry.analysis_key) }}</span>
                  <time
                    v-if="entry.analysis_key === 'weight_kg' && weightResponse?.summary.latest"
                    :datetime="weightResponse.summary.latest.date"
                  >
                    {{ formatGermanDate(weightResponse.summary.latest.date) }}
                  </time>
                  <AnalysisMiniChart
                    v-if="cardHasChartData(entry.analysis_key)"
                    class="analysis-card-chart"
                    :values="cardChartValues(entry.analysis_key)"
                    :dates="cardChartDates(entry.analysis_key)"
                    :date-range="range"
                    :label="chartLabel(entry.analysis_key, presentation.label)"
                    :variant="entry.analysis_key === 'active_energy_kcal' ? 'bars' : 'line'"
                  />
                </span>
              </span>
            </RouterLink>

            <div class="analysis-card-tools">
              <button
                class="analysis-drag-handle"
                type="button"
                :aria-label="t('analysisV2.dragHandle', { label: presentation.label })"
                :disabled="saving"
                @pointerdown="pointerDown(entry.analysis_key, $event)"
              >
                <span aria-hidden="true">⋮⋮</span>
              </button>
              <details class="analysis-overflow">
                <summary :aria-label="t('analysisV2.cardActions', { label: presentation.label })">
                  <span aria-hidden="true">⋯</span>
                </summary>
                <div class="analysis-overflow-menu">
                  <button
                    type="button"
                    :aria-label="t('analysisV2.moveForward', { label: presentation.label })"
                    :disabled="saving || selectedKeys.indexOf(entry.analysis_key) === 0"
                    @click="moveKey(entry.analysis_key, -1)"
                  >
                    {{ t('analysisV2.moveForward', { label: presentation.label }) }}
                  </button>
                  <button
                    type="button"
                    :aria-label="t('analysisV2.moveBackward', { label: presentation.label })"
                    :disabled="saving || selectedKeys.indexOf(entry.analysis_key) === selectedKeys.length - 1"
                    @click="moveKey(entry.analysis_key, 1)"
                  >
                    {{ t('analysisV2.moveBackward', { label: presentation.label }) }}
                  </button>
                  <button
                    type="button"
                    :aria-label="t('analysisV2.removeCard', { label: presentation.label })"
                    :disabled="saving"
                    @click="removeKey(entry.analysis_key)"
                  >
                    {{ t('analysisV2.remove') }}
                  </button>
                </div>
              </details>
            </div>
          </li>
        </ol>
      </section>

      <section class="analysis-section" aria-labelledby="analysis-catalog-title">
        <h2 id="analysis-catalog-title">{{ t('analysisV2.allAnalyses') }}</h2>
        <div class="analysis-category-grid">
          <section
            v-for="category in categorySections"
            :id="`analysis-category-${category.key}`"
            :key="category.key"
            class="card analysis-category"
          >
            <h3 class="analysis-category-title">
              <RouterLink :to="categoryRoute(category.key)">
                {{ t(category.labelKey) }} <span aria-hidden="true">→</span>
              </RouterLink>
            </h3>
            <AnalysisCatalogEntryGrid
              :entries="categoryExamples(category.key, category.entries)"
              :selected-keys="selectedKeys"
              :saving="saving"
              :period="period"
              @add="addKey"
            />
          </section>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.analysis-hub { display: grid; gap: 1rem; }
.analysis-header { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 1rem; }
.analysis-heading-copy h1, .analysis-heading-copy p { margin: 0; }
.analysis-heading-copy p { margin-top: .35rem; color: var(--muted); }
.analysis-period { display: flex; flex-wrap: wrap; align-items: center; gap: .65rem; }
.analysis-period [role="group"] { display: flex; flex-wrap: wrap; gap: .5rem; }
.analysis-period button[aria-pressed="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-section { display: grid; gap: .75rem; min-width: 0; }
.analysis-section > h2 { margin: 0; font-size: 1.2rem; }
.analysis-empty-state { display: grid; justify-items: start; gap: .45rem; }
.analysis-empty-state h3, .analysis-empty-state p { margin: 0; }
.analysis-empty-state h3 { font-size: 1rem; }
.analysis-card-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .85rem; list-style: none; margin: 0; padding: 0; }
.analysis-card { position: relative; display: grid; min-width: 0; transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease; }
.analysis-card-content { display: grid; grid-template-columns: 2.8rem minmax(0, 1fr); align-content: start; align-items: start; gap: .75rem; min-width: 0; height: 100%; padding: 1rem; padding-right: 4rem; color: inherit; text-decoration: none; }
.analysis-card-content:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; border-radius: .5rem; }
.analysis-card-icon { display: grid; place-items: center; width: 2.8rem; height: 2.8rem; border-radius: .8rem; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); font-size: 1.35rem; }
.analysis-card-copy { display: grid; gap: .3rem; min-width: 0; }
.analysis-card-heading { margin: 0; font-weight: 700; font-size: 1.05rem; }
.analysis-card-description, .analysis-card-state { color: var(--muted); overflow-wrap: anywhere; }
.analysis-card-state { display: grid; gap: .28rem; }
.analysis-card-kpi { display: grid; gap: .08rem; }
.analysis-card-kpi-label, .analysis-card-extra, .analysis-card-state time { font-size: .84rem; color: var(--muted); }
.analysis-card-kpi strong { color: var(--text); font-size: 1.15rem; }
.analysis-card-state time { display: block; margin-top: .05rem; }
.analysis-card-chart { margin-top: .25rem; }
.analysis-card-error { color: var(--danger, #a12622); }
.analysis-card-tools { position: absolute; z-index: 3; top: .5rem; right: .5rem; display: flex; align-items: center; gap: .35rem; }
.analysis-drag-handle, .analysis-overflow summary { display: grid; place-items: center; min-width: 2.75rem; min-height: 2.75rem; border: 1px solid var(--border); border-radius: .55rem; background: var(--surface); color: var(--text); }
.analysis-drag-handle { cursor: grab; touch-action: none; user-select: none; font-size: 1.35rem; }
.analysis-drag-handle:active { cursor: grabbing; }
.analysis-drag-handle:disabled { cursor: not-allowed; }
.analysis-overflow { position: relative; }
.analysis-overflow summary { cursor: pointer; list-style: none; }
.analysis-overflow summary::-webkit-details-marker { display: none; }
.analysis-overflow-menu { position: absolute; top: calc(100% + .25rem); right: 0; display: grid; min-width: 13rem; padding: .3rem; border: 1px solid var(--border); border-radius: .55rem; background: var(--surface); box-shadow: 0 .6rem 1.5rem rgb(0 0 0 / 18%); }
.analysis-overflow-menu button { min-height: 2.75rem; padding: .5rem .65rem; border: 0; border-radius: .35rem; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.analysis-overflow-menu button:hover, .analysis-overflow-menu button:focus-visible { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-overflow-menu button:disabled { color: var(--muted); cursor: not-allowed; }
.analysis-card.is-dragging { z-index: 2; transform: translateY(-4px); border-color: var(--accent); box-shadow: 0 .8rem 1.8rem rgb(0 0 0 / 22%); }
.analysis-card.is-drop-before::before, .analysis-card.is-drop-after::before { content: ''; position: absolute; z-index: 4; top: .55rem; bottom: .55rem; width: .22rem; border-radius: .2rem; background: var(--accent); pointer-events: none; }
.analysis-card.is-drop-before::before { left: -.52rem; }
.analysis-card.is-drop-after::before { right: -.52rem; }
.analysis-category-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.analysis-category { display: grid; align-content: start; gap: .65rem; min-width: 0; padding: .85rem; scroll-margin-top: 1rem; }
.analysis-category-title { margin: 0; font-size: 1rem; }
.analysis-category-title a { display: flex; justify-content: space-between; align-items: center; gap: .5rem; min-height: 2.75rem; color: inherit; text-decoration: none; }
.analysis-category-title a:hover { color: var(--accent-hover); }
.analysis-category-title span { color: var(--accent-hover); }
.analysis-save-error { margin: 0; }
@media (max-width: 950px) {
  .analysis-card-list, .analysis-category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .analysis-card-list, .analysis-category-grid { grid-template-columns: minmax(0, 1fr); }
  .analysis-card-content { padding-right: 4rem; }
  .analysis-header { align-items: flex-start; }
}
</style>
