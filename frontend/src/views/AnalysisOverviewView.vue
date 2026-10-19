<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { PhCheck, PhPlus, PhX } from '@phosphor-icons/vue'
import { useRoute, useRouter } from 'vue-router'

import { api, localizeApiError } from '../api'
import AnalysisHubCard from '../components/AnalysisHubCard.vue'
import AnalysisMiniChart from '../components/AnalysisMiniChart.vue'
import PeriodPicker from '../components/PeriodPicker.vue'
import {
  addAnalysisPreference,
  analysisCategories,
  analysisHref,
  analysisHubQuery,
  analysisRangeDayCount,
  AnalysisIcon,
  canonicalAnalysisCardId,
  DEFAULT_ACTIVE_CARD_IDS,
  HUB_MINI_CHART_BAR_WIDTH_RATIO,
  HUB_MINI_CHART_MAX_BAR_WIDTH,
  hubMiniChartVariant,
  isNutritionAnalysisKey,
  localizedAnalysisPresentation,
  nutritionAnalysisKeys,
} from '../analysis-v2'
import { groupNutritionMealOccasions } from '../analysis-nutrition-overview'
import { analysisRangeWithinDays, commonAllDataRange, periodFromQuery, validPeriodRange, type AppliedPeriod, type PeriodLimits, type PeriodRange, type PeriodSelection } from '../analytics-period'
import { useAnalysisPreferences } from '../composables/useAnalysisPreferences'
import { useAuthStore } from '../stores/auth'
import { formatDate, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createKcalFormatter, createNumberFormatter, i18n } from '../i18n'
import type {
  AnalysisCategoryKey,
  AnalysisMicronutrientResponse,
  AnalysisNutritionMetricKey,
  AnalysisNutritionResponse,
  AnalysisRegistryResponse,
  ReaderPeriodCapabilities,
  VerificationActivityResponse,
  WeightResponse,
} from '../types'

type ReaderState = 'idle' | 'loading' | 'ready' | 'error'
type PreviewReader = 'weight' | 'activity' | 'nutrition' | 'micronutrients'
type BuiltInCardId = 'weight' | 'nutrition' | 'micronutrients' | 'activity'
type ManagerEntry = { id: string; label: string; icon: string; status: 'available' | 'coming_soon' }
type HubMealType = 'breakfast' | 'lunch' | 'dinner' | 'snack' | 'uncategorized'
const hubMealTypeOrder: readonly HubMealType[] = [
  'breakfast', 'lunch', 'dinner', 'snack', 'uncategorized',
]
const recognizedHubMealTypes = new Set<HubMealType>([
  'breakfast', 'lunch', 'dinner', 'snack',
])
type ManagerGroup = { key: AnalysisCategoryKey; labelKey: string; entries: ManagerEntry[] }
type ManagerScrollSnapshot = {
  scrollX: number
  scrollY: number
  rootOverflow: string
  rootScrollBehavior: string
  bodyOverflow: string
  bodyPaddingRight: string
}
const HUB_LINE_AREA_STOPS = [
  { offset: '0%', opacity: 0.22 },
  { offset: '60%', opacity: 0.2 },
  { offset: '100%', opacity: 0.03 },
] as const
const hiddenHubCardIds = new Set(['micronutrients'])
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const kcal = createKcalFormatter()
const preferences = useAnalysisPreferences()
const activeCardIds = preferences.activeCardIds
const saving = preferences.saving
const saveError = preferences.saveError
function queryString(value: unknown): string | null {
  if (typeof value === 'string') return value
  return Array.isArray(value) && typeof value[0] === 'string' ? value[0] : null
}

const initialSelection = periodFromQuery(route.query, {
  minDays: 1, maxDays: 3661, customRange: true,
  maxDate: isoDateInTimeZone(auth.user?.timezone ?? 'UTC'),
}, isoDateInTimeZone(auth.user?.timezone ?? 'UTC'), null)
const period = ref<PeriodSelection>(initialSelection.period)
const customRange = ref<PeriodRange | null>(initialSelection.period === 'custom'
  ? { start: initialSelection.start, end: initialSelection.end } : null)
const committedAllRange = ref<PeriodRange | null>(null)
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
const mealsTodayResponse = ref<AnalysisNutritionResponse | null>(null)
const mealsTodayState = ref<ReaderState>('idle')
const mealsTodayError = ref('')
const micronutrientStates = reactive<Record<string, ReaderState>>({})
const micronutrientErrors = reactive<Record<string, string>>({})
const pointerDrag = ref<string | null>(null)
const pointerDragLift = 6
let pointerDragOffset = { x: 0, y: -pointerDragLift }
let pointerDragOriginScroll = { x: 0, y: 0 }

type PointerDragOriginBounds = Pick<DOMRectReadOnly, 'left' | 'right' | 'top' | 'bottom'>
let pointerDragOrigin = { x: 0, y: 0 }
let pointerDragOriginBounds: PointerDragOriginBounds | null = null
let pointerDragPointer = { x: 0, y: 0 }
let pointerDragGrabOffset = { x: 0, y: 0 }
let pointerDragAnimationFrame: number | null = null
const pointerDragId = ref<number | null>(null)
const orderPreview = ref<string[] | null>(null)
const insertionTarget = ref<{ key: string; slotIndex: number } | null>(null)
const openMenuCard = ref<string | null>(null)
const today = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const managerDialog = ref<HTMLDialogElement | null>(null)
const managerHeading = ref<HTMLElement | null>(null)
const managerTrigger = ref<HTMLButtonElement | null>(null)
const managerSearch = ref('')
const selectedManagerCategory = ref<AnalysisCategoryKey | null>(null)
let managerPageScrollSnapshot: ManagerScrollSnapshot | null = null
let managerBackdropPointerId: number | null = null
let managerBackdropPointerStartedOutside = false
let managerBackdropClickReady = false
const mealsTodayDay = computed(() => mealsTodayResponse.value?.days.find((day) => day.date === today.value) ?? null)
function normalizeHubMealType(value: string | null): HubMealType {
  const normalized = value?.trim().toLowerCase()
  return normalized && recognizedHubMealTypes.has(normalized as HubMealType)
    ? normalized as HubMealType : 'uncategorized'
}
function formatHubMealType(type: HubMealType): string {
  return type === 'uncategorized'
    ? t('analysisV2.mealsUncategorized')
    : t(`nutritionVerification.mealTypes.${type}`)
}
const todayMealGroups = computed(() => {
  const entries = (mealsTodayDay.value?.meals ?? []).map((meal) => ({
    id: meal.event_id,
    time: meal.local_time,
    type: normalizeHubMealType(meal.meal_type),
    name: meal.display_name,
    energy: meal.dietary_energy_kcal,
  }))
  return groupNutritionMealOccasions(entries).map((group) => {
    const energies = group.entries.map((entry) => entry.energy)
    const totalEnergy = energies.every((energy): energy is number =>
      typeof energy === 'number' && Number.isFinite(energy))
      ? energies.reduce((total, energy) => total + energy, 0)
      : null
    return {
      ...group,
      type: group.type as HubMealType,
      totalEnergy: totalEnergy !== null && Number.isFinite(totalEnergy) ? totalEnergy : null,
    }
  }).sort((left, right) =>
    hubMealTypeOrder.indexOf(left.type) - hubMealTypeOrder.indexOf(right.type))
})
const todayMealEntryCount = computed(() =>
  todayMealGroups.value.reduce((total, group) => total + group.entryCount, 0))
function mealOccasionCountLabel(count: number): string {
  return t(count === 1 ? 'analysisV2.mealOccasionSingular' : 'analysisV2.mealOccasionPlural', {
    count: number.format(count),
  })
}
function mealEntryCountLabel(count: number): string {
  return t(count === 1 ? 'analysisV2.mealEntrySingular' : 'analysisV2.mealEntryPlural', {
    count: number.format(count),
  })
}
function mealSummaryLabel(occasionCount: number, entryCount: number): string {
  return `${mealOccasionCountLabel(occasionCount)} · ${mealEntryCountLabel(entryCount)}`
}
const hubMetricIcons: Record<string, string> = {
  dietary_energy_kcal: 'energy',
  protein_g: 'protein',
  carbohydrates_g: 'carbohydrates',
  fat_g: 'fat',
  saturated_fat_g: 'fat',
  sugar_g: 'cube',
  fiber_g: 'fiber',
  vitamin_a_ug: 'carrot',
  calcium_mg: 'diamond',
  iron_mg: 'diamond',
  potassium_mg: 'diamond',
  magnesium_mg: 'diamond',
  phosphorus_mg: 'diamond',
  zinc_mg: 'diamond',
  copper_mg: 'diamond',
  manganese_mg: 'diamond',
  selenium_ug: 'diamond',
  iodine_ug: 'diamond',
  fluoride_mg: 'diamond',
  chloride_mg: 'diamond',
}
const managerBuiltIns: Record<AnalysisCategoryKey, Array<{ id: string; label: string; icon: string }>> = {
  body: [{ id: 'weight', label: t('analysisV2.weightTitle'), icon: 'scale' }],
  activity: [{ id: 'activity', label: t('analysisV2.activeEnergyTitle'), icon: 'fire' }],
  nutrition: [
    { id: 'nutrition', label: t('analysisV2.categories.nutrition'), icon: 'nutrition' },
    { id: 'meals_today', label: t('analysisV2.mealsToday'), icon: 'bowl' },
  ],
  micronutrients: [{ id: 'micronutrients', label: t('analysisV2.categories.micronutrients'), icon: 'atom' }],
  fluid: [],
}
const managerCategoryIcons: Record<AnalysisCategoryKey, string> = {
  body: 'scale',
  activity: 'activity',
  nutrition: 'nutrition',
  micronutrients: 'atom',
  fluid: 'drop',
}
const managerCatalogGroups = computed<ManagerGroup[]>(() => analysisCategories.map((category) => ({
  ...category,
  entries: [
    ...managerBuiltIns[category.key].map((item) => ({ ...item, status: 'available' as const })),
    ...registry.value.flatMap((entry) => {
      if (entry.category_key !== category.key
        || ['weight_kg', 'active_energy_kcal'].includes(entry.analysis_key)
        || (!entry.card_capable && entry.status !== 'coming_soon')) return []
      const presentation = localizedAnalysisPresentation(entry.analysis_key)
      return presentation ? [{
        id: canonicalAnalysisCardId(entry.analysis_key),
        label: presentation.label,
        icon: hubCardIcon(entry.analysis_key),
        status: entry.status,
      }] : []
    }),
  ],
})))
function managerEntryMatches(entry: { id: string; label: string }): boolean {
  const query = managerSearch.value.trim().normalize('NFKC').toLocaleLowerCase()
  if (!query) return true
  return `${entry.label} ${entry.id}`.normalize('NFKC').toLocaleLowerCase().includes(query)
}
const managerGroups = computed(() => managerCatalogGroups.value
  .map((group) => ({
    ...group,
    entries: group.entries
      .filter(managerEntryMatches)
      .sort((left, right) => (
        Number(activeCardIds.value.includes(left.id)) - Number(activeCardIds.value.includes(right.id))
        || Number(left.status !== 'available') - Number(right.status !== 'available')
      )),
  }))
  .filter((group) => group.entries.length > 0))
const managerSearchActive = computed(() => managerSearch.value.trim().length > 0)
const managerVisibleGroups = computed(() => {
  if (managerSearchActive.value) return managerGroups.value
  if (!selectedManagerCategory.value) return []
  return managerGroups.value.filter((group) => group.key === selectedManagerCategory.value)
})
function toggleManagerCategory(key: AnalysisCategoryKey): void {
  selectedManagerCategory.value = selectedManagerCategory.value === key ? null : key
}

function lockManagerPageScroll(): void {
  if (managerPageScrollSnapshot) return
  const root = document.documentElement
  const body = document.body
  const scrollbarWidth = window.innerWidth - root.clientWidth
  const computedPaddingRight = window.getComputedStyle(body).paddingRight
  managerPageScrollSnapshot = {
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    rootOverflow: root.style.overflow,
    rootScrollBehavior: root.style.scrollBehavior,
    bodyOverflow: body.style.overflow,
    bodyPaddingRight: body.style.paddingRight,
  }
  root.style.overflow = 'hidden'
  body.style.overflow = 'hidden'
  if (scrollbarWidth > 0) body.style.paddingRight = `calc(${computedPaddingRight} + ${scrollbarWidth}px)`
}

function restoreManagerPageScroll(): void {
  const snapshot = managerPageScrollSnapshot
  if (!snapshot) return
  const root = document.documentElement
  const body = document.body
  root.style.overflow = snapshot.rootOverflow
  body.style.overflow = snapshot.bodyOverflow
  body.style.paddingRight = snapshot.bodyPaddingRight
  managerPageScrollSnapshot = null
  if (window.scrollX !== snapshot.scrollX || window.scrollY !== snapshot.scrollY) {
    root.style.scrollBehavior = 'auto'
    window.scrollTo(snapshot.scrollX, snapshot.scrollY)
    root.style.scrollBehavior = snapshot.rootScrollBehavior
  }
}

function focusManagerTriggerWhenAvailable(): void {
  void nextTick(() => {
    const trigger = managerTrigger.value
    if (!trigger?.isConnected) return
    if (!trigger.disabled) {
      trigger.focus({ preventScroll: true })
      return
    }
    const stop = watch([saving, workspaceLoading, workspaceReady], () => {
      const availableTrigger = managerTrigger.value
      if (!availableTrigger?.isConnected || availableTrigger.disabled) return
      stop()
      availableTrigger.focus({ preventScroll: true })
    })
  })
}

function openManager(): void {
  const dialog = managerDialog.value
  if (!workspaceReady.value || saving.value || !dialog || dialog.open) return
  managerSearch.value = ''
  selectedManagerCategory.value = null
  managerBackdropPointerId = null
  managerBackdropPointerStartedOutside = false
  managerBackdropClickReady = false
  lockManagerPageScroll()
  try {
    dialog.showModal()
  } catch (error) {
    restoreManagerPageScroll()
    throw error
  }
  void nextTick(() => managerHeading.value?.focus({ preventScroll: true }))
}

function closeManager(): void {
  managerDialog.value?.close()
}
function managerFocusableElements(): HTMLElement[] {
  const dialog = managerDialog.value
  if (!dialog) return []
  return [...dialog.querySelectorAll<HTMLElement>(
    'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
  )].filter((element) => element.getClientRects().length > 0)
}

function handleManagerTab(event: KeyboardEvent): void {
  const focusable = managerFocusableElements()
  if (!focusable.length) {
    event.preventDefault()
    managerHeading.value?.focus({ preventScroll: true })
    return
  }
  const activeIndex = focusable.indexOf(document.activeElement as HTMLElement)
  const lastIndex = focusable.length - 1
  if ((event.shiftKey && activeIndex <= 0) || (!event.shiftKey && activeIndex === lastIndex)) {
    event.preventDefault()
    focusable[event.shiftKey ? lastIndex : 0]?.focus({ preventScroll: true })
  }
}

function handleManagerFocusIn(event: FocusEvent): void {
  const dialog = managerDialog.value
  if (!dialog?.open || (event.target instanceof Node && dialog.contains(event.target))) return
  managerHeading.value?.focus({ preventScroll: true })
}


function managerDialogPointIsOutside(event: { clientX: number; clientY: number }): boolean {
  const rect = managerDialog.value?.getBoundingClientRect()
  return !rect
    || event.clientX < rect.left
    || event.clientX > rect.right
    || event.clientY < rect.top
    || event.clientY > rect.bottom
}

function handleManagerPointerDown(event: PointerEvent): void {
  managerBackdropClickReady = false
  managerBackdropPointerId = event.pointerId
  managerBackdropPointerStartedOutside = event.target === managerDialog.value
    && managerDialogPointIsOutside(event)
}

function handleManagerPointerUp(event: PointerEvent): void {
  managerBackdropClickReady = managerBackdropPointerId === event.pointerId
    && managerBackdropPointerStartedOutside
    && event.target === managerDialog.value
    && managerDialogPointIsOutside(event)
  managerBackdropPointerId = null
  managerBackdropPointerStartedOutside = false
}

function handleManagerPointerCancel(): void {
  managerBackdropPointerId = null
  managerBackdropPointerStartedOutside = false
  managerBackdropClickReady = false
}

function handleManagerClick(event: MouseEvent): void {
  const shouldClose = managerBackdropClickReady
    && event.target === managerDialog.value
    && managerDialogPointIsOutside(event)
  managerBackdropClickReady = false
  if (shouldClose) closeManager()
}

function restoreManagerFocus(): void {
  managerBackdropPointerId = null
  managerBackdropPointerStartedOutside = false
  managerBackdropClickReady = false
  restoreManagerPageScroll()
  focusManagerTriggerWhenAvailable()
}
const cardEntries = computed(() => activeCardIds.value.flatMap((key) => {
  if (['weight', 'nutrition', 'micronutrients', 'activity', 'meals_today'].includes(key)) return []
  const entry = registry.value.find((item) => item.analysis_key === key)
  const presentation = localizedAnalysisPresentation(key)
  return entry && entry.status === 'available' && entry.card_capable && presentation
    ? [{ entry, presentation }]
    : []
}))

const fixedReaderEntries = computed(() => DEFAULT_ACTIVE_CARD_IDS.flatMap((id) => {
  const representative = id === 'weight' ? 'weight_kg'
    : id === 'activity' ? 'active_energy_kcal'
      : id === 'nutrition' ? 'dietary_energy_kcal' : id === 'micronutrients' ? 'vitamin_a_ug' : null
  const entry = representative && registry.value.find((item) => item.analysis_key === representative)
  return entry ? [entry] : []
}))
const capabilityEntries = computed(() => [
  ...fixedReaderEntries.value,
  ...cardEntries.value.map(({ entry }) => entry),
])
const selectedMicronutrientEntries = computed(() => cardEntries.value.filter(({ entry }) =>
  entry.category_key === 'micronutrients' && entry.reader_key === 'analytics.nutrition.micronutrients'))
const summaryNutrients = computed(() => (micronutrientSummary.value?.metrics ?? [])
  .filter((metric) => registry.value.some((entry) =>
    entry.analysis_key === metric.metric_key && entry.category_key === 'micronutrients'
      && entry.status === 'available' && entry.reader_key === 'analytics.nutrition.micronutrients')))
const trendNutrients = computed(() => {
  const candidates = summaryNutrients.value.filter((metric) => metric.days_with_data > 0
    && metric.total !== null && !activeCardIds.value.includes(metric.metric_key))
  const chosen = new Set<string>()
  const categories = new Set<string>()
  while (chosen.size < 3) {
    const remaining = candidates.filter((metric) => !chosen.has(metric.metric_key))
    const next = remaining.find((metric) => !categories.has(metric.category)) ?? remaining[0]
    if (!next) break
    chosen.add(next.metric_key)
    categories.add(next.category)
  }
  return candidates.filter((metric) => chosen.has(metric.metric_key))
})
const nutrientGroups = computed(() => ([
  { key: 'vitamin', labelKey: 'analysisV2.micronutrientTypes.vitamins' },
  { key: 'mineral', labelKey: 'analysisV2.micronutrientTypes.minerals' },
] as const).map((group) => {
  const metrics = summaryNutrients.value.filter((metric) => metric.category === group.key)
  return { ...group, total: metrics.length,
    count: metrics.filter((metric) => metric.days_with_data > 0 && metric.total !== null).length }
}))
const nutritionKpiKeys = ['dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g'] as const
const visibleNutritionKpiKeys = computed(() => nutritionKpiKeys.filter(
  (metric) => metric !== 'dietary_energy_kcal' || !activeCardIds.value.includes(metric),
))
const presentNutrientCount = computed(() => summaryNutrients.value
  .filter((metric) => metric.days_with_data > 0 && metric.total !== null).length)
const selectedReaderEntries = computed(() => [...new Map(cardEntries.value.map(({ entry }) => [
  entry.reader_key ?? entry.analysis_key, entry,
])).values()])
const readerIdentity = computed(() => JSON.stringify([
  selectedReaderEntries.value.map((entry) => entry.reader_key ?? entry.analysis_key).sort(),
  [...new Set(cardEntries.value.map(({ entry }) => entry.analysis_key))].sort(),
  activeCardIds.value.includes('meals_today'),
]))
const limits = computed<PeriodLimits>(() => ({
  minDays: Math.max(1, ...capabilityEntries.value.map((entry) => entry.period_capability?.min_days ?? 1)),
  maxDays: Math.min(365, ...capabilityEntries.value.map((entry) => entry.period_capability?.max_days ?? 365)),
  customRange: capabilityEntries.value.every((entry) => entry.period_capability?.custom_range === true),
  maxDate: today.value,
}))
const currentReaderContracts = computed(() => {
  const byKey = {
    'analytics.weight': readerStates.weight === 'ready' ? weightResponse.value : undefined,
    'analytics.verification.activity': readerStates.activity === 'ready' ? activityResponse.value : undefined,
    'analytics.nutrition.analysis': readerStates.nutrition === 'ready' ? nutritionResponse.value : undefined,
    'analytics.nutrition.micronutrients': readerStates.micronutrients === 'ready' ? micronutrientSummary.value : undefined,
  }
  const requirementsFor = (
    entry: AnalysisRegistryResponse['analyses'][number],
    includeMetricSeries: boolean,
  ) => {
    const bound = entry.period_capability
    const readerLimits: PeriodLimits = {
      minDays: bound?.min_days ?? 1, maxDays: bound?.max_days ?? 365,
      customRange: bound?.custom_range ?? false, maxDate: today.value,
    }
    const reader = entry.reader_key
    const source = reader && Object.hasOwn(byKey, reader)
      ? byKey[reader as keyof typeof byKey] : undefined
    const requirements = [{
      limits: readerLimits, capability: bound ? source?.period_capabilities : undefined,
    }]
    if (includeMetricSeries && reader === 'analytics.nutrition.micronutrients') {
      requirements.push({
        limits: readerLimits,
        capability: bound && micronutrientStates[entry.analysis_key] === 'ready'
          ? micronutrientResponses[entry.analysis_key]?.period_capabilities : undefined,
      })
    }
    return requirements
  }
  return [
    ...fixedReaderEntries.value.flatMap((entry) => requirementsFor(entry, false)),
    ...cardEntries.value.flatMap(({ entry }) => requirementsFor(entry, true)),
  ]
})
const allRange = computed(() => commonAllDataRange(currentReaderContracts.value, today.value))
const capability = computed<ReaderPeriodCapabilities>(() => ({
  supports_all_data: allRange.value !== null,
  all_data_start_date: allRange.value?.start ?? null,
}))
const normalizedSelection = computed(() => periodFromQuery(
  { period: period.value, ...(customRange.value ?? {}) }, limits.value, today.value,
  period.value === 'all' ? committedAllRange.value : allRange.value,
))
const range = computed(() => ({
  start: normalizedSelection.value.start, end: normalizedSelection.value.end,
}))
const visiblePeriod = computed(() => normalizedSelection.value.period)
const periodDays = computed(() => analysisRangeDayCount(range.value))
const displayedActiveCardIds = computed(() => orderPreview.value ?? activeCardIds.value)
const calendarDates = computed(() => Array.from(
  { length: periodDays.value }, (_, index) => shiftIsoDate(range.value.start, index),
))

const orderedActivityDays = computed(() => [...(activityResponse.value?.days ?? [])]
  .sort((left, right) => left.date.localeCompare(right.date)))
const readingsByActivity = computed(() => orderedActivityDays.value.flatMap((day) => {
  const record = day.canonical
  if (record?.status !== 'available' || typeof record.active_energy_kcal !== 'number'
    || !Number.isFinite(record.active_energy_kcal)) return []
  return [{ date: day.date, value: record.active_energy_kcal }]
}))
const activityByDate = computed(() => new Map(
  orderedActivityDays.value.map(({ date, canonical }) => [date, canonical] as const),
))
const activityChartValues = computed(() => calendarDates.value.map((date) => {
  const record = activityByDate.value.get(date)
  return record?.status === 'available'
    && typeof record.active_energy_kcal === 'number'
    && Number.isFinite(record.active_energy_kcal)
    ? record.active_energy_kcal
    : null
}))
const activityChartDates = calendarDates
const weightPoints = computed(() => [...(weightResponse.value?.points ?? [])]
  .filter((point) => typeof point.weight_kg === 'number' && Number.isFinite(point.weight_kg) && point.weight_kg > 0)
  .sort((left, right) => left.date.localeCompare(right.date)))
const weightSummary = computed(() => {
  const values = weightPoints.value.map(({ weight_kg }) => weight_kg)
  if (!values.length) return null
  return {
    count: values.length,
    latest: weightPoints.value[weightPoints.value.length - 1]!,
    average: values.reduce((sum, value) => sum + value, 0) / values.length,
    minimum: Math.min(...values),
    maximum: Math.max(...values),
    change: values.length > 1 ? values[values.length - 1]! - values[0]! : null,
  }
})
const weightChartValues = computed(() => weightPoints.value.map((point) => point.weight_kg))
const weightChartDates = computed(() => weightPoints.value.map((point) => point.date))
const activityAverage = computed(() => {
  const readings = readingsByActivity.value
  return readings.length
    ? readings.reduce((sum, reading) => sum + reading.value, 0) / readings.length
    : null
})
const activityTotal = computed(() => readingsByActivity.value
  .reduce((total, reading) => total + reading.value, 0))
const nutritionDays = computed(() => [...(nutritionResponse.value?.days ?? [])]
  .filter((day) => day.date >= range.value.start && day.date <= range.value.end)
  .sort((left, right) => left.date.localeCompare(right.date)))
const nutritionDaysByDate = computed(() => new Map(nutritionDays.value
  .map((day) => [day.date, day] as const)))
function nutritionChartValue(key: AnalysisNutritionMetricKey, date: string): number | null {
  const value = nutritionDaysByDate.value.get(date)?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
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
  { key: 'protein_g', label: t('analysisV2.nutritionProtein'), colorRole: 'teal' },
  { key: 'carbohydrates_g', label: localizedAnalysisPresentation('carbohydrates_g')?.label ?? 'Kohlenhydrate', colorRole: 'blue' },
  { key: 'fat_g', label: localizedAnalysisPresentation('fat_g')?.label ?? 'Fett', colorRole: 'amber' },
] as const).map(({ key, label, colorRole }) => ({
  key, label, colorRole, values: calendarDates.value.map((date) => nutritionChartValue(key, date)),
})))
const nutritionMacroDateRange = computed(() => hubChartDateRange(
  nutritionMacroSeries.value.map(({ values }) => values),
  calendarDates.value,
))

let previewLoadId = 0

let workspaceLoadId = 0

function currentWorkspace(loadId: number, userId: string): boolean {
  return loadId === workspaceLoadId && auth.user?.id === userId
}

function currentPreview(loadId: number, userId: string): boolean {
  return loadId === previewLoadId && auth.user?.id === userId
}
function normalizePeriodForCapabilities(): void {
  const normalized = normalizedSelection.value
  period.value = normalized.period
  const nextCustomRange = normalized.period === 'custom'
    ? { start: normalized.start, end: normalized.end } : null
  if (customRange.value?.start !== nextCustomRange?.start
    || customRange.value?.end !== nextCustomRange?.end) customRange.value = nextCustomRange
  if (normalized.period !== 'all') committedAllRange.value = null
}

function applyHubPeriod(value: AppliedPeriod): void {
  if (!validPeriodRange(value, limits.value)) return
  const normalized = periodFromQuery(
    { period: value.period, ...(value.period === 'custom' ? { start: value.start, end: value.end } : {}) },
    limits.value, today.value, allRange.value,
  )
  committedAllRange.value = normalized.period === 'all'
    ? { start: normalized.start, end: normalized.end } : null
  period.value = normalized.period
  customRange.value = normalized.period === 'custom'
    ? { start: normalized.start, end: normalized.end } : null
}
let lastPreviewSignature = ''

function invalidateReaderResponses(): void {
  previewLoadId += 1
  lastPreviewSignature = ''
  weightResponse.value = null
  activityResponse.value = null
  nutritionResponse.value = null
  micronutrientSummary.value = null
  mealsTodayResponse.value = null
  mealsTodayState.value = 'idle'
  mealsTodayError.value = ''
  for (const reader of ['weight', 'activity', 'nutrition', 'micronutrients'] as const) {
    readerStates[reader] = 'idle'
    readerErrors[reader] = ''
  }
  for (const key of Object.keys(micronutrientStates)) {
    delete micronutrientResponses[key]
    delete micronutrientStates[key]
    delete micronutrientErrors[key]
  }
}

watch(readerIdentity, () => {
  invalidateReaderResponses()
  committedAllRange.value = null
  if (workspaceReady.value) normalizePeriodForCapabilities()
}, { flush: 'sync' })

async function syncPeriodQuery(): Promise<void> {
  const query = {
    ...analysisHubQuery(visiblePeriod.value, range.value),
    start: visiblePeriod.value === 'custom' ? range.value.start : undefined,
    end: visiblePeriod.value === 'custom' ? range.value.end : undefined,
  }
  if (route.query.period === query.period
    && queryString(route.query.start) === (query.start ?? null)
    && queryString(route.query.end) === (query.end ?? null)) return
  await router.replace({ query: { ...route.query, ...query }, hash: route.hash })
}

async function loadWorkspace(): Promise<void> {
  const userId = auth.user?.id
  const loadId = ++workspaceLoadId
  if (!userId) {
    invalidateReaderResponses()
    registry.value = []
    workspaceReady.value = false
    workspaceLoading.value = false
    workspaceError.value = ''
    return
  }
  workspaceLoading.value = true
  lastPreviewSignature = ''
  workspaceError.value = ''
  try {
    const [registryResponse] = await Promise.all([
      api<AnalysisRegistryResponse>('/analytics/registry'),
      preferences.load(),
    ])
    if (!currentWorkspace(loadId, userId)) return
    registry.value = registryResponse.analyses
    workspaceReady.value = true
    normalizePeriodForCapabilities()
  } catch (cause) {
    if (!currentWorkspace(loadId, userId)) return
    workspaceReady.value = false
    workspaceError.value = localizeApiError(cause, 'analysisV2.loadFailed')
  } finally {
    if (currentWorkspace(loadId, userId)) workspaceLoading.value = false
  }
  if (!currentWorkspace(loadId, userId) || !workspaceReady.value) return
  await syncPeriodQuery()
}
function currentNutritionResponse(): AnalysisNutritionResponse | null {
  return nutritionResponse.value
}


async function loadPreviews(): Promise<void> {
  if (!workspaceReady.value || !auth.user) return
  const userId = auth.user.id
  const { start, end } = range.value
  const params = new URLSearchParams({ start, end }).toString()
  const micronutrientKeys = selectedMicronutrientEntries.value.map(({ entry }) => entry.analysis_key)
  const needsMicronutrients = true
  const requestedReaders: PreviewReader[] = ['weight', 'activity', 'nutrition', 'micronutrients']
  const signature = `${start}|${end}|${requestedReaders.join(',')}|${[...micronutrientKeys].sort().join(',')}`
  if (signature === lastPreviewSignature) return
  lastPreviewSignature = signature
  const loadId = ++previewLoadId
  weightResponse.value = null
  activityResponse.value = null
  nutritionResponse.value = null
  micronutrientSummary.value = null
  mealsTodayResponse.value = null
  mealsTodayError.value = ''
  mealsTodayState.value = activeCardIds.value.includes('meals_today') ? 'loading' : 'idle'
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
  if (needsMicronutrients && !analysisRangeWithinDays(start, end, 365)) {
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
        if (currentPreview(loadId, userId)) weightResponse.value = response
      } else if (reader === 'activity') {
        const response = await api<VerificationActivityResponse>(
          `/analytics/verification/activity?${params}&view=canonical`,
        )
        if (currentPreview(loadId, userId)) activityResponse.value = response
      } else {
        const response = await api<AnalysisNutritionResponse>(`/analytics/nutrition/analysis?${params}`)
        if (currentPreview(loadId, userId)) nutritionResponse.value = response
      }
      if (currentPreview(loadId, userId)) readerStates[reader] = 'ready'
    } catch (cause) {
      if (currentPreview(loadId, userId)) {
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
        if (currentPreview(loadId, userId)) {
          micronutrientResponses[key] = response
          micronutrientStates[key] = 'ready'
        }
      } catch (cause) {
        if (currentPreview(loadId, userId)) {
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
  requests.push((async () => {
    try {
      const response = await api<AnalysisMicronutrientResponse>(
        `/analytics/nutrition/micronutrients?${params}`,
      )
      if (!currentPreview(loadId, userId)) return
      micronutrientSummary.value = response
      readerStates.micronutrients = 'ready'
      await Promise.all(trendNutrients.value
        .filter((metric) => metric.days_with_data > 0)
        .map((metric) => requestMetric(metric.metric_key)))
    } catch (cause) {
      if (currentPreview(loadId, userId)) {
        readerErrors.micronutrients = localizeApiError(cause, 'analysisV2.loadFailed')
        readerStates.micronutrients = 'error'
        lastPreviewSignature = ''
      }
    }
  })())
  await Promise.all(requests)
  if (activeCardIds.value.includes('meals_today')) {
    const currentNutrition = currentNutritionResponse()
    const loadedToday = currentNutrition?.days.some((day) => day.date === today.value)
    if (loadedToday && currentNutrition) {
      mealsTodayResponse.value = currentNutrition
      mealsTodayState.value = 'ready'
    } else {
      try {
        const todayParams = new URLSearchParams({ start: today.value, end: today.value })
        const response = await api<AnalysisNutritionResponse>(
          `/analytics/nutrition/analysis?${todayParams.toString()}`,
        )
        if (currentPreview(loadId, userId)) {
          mealsTodayResponse.value = response
          mealsTodayState.value = 'ready'
        }
      } catch (cause) {
        if (currentPreview(loadId, userId)) {
          mealsTodayError.value = localizeApiError(cause, 'analysisV2.loadFailed')
          mealsTodayState.value = 'error'
          lastPreviewSignature = ''
        }
      }
    }
  }
  const hasRequiredReaderError = readerStates.weight === 'error'
    || readerStates.activity === 'error'
    || readerStates.nutrition === 'error'
    || readerStates.micronutrients === 'error'
    || selectedMicronutrientEntries.value.some(({ entry }) => micronutrientStates[entry.analysis_key] === 'error')
  if (currentPreview(loadId, userId) && period.value === 'all' && !allRange.value && !hasRequiredReaderError) {
    committedAllRange.value = null
    period.value = '30'
    customRange.value = null
    await syncPeriodQuery()
    await loadPreviews()
  }
}

function activeOrder(): string[] {
  return [...displayedActiveCardIds.value]
}

function visibleHubOrder(order: readonly string[]): string[] {
  return order.filter((key) => !hiddenHubCardIds.has(key))
}

function adjacentVisibleHubCard(index: number): string | undefined {
  const visible = visibleHubOrder(activeCardIds.value)
  return visible[Math.min(index, visible.length - 1)]
}

function canMoveKey(key: string, offset: -1 | 1): boolean {
  const visible = visibleHubOrder(activeOrder())
  const index = visible.indexOf(key)
  return index >= 0 && index + offset >= 0 && index + offset < visible.length
}

function persistOrder(next: string[]): Promise<boolean> {
  return preferences.replaceActiveCardIds(next)
}

async function moveKey(key: string, offset: -1 | 1): Promise<void> {
  if (!workspaceReady.value || saving.value || !canMoveKey(key, offset)) {
    closeOverflow(true)
    return
  }
  const next = activeOrder()
  const visible = visibleHubOrder(next)
  const index = visible.indexOf(key)
  visible.splice(index, 1)
  visible.splice(index + offset, 0, key)
  let visibleIndex = 0
  const reordered = next.map((cardId) => (
    hiddenHubCardIds.has(cardId) ? cardId : visible[visibleIndex++]!
  ))
  void persistOrder(reordered)
  await nextTick()
  closeOverflow(true)
}

async function removeKey(key: string, source: 'hub' | 'manager' = 'hub'): Promise<void> {
  const canonicalKey = canonicalAnalysisCardId(key)
  const index = activeCardIds.value.indexOf(canonicalKey)
  if (!workspaceReady.value || workspaceLoading.value || saving.value) {
    closeOverflow(source === 'hub')
    return
  }
  closeOverflow()
  const update = preferences.replaceActiveCardIds(
    activeCardIds.value.filter((item) => item !== canonicalKey),
  )
  if (source === 'hub') {
    const visibleIndex = visibleHubOrder(activeCardIds.value.slice(0, index)).length
    await nextTick()
    focusHubCardAction(adjacentVisibleHubCard(visibleIndex))
  }
  await update
  if (source === 'manager') {
    await nextTick()
    focusManagerAction(canonicalKey)
  } else {
    const visibleIndex = visibleHubOrder(activeCardIds.value.slice(0, index)).length
    await nextTick()
    focusHubCardAction(adjacentVisibleHubCard(visibleIndex))
  }
}

async function addKey(key: string): Promise<void> {
  const canonicalKey = canonicalAnalysisCardId(key)
  if (saving.value || activeCardIds.value.includes(canonicalKey)) return
  await preferences.replaceActiveCardIds(addAnalysisPreference(activeCardIds.value, canonicalKey))
  await nextTick()
  focusManagerAction(canonicalKey)
}

function focusManagerAction(key: string): void {
  if (!managerDialog.value?.open) {
    managerTrigger.value?.focus()
    return
  }
  const action = [...managerDialog.value.querySelectorAll<HTMLButtonElement>('[data-manager-action]')]
    .find((button) => button.dataset.managerAction === key)
  action?.focus()
}

function focusHubCardAction(key?: string): void {
  if (!key) {
    managerTrigger.value?.focus()
    return
  }
  const card = [...document.querySelectorAll<HTMLElement>('.analysis-card[data-card-key]')]
    .find((element) => element.dataset.cardKey === key)
  const trigger = card?.querySelector<HTMLButtonElement>('.analysis-overflow-trigger')
  if (trigger) trigger.focus()
  else managerTrigger.value?.focus()
}

function closeOverflow(restoreFocus = false): void {
  const key = openMenuCard.value
  openMenuCard.value = null
  if (!restoreFocus || !key) return
  const card = [...document.querySelectorAll<HTMLElement>('.analysis-card[data-card-key]')]
    .find((element) => element.dataset.cardKey === key)
  card?.querySelector<HTMLElement>('.analysis-overflow-trigger')?.focus()
}

function toggleOverflow(key: string): void {
  openMenuCard.value = openMenuCard.value === key ? null : key
}

function outsideOverflow(event: PointerEvent): void {
  if (openMenuCard.value && !(event.target as Element | null)?.closest('.analysis-overflow')) closeOverflow()
}

function escapeOverflow(event: KeyboardEvent): void {
  if (event.key === 'Escape' && openMenuCard.value) {
    event.preventDefault()
    closeOverflow(true)
  }
}

function pointerDown(key: string, event: PointerEvent): void {
  if (!workspaceReady.value || !event.isPrimary || (event.button !== undefined && event.button !== 0) || saving.value) return
  closeOverflow()
  const card = (event.currentTarget as HTMLElement | null)?.closest<HTMLElement>('.analysis-card[data-card-key]')
  const grid = card?.closest<HTMLElement>('.analysis-card-list')
  const rect = card?.getBoundingClientRect()
  if (!card || !grid || !rect) return
  pointerDrag.value = key
  pointerDragId.value = event.pointerId
  pointerDragPointer = { x: event.clientX, y: event.clientY }
  pointerDragOriginBounds = { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom }
  pointerDragOrigin = { x: event.clientX, y: event.clientY }
  pointerDragOriginScroll = { x: window.scrollX, y: window.scrollY }
  pointerDragGrabOffset = { x: event.clientX - rect.left, y: event.clientY - rect.top }
  pointerDragOffset = { x: 0, y: -pointerDragLift }
  orderPreview.value = [...activeCardIds.value]
  insertionTarget.value = null
  if (pointerDragAnimationFrame !== null) cancelAnimationFrame(pointerDragAnimationFrame)
  pointerDragAnimationFrame = requestAnimationFrame(trackPointerDrag)
}

function syncPointerDragOffset(): void {
  if (pointerDrag.value === null) return
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
  if (pointerDrag.value === null) {
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
  if (pointerDrag.value !== key) return undefined
  return {
    '--drag-x': `${pointerDragOffset.x}px`,
    '--drag-y': `${pointerDragOffset.y}px`,
  }
}

type HubCardBounds = {
  left: number
  top: number
  right: number
  bottom: number
  width: number
  height: number
}

function logicalCardBounds(card: HTMLElement): HubCardBounds {
  const rect = card.getBoundingClientRect()
  const transform = window.getComputedStyle(card).transform
  const matrix = transform !== 'none' && typeof DOMMatrixReadOnly !== 'undefined'
    ? new DOMMatrixReadOnly(transform)
    : null
  const width = card.offsetWidth || rect.width
  const height = card.offsetHeight || rect.height
  const left = rect.left - (matrix?.m41 ?? 0)
  const top = rect.top - (matrix?.m42 ?? 0)
  return { left, top, right: left + width, bottom: top + height, width, height }
}

function firstPointerContact(
  from: { x: number; y: number },
  to: { x: number; y: number },
  target: HubCardBounds,
): number | null {
  const deltaX = to.x - from.x
  const deltaY = to.y - from.y
  let entryX = Number.NEGATIVE_INFINITY
  let exitX = Number.POSITIVE_INFINITY
  let entryY = Number.NEGATIVE_INFINITY
  let exitY = Number.POSITIVE_INFINITY

  if (deltaX > 0) {
    entryX = (target.left - from.x) / deltaX
    exitX = (target.right - from.x) / deltaX
  } else if (deltaX < 0) {
    entryX = (target.right - from.x) / deltaX
    exitX = (target.left - from.x) / deltaX
  } else if (from.x < target.left || from.x > target.right) {
    return null
  }

  if (deltaY > 0) {
    entryY = (target.top - from.y) / deltaY
    exitY = (target.bottom - from.y) / deltaY
  } else if (deltaY < 0) {
    entryY = (target.bottom - from.y) / deltaY
    exitY = (target.top - from.y) / deltaY
  } else if (from.y < target.top || from.y > target.bottom) {
    return null
  }

  const entry = Math.max(entryX, entryY)
  const exit = Math.min(exitX, exitY)
  return entry >= 0 && entry <= 1 && entry <= exit ? entry : null
}
function centerCrossingTime(from: number, to: number, target: number): number | null {
  const delta = to - from
  if (delta > 0 && from < target && to >= target) return (target - from) / delta
  if (delta < 0 && from > target && to <= target) return (target - from) / delta
  return null
}

function dragOriginCrossingTime(
  from: { x: number; y: number },
  to: { x: number; y: number },
  origin: { x: number; y: number },
  originBounds: PointerDragOriginBounds,
  width: number,
  height: number,
): number | null {
  const horizontalProgress = Math.abs(from.x - origin.x) / Math.max(width, 1)
  const verticalProgress = Math.abs(from.y - origin.y) / Math.max(height, 1)
  const crossing = horizontalProgress >= verticalProgress
    ? centerCrossingTime(from.x, to.x, origin.x)
    : centerCrossingTime(from.y, to.y, origin.y)
  if (crossing === null) return null
  const crossingX = from.x + (to.x - from.x) * crossing
  const crossingY = from.y + (to.y - from.y) * crossing
  return crossingX >= originBounds.left && crossingX <= originBounds.right
    && crossingY >= originBounds.top && crossingY <= originBounds.bottom
    ? crossing
    : null
}

function boundsOverlap(left: HubCardBounds, right: HubCardBounds): boolean {
  return left.left < right.right && left.right > right.left
    && left.top < right.bottom && left.bottom > right.top
}

function isMovingTowardTargetCenter(from: HubCardBounds, to: HubCardBounds, target: HubCardBounds): boolean {
  const deltaX = to.left - from.left
  const deltaY = to.top - from.top
  if (Math.abs(deltaX) / target.width >= Math.abs(deltaY) / target.height) {
    const fromCenter = from.left + from.width / 2
    const targetCenter = target.left + target.width / 2
    return (deltaX > 0 && fromCenter < targetCenter)
      || (deltaX < 0 && fromCenter > targetCenter)
  }
  const fromCenter = from.top + from.height / 2
  const targetCenter = target.top + target.height / 2
  return (deltaY > 0 && fromCenter < targetCenter)
    || (deltaY < 0 && fromCenter > targetCenter)
}

function firstDragContact(from: HubCardBounds, to: HubCardBounds, target: HubCardBounds): number | null {
  if (boundsOverlap(from, target)) {
    const deltaX = to.left - from.left
    const deltaY = to.top - from.top
    if (Math.abs(deltaX) / target.width >= Math.abs(deltaY) / target.height) {
      return centerCrossingTime(
        from.left + from.width / 2,
        to.left + to.width / 2,
        target.left + target.width / 2,
      )
    }
    return centerCrossingTime(
      from.top + from.height / 2,
      to.top + to.height / 2,
      target.top + target.height / 2,
    )
  }
  const deltaX = to.left - from.left
  const deltaY = to.top - from.top
  let entryX = Number.NEGATIVE_INFINITY
  let exitX = Number.POSITIVE_INFINITY
  let entryY = Number.NEGATIVE_INFINITY
  let exitY = Number.POSITIVE_INFINITY

  if (deltaX > 0) {
    entryX = (target.left - from.right) / deltaX
    exitX = (target.right - from.left) / deltaX
  } else if (deltaX < 0) {
    entryX = (target.right - from.left) / deltaX
    exitX = (target.left - from.right) / deltaX
  } else if (from.right <= target.left || from.left >= target.right) {
    return null
  }

  if (deltaY > 0) {
    entryY = (target.top - from.bottom) / deltaY
    exitY = (target.bottom - from.top) / deltaY
  } else if (deltaY < 0) {
    entryY = (target.bottom - from.top) / deltaY
    exitY = (target.top - from.bottom) / deltaY
  } else if (from.bottom <= target.top || from.top >= target.bottom) {
    return null
  }

  const entry = Math.max(entryX, entryY)
  const exit = Math.min(exitX, exitY)
  return entry >= 0 && entry <= 1 && entry <= exit ? entry : null
}

function dragRectAtPointer(pointer: { x: number; y: number }, width: number, height: number): HubCardBounds {
  const left = pointer.x - pointerDragGrabOffset.x
  const top = pointer.y - pointerDragGrabOffset.y - pointerDragLift
  return { left, top, right: left + width, bottom: top + height, width, height }
}

async function updatePointerPreview(event: PointerEvent): Promise<void> {
  const draggingKey = pointerDrag.value
  if (!draggingKey || pointerDragId.value !== event.pointerId
    || typeof document.elementFromPoint !== 'function') return

  const previousPointer = pointerDragPointer
  const currentPointer = { x: event.clientX, y: event.clientY }
  pointerDragPointer = currentPointer
  const originalOrder = activeCardIds.value
  const preview = orderPreview.value
  const previewChanged = preview !== null && preview.some((key, index) => key !== originalOrder[index])
  const draggingElement = document.querySelector<HTMLElement>('.analysis-card.is-dragging')
  if (previewChanged && draggingElement && pointerDragOriginBounds) {
    const rect = draggingElement.getBoundingClientRect()
    const scrollDeltaX = window.scrollX - pointerDragOriginScroll.x
    const scrollDeltaY = window.scrollY - pointerDragOriginScroll.y
    const origin = {
      x: pointerDragOrigin.x - scrollDeltaX,
      y: pointerDragOrigin.y - scrollDeltaY,
    }
    const originBounds = {
      left: pointerDragOriginBounds.left - scrollDeltaX,
      right: pointerDragOriginBounds.right - scrollDeltaX,
      top: pointerDragOriginBounds.top - scrollDeltaY,
      bottom: pointerDragOriginBounds.bottom - scrollDeltaY,
    }
    const crossing = dragOriginCrossingTime(
      previousPointer, currentPointer, origin,
      originBounds, rect.width, rect.height,
    )
    if (crossing !== null) {
      orderPreview.value = null
      insertionTarget.value = null
      const resumedFrom = {
        x: previousPointer.x + (currentPointer.x - previousPointer.x) * crossing,
        y: previousPointer.y + (currentPointer.y - previousPointer.y) * crossing,
      }
      await nextTick()
      if (pointerDrag.value !== draggingKey || pointerDragId.value !== event.pointerId) return
      updatePointerPreviewSegment(draggingKey, resumedFrom, currentPointer)
      return
    }
  }
  updatePointerPreviewSegment(draggingKey, previousPointer, currentPointer)
}

function updatePointerPreviewSegment(
  draggingKey: string,
  previousPointer: { x: number; y: number },
  currentPointer: { x: number; y: number },
): void {
  const hit = document.elementFromPoint(currentPointer.x, currentPointer.y)
  const grid = hit?.closest<HTMLElement>('.analysis-card-list')
  if (!grid) {
    insertionTarget.value = null
    void nextTick(syncPointerDragOffset)
    return
  }

  if (previousPointer.x === currentPointer.x && previousPointer.y === currentPointer.y) {
    void nextTick(syncPointerDragOffset)
    return
  }

  const elements = [...grid.querySelectorAll<HTMLElement>('.analysis-card[data-card-key]')]
  const draggingCard = elements.find((card) => card.dataset.cardKey === draggingKey)
  if (!draggingCard) return
  const rect = draggingCard.getBoundingClientRect()
  const width = draggingCard.offsetWidth || rect.width
  const height = draggingCard.offsetHeight || rect.height
  const from = dragRectAtPointer(previousPointer, width, height)
  const to = dragRectAtPointer(currentPointer, width, height)
  const current = activeOrder()
  const sourceIndex = current.indexOf(draggingKey)
  if (sourceIndex < 0) return

  const hiddenKeys = new Set(elements
    .filter((card) => card !== draggingCard && window.getComputedStyle(card).display === 'none')
    .map((card) => card.dataset.cardKey)
    .filter((key): key is string => Boolean(key)))
  const visibleCurrent = current.filter((key) => !hiddenKeys.has(key))
  const sourceVisibleIndex = visibleCurrent.indexOf(draggingKey)
  if (sourceVisibleIndex < 0) return
  const visibleCards = visibleCurrent.map((key) => elements.find((card) => card.dataset.cardKey === key))
  if (visibleCards.some((card) => !card)) return
  const slots = visibleCards.map((card) => logicalCardBounds(card!))
  const hitKey = hit?.closest<HTMLElement>('.analysis-card[data-card-key]')?.dataset.cardKey

  let pointerCrossedSlots = 0
  for (let slotIndex = 0; slotIndex < slots.length; slotIndex += 1) {
    if (slotIndex !== sourceVisibleIndex
      && firstPointerContact(previousPointer, currentPointer, slots[slotIndex]) !== null) {
      pointerCrossedSlots += 1
    }
  }
  const preferLatestContact = pointerCrossedSlots > 1

  let destination: { key: string; orderIndex: number; slotIndex: number; contact: number } | null = null
  for (let slotIndex = 0; slotIndex < slots.length; slotIndex += 1) {
    if (slotIndex === sourceVisibleIndex) continue
    const key = visibleCurrent[slotIndex]
    if (!key) continue
    const orderIndex = current.indexOf(key)
    let contact = firstDragContact(from, to, slots[slotIndex])
    const pointerContact = firstPointerContact(previousPointer, currentPointer, slots[slotIndex])
    if (contact === null && key === hitKey && pointerContact !== null
      && boundsOverlap(from, slots[slotIndex])
      && boundsOverlap(to, slots[slotIndex])
      && isMovingTowardTargetCenter(from, to, slots[slotIndex])) {
      contact = pointerContact
    }
    if (orderIndex < 0 || contact === null) continue
    const improvesContact = destination === null || (preferLatestContact
      ? contact > destination.contact
      : contact < destination.contact)
    const winsPointerTie = destination !== null && contact === destination.contact
      && key === hitKey && destination.key !== hitKey
    if (!improvesContact && !winsPointerTie) continue
    destination = { key, orderIndex, slotIndex, contact }
  }

  if (!destination) {
    void nextTick(syncPointerDragOffset)
    return
  }

  const next = [...current]
  next.splice(sourceIndex, 1)
  next.splice(destination.orderIndex, 0, draggingKey)
  orderPreview.value = next
  insertionTarget.value = {
    key: destination.key,
    slotIndex: next.filter((key) => !hiddenKeys.has(key)).indexOf(draggingKey),
  }
  void nextTick(syncPointerDragOffset)
}

async function finishPointerDrag(event: PointerEvent): Promise<void> {
  const draggingKey = pointerDrag.value
  if (!draggingKey || pointerDragId.value !== event.pointerId) return
  await updatePointerPreview(event)
  const hit = typeof document.elementFromPoint === 'function'
    ? document.elementFromPoint(event.clientX, event.clientY) : null
  const listSelector = '.analysis-card-list'
  const insideList = Boolean(hit?.closest(listSelector))
  const next = orderPreview.value
  const original = activeCardIds.value
  const changed = next !== null && next.length === original.length
    && next.some((key, index) => key !== original[index])
  const shouldSave = insideList && insertionTarget.value !== null
    && next !== null && changed && !saving.value
  stopPointerDragTracking()
  pointerDrag.value = null
  pointerDragOriginBounds = null
  pointerDragOriginScroll = { x: 0, y: 0 }
  orderPreview.value = null
  insertionTarget.value = null
  if (shouldSave && next) await persistOrder(next)
}

function discardPointerDrag(): void {
  stopPointerDragTracking()
  pointerDrag.value = null
  pointerDragId.value = null
  orderPreview.value = null
  pointerDragOriginBounds = null
  pointerDragOriginScroll = { x: 0, y: 0 }
  insertionTarget.value = null
}

function cancelPointerDrag(event: PointerEvent): void {
  if (pointerDragId.value !== null && pointerDragId.value !== event.pointerId) return
  discardPointerDrag()
}

function entryRoute(key: string) {
  return { path: analysisHref(key), query: handoffQuery(), hash: route.hash }
}

function handoffQuery() {
  const query = { ...route.query }
  delete query.start
  delete query.end
  return { ...query, ...analysisHubQuery(visiblePeriod.value, range.value) }
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
    hash: key === 'fluid' ? '#analysis-category-fluid' : route.hash,
    query: handoffQuery(),
  }
}
const summaryReaders: Readonly<Record<BuiltInCardId, PreviewReader>> = {
  weight: 'weight',
  nutrition: 'nutrition',
  micronutrients: 'micronutrients',
  activity: 'activity',
}

function summaryLabel(id: BuiltInCardId): string {
  if (id === 'weight') return t('analysisV2.weightTitle')
  if (id === 'activity') return t('analysisV2.activeEnergyTitle')
  return t(`analysisV2.categories.${id}`)
}

function summaryIcon(id: BuiltInCardId): string {
  return id === 'weight' ? 'scale'
    : id === 'activity' ? 'fire'
      : id === 'nutrition' ? 'nutrition' : 'atom'
}

function summaryRoute(id: BuiltInCardId) {
  if (id === 'weight') return entryRoute('weight_kg')
  if (id === 'nutrition') return categoryRoute('nutrition')
  if (id === 'micronutrients') return categoryRoute('micronutrients')
  return entryRoute('active_energy_kcal')
}

function summaryState(id: BuiltInCardId): 'loading' | 'empty' | 'ready' | 'error' {
  if (workspaceError.value) return 'error'
  if (!workspaceReady.value) return 'loading'
  const state = readerStates[summaryReaders[id]]
  if (state === 'error') return 'error'
  if (state !== 'ready') return 'loading'
  if (id === 'weight') return weightPoints.value.length ? 'ready' : 'empty'
  if (id === 'activity') return readingsByActivity.value.length ? 'ready' : 'empty'
  if (id === 'nutrition') {
    return nutritionAnalysisKeys.some((key) => nutritionStats.value.get(key)!.count > 0)
      ? 'ready' : 'empty'
  }
  return presentNutrientCount.value > 0 ? 'ready' : 'empty'
}

function isBuiltInCardId(key: string): key is BuiltInCardId {
  return key === 'weight' || key === 'nutrition' || key === 'micronutrients' || key === 'activity'
}


function cardState(key: string): ReaderState {
  if (key === 'weight' || key === 'weight_kg') return readerStates.weight
  if (key === 'activity' || key === 'active_energy_kcal') return readerStates.activity
  if (key === 'meals_today') return mealsTodayState.value
  if (key === 'nutrition' || isNutritionAnalysisKey(key)) return readerStates.nutrition
  if (key === 'micronutrients') return readerStates.micronutrients
  return micronutrientStates[key] ?? 'idle'
}

function cardError(key: string): string {
  if (workspaceError.value && !isBuiltInCardId(key)) return workspaceError.value
  if (key === 'weight' || key === 'weight_kg') return readerErrors.weight
  if (key === 'activity' || key === 'active_energy_kcal') return readerErrors.activity
  if (key === 'meals_today') return mealsTodayError.value
  if (key === 'nutrition' || isNutritionAnalysisKey(key)) return readerErrors.nutrition
  if (key === 'micronutrients') return readerErrors.micronutrients
  return micronutrientErrors[key] ?? ''
}

function metricCardState(key: string): 'loading' | 'empty' | 'ready' | 'error' {
  if (workspaceError.value) return 'error'
  const state = cardState(key)
  if (state === 'error') return 'error'
  if (state !== 'ready') return 'loading'
  if (['weight', 'activity', 'nutrition', 'micronutrients'].includes(key)) return 'ready'
  if (key === 'meals_today') return mealsTodayDay.value ? 'ready' : 'empty'
  return cardHasReadings(key) ? 'ready' : 'empty'
}

function hubCardState(key: string): 'loading' | 'empty' | 'ready' | 'error' {
  if (isBuiltInCardId(key)) return summaryState(key)
  return metricCardState(key)
}
function hubCardLabel(key: string): string {
  if (isBuiltInCardId(key)) return summaryLabel(key)
  if (key === 'meals_today') return t('analysisV2.mealsToday')
  return localizedAnalysisPresentation(key)?.label ?? key
}

function hubCardIcon(key: string): string {
  if (isBuiltInCardId(key)) return summaryIcon(key)
  if (key === 'meals_today') return 'bowl'
  return hubMetricIcons[key] ?? localizedAnalysisPresentation(key)?.icon ?? 'leaf'
}


function hubCardRoute(key: string) {
  if (key === 'meals_today') return { name: 'nutrition', query: { date: today.value } }
  if (isBuiltInCardId(key)) return summaryRoute(key)
  return entryRoute(key)
}
function retryHubReaders(): void {
  if (workspaceReady.value) void loadPreviews()
  else void loadWorkspace()
}

function cardMetric(key: string) {
  return micronutrientResponses[key]?.metrics.find((metric) => metric.metric_key === key) ?? null
}

function countLabel(count: number, singularKey: string, pluralKey: string): string {
  return t(count === 1 ? singularKey : pluralKey, { count: number.format(count) })
}

function weightMeasurementCount(): string {
  return countLabel(
    weightPoints.value.length,
    'analysisV2.hubWeightMeasurement', 'analysisV2.hubWeightMeasurements',
  )
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


function cardValue(key: string): string | null {
  if (key === 'weight' || key === 'weight_kg') {
    const latest = weightSummary.value?.latest
    return latest ? `${number.format(latest.weight_kg)} kg` : null
  }
  if (key === 'active_energy_kcal') {
    return activityAverage.value === null ? null : `${kcal.format(activityAverage.value)} kcal`
  }
  if (isNutritionAnalysisKey(key)) {
    const average = nutritionAverage(key)
    if (average === null) return null
    return `${key === 'dietary_energy_kcal' ? kcal.format(average) : number.format(average)} ${key === 'dietary_energy_kcal' ? 'kcal' : 'g'}`
  }
  const metric = cardMetric(key)
  if (!metric || metric.days_with_data === 0 || metric.total === null) return null
  const average = metric.total / metric.days_with_data
  const unit: string = metric.unit
  return `${unit === 'kcal' ? kcal.format(average) : number.format(average)} ${unit}`
}

function weightChange(): string | null {
  const change = weightSummary.value?.change
  if (typeof change !== 'number' || !Number.isFinite(change)) return null
  return `${change > 0 ? '+' : ''}${number.format(change)} kg`
}


function cardHasReadings(key: string): boolean {
  if (key === 'weight_kg') return weightPoints.value.length > 0
  if (key === 'active_energy_kcal') return readingsByActivity.value.length > 0
  if (isNutritionAnalysisKey(key)) return nutritionStats.value.get(key)!.count > 0
  return (cardMetric(key)?.days_with_data ?? 0) > 0
}

function cardChartValues(key: string): Array<number | null> {
  if (key === 'weight_kg') return weightChartValues.value
  if (key === 'active_energy_kcal') return activityChartValues.value
  if (isNutritionAnalysisKey(key)) {
    return calendarDates.value.map((date) => nutritionChartValue(key, date))
  }
  const valuesByDate = new Map(
    (micronutrientResponses[key]?.series ?? []).map(({ date, value }) => [date, value] as const),
  )
  return calendarDates.value.map((date) => valuesByDate.get(date) ?? null)
}

function cardChartDates(): Array<string | null> {
  return calendarDates.value
}
function hubLineDomainMinimumStep(key: string): number {
  return key === 'dietary_energy_kcal' ? 1 : 0.1
}

function hubMetricChartVariant(
  key: string,
  values: readonly (number | null)[],
  dates: readonly (string | null)[],
): 'line' | 'bars' {
  switch (localizedAnalysisPresentation(key)?.route) {
    case 'weight': return 'line'
    case 'active-energy': return 'bars'
    case 'micronutrient': return hubMiniChartVariant(values, dates)
    case 'nutrition': return key === 'dietary_energy_kcal' ? 'line' : hubMiniChartVariant(values, dates)
    default: return 'line'
  }
}
function hubChartDateRange(
  series: readonly (readonly (number | null)[])[],
  dates: readonly (string | null)[],
): { start: string; end: string } {
  let firstValueIndex = -1
  let lastValueIndex = -1
  for (let index = 0; index < dates.length; index += 1) {
    const hasValue = series.some((values) => {
      const value = values[index]
      return typeof value === 'number' && Number.isFinite(value)
    })
    if (!hasValue) continue
    if (firstValueIndex === -1) firstValueIndex = index
    lastValueIndex = index
  }
  const firstValueDate = dates[firstValueIndex]?.slice(0, 10)
  const lastValueDate = dates[lastValueIndex]?.slice(0, 10)
  return firstValueDate && lastValueDate
    && firstValueDate >= range.value.start && firstValueDate <= range.value.end
    && lastValueDate >= firstValueDate && lastValueDate <= range.value.end
    ? { start: firstValueDate, end: lastValueDate }
    : range.value
}

function cardShowsDailyAverage(cardId: string): boolean {
  if (cardId === 'activity') return true
  return cardId !== 'weight'
    && cardId !== 'micronutrients'
    && cardId !== 'meals_today'
    && cardId !== 'weight_kg'
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

async function scrollToCategoryHash(): Promise<void> {
  const id = route.hash.slice(1)
  if (!id.startsWith('analysis-category-')) return
  await nextTick()
  document.getElementById(id)?.scrollIntoView?.({ block: 'start' })
}

watch(
  () => [route.query.period, route.query.start, route.query.end] as const,
  () => {
    const normalized = periodFromQuery(
      route.query,
      workspaceReady.value ? limits.value
        : { minDays: 1, maxDays: 3661, customRange: true, maxDate: today.value },
      today.value,
      workspaceReady.value
        ? (period.value === 'all' ? committedAllRange.value : allRange.value) : null,
    )
    const nextCustomRange = normalized.period === 'custom'
      ? { start: normalized.start, end: normalized.end } : null
    committedAllRange.value = normalized.period === 'all'
      ? { start: normalized.start, end: normalized.end } : null
    if (period.value !== normalized.period || customRange.value?.start !== nextCustomRange?.start
      || customRange.value?.end !== nextCustomRange?.end) {
      period.value = normalized.period
      customRange.value = nextCustomRange
    }
    if (workspaceReady.value) void syncPeriodQuery()
  },
)
watch([() => auth.user?.id, () => auth.user?.timezone], () => {
  workspaceLoadId += 1
  discardPointerDrag()
  invalidateReaderResponses()
  registry.value = []
  workspaceReady.value = false
  workspaceError.value = ''
  committedAllRange.value = null
  if (auth.user) void loadWorkspace()
  else workspaceLoading.value = false
}, { flush: 'sync' })

watch(() => route.hash, () => { void scrollToCategoryHash() })
watch(() => route.fullPath, () => { closeOverflow() })

watch(
  [period, customRange, readerIdentity, workspaceReady],
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
  window.addEventListener('pointerdown', outsideOverflow)
  window.addEventListener('keydown', escapeOverflow)
  await loadWorkspace()
  await scrollToCategoryHash()
})

onBeforeUnmount(() => {
  previewLoadId += 1
  workspaceLoadId += 1
  window.removeEventListener('pointermove', updatePointerPreview)
  window.removeEventListener('pointerup', finishPointerDrag)
  window.removeEventListener('pointercancel', cancelPointerDrag)
  window.removeEventListener('pointerdown', outsideOverflow)
  window.removeEventListener('keydown', escapeOverflow)
  stopPointerDragTracking()
  restoreManagerPageScroll()
})
</script>

<template>
  <div class="analysis-hub">
    <section class="analysis-header page-heading page-heading--period">
      <div class="analysis-heading-copy">
        <h1>{{ t('analysisV2.title') }}</h1>
      </div>
      <PeriodPicker
        v-if="workspaceReady"
        :start="range.start"
        :end="range.end"
        :today="today"
        :period="visiblePeriod"
        :min-days="limits.minDays"
        :max-days="limits.maxDays"
        :custom-range="limits.customRange"
        :max-date="limits.maxDate"
        :period-capabilities="capability"
        :all-range="allRange"
        @apply="applyHubPeriod"
      />
    </section>

    <div v-if="workspaceLoading" class="dashboard-loading" role="status" aria-live="polite">
      {{ t('analysisV2.loading') }}
    </div>
    <section v-else-if="workspaceError" class="card error analysis-workspace-error" role="alert">
      <p>{{ workspaceError }}</p>
      <button class="button secondary" type="button" @click="loadWorkspace">{{ t('analysisV2.retry') }}</button>
    </section>
    <section v-if="saveError" class="card error analysis-save-error" role="alert" aria-live="assertive">
      {{ saveError }}
    </section>

    <section class="analysis-section" aria-labelledby="analysis-summary-title">
      <div class="analysis-list-heading">
        <h2 id="analysis-summary-title">{{ t('analysisV2.myAnalyses') }}</h2>
      </div>
      <TransitionGroup
        tag="ol"
        name="analysis-card-reorder"
        class="analysis-card-list"
        :aria-label="t('analysisV2.myAnalyses')"
      >
        <AnalysisHubCard
          v-for="cardId in displayedActiveCardIds"
          v-show="!hiddenHubCardIds.has(cardId)"
          :key="cardId"
          :group="['weight', 'nutrition', 'micronutrients', 'activity'].includes(cardId) ? 'summary' : 'metric'"
          :card-key="cardId"
          :label="hubCardLabel(cardId)"
          :icon="hubCardIcon(cardId)"
          :to="hubCardRoute(cardId)"
          :daily-average="cardShowsDailyAverage(cardId)"
          :state="hubCardState(cardId)"
          :error="cardError(cardId)"
          :saving="saving"
          :empty-message="cardId === 'weight' ? t('analysisV2.hubWeightEmpty') : t('analysisV2.hubNoDataPeriod')"
          :menu-open="openMenuCard === cardId"
          :can-move-before="workspaceReady && canMoveKey(cardId, -1)"
          :can-move-after="workspaceReady && canMoveKey(cardId, 1)"
          :removable="workspaceReady && !workspaceLoading"
          :dragging="pointerDrag === cardId"
          :style="dragStyle(cardId)"
          @drag-start="pointerDown(cardId, $event)"
          @toggle-menu="toggleOverflow(cardId)"
          @move="moveKey(cardId, $event)"
          @remove="removeKey(cardId, 'hub')"
          @retry="retryHubReaders"
        >
          <template #default>
            <template v-if="cardId === 'weight'">
              <strong v-if="weightPoints.length" class="analysis-metric-value">{{ cardValue('weight_kg') ?? '–' }}</strong>
              <span v-if="weightPoints.length" class="analysis-card-extra">{{ t('analysisV2.change') }} {{ weightChange() ?? '–' }}</span>
              <AnalysisMiniChart
                v-if="cardHasChartData('weight_kg')"
                class="analysis-card-chart"
                :values="weightChartValues" :dates="weightChartDates" :date-range="hubChartDateRange([weightChartValues], weightChartDates)"
                :label="chartLabel('weight_kg', hubCardLabel(cardId))"
                variant="line" color-role="violet"
                :show-points="true" :point-radius="1.25" :area="true"
                :smooth-line="true" :area-stops="HUB_LINE_AREA_STOPS"
                :pad-line-domain="true"
              />
            </template>
            <template v-else-if="cardId === 'nutrition'">
              <template v-if="visibleNutritionKpiKeys.length">
                <div class="analysis-nutrition-kpis">
                  <span
                    v-for="key in visibleNutritionKpiKeys"
                    :key="key" class="analysis-nutrition-kpi"
                    :class="`analysis-nutrition-kpi--${miniChartColorRole(key)}`"
                  >
                    <span class="analysis-nutrition-kpi-label">
                      <span class="analysis-card-extra">{{ key === 'protein_g' ? t('analysisV2.nutritionProtein') : localizedAnalysisPresentation(key)?.label }}</span>
                    </span>
                    <strong class="analysis-metric-value">{{ cardValue(key) ?? '–' }}</strong>
                  </span>
                </div>
                <div class="analysis-nutrition-macro-trends">
                  <div v-for="series in nutritionMacroSeries" :key="series.key" class="analysis-nutrition-macro-trend">
                    <AnalysisMiniChart
                      v-if="series.values.some((value) => value !== null)"
                      class="analysis-nutrition-macro-trend-chart"
                      :values="series.values" :dates="calendarDates"
                      :date-range="nutritionMacroDateRange"
                      :label="chartLabel(series.key, series.label)"
                      variant="line" :color-role="series.colorRole" :show-points="true" :point-radius="1.25"
                      :area="true" :area-stops="HUB_LINE_AREA_STOPS"
                      :hub-line-domain-padding-step="hubLineDomainMinimumStep(series.key)"
                    />
                    <span v-else class="analysis-nutrition-macro-trend-empty" aria-hidden="true">–</span>
                  </div>
                </div>
              </template>
              <p v-else class="analysis-nutrition-summary-note">
                {{ t('analysisV2.nutritionSummaryNoDuplicateMetrics') }}
              </p>
            </template>
            <template v-else-if="cardId === 'micronutrients'">
              <p class="analysis-nutrient-count">
                {{ t('analysisV2.nutrientCount', { count: number.format(presentNutrientCount), total: number.format(summaryNutrients.length) }) }}
              </p>
              <div class="analysis-nutrient-groups">
                <div v-for="group in nutrientGroups" :key="group.key" :data-nutrient-group="group.key">
                  <span>{{ t(group.labelKey) }}</span>
                  <strong>{{ t('analysisV2.nutrientCount', { count: number.format(group.count), total: number.format(group.total) }) }}</strong>
                </div>
              </div>
              <ul v-if="trendNutrients.length" class="analysis-nutrient-trends">
                <li v-for="metric in trendNutrients" :key="metric.metric_key">
                  <strong>{{ localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label }}</strong>
                  <strong class="analysis-nutrient-trend-value">{{ cardValue(metric.metric_key) ?? '–' }}</strong>
                  <AnalysisMiniChart
                    v-if="cardHasChartData(metric.metric_key)" class="analysis-nutrient-trend-chart"
                    :values="cardChartValues(metric.metric_key)" :dates="cardChartDates()"
                    :date-range="hubMiniChartVariant(cardChartValues(metric.metric_key), cardChartDates()) === 'bars'
                      ? hubChartDateRange([cardChartValues(metric.metric_key)], cardChartDates()) : range"
                    :label="chartLabel(metric.metric_key, localizedAnalysisPresentation(metric.metric_key)?.label ?? metric.label)"
                    :variant="hubMiniChartVariant(cardChartValues(metric.metric_key), cardChartDates())" color-role="emerald"
                    :max-bar-width="HUB_MINI_CHART_MAX_BAR_WIDTH" :zero-as-bar="true" :point-radius="1.25"
                    :bar-width-ratio="hubMiniChartVariant(cardChartValues(metric.metric_key), cardChartDates()) === 'bars'
                      ? HUB_MINI_CHART_BAR_WIDTH_RATIO : null"
                    :hub-line-domain-padding-step="hubLineDomainMinimumStep(metric.metric_key)"
                  />
                </li>
              </ul>
            </template>
            <template v-else-if="cardId === 'activity'">
              <strong class="analysis-metric-value">{{ activityAverage === null ? '–' : `${kcal.format(activityAverage)} kcal` }}</strong>
              <span class="analysis-card-extra">{{ t('analysisV2.hubActivityTotalInPeriod', { value: kcal.format(activityTotal) }) }}</span>
              <AnalysisMiniChart
                v-if="cardHasChartData('active_energy_kcal')" class="analysis-card-chart"
                :values="activityChartValues" :dates="activityChartDates" :date-range="hubChartDateRange([activityChartValues], activityChartDates)"
                :label="chartLabel('active_energy_kcal', hubCardLabel(cardId))" variant="bars" color-role="amber"
              />
            </template>
            <template v-else-if="cardId === 'meals_today'">
              <div class="analysis-meal-summary">
                <strong v-if="mealsTodayDay && typeof mealsTodayDay.dietary_energy_kcal === 'number' && Number.isFinite(mealsTodayDay.dietary_energy_kcal)" class="analysis-metric-value">
                  {{ kcal.format(mealsTodayDay.dietary_energy_kcal) }} kcal
                </strong>
                <span v-if="todayMealGroups.length" class="analysis-meal-count">
                  {{ mealSummaryLabel(todayMealGroups.length, todayMealEntryCount) }}
                </span>
              </div>
              <p v-if="!todayMealGroups.length" class="analysis-meal-empty">
                {{ t('analysisV2.mealsNoEntries') }}
              </p>
              <ul v-else class="analysis-meal-list" :aria-label="t('analysisV2.mealsToday')">
                <li v-for="group in todayMealGroups" :key="group.key">
                  <span class="analysis-meal-group-label">{{ formatHubMealType(group.type) }}</span>
                  <span class="analysis-meal-entry-count">{{ mealEntryCountLabel(group.entryCount) }}</span>
                  <span v-if="group.totalEnergy !== null" class="analysis-meal-group-kcal">
                    {{ kcal.format(group.totalEnergy) }} kcal
                  </span>
                </li>
              </ul>
            </template>
            <template v-else>
              <strong class="analysis-metric-value">{{ cardValue(cardId) ?? '–' }}</strong>
              <span v-if="cardId === 'weight_kg'" class="analysis-card-extra">{{ t('analysisV2.latestMeasurement') }}</span>
              <AnalysisMiniChart
                v-if="cardHasChartData(cardId)" class="analysis-card-chart"
                :values="cardChartValues(cardId)" :dates="cardChartDates()" :date-range="hubChartDateRange([cardChartValues(cardId)], cardChartDates())"
                :label="chartLabel(cardId, hubCardLabel(cardId))"
                :variant="hubMetricChartVariant(cardId, cardChartValues(cardId), cardChartDates())"
                :max-bar-width="cardId === 'active_energy_kcal' ? null : HUB_MINI_CHART_MAX_BAR_WIDTH"
                :bar-width-ratio="hubMetricChartVariant(cardId, cardChartValues(cardId), cardChartDates()) === 'bars'
                  && cardId !== 'active_energy_kcal' ? HUB_MINI_CHART_BAR_WIDTH_RATIO : null"
                :point-radius="1.25"
                :zero-as-bar="cardId !== 'active_energy_kcal'"
                :color-role="miniChartColorRole(cardId)"
                :area="cardId !== 'active_energy_kcal'"
                :smooth-line="cardId === 'weight_kg'"
                :area-stops="HUB_LINE_AREA_STOPS"
                :hub-line-domain-padding-step="hubLineDomainMinimumStep(cardId)"
              />
            </template>
          </template>
          <template #footer>
            <span v-if="cardId === 'weight' && cardState(cardId) === 'ready'">{{ weightMeasurementCount() }}</span>
            <span v-else-if="cardId === 'meals_today' && mealsTodayDay">{{ t('analysisV2.mealsTodayDate', { date: formatDate(today) }) }}</span>
          </template>
        </AnalysisHubCard>
        <li key="analysis-add-card" class="analysis-add-card" data-analysis-add-card>
          <button
            ref="managerTrigger"
            class="analysis-add-card-button"
            type="button"
            :disabled="workspaceLoading || !workspaceReady || saving"
            @click="openManager"
          >
            <PhPlus size="24" weight="regular" aria-hidden="true" />
            <span>{{ t('analysisV2.addCardAction') }}</span>
          </button>
        </li>
      </TransitionGroup>
    </section>

    <dialog
      ref="managerDialog"
      class="analysis-manager-dialog"
      aria-labelledby="analysis-manager-title"
      @close="restoreManagerFocus"
      @pointerdown="handleManagerPointerDown"
      @pointerup="handleManagerPointerUp"
      @pointercancel="handleManagerPointerCancel"
      @click="handleManagerClick"
      @focusin="handleManagerFocusIn"
      @keydown.tab="handleManagerTab"
      @keydown.esc.prevent.stop="closeManager"
    >
      <div class="analysis-manager-header">
        <h2 id="analysis-manager-title" ref="managerHeading" tabindex="-1">{{ t('analysisV2.addCardAction') }}</h2>
        <button class="analysis-manager-close" type="button" :aria-label="t('common.close')" @click="closeManager">
          <PhX size="20" weight="regular" aria-hidden="true" />
        </button>
      </div>
      <div class="analysis-manager-controls">
        <input
          v-model="managerSearch"
          type="search"
          :aria-label="t('analysisV2.managerSearchLabel')"
          :placeholder="t('analysisV2.managerSearchPlaceholder')"
        >
      </div>
      <div class="analysis-manager-body">
        <p v-if="saveError" class="analysis-manager-error" role="alert" aria-live="assertive">{{ saveError }}</p>
        <div v-if="!managerSearchActive" class="analysis-manager-category-grid">
          <button
            v-for="category in analysisCategories"
            :key="category.key"
            class="analysis-manager-category-tile"
            type="button"
            :data-manager-category="category.key"
            :aria-expanded="selectedManagerCategory === category.key"
            aria-controls="analysis-manager-results"
            @click="toggleManagerCategory(category.key)"
          >
            <span
              class="analysis-manager-category-icon"
              :data-icon="managerCategoryIcons[category.key]"
              aria-hidden="true"
            >
              <AnalysisIcon :icon="managerCategoryIcons[category.key]" />
            </span>
            <span class="analysis-manager-category-label">{{ t(category.labelKey) }}</span>
          </button>
        </div>
        <div id="analysis-manager-results" class="analysis-manager-results">
          <p v-if="managerSearchActive && !managerGroups.length" class="analysis-manager-empty" role="status" aria-live="polite">
            {{ t('analysisV2.catalogSearchNoResults') }}
          </p>
          <section
            v-for="group in managerVisibleGroups"
            :key="group.key"
            :data-manager-group="group.key"
            :aria-labelledby="`analysis-manager-group-title-${group.key}`"
            class="analysis-manager-group"
          >
            <h3 :id="`analysis-manager-group-title-${group.key}`">{{ t(group.labelKey) }}</h3>
            <ul>
              <li
                v-for="option in group.entries"
                :key="option.id"
                :data-hub-card-option="option.id"
                :aria-disabled="option.status === 'coming_soon' ? 'true' : undefined"
                :class="{ 'is-unavailable': option.status === 'coming_soon' }"
              >
                <span class="analysis-manager-option-icon" :data-icon="option.icon" aria-hidden="true">
                  <AnalysisIcon :icon="option.icon" />
                </span>
                <span class="analysis-manager-option-label">{{ option.label }}</span>
                <template v-if="option.status === 'available'">
                  <span v-if="activeCardIds.includes(option.id)" class="analysis-manager-status">
                    <PhCheck size="16" weight="regular" aria-hidden="true" />
                    {{ t('analysisV2.added') }}
                  </span>
                  <button
                    class="analysis-manager-action" type="button"
                    :disabled="workspaceLoading || !workspaceReady || saving"
                    :data-manager-action="option.id"
                    @click="activeCardIds.includes(option.id) ? removeKey(option.id, 'manager') : addKey(option.id)"
                  >{{ activeCardIds.includes(option.id) ? t('analysisV2.remove') : t('analysisV2.add') }}</button>
                </template>
                <span v-else class="analysis-manager-unavailable">{{ t('analysisV2.notAvailable') }}</span>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </dialog>



  </div>
</template>

<style scoped>
.analysis-hub { display: grid; gap: 1rem; container-name: analysis-overview; container-type: inline-size; }
.analysis-header { display: flex; justify-content: space-between; gap: 1.5rem; margin-bottom: 0; }
.analysis-heading-copy { flex: 1 1 auto; min-width: 0; }
.analysis-heading-copy h1 { margin: 0; }
.analysis-header > :deep(.analysis-period-picker) { flex: 0 0 auto; }
.analysis-section { display: grid; gap: .75rem; min-width: 0; }

.analysis-card-list {
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr));
  align-items: start; gap: .85rem; margin: 0; padding: 0; list-style: none;
}
.analysis-add-card { min-width: 0; }
.analysis-add-card-button {
  display: grid; place-content: center; justify-items: center; gap: .35rem;
  width: 100%; height: 6rem; box-sizing: border-box; padding: .75rem;
  border: 1px dashed var(--border); border-radius: .75rem; background: transparent;
  color: var(--muted-strong); font: inherit; font-size: .9rem; font-weight: 400; text-align: center; cursor: pointer;
}
.analysis-add-card-button svg { color: var(--muted); }
.analysis-add-card-button:hover:not(:disabled) {
  border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--surface)); color: var(--ink);
}
.analysis-add-card-button:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 3px; }
.analysis-add-card-button:disabled { cursor: not-allowed; opacity: .55; }
.analysis-card-reorder-move { transition: transform .25s cubic-bezier(.2, .7, .2, 1); }
.analysis-card-extra { font-size: .84rem; font-weight: 400; color: var(--muted); }
.analysis-metric-value { color: var(--ink); font-size: 1.55rem; line-height: 1.15; font-weight: 700; overflow-wrap: anywhere; }
.analysis-card-chart { width: 100%; height: 5rem; min-width: 0; margin-top: .1rem; }
.analysis-card-coverage { display: inline-flex; align-items: center; gap: .32rem; }
.analysis-nutrition-kpis { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: .45rem; }
.analysis-nutrition-kpi-label { display: inline-flex; align-items: baseline; gap: .2rem; min-width: 0; }
.analysis-nutrition-macro-trends { display: grid; gap: .12rem; min-width: 0; }
.analysis-nutrition-macro-trend { display: grid; grid-template-columns: minmax(0, 1fr); align-items: center; gap: .35rem; min-width: 0; }
.analysis-nutrition-macro-trend-chart { width: 100%; height: 1.25rem; min-width: 0; }
.analysis-nutrition-macro-trend-empty { justify-self: center; color: var(--muted); line-height: 1.25rem; }
:deep(.analysis-average-mark) { margin-inline-start: .2em; color: var(--muted); font-size: .72em; font-weight: 400; line-height: 1; text-decoration: none; vertical-align: baseline; cursor: help; }
.analysis-nutrition-kpi { display: grid; align-content: start; gap: .15rem; min-width: 0; }
.analysis-nutrition-kpi > .analysis-card-extra { line-height: 1.25; }
.analysis-nutrition-kpi .analysis-metric-value { font-size: 1.25rem; }
.analysis-nutrition-summary-note { margin: 0; color: var(--muted); font-size: .84rem; line-height: 1.4; }
.analysis-nutrition-kpi--violet strong { color: var(--chart-series-violet); }
.analysis-nutrition-kpi--teal strong { color: var(--chart-series-teal); }
.analysis-nutrition-kpi--blue strong { color: var(--chart-series-blue); }
.analysis-nutrition-kpi--amber strong { color: var(--chart-series-amber); }
.analysis-nutrient-count { margin: 0; color: var(--ink); font-size: 1.25rem; font-weight: 700; line-height: 1.1; }
.analysis-nutrient-groups { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .25rem .5rem; color: var(--muted); font-size: .75rem; }
.analysis-nutrient-groups > div { display: flex; align-items: baseline; gap: .25rem; min-width: 0; white-space: nowrap; }
.analysis-nutrient-groups strong { color: var(--ink); font-weight: 600; white-space: nowrap; }
.analysis-nutrient-trends { display: grid; grid-template-columns: minmax(0, 1fr); gap: .1rem; min-height: 0; align-content: start; margin: 0; padding: .25rem 0 0; border-top: 1px solid var(--border); list-style: none; }
.analysis-nutrient-trends li {
  display: grid; grid-template-columns: minmax(0, 1fr) 84px; grid-template-rows: auto auto;
  align-items: center; gap: .05rem .4rem; min-width: 0; padding: .1rem 0;
}
.analysis-nutrient-trends li > strong:first-child { grid-column: 1; grid-row: 1; font-size: .78rem; font-weight: 400; line-height: 1.1; overflow-wrap: anywhere; }
.analysis-nutrient-trends li + li { border-top: 1px solid var(--border); }
.analysis-nutrient-trend-copy { display: grid; gap: .15rem; min-width: 0; overflow-wrap: anywhere; }
.analysis-nutrient-trend-copy a { color: inherit; text-decoration: none; }
.analysis-nutrient-trend-copy a:hover { color: var(--accent-hover); text-decoration: underline; }
.analysis-nutrient-trend-copy a:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; border-radius: .25rem; }
.analysis-nutrient-trend-value { grid-column: 1; grid-row: 2; color: var(--ink); font-size: .78rem; font-weight: 500; line-height: 1.1; }
.analysis-nutrient-trend-state { grid-column: 1 / -1; overflow-wrap: anywhere; }
.analysis-nutrient-trends .analysis-nutrient-trend-chart { grid-column: 2; grid-row: 1 / 3; width: 84px; height: 26px; }
.analysis-save-error, .analysis-workspace-error { margin: 0; }
.analysis-workspace-error { display: grid; justify-items: start; gap: .65rem; }
.analysis-workspace-error p { margin: 0; }
.analysis-list-heading { display: flex; align-items: center; justify-content: flex-start; gap: .75rem; }
.analysis-list-heading h2 { margin: 0; font-size: .92rem; line-height: 1.5; font-weight: 400; }
.analysis-meal-summary { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: .1rem .5rem; min-width: 0; }
.analysis-meal-count { color: var(--muted); font-size: .75rem; line-height: 1.4; font-weight: 400; }
.analysis-meal-empty { margin: 0; color: var(--muted); }
.analysis-meal-list { position: relative; z-index: 2; display: grid; gap: .2rem; min-width: 0; margin: 0; padding: 0; list-style: none; }
.analysis-meal-list li { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: .35rem; min-width: 0; padding: .05rem 0; border-bottom: 1px solid var(--border); }
.analysis-meal-group-label { min-width: 0; font-size: .82rem; line-height: 1.2; overflow-wrap: anywhere; }
.analysis-meal-entry-count { color: var(--muted); font-size: .72rem; line-height: 1.2; white-space: nowrap; }
.analysis-meal-group-kcal { justify-self: end; font-size: .9rem; line-height: 1.2; white-space: nowrap; }
.analysis-manager-dialog {
  position: fixed; inset: 0; margin: auto; width: min(680px, calc(100vw - 32px));
  max-height: min(80dvh, 720px); box-sizing: border-box; padding: 0; border: 1px solid var(--border);
  border-radius: .9rem; background: var(--surface); color: var(--ink); box-shadow: var(--shadow-popover); overflow: hidden;
}
.analysis-manager-dialog[open] { display: flex; flex-direction: column; }
.analysis-manager-dialog::backdrop { background: rgb(0 0 0 / .48); }
.analysis-manager-header { display: flex; flex: 0 0 auto; align-items: center; justify-content: space-between; gap: .75rem; padding: .5rem .75rem; background: var(--surface); border-bottom: 1px solid var(--border); }
.analysis-manager-header h2 { margin: 0; font-size: 1rem; line-height: 1.5; font-weight: 650; letter-spacing: -.01em; }
.analysis-manager-header h2:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 3px; }
.analysis-manager-close { display: grid; flex: 0 0 44px; place-items: center; width: 44px; height: 44px; padding: 0; border: 0; border-radius: .4rem; background: transparent; color: var(--muted); cursor: pointer; }
.analysis-manager-close:hover { background: color-mix(in srgb, var(--muted) 10%, var(--surface)); color: var(--ink); }
 .analysis-manager-controls { display: grid; flex: 0 0 auto; gap: .65rem; padding: .75rem; border-bottom: 1px solid var(--border); }
 .analysis-manager-controls input { width: 100%; min-height: 2.75rem; box-sizing: border-box; padding: .55rem .75rem; border: 1px solid var(--border); border-radius: .5rem; background: var(--surface); color: var(--ink); font: inherit; font-size: .9rem; }
 .analysis-manager-controls input:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
 .analysis-manager-body { display: grid; flex: 1 1 auto; gap: .75rem; min-height: 0; padding: .75rem; overflow-y: auto; overscroll-behavior: contain; }
 .analysis-manager-group { min-width: 0; border: 1px solid var(--border); border-radius: .65rem; background: var(--surface); }
 .analysis-manager-group h3 { margin: 0; padding: .6rem .7rem; border-bottom: 1px solid var(--border); font-size: .9rem; font-weight: 600; line-height: 1.35; }
 .analysis-manager-category-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .55rem; }
 .analysis-manager-category-tile { display: grid; grid-template-rows: 3.5rem auto; align-content: center; justify-items: center; gap: .35rem; min-width: 0; min-height: 7rem; padding: .6rem .4rem; border: 1px solid var(--border); border-radius: .7rem; background: var(--surface); color: var(--ink); font: inherit; text-align: center; cursor: pointer; }
 .analysis-manager-category-tile[aria-expanded="true"] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--surface)); }
 .analysis-manager-category-tile:hover:not([aria-expanded="true"]) { border-color: color-mix(in srgb, var(--accent) 40%, var(--border)); background: color-mix(in srgb, var(--accent) 4%, var(--surface)); }
 .analysis-manager-category-tile:focus-visible { outline: 3px solid var(--focus-ring); outline-offset: 2px; }
 .analysis-manager-category-icon { display: grid; place-items: center; width: 3.5rem; height: 3.5rem; border-radius: .7rem; color: var(--analysis-icon-color, var(--chart-series-violet)); background: color-mix(in srgb, var(--analysis-icon-color, var(--chart-series-violet)) 12%, transparent); font-size: 2.5rem; }
 .analysis-manager-category-icon[data-icon="activity"] { --analysis-icon-color: var(--chart-series-amber); }
 .analysis-manager-category-icon[data-icon="nutrition"] { --analysis-icon-color: var(--chart-series-teal); }
 .analysis-manager-category-icon[data-icon="atom"] { --analysis-icon-color: var(--chart-series-emerald); }
 .analysis-manager-category-icon[data-icon="drop"] { --analysis-icon-color: var(--chart-series-blue); }
 .analysis-manager-category-label { min-width: 0; font-size: .88rem; font-weight: 500; line-height: 1.2; overflow-wrap: anywhere; }
 .analysis-manager-results { display: grid; gap: .5rem; min-width: 0; }
 .analysis-manager-group ul { display: grid; gap: .25rem; margin: 0; padding: .25rem .55rem .55rem; list-style: none; }
 .analysis-manager-group li { display: grid; grid-template-columns: 1.75rem minmax(0, 1fr) auto auto; align-items: center; gap: .5rem; min-width: 0; padding: .35rem .4rem; border-bottom: 1px solid var(--border); }
 .analysis-manager-group li:last-child { border-bottom: 0; }
 .analysis-manager-group li.is-unavailable { grid-template-columns: 1.75rem minmax(0, 1fr) auto; background: var(--surface-soft); opacity: .65; }
 .analysis-manager-option-icon { display: grid; place-items: center; width: 1.75rem; height: 1.75rem; border-radius: .4rem; color: var(--analysis-icon-color, var(--chart-series-violet)); background: color-mix(in srgb, var(--analysis-icon-color, var(--chart-series-violet)) 10%, transparent); font-size: 1rem; }
 .analysis-manager-option-icon[data-icon="protein"], :deep(.analysis-card-icon[data-icon="protein"]) { --analysis-icon-color: var(--chart-series-teal); }
 .analysis-manager-option-icon:is([data-icon="carbohydrates"], [data-icon="cube"]), :deep(.analysis-card-icon:is([data-icon="carbohydrates"], [data-icon="cube"])) { --analysis-icon-color: var(--chart-series-blue); }
 .analysis-manager-option-icon:is([data-icon="energy"], [data-icon="fire"], [data-icon="fat"]), :deep(.analysis-card-icon:is([data-icon="energy"], [data-icon="fire"], [data-icon="fat"])) { --analysis-icon-color: var(--chart-series-amber); }
 .analysis-manager-option-icon:is([data-icon="atom"], [data-icon="carrot"], [data-icon="diamond"], [data-icon="fiber"], [data-icon="leaf"]), :deep(.analysis-card-icon:is([data-icon="atom"], [data-icon="carrot"], [data-icon="diamond"], [data-icon="fiber"], [data-icon="leaf"])) { --analysis-icon-color: var(--chart-series-emerald); }
 .analysis-manager-option-label { min-width: 0; font-size: .9rem; font-weight: 400; overflow-wrap: anywhere; }
 .analysis-manager-status { display: inline-flex; grid-column: 3; align-items: center; gap: .25rem; white-space: nowrap; }
 .analysis-manager-status svg { flex: 0 0 auto; color: var(--chart-series-emerald); }
 .analysis-manager-status, .analysis-manager-unavailable { color: var(--muted); font-size: .78rem; }
 .analysis-manager-group li.is-unavailable .analysis-manager-unavailable { grid-column: 3; }
 .analysis-manager-action { grid-column: 4; min-height: 2rem; padding: .3rem .5rem; border: 1px solid var(--border); border-radius: .35rem; background: transparent; color: var(--ink); font: inherit; font-size: .82rem; cursor: pointer; }
 .analysis-manager-action:hover:not(:disabled) { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--surface)); }
 .analysis-manager-action:disabled { color: var(--muted); cursor: not-allowed; }
 .analysis-manager-empty { margin: 0; color: var(--muted); font-size: .9rem; }
 .analysis-manager-error { margin: 0; color: var(--danger); }
@media (min-width: 1201px) {
  .analysis-card-list { grid-auto-rows: auto; }
  :deep(.analysis-card-content) { grid-template-rows: auto minmax(4rem, 1fr); }
  :deep(.analysis-card-content:has(> .analysis-card-extra + .analysis-card-chart:last-child)) {
    grid-template-rows: auto auto minmax(4rem, 1fr);
  }
  :deep(.analysis-card:has(> .analysis-card-content > .analysis-card-chart:last-child)) { row-gap: 0; }
  :deep(.analysis-card-content:has(> .analysis-card-chart:last-child)) { margin-top: .5rem; padding-bottom: 0; }
  :deep(.analysis-card-content .analysis-card-chart) { height: 100%; min-height: 4rem; margin-top: 0; }
  :deep(.analysis-card:has(> .analysis-card-content > .analysis-nutrition-macro-trends:last-child)) { row-gap: 0; }
  :deep(.analysis-card-content:has(> .analysis-nutrition-macro-trends:last-child)) { margin-top: .5rem; padding-bottom: 0; }
  :deep(.analysis-card[data-card-key="activity"] .analysis-card-content) {
    grid-template-rows: auto auto minmax(4rem, 1fr);
  }
  :deep(.analysis-card[data-card-key="meals_today"] .analysis-card-content) {
    grid-template-rows: none;
  }
  :deep(.analysis-card[data-card-key="nutrition"] .analysis-nutrition-summary-note) {
    grid-row: 1 / -1; align-self: center;
  }
  :deep(.analysis-card[data-card-key="micronutrients"] .analysis-card-content) {
    grid-template-rows: auto auto minmax(0, 1fr); gap: .3rem;
  }
  :deep(.analysis-card[data-card-key="micronutrients"] .analysis-nutrient-trend-chart) {
    height: 26px; min-height: 26px; max-height: 26px;
  }
}
@media (max-width: 1200px) {
  .analysis-card-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .analysis-header { align-items: flex-start; flex-wrap: wrap; }
  .analysis-card-list { grid-template-columns: minmax(0, 1fr); }
  .analysis-manager-category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .analysis-manager-status { grid-column: 2; grid-row: 2; }
  .analysis-manager-action { grid-column: 3; grid-row: 1 / 3; }
}
@container analysis-overview (max-width: 26rem) {
  .analysis-card-list { grid-template-columns: minmax(0, 1fr); }
}
@media (prefers-reduced-motion: reduce) {
  .analysis-card-reorder-move, .analysis-card { transition: none; }
  .analysis-card.is-dragging { scale: 1; transform: none; }
}
</style>
