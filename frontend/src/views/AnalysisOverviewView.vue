<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisCatalogEntryGrid from '../components/AnalysisCatalogEntryGrid.vue'
import AnalysisPeriodPicker from '../components/AnalysisPeriodPicker.vue'
import AnalysisMiniChart from '../components/AnalysisMiniChart.vue'
import WeightHistoryChart from '../components/WeightHistoryChart.vue'
import { analysisCategories, analysisHref, analysisHubQuery, analysisIconSymbol, isNutritionAnalysisKey, localizedAnalysisPresentation, nutritionAnalysisKeys, analysisRangeDayCount, normalizeAnalysisPeriodForCapability, parseAnalysisPeriodSelection, supportedAnalysisPeriods, type AnalysisPeriodSelection } from '../analysis-v2'
import { analysisRangeWithinDays } from '../analytics-period'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { useAuthStore } from '../stores/auth'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import type {
  AnalysisCategoryKey,
  AnalysisMicronutrientResponse,
  AnalysisNutritionMetricKey,
  AnalysisNutritionResponse,
  AnalysisRegistryResponse,
  VerificationActivityResponse,
  WeightResponse,
} from '../types'

type CatalogEntry = {
  entry: AnalysisRegistryResponse['analyses'][number]
  presentation: NonNullable<ReturnType<typeof localizedAnalysisPresentation>>
}
type ReaderState = 'idle' | 'loading' | 'ready' | 'error'
type PreviewReader = 'weight' | 'activity' | 'nutrition' | 'micronutrients'

const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const preferences = useAnalysisPreferences()
const selectedKeys = preferences.selectedKeys
const saving = preferences.saving
const saveError = preferences.saveError
function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const requestedPeriod = parseAnalysisPeriodSelection(route.query.period)
const queryStart = queryString(route.query.start)
const queryEnd = queryString(route.query.end)
const period = ref<AnalysisPeriodSelection>(requestedPeriod)
const customRange = ref(requestedPeriod === 'custom' && queryStart && queryEnd
  ? { start: queryStart, end: queryEnd } : null)
const registry = ref<AnalysisRegistryResponse['analyses']>([])
const workspaceLoading = ref(true)
const workspaceError = ref('')
const workspaceReady = ref(false)
const weightResponse = ref<WeightResponse | null>(null)
const activityResponse = ref<VerificationActivityResponse | null>(null)
const nutritionResponse = ref<AnalysisNutritionResponse | null>(null)
const micronutrientSummary = ref<AnalysisMicronutrientResponse | null>(null)
const micronutrientResponses = reactive<Record<string, AnalysisMicronutrientResponse | null>>({})
const readerStates = reactive<Record<PreviewReader, ReaderState>>({
  weight: 'idle', activity: 'idle', nutrition: 'idle', micronutrients: 'idle',
})
const readerErrors = reactive<Record<PreviewReader, string>>({
  weight: '', activity: '', nutrition: '', micronutrients: '',
})
const micronutrientStates = reactive<Record<string, ReaderState>>({})
const micronutrientErrors = reactive<Record<string, string>>({})
const pointerDragKey = ref<string | null>(null)
const pointerDragLift = 6
let pointerDragOffset = { x: 0, y: -pointerDragLift }
let pointerDragPointer = { x: 0, y: 0 }
let pointerDragGrabOffset = { x: 0, y: 0 }
const pointerDragId = ref<number | null>(null)
const previewOrder = ref<string[] | null>(null)
const insertionTarget = ref<{ key: string; side: 'before' | 'after' } | null>(null)
const dropFeedback = ref<{ key: string; phase: 'dropped' | 'settled' } | null>(null)
let dropFeedbackTimer: ReturnType<typeof setTimeout> | null = null
let pointerDragAnimationFrame: number | null = null
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const cardEntries = computed(() => selectedKeys.value.flatMap((key) => {
  const entry = registry.value.find((item) => item.analysis_key === key)
  const presentation = localizedAnalysisPresentation(key)
  return entry && entry.status === 'available' && entry.card_capable && presentation
    ? [{ entry, presentation }]
    : []
}))
const selectedWeightEntry = computed(() => cardEntries.value.find(({ entry }) => entry.analysis_key === 'weight_kg'))
const selectedMicronutrientEntries = computed(() => cardEntries.value.filter(({ entry }) =>
  entry.category_key === 'micronutrients' && entry.reader_key === 'analytics.nutrition.micronutrients'))
const selectedNutritionEntries = computed(() => cardEntries.value.filter(({ entry }) =>
  entry.category_key === 'nutrition' && entry.reader_key === 'analytics.nutrition.analysis'))
const summaryNutrients = computed(() => (micronutrientSummary.value?.metrics ?? [])
  .filter((metric) => registry.value.some((entry) =>
    entry.analysis_key === metric.metric_key && entry.category_key === 'micronutrients'
      && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients')))
const trendNutrients = computed(() => {
  const withReadings = summaryNutrients.value.filter((metric) => metric.days_with_data > 0)
  return (withReadings.length ? withReadings : summaryNutrients.value).slice(0, 3)
})
const presentNutrientCount = computed(() => summaryNutrients.value
  .filter((metric) => metric.days_with_data > 0 && metric.total !== null).length)
const readerCapabilities = computed(() => selectedKeys.value.flatMap((key) => {
  const entry = registry.value.find((item) => item.analysis_key === key)
  return entry?.status === 'available' && entry.period_capability ? [entry.period_capability] : []
}))
const maxDays = computed(() => readerCapabilities.value.length
  ? Math.min(...readerCapabilities.value.map(({ max_days }) => max_days))
  : 30)
const supportsCustomRange = computed(() => readerCapabilities.value.every(({ custom_range }) => custom_range))
const presets = computed(() => supportedAnalysisPeriods(maxDays.value, maxDays.value >= 3661))
const normalizedSelection = computed(() => normalizeAnalysisPeriodForCapability(
  period.value === 'custom' && !supportsCustomRange.value ? '30' : period.value,
  customRange.value, maxDays.value, today.value,
))
const range = computed(() => normalizedSelection.value.range)
const visiblePeriod = computed(() => normalizedSelection.value.period)
const periodDays = computed(() => analysisRangeDayCount(range.value))
const displayedCardEntries = computed(() => {
  if (!previewOrder.value) return cardEntries.value
  const positions = new Map(previewOrder.value.map((key, index) => [key, index]))
  return [...cardEntries.value].sort((left, right) =>
    (positions.get(left.entry.analysis_key) ?? Number.MAX_SAFE_INTEGER)
      - (positions.get(right.entry.analysis_key) ?? Number.MAX_SAFE_INTEGER))
})

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
const nutritionDays = computed(() => [...(nutritionResponse.value?.days ?? [])]
  .filter((day) => day.date >= range.value.start && day.date <= range.value.end)
  .sort((left, right) => left.date.localeCompare(right.date)))
const nutritionStats = computed(() => {
  const stats = new Map<AnalysisNutritionMetricKey, { count: number; total: number }>()
  for (const key of nutritionAnalysisKeys) stats.set(key, { count: 0, total: 0 })
  for (const day of nutritionDays.value) {
    for (const key of nutritionAnalysisKeys) {
      const value = day[key]
      if (typeof value !== 'number' || !Number.isFinite(value)) continue
      const stat = stats.get(key)!
      stat.count += 1
      stat.total += value
    }
  }
  return stats
})
function nutritionAverage(key: AnalysisNutritionMetricKey): number | null {
  const stat = nutritionStats.value.get(key)!
  return stat.count ? stat.total / stat.count : null
}
const nutritionMacroSeries = computed(() => ([
  { key: 'protein_g', colorRole: 'teal' },
  { key: 'carbohydrates_g', colorRole: 'blue' },
  { key: 'fat_g', colorRole: 'amber' },
] as const).map(({ key, colorRole }) => ({
  key, colorRole, values: nutritionDays.value.map((day) => day[key]),
})))
const weightCoverageDays = computed(() => new Set(weightPoints.value
  .filter((point) => point.date >= range.value.start && point.date <= range.value.end)
  .map((point) => point.date)).size)
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
function normalizePeriodForCapabilities(): void {
  period.value = normalizedSelection.value.period
  customRange.value = period.value === 'custom' ? range.value : null
}

function applyHubPeriod(value: { period: AnalysisPeriodSelection; start: string; end: string }): void {
  const normalized = normalizeAnalysisPeriodForCapability(
    value.period === 'custom' && !supportsCustomRange.value ? '30' : value.period,
    value.period === 'custom' ? value : null, maxDays.value, today.value,
  )
  period.value = normalized.period
  customRange.value = normalized.period === 'custom' ? normalized.range : null
}
let lastPreviewSignature = ''

async function syncPeriodQuery(): Promise<void> {
  const query = {
    ...analysisHubQuery(visiblePeriod.value, range.value),
    start: visiblePeriod.value === 'custom' ? range.value.start : undefined,
    end: visiblePeriod.value === 'custom' ? range.value.end : undefined,
  }
  if (route.query.period === query.period
    && queryString(route.query.start) === query.start
    && queryString(route.query.end) === query.end) return
  await router.replace({ query: { ...route.query, ...query }, hash: route.hash })
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
    normalizePeriodForCapabilities()
  } catch (cause) {
    workspaceReady.value = false
    workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    workspaceLoading.value = false
  }
  if (workspaceReady.value) {
    await syncPeriodQuery()
    await loadPreviews()
  }
}

async function loadPreviews(): Promise<void> {
  if (!workspaceReady.value) return
  const keys = new Set(cardEntries.value.map(({ entry }) => entry.analysis_key))
  const { start, end } = range.value
  const params = new URLSearchParams({ start, end }).toString()
  const micronutrientKeys = selectedMicronutrientEntries.value.map(({ entry }) => entry.analysis_key)
  const needsMicronutrients = micronutrientKeys.length > 0
  const requestedReaders: PreviewReader[] = []
  if (keys.has('weight_kg')) requestedReaders.push('weight')
  if (keys.has('active_energy_kcal')) requestedReaders.push('activity')
  if (selectedNutritionEntries.value.length) requestedReaders.push('nutrition')
  if (needsMicronutrients) requestedReaders.push('micronutrients')
  const signature = `${start}|${end}|${requestedReaders.join(',')}|${[...micronutrientKeys].sort().join(',')}`
  if (signature === lastPreviewSignature) return
  lastPreviewSignature = signature
  const loadId = ++previewLoadId
  weightResponse.value = null
  activityResponse.value = null
  nutritionResponse.value = null
  micronutrientSummary.value = null
  for (const reader of ['weight', 'activity', 'nutrition', 'micronutrients'] as const) {
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
  if (needsMicronutrients && !analysisRangeWithinDays(start, end, 31)) {
    readerStates.micronutrients = 'error'
    readerErrors.micronutrients = t('analysisV2.micronutrientRangeInvalid')
    for (const key of micronutrientKeys) {
      micronutrientStates[key] = 'error'
      micronutrientErrors[key] = readerErrors.micronutrients
    }
    return
  }

  const requests = requestedReaders.filter((reader) => reader !== 'micronutrients').map(async (reader) => {
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
        const response = await api<AnalysisNutritionResponse>(`/analytics/nutrition/analysis?${params}`)
        if (loadId === previewLoadId) nutritionResponse.value = response
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
  const metricRequests = new Map<string, Promise<void>>()
  function requestMetric(key: string): Promise<void> {
    const existing = metricRequests.get(key)
    if (existing) return existing
    micronutrientResponses[key] = null
    micronutrientStates[key] = 'loading'
    micronutrientErrors[key] = ''
    const request = (async () => {
      try {
        const metricParams = new URLSearchParams({ start, end, metric_key: key }).toString()
        const response = await api<AnalysisMicronutrientResponse>(
          `/analytics/nutrition/micronutrients?${metricParams}`,
        )
        if (loadId === previewLoadId) {
          micronutrientResponses[key] = response
          micronutrientStates[key] = 'ready'
        }
      } catch (cause) {
        if (loadId === previewLoadId) {
          micronutrientErrors[key] = localizeApiError(cause, 'analysisV2.loadFailed')
          micronutrientStates[key] = 'error'
          lastPreviewSignature = ''
        }
      }
    })()
    metricRequests.set(key, request)
    return request
  }
  for (const key of micronutrientKeys) requests.push(requestMetric(key))
  if (needsMicronutrients) requests.push((async () => {
    try {
      const response = await api<AnalysisMicronutrientResponse>(
        `/analytics/nutrition/micronutrients?${params}`,
      )
      if (loadId !== previewLoadId) return
      micronutrientSummary.value = response
      readerStates.micronutrients = 'ready'
      const visibleTrends = response.metrics
        .filter((metric) => metric.days_with_data > 0 && registry.value.some((entry) =>
          entry.analysis_key === metric.metric_key && entry.category_key === 'micronutrients'
            && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients'))
        .slice(0, 3)
      await Promise.all(visibleTrends.map((metric) => requestMetric(metric.metric_key)))
    } catch (cause) {
      if (loadId === previewLoadId) {
        readerErrors.micronutrients = localizeApiError(cause, 'analysisV2.loadFailed')
        readerStates.micronutrients = 'error'
        lastPreviewSignature = ''
      }
    }
  })())
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
  if (dropFeedbackTimer !== null) clearTimeout(dropFeedbackTimer)
  dropFeedbackTimer = null
  dropFeedback.value = null
  const card = (event.currentTarget as HTMLElement | null)?.closest<HTMLElement>('.analysis-card')
  const rect = card?.getBoundingClientRect()
  if (!rect) return
  pointerDragKey.value = key
  pointerDragId.value = event.pointerId
  pointerDragPointer = { x: event.clientX, y: event.clientY }
  pointerDragGrabOffset = { x: event.clientX - rect.left, y: event.clientY - rect.top }
  pointerDragOffset = { x: 0, y: -pointerDragLift }
  previewOrder.value = [...selectedKeys.value]
  insertionTarget.value = null
  if (pointerDragAnimationFrame !== null) cancelAnimationFrame(pointerDragAnimationFrame)
  pointerDragAnimationFrame = requestAnimationFrame(trackPointerDrag)
}

function syncPointerDragOffset(): void {
  if (pointerDragKey.value === null) return
  const card = document.querySelector<HTMLElement>('.analysis-card.is-dragging')
  if (!card) return
  const rect = card.getBoundingClientRect()
  const baseLeft = rect.left - pointerDragOffset.x
  const baseTop = rect.top - pointerDragOffset.y
  const nextOffset = {
    x: pointerDragPointer.x - pointerDragGrabOffset.x - baseLeft,
    y: pointerDragPointer.y - pointerDragGrabOffset.y - pointerDragLift - baseTop,
  }
  if (Math.abs(nextOffset.x - pointerDragOffset.x) < .25
    && Math.abs(nextOffset.y - pointerDragOffset.y) < .25) return
  pointerDragOffset = nextOffset
  card.style.setProperty('--drag-x', `${nextOffset.x}px`)
  card.style.setProperty('--drag-y', `${nextOffset.y}px`)

}
function trackPointerDrag(): void {
  if (pointerDragKey.value === null) {
    pointerDragAnimationFrame = null
    return
  }
  syncPointerDragOffset()
  pointerDragAnimationFrame = requestAnimationFrame(trackPointerDrag)
}

function stopPointerDragTracking(): void {
  if (pointerDragAnimationFrame === null) return
  cancelAnimationFrame(pointerDragAnimationFrame)
  pointerDragAnimationFrame = null
}

function dragStyle(key: string): Record<string, string> | undefined {
  if (pointerDragKey.value !== key) return undefined
  return {
    '--drag-x': `${pointerDragOffset.x}px`,
    '--drag-y': `${pointerDragOffset.y}px`,
  }
}

function updatePointerPreview(event: PointerEvent): void {
  if (pointerDragKey.value === null || pointerDragId.value !== event.pointerId
    || typeof document.elementFromPoint !== 'function') return
  pointerDragPointer = { x: event.clientX, y: event.clientY }
  const hit = document.elementFromPoint(event.clientX, event.clientY)
  const target = hit?.closest<HTMLElement>('.analysis-card[data-analysis-key]')
  const key = target?.dataset.analysisKey
  if (!previewOrder.value) return
  if (!target || !key) {
    // Keep the last target while the pointer rests over the pointer-transparent dragged card.
    if (!hit?.closest('.analysis-card-list')) insertionTarget.value = null
    void nextTick(syncPointerDragOffset)
    return
  }
  if (key === pointerDragKey.value) {
    void nextTick(syncPointerDragOffset)
    return
  }
  const rect = target.getBoundingClientRect()
  const grid = target.closest<HTMLElement>('.analysis-card-list')
  const tracks = grid
    ? window.getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).filter((track) => track && track !== 'none')
    : []
  const singleColumn = tracks.length
    ? tracks.length === 1
    : (window.matchMedia?.('(max-width: 600px)').matches ?? window.innerWidth <= 600)
  const after = singleColumn
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
  void nextTick(syncPointerDragOffset)
}


async function finishPointerDrag(event: PointerEvent): Promise<void> {
  if (pointerDragKey.value === null || pointerDragId.value !== event.pointerId) return
  updatePointerPreview(event)
  const draggedKey = pointerDragKey.value
  const next = previewOrder.value
  const shouldSave = insertionTarget.value !== null
    && next !== null
    && next.some((key, index) => key !== selectedKeys.value[index])
    && !saving.value
  stopPointerDragTracking()
  pointerDragKey.value = null
  pointerDragId.value = null
  previewOrder.value = null
  insertionTarget.value = null
  if (shouldSave && next && await preferences.replace(next)) {
    showDropFeedback(draggedKey)
  }
}

function cancelPointerDrag(event: PointerEvent): void {
  if (pointerDragId.value !== null && pointerDragId.value !== event.pointerId) return
  stopPointerDragTracking()
  pointerDragKey.value = null
  pointerDragId.value = null
  previewOrder.value = null
  insertionTarget.value = null
}

function entryRoute(key: string) {
  const category = registry.value.find((entry) => entry.analysis_key === key)?.category_key
  return { path: analysisHref(key), query: handoffQuery(
    category === 'body' || category === 'activity' ? 3661 : category === 'nutrition' ? 365 : 31,
  ) }
}

function handoffQuery(capability: number) {
  const normalized = normalizeAnalysisPeriodForCapability(visiblePeriod.value,
    visiblePeriod.value === 'custom' ? range.value : null, capability, today.value)
  return analysisHubQuery(normalized.period, normalized.range)
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
    query: key === 'fluid' ? analysisHubQuery(visiblePeriod.value, range.value)
      : handoffQuery(key === 'body' || key === 'activity' ? 3661 : key === 'nutrition' ? 365 : 31),
  }
}

function cardState(key: string): ReaderState {
  if (key === 'weight_kg') return readerStates.weight
  if (key === 'active_energy_kcal') return readerStates.activity
  if (isNutritionAnalysisKey(key)) return readerStates.nutrition
  return micronutrientStates[key] ?? 'idle'
}

function cardError(key: string): string {
  if (key === 'weight_kg') return readerErrors.weight
  if (key === 'active_energy_kcal') return readerErrors.activity
  if (isNutritionAnalysisKey(key)) return readerErrors.nutrition
  return micronutrientErrors[key] ?? ''
}

function cardMetric(key: string) {
  return micronutrientResponses[key]?.metrics.find((metric) => metric.metric_key === key) ?? null
}

function cardCoverage(key: string): string {
  const count = key === 'weight_kg' ? weightCoverageDays.value
    : key === 'active_energy_kcal' ? readingsByActivity.value.length
      : isNutritionAnalysisKey(key) ? nutritionStats.value.get(key)!.count
        : cardMetric(key)?.days_with_data ?? 0
  return t('analysisV2.daysCoverage', {
    count: number.format(count),
    total: number.format(periodDays.value),
  })
}

function cardCoverageState(key: string): 'complete' | 'incomplete' {
  const count = key === 'weight_kg' ? weightCoverageDays.value
    : key === 'active_energy_kcal' ? readingsByActivity.value.length
      : isNutritionAnalysisKey(key) ? nutritionStats.value.get(key)!.count
        : cardMetric(key)?.days_with_data ?? 0
  return count === periodDays.value ? 'complete' : 'incomplete'
}

function miniChartColorRole(key: string): 'violet' | 'teal' | 'blue' | 'amber' | 'emerald' | 'lilac' {
  if (key === 'weight_kg' || key === 'dietary_energy_kcal' || key === 'calories_kcal') return 'violet'
  if (key === 'active_energy_kcal') return 'amber'
  if (registry.value.find((entry) => entry.analysis_key === key)?.category_key === 'micronutrients') return 'emerald'
  if (key === 'protein_g') return 'teal'
  if (key === 'carbohydrates_g') return 'blue'
  if (key === 'fat_g') return 'amber'
  return 'lilac'
}

function cardIconRole(key: string, category: AnalysisCategoryKey): string {
  if (key === 'weight_kg') return 'weight'
  if (key === 'active_energy_kcal') return 'activity'
  if (category === 'micronutrients') return 'micronutrient'
  if (key === 'protein_g') return 'protein'
  if (key === 'carbohydrates_g') return 'carbohydrates'
  if (key === 'fat_g') return 'fat'
  return ''
}

function cardValue(key: string): string | null {
  if (key === 'weight_kg') {
    const latest = weightPoints.value.at(-1)
    return latest ? `${number.format(latest.weight_kg)} kg` : null
  }
  if (key === 'active_energy_kcal') {
    return activityAverage.value === null ? null : `${number.format(activityAverage.value)} kcal`
  }
  if (isNutritionAnalysisKey(key)) {
    const average = nutritionAverage(key)
    return average === null ? null : `${number.format(average)} ${key === 'dietary_energy_kcal' ? 'kcal' : 'g'}`
  }
  const metric = cardMetric(key)
  if (!metric || metric.days_with_data === 0 || metric.total === null) return null
  return `${number.format(metric.total / metric.days_with_data)} ${metric.unit}`
}

function weightChange(): string | null {
  const points = weightPoints.value
  if (points.length < 2) return null
  const change = points[points.length - 1]!.weight_kg - points[0]!.weight_kg
  return `${change > 0 ? '+' : ''}${number.format(change)} kg`
}

function formatWeight(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${number.format(value)} kg` : '–'
}

function weightSummaryDetail(): string {
  const parts = [
    weightChange(),
    t('analysisV2.measurementCount', {
      count: number.format(weightResponse.value?.summary.measurement_count ?? 0),
    }),
  ].filter((value): value is string => Boolean(value))
  return parts.join(' · ')
}

function cardHasReadings(key: string): boolean {
  if (key === 'weight_kg') return weightPoints.value.length > 0
  if (key === 'active_energy_kcal') return readingsByActivity.value.length > 0
  if (isNutritionAnalysisKey(key)) return nutritionStats.value.get(key)!.count > 0
  return (cardMetric(key)?.days_with_data ?? 0) > 0
}

function cardChartValues(key: string): Array<number | null> {
  if (key === 'weight_kg') return weightPoints.value.map((point) => point.weight_kg)
  if (key === 'active_energy_kcal') return activityChartValues.value
  if (isNutritionAnalysisKey(key)) return nutritionDays.value.map((day) => day[key])
  const valuesByDate = new Map(
    (micronutrientResponses[key]?.series ?? []).map(({ date, value }) => [date, value]),
  )
  return Array.from({ length: periodDays.value }, (_, index) => (
    valuesByDate.get(shiftIsoDate(range.value.start, index)) ?? null
  ))
}

function cardChartDates(key: string): Array<string | null> {
  if (key === 'weight_kg') return weightPoints.value.map((point) => point.date)
  if (key === 'active_energy_kcal') return activityChartDates.value
  if (isNutritionAnalysisKey(key)) return nutritionDays.value.map((day) => day.date)
  return Array.from({ length: periodDays.value }, (_, index) => shiftIsoDate(range.value.start, index))
}

function cardHasChartData(key: string): boolean {
  return cardChartValues(key).some((value) => typeof value === 'number' && Number.isFinite(value))
}

function chartLabel(key: string, label: string): string {
  if (key === 'weight_kg') return `${label} · ${t('analysisV2.weightHistory')}`
  if (key === 'active_energy_kcal') return `${label} · ${t('analysisV2.activeEnergyChart')}`
  if (isNutritionAnalysisKey(key)) return `${label} · ${t('analysisV2.nutritionDaily')}`
  return `${label} · ${t('analysisV2.micronutrientDaily')}`
}

function showDropFeedback(key: string): void {
  if (dropFeedbackTimer !== null) clearTimeout(dropFeedbackTimer)
  dropFeedback.value = { key, phase: 'dropped' }
  dropFeedbackTimer = setTimeout(() => {
    if (dropFeedback.value?.key !== key) return
    dropFeedback.value = { key, phase: 'settled' }
    dropFeedbackTimer = setTimeout(() => {
      if (dropFeedback.value?.key === key) dropFeedback.value = null
      dropFeedbackTimer = null
    }, 600)
  }, 160)
}

function insertionClass(key: string): string | null {
  if (pointerDragKey.value === key) return 'is-dragging'
  if (insertionTarget.value?.key === key) return `is-drop-${insertionTarget.value.side}`
  if (dropFeedback.value?.key === key) return `is-${dropFeedback.value.phase}`
  return null
}
async function scrollToCategoryHash(): Promise<void> {
  const id = route.hash.slice(1)
  if (!id.startsWith('analysis-category-')) return
  await nextTick()
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

watch(
  () => [route.query.period, route.query.start, route.query.end] as const,
  ([nextPeriod, nextStart, nextEnd]) => {
    const requested = parseAnalysisPeriodSelection(nextPeriod)
    const start = queryString(nextStart)
    const end = queryString(nextEnd)
    if (!workspaceReady.value) {
      period.value = requested
      customRange.value = requested === 'custom' && start && end ? { start, end } : null
      return
    }
    const normalized = normalizeAnalysisPeriodForCapability(
      requested === 'custom' && !supportsCustomRange.value ? '30' : requested,
      requested === 'custom' && start && end ? { start, end } : null,
      maxDays.value, today.value,
    )
    const nextPeriodValue = normalized.period
    const nextCustomRange = normalized.period === 'custom' ? normalized.range : null
    if (period.value !== nextPeriodValue || customRange.value?.start !== nextCustomRange?.start
      || customRange.value?.end !== nextCustomRange?.end) {
      period.value = nextPeriodValue
      customRange.value = nextCustomRange
    }
    if (workspaceReady.value) void syncPeriodQuery()
  },
)
watch(() => route.hash, () => { void scrollToCategoryHash() })

watch(
  [period, customRange, () => selectedKeys.value.join('|')],
  async () => {
    if (!workspaceReady.value) return
    normalizePeriodForCapabilities()
    const previews = loadPreviews()
    await syncPeriodQuery()
    await previews
  },
)

onMounted(async () => {
  window.addEventListener('pointermove', updatePointerPreview)
  window.addEventListener('pointerup', finishPointerDrag)
  window.addEventListener('pointercancel', cancelPointerDrag)
  await loadWorkspace()
  await scrollToCategoryHash()
})

onBeforeUnmount(() => {
  previewLoadId += 1
  window.removeEventListener('pointermove', updatePointerPreview)
  window.removeEventListener('pointerup', finishPointerDrag)
  window.removeEventListener('pointercancel', cancelPointerDrag)
  if (dropFeedbackTimer !== null) clearTimeout(dropFeedbackTimer)
  stopPointerDragTracking()
})
</script>

<template>
  <div class="analysis-hub">
    <section class="analysis-header page-heading">
      <div class="analysis-heading-copy">
        <h1>{{ t('analysisV2.title') }}</h1>
        <p>{{ t('analysisV2.description') }}</p>
      </div>
      <AnalysisPeriodPicker
        v-if="workspaceReady"
        :start="range.start"
        :end="range.end"
        :today="today"
        :period="visiblePeriod"
        :presets="presets"
        :custom-range="supportsCustomRange"
        :max-days="maxDays"
        @apply="applyHubPeriod"
      />
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

        <TransitionGroup v-else tag="ol" name="analysis-card-reorder" class="analysis-card-list" :aria-label="t('analysisV2.myAnalyses')">
          <li
            v-for="({ entry, presentation }) in displayedCardEntries"
            :key="entry.analysis_key"
            class="card analysis-card"
            :class="insertionClass(entry.analysis_key)"
            :data-analysis-key="entry.analysis_key"
            :data-analysis-card="entry.analysis_key"
            :style="dragStyle(entry.analysis_key)"
            :data-drop-state="dropFeedback?.key === entry.analysis_key ? dropFeedback.phase : undefined"
          >
            <div class="analysis-card-header">
              <button
                class="analysis-drag-handle"
                type="button"
                :aria-label="t('analysisV2.dragHandle', { label: presentation.label })"
                :disabled="saving"
                @pointerdown="pointerDown(entry.analysis_key, $event)"
              >
                <span aria-hidden="true">⋮⋮</span>
              </button>
              <span
                class="analysis-card-icon"
                :class="`analysis-card-icon--${cardIconRole(entry.analysis_key, entry.category_key)}`"
                aria-hidden="true"
              >
                {{ analysisIconSymbol(presentation.icon) }}
              </span>
              <h3 class="analysis-card-heading">{{ presentation.label }}</h3>
              <div class="analysis-card-tools">
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
            </div>
            <RouterLink class="analysis-card-content" :to="entryRoute(entry.analysis_key)" :aria-label="presentation.label">
              <span class="analysis-card-copy">
                <span v-if="cardState(entry.analysis_key) === 'loading'" class="analysis-card-state" role="status">
                  {{ t('common.loading') }}
                </span>
                <span v-if="cardState(entry.analysis_key) === 'ready' && !cardHasReadings(entry.analysis_key)" class="analysis-card-state">
                  {{ entry.category_key === 'micronutrients' ? t('analysisV2.noDataPeriod') : t('analysisV2.noReadings') }}
                </span>
                <span
                  v-if="cardState(entry.analysis_key) === 'ready' && !cardHasReadings(entry.analysis_key)"
                  class="analysis-card-extra analysis-card-coverage analysis-card-coverage--incomplete"
                  data-coverage-status="incomplete"
                >
                  <span class="analysis-card-coverage-icon" aria-hidden="true">!</span>
                  {{ cardCoverage(entry.analysis_key) }}
                </span>
                <span v-else-if="cardState(entry.analysis_key) === 'ready' && cardHasReadings(entry.analysis_key)" class="analysis-card-state">
                  <strong class="analysis-metric-value">{{ cardValue(entry.analysis_key) ?? '–' }}</strong>
                  <span class="analysis-card-extra">
                    {{ entry.analysis_key === 'weight_kg' ? weightSummaryDetail() : t('analysisV2.averagePerDay') }}
                  </span>
                  <span
                    v-if="entry.analysis_key === 'dietary_energy_kcal' && nutritionAverage('protein_g') !== null"
                    class="analysis-card-extra analysis-card-protein"
                  >
                    {{ i18n.global.locale.value === 'de' ? 'Eiweiß' : t('charts.protein') }} ·
                    {{ number.format(nutritionAverage('protein_g')!) }} g
                  </span>
                  <AnalysisMiniChart
                    v-if="cardHasChartData(entry.analysis_key)"
                    class="analysis-card-chart"
                    :values="cardChartValues(entry.analysis_key)"
                    :dates="cardChartDates(entry.analysis_key)"
                    :date-range="range"
                    :label="chartLabel(entry.analysis_key, presentation.label)"
                    :variant="entry.analysis_key === 'active_energy_kcal' ? 'bars' : entry.analysis_key === 'dietary_energy_kcal' ? 'stacked-bars' : 'line'"
                    :color-role="miniChartColorRole(entry.analysis_key)"
                    :series="entry.analysis_key === 'dietary_energy_kcal' ? nutritionMacroSeries : undefined"
                  />
                  <span
                    class="analysis-card-extra analysis-card-coverage"
                    :class="cardCoverageState(entry.analysis_key) === 'complete' ? 'analysis-card-coverage--complete' : 'analysis-card-coverage--incomplete'"
                    :data-coverage-status="cardCoverageState(entry.analysis_key)"
                  >
                    <span class="analysis-card-coverage-icon" aria-hidden="true">
                      {{ cardCoverageState(entry.analysis_key) === 'complete' ? '✓' : '!' }}
                    </span>
                    {{ cardCoverage(entry.analysis_key) }}
                  </span>
                </span>
              </span>
            </RouterLink>
            <div v-if="cardState(entry.analysis_key) === 'error'" class="analysis-card-retry" role="alert">
              <span>{{ cardError(entry.analysis_key) }}</span>
              <button class="button secondary" type="button" @click="loadPreviews">{{ t('analysisV2.retry') }}</button>
            </div>
          </li>
        </TransitionGroup>
        <section
          v-if="selectedWeightEntry"
          class="card analysis-weight-section"
          aria-labelledby="analysis-weight-title"
        >
          <div class="analysis-weight-header">
            <div>
              <h3 id="analysis-weight-title">{{ t('analysisV2.weightHistory') }}</h3>
              <p>{{ formatGermanDate(range.start) }} – {{ formatGermanDate(range.end) }}</p>
            </div>
            <RouterLink class="button secondary analysis-weight-details" :to="entryRoute('weight_kg')">
              {{ t('analysisV2.details') }}
            </RouterLink>
          </div>
          <p v-if="readerStates.weight === 'loading' || readerStates.weight === 'idle'" role="status" aria-live="polite">
            {{ t('common.loading') }}
          </p>
          <div v-else-if="readerStates.weight === 'error'" class="analysis-weight-error" role="alert">
            <p>{{ readerErrors.weight }}</p>
            <button class="button secondary" type="button" @click="loadPreviews">{{ t('analysisV2.retry') }}</button>
          </div>
          <template v-else-if="readerStates.weight === 'ready' && weightResponse">
            <div v-if="!weightPoints.length" class="analysis-weight-empty">
              <p role="status">{{ t('analysisV2.weightEmpty') }}</p>
              <p>{{ cardCoverage('weight_kg') }}</p>
            </div>
            <div v-else class="analysis-weight-body">
              <WeightHistoryChart
                class="analysis-weight-chart"
                :points="weightPoints"
                :date-range="range"
                :title="t('analysisV2.weightTitle')"
                :height="320"
              />
              <div class="analysis-weight-facts">
                <dl class="analysis-weight-current">
                  <div>
                    <dt>{{ t('analysisV2.latestMeasurement') }}</dt>
                    <dd class="analysis-weight-value">{{ cardValue('weight_kg') }}</dd>
                    <dd><time :datetime="weightPoints.at(-1)!.date">{{ formatGermanDate(weightPoints.at(-1)!.date) }}</time></dd>
                  </div>
                  <div>
                    <dt>{{ t('analysisV2.change') }}</dt>
                    <dd class="analysis-weight-change">{{ weightChange() ?? '–' }}</dd>
                    <dd v-if="weightPoints.length < 2">{{ t('analysisV2.changeUnavailable') }}</dd>
                  </div>
                </dl>
                <div class="analysis-weight-coverage">
                  <p>{{ t('analysisV2.measurementCount', { count: number.format(weightResponse.summary.measurement_count) }) }}</p>
                  <p>{{ cardCoverage('weight_kg') }}</p>
                </div>
                <dl class="analysis-weight-statistics">
                  <div>
                    <dt>{{ t('analysisV2.average') }}</dt>
                    <dd>{{ formatWeight(weightResponse.summary.average_kg) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('analysisV2.minimum') }}</dt>
                    <dd>{{ formatWeight(weightResponse.summary.minimum_kg) }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('analysisV2.maximum') }}</dt>
                    <dd>{{ formatWeight(weightResponse.summary.maximum_kg) }}</dd>
                  </div>
                </dl>
              </div>
            </div>
          </template>
        </section>
        <section
          v-if="selectedMicronutrientEntries.length"
          class="card analysis-nutrient-summary"
          role="region"
          aria-labelledby="analysis-nutrient-summary-title"
        >
          <h3 id="analysis-nutrient-summary-title">
            <RouterLink :to="categoryRoute('micronutrients')">{{ t('analysisV2.categories.micronutrients') }} <span aria-hidden="true">→</span></RouterLink>
          </h3>
          <p v-if="readerStates.micronutrients === 'loading'" role="status">{{ t('common.loading') }}</p>
          <div v-else-if="readerStates.micronutrients === 'error'" class="analysis-card-error" role="alert">
            <span>{{ readerErrors.micronutrients }}</span>
            <button class="button secondary" type="button" @click="loadPreviews">{{ t('analysisV2.retry') }}</button>
          </div>
          <template v-else-if="readerStates.micronutrients === 'ready'">
            <p class="analysis-nutrient-count">{{ number.format(presentNutrientCount) }} {{ i18n.global.locale.value === 'de' ? 'von' : 'of' }} {{ number.format(summaryNutrients.length) }}</p>
            <p v-if="!presentNutrientCount" class="analysis-card-extra">{{ t('analysisV2.noDataPeriod') }}</p>
            <ul v-if="trendNutrients.length" class="analysis-nutrient-trends">
              <li v-for="metric in trendNutrients" :key="metric.metric_key">
                <RouterLink :to="entryRoute(metric.metric_key)">{{ localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label }}</RouterLink>
                <span class="analysis-card-extra">{{ t('analysisV2.daysCoverage', {
                  count: number.format(metric.days_with_data), total: number.format(periodDays),
                }) }}</span>
                <span v-if="metric.days_with_data > 0 && micronutrientStates[metric.metric_key] === 'loading'" role="status">{{ t('common.loading') }}</span>
                <span v-else-if="micronutrientStates[metric.metric_key] === 'error'" class="analysis-card-error" role="alert">
                  {{ micronutrientErrors[metric.metric_key] }}
                  <button class="button secondary" type="button" @click="loadPreviews">{{ t('analysisV2.retry') }}</button>
                </span>
                <AnalysisMiniChart
                  v-else-if="metric.days_with_data > 0 && cardHasChartData(metric.metric_key)"
                  :values="cardChartValues(metric.metric_key)"
                  :dates="cardChartDates(metric.metric_key)"
                  :date-range="range"
                  :label="chartLabel(metric.metric_key, metric.label)"
                  variant="line"
                  color-role="emerald"
                />
              </li>
            </ul>
          </template>
        </section>
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
              :period-range="range"
              :period-days="periodDays"
              :micronutrients="category.key === 'micronutrients' ? micronutrientSummary : null"
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
.analysis-header { align-items: end; margin-bottom: 0; }
.analysis-heading-copy h1, .analysis-heading-copy p { margin: 0; }
.analysis-heading-copy p { margin-top: .35rem; color: var(--muted); }
.analysis-section { display: grid; gap: .75rem; min-width: 0; }
.analysis-section > h2 { margin: 0; font-size: 1.2rem; }
.analysis-empty-state { display: grid; justify-items: start; gap: .45rem; }
.analysis-empty-state h3, .analysis-empty-state p { margin: 0; }
.analysis-empty-state h3 { font-size: 1rem; }
.analysis-card-list { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: .85rem; list-style: none; margin: 0; padding: 0; }
.analysis-card { position: relative; display: grid; min-width: 0; transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease, background-color .16s ease; }
.analysis-card-reorder-move { transition: transform .25s cubic-bezier(.2, .7, .2, 1); }
.analysis-card { grid-template-columns: minmax(0, 1fr); }
.analysis-card-header { display: grid; grid-template-columns: 44px auto minmax(0, 1fr) auto; align-items: center; gap: .5rem; min-width: 0; padding: .6rem .65rem 0; }
.analysis-card-content { display: block; min-width: 0; padding: .65rem .9rem .9rem; color: inherit; text-decoration: none; }
.analysis-card-retry { display: grid; justify-items: start; gap: .5rem; padding: 0 .9rem .9rem; color: var(--danger); }
.analysis-card-content:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; border-radius: .5rem; }
.analysis-card-icon { display: grid; place-items: center; width: 2.35rem; height: 2.35rem; border-radius: .7rem; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); font-size: 1.2rem; }
.analysis-card-icon--weight { color: var(--chart-series-violet); background: color-mix(in srgb, var(--chart-series-violet) 14%, transparent); }
.analysis-card-icon--activity, .analysis-card-icon--fat { color: var(--chart-series-amber); background: color-mix(in srgb, var(--chart-series-amber) 14%, transparent); }
.analysis-card-icon--micronutrient { color: var(--chart-series-emerald); background: color-mix(in srgb, var(--chart-series-emerald) 14%, transparent); }
.analysis-card-icon--protein { color: var(--chart-series-teal); background: color-mix(in srgb, var(--chart-series-teal) 14%, transparent); }
.analysis-card-icon--carbohydrates { color: var(--chart-series-blue); background: color-mix(in srgb, var(--chart-series-blue) 14%, transparent); }
.analysis-card-copy { display: grid; gap: .38rem; min-width: 0; }
.analysis-card-heading { margin: 0; font-weight: 700; font-size: 1.05rem; overflow-wrap: anywhere; }
.analysis-card-state { color: var(--muted); overflow-wrap: anywhere; }
.analysis-card-state { display: grid; gap: .32rem; }
.analysis-card-extra { font-size: .84rem; color: var(--muted); }
.analysis-metric-value { color: var(--ink); font-size: 1.55rem; line-height: 1.15; font-weight: 700; }
.analysis-card-chart { margin-top: .1rem; }
.analysis-card-error { color: var(--danger); }
.analysis-card-coverage { display: inline-flex; align-items: center; gap: .32rem; }
.analysis-card-coverage--complete, .analysis-card-coverage--incomplete { color: var(--muted); }
.analysis-card-coverage-icon { flex: 0 0 auto; font-weight: 750; }
.analysis-card-tools { position: relative; z-index: 3; }
.analysis-drag-handle { display: grid; place-items: center; min-width: 44px; min-height: 44px; padding: 0; border: 0; border-radius: .35rem; background: transparent; color: var(--muted); cursor: grab; touch-action: none; user-select: none; font-size: 1rem; line-height: 1; }
.analysis-drag-handle:hover, .analysis-drag-handle:focus-visible { background: color-mix(in srgb, var(--accent) 10%, var(--surface)); color: var(--accent-hover); outline: 2px solid var(--focus-ring); outline-offset: 1px; }
.analysis-drag-handle:active { cursor: grabbing; }
.analysis-drag-handle:disabled { cursor: not-allowed; }
.analysis-overflow summary { display: grid; place-items: center; min-width: 2.75rem; min-height: 2.75rem; border: 1px solid var(--border); border-radius: .55rem; background: var(--surface); color: var(--ink); cursor: pointer; list-style: none; }
.analysis-overflow summary::-webkit-details-marker { display: none; }
.analysis-overflow-menu { position: absolute; top: calc(100% + .25rem); right: 0; display: grid; min-width: 13rem; padding: .3rem; border: 1px solid var(--border); border-radius: .55rem; background: var(--popover); box-shadow: var(--shadow-popover); }
.analysis-overflow-menu button { min-height: 2.75rem; padding: .5rem .65rem; border: 0; border-radius: .35rem; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
.analysis-overflow-menu button:hover, .analysis-overflow-menu button:focus-visible { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.analysis-overflow-menu button:disabled { color: var(--muted); cursor: not-allowed; }
.analysis-card.is-dragging { z-index: 4; translate: var(--drag-x, 0px) var(--drag-y, 0px); scale: 1.025; border-color: var(--accent); box-shadow: var(--shadow-popover); pointer-events: none; }
.analysis-card.is-drop-before, .analysis-card.is-drop-after { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--surface)); }
.analysis-card.is-drop-before::before, .analysis-card.is-drop-after::after { content: ''; position: absolute; z-index: 4; left: .55rem; right: .55rem; height: 3px; border-radius: 3px; background: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 18%, transparent); pointer-events: none; }
.analysis-card.is-drop-before::before { top: -.2rem; }
.analysis-card.is-drop-after::after { bottom: -.2rem; }
.analysis-overflow { position: relative; }
.analysis-card.is-dropped { border-color: var(--success); background: color-mix(in srgb, var(--success) 10%, var(--surface)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 24%, transparent); }
.analysis-card.is-settled { border-color: var(--success); }
.analysis-weight-section { display: grid; gap: 1rem; min-width: 0; padding: 1rem; }
.analysis-weight-header { display: flex; align-items: center; justify-content: space-between; gap: 1rem; }
.analysis-weight-header h3, .analysis-weight-section p { margin: 0; }
.analysis-weight-header h3 { font-size: 1.15rem; }
.analysis-weight-header p { margin-top: .35rem; color: var(--muted); font-size: .9rem; }
.analysis-weight-details { flex-shrink: 0; text-decoration: none; }
.analysis-weight-details:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
.analysis-weight-body { display: grid; grid-template-columns: minmax(0, 2fr) minmax(15rem, 1fr); gap: 1.25rem; align-items: start; }
.analysis-weight-chart { min-width: 0; min-height: 0; padding: 0; border: 0; box-shadow: none; }
.analysis-weight-facts { display: grid; gap: 1rem; min-width: 0; padding: .5rem 0 .5rem 1.25rem; border-left: 1px solid var(--border); }
.analysis-weight-facts dl, .analysis-weight-facts dd { margin: 0; }
.analysis-weight-current, .analysis-weight-statistics { display: grid; gap: 1rem; }
.analysis-weight-current > div, .analysis-weight-statistics > div { display: grid; gap: .3rem; min-width: 0; }
.analysis-weight-facts dt, .analysis-weight-current dd:not(.analysis-weight-value):not(.analysis-weight-change), .analysis-weight-coverage { color: var(--muted); font-size: .88rem; }
.analysis-weight-value { color: var(--chart-series-violet); font-size: 1.9rem; font-weight: 750; line-height: 1.15; }
.analysis-weight-change, .analysis-weight-statistics dd { color: var(--ink); font-size: 1.05rem; font-weight: 650; }
.analysis-weight-value, .analysis-weight-facts dd { overflow-wrap: anywhere; }
.analysis-weight-coverage { display: grid; gap: .3rem; }
.analysis-weight-statistics { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .65rem; padding-top: 1rem; border-top: 1px solid var(--border); }
.analysis-weight-empty, .analysis-weight-error { display: grid; justify-items: start; gap: .65rem; }
.analysis-weight-empty { color: var(--muted); padding-block: 1rem; }
.analysis-weight-error { color: var(--danger); }
.analysis-nutrient-summary { display: grid; gap: .7rem; min-width: 0; padding: 1rem; }
.analysis-nutrient-summary h3, .analysis-nutrient-summary p { margin: 0; }
.analysis-nutrient-summary h3 { font-size: 1.05rem; }
.analysis-nutrient-summary a { color: inherit; text-decoration: none; }
.analysis-nutrient-summary a:hover, .analysis-nutrient-summary a:focus-visible { color: var(--accent-hover); text-decoration: underline; }
.analysis-nutrient-count { color: var(--ink); font-size: 1.55rem; font-weight: 700; }
.analysis-nutrient-trends { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .85rem; margin: 0; padding: 0; list-style: none; }
.analysis-nutrient-trends li { display: grid; align-content: start; gap: .4rem; min-width: 0; padding: .8rem; border: 1px solid var(--border); border-radius: .65rem; }
.analysis-nutrient-trends .analysis-mini-chart { height: 6.5rem; }
.analysis-category-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .8rem; }
.analysis-category { display: grid; align-content: start; gap: .65rem; min-width: 0; padding: .85rem; scroll-margin-top: 1rem; }
.analysis-category-title { margin: 0; font-size: 1rem; }
.analysis-category-title a { display: flex; justify-content: space-between; align-items: center; gap: .5rem; min-height: 2.75rem; color: inherit; text-decoration: none; }
.analysis-category-title a:hover { color: var(--accent-hover); }
.analysis-category-title span { color: var(--accent-hover); }
.analysis-save-error { margin: 0; }
@media (max-width: 1200px) {
  .analysis-card-list, .analysis-category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .analysis-nutrient-trends { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 900px) {
  .analysis-weight-body { grid-template-columns: minmax(0, 1fr); }
  .analysis-weight-facts { border-left: 0; border-top: 1px solid var(--border); padding: 1rem 0 0; }
  .analysis-weight-current { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .analysis-card-list, .analysis-category-grid { grid-template-columns: minmax(0, 1fr); }
  .analysis-header { align-items: flex-start; }
  .analysis-nutrient-trends { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  .analysis-card-reorder-move { transition: none; }
  .analysis-card.is-dragging { scale: 1; }
  .analysis-card { transition: none; }
  .analysis-card.is-dragging { transform: none; }
}
</style>
