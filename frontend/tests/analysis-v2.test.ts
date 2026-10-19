import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PhAtom, PhAvocado, PhBowlFood, PhBread, PhCarrot, PhCube, PhDiamond, PhDrop, PhEgg, PhFire, PhFootprints, PhForkKnife, PhLeaf, PhLightning, PhPersonSimpleRun, PhScales } from '@phosphor-icons/vue'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Die Analyse konnte nicht gespeichert werden.'),
  setCsrfToken: vi.fn(),
}))
import { localizeApiError } from '../src/api'
import AnalysisMiniChart from '../src/components/AnalysisMiniChart.vue'
import PeriodPicker from '../src/components/PeriodPicker.vue'
import { chartColors } from '../src/chart-theme'
import AnalysisActivityView from '../src/views/AnalysisActivityView.vue'
import AnalysisActivityOverviewView from '../src/views/AnalysisActivityOverviewView.vue'
import AnalysisCatalogView from '../src/views/AnalysisCatalogView.vue'
import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisNutritionDetailView from '../src/views/AnalysisNutritionDetailView.vue'
import AnalysisNutritionOverviewView from '../src/views/AnalysisNutritionOverviewView.vue'
import AnalysisOverviewView from '../src/views/AnalysisOverviewView.vue'
import AnalysisWeightDetailView from '../src/views/AnalysisWeightDetailView.vue'
import { AnalysisIcon, analysisHref, DEFAULT_ACTIVE_CARD_IDS, localizedAnalysisPresentation } from '../src/analysis-v2'
import * as analysisV2 from '../src/analysis-v2'

import { rangeForDays } from '../src/analytics-period'
import { formatGermanDate, isoDateInTimeZone, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { setThemePreference } from '../src/theme'
import type {
  AnalysisMicronutrientResponse, AnalysisNutritionResponse, AnalysisRegistryEntry,
  ReaderPeriodCapabilities, VerificationActivityResponse, WeightResponse,
} from '../src/types'

describe('AnalysisIcon semantic mappings', () => {
  it('renders a distinct Phosphor icon for each hub concept', () => {
    const mappings = [
      ['scale', PhScales],
      ['nutrition', PhForkKnife],
      ['energy', PhLightning],
      ['protein', PhEgg],
      ['carbohydrates', PhBread],
      ['fat', PhAvocado],
      ['cube', PhCube],
      ['fiber', PhLeaf],
      ['fire', PhFire],
      ['activity', PhPersonSimpleRun],
      ['steps', PhFootprints],
      ['drop', PhDrop],
      ['bowl', PhBowlFood],
      ['atom', PhAtom],
      ['carrot', PhCarrot],
      ['diamond', PhDiamond],
    ] as const
    for (const [icon, component] of mappings) {
      const wrapper = mount(AnalysisIcon, { props: { icon } })
      expect(wrapper.findComponent(component).exists()).toBe(true)
      wrapper.unmount()
    }
    expect(new Set(mappings.map(([, component]) => component)).size).toBe(mappings.length)
  })
})

describe('Analysis lookup keys', () => {
  it('returns the mapped presentation for a known micronutrient', () => {
    expect(localizedAnalysisPresentation('vitamin_a_ug')).toMatchObject({
      route: 'micronutrient',
    })
    expect(analysisHref('vitamin_a_ug')).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
  })

  it.each(['unknown_metric', 'toString', 'constructor', '__proto__'])(
    'rejects unknown and inherited lookup keys: %s',
    (key) => {
      expect(localizedAnalysisPresentation(key)).toBeUndefined()
      expect(analysisHref(key)).toBe('/analyse/katalog')
      const icon = mount(AnalysisIcon, { props: { icon: key } })
      expect(icon.findComponent(PhAtom).exists()).toBe(true)
      icon.unmount()
    },
  )
})
describe('Hub mini-chart density', () => {
  const selectVariant = Reflect.get(analysisV2, 'hubMiniChartVariant') as
    | ((values: readonly (number | null)[], dates: readonly (string | null)[]) => 'line' | 'bars')
    | undefined

  it('uses a line at the four-value, fifty-percent actual-span boundary and counts zero as a value', () => {
    expect(selectVariant?.(
      [null, 4, null, 0, null, 6, null, null, 8, null],
      [
        '2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05',
        '2026-03-06', '2026-03-07', '2026-03-08', '2026-03-09', '2026-03-10',
      ],
    )).toBe('line')
  })

  it('uses bars for fewer than four values and for coverage below half of the actual span', () => {
    expect(selectVariant?.(
      [3, null, null, null, null, null, 0],
      [
        '2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04',
        '2026-03-05', '2026-03-06', '2026-03-07',
      ],
    )).toBe('bars')
    expect(selectVariant?.(
      [1, null, 2, null, 3, null, null, null, 4],
      [
        '2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05',
        '2026-03-06', '2026-03-07', '2026-03-08', '2026-03-09',
      ],
    )).toBe('bars')
    expect(selectVariant?.([2, 3, 4], ['2026-03-01', '2026-03-02', '2026-03-03'])).toBe('bars')
  })
})


const registry = [
  { analysis_key: 'weight_kg', category_key: 'body', domain_key: 'weight_kg', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.weight', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'active_energy_kcal', category_key: 'activity', domain_key: 'active_energy_kcal', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.verification.activity', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'vitamin_a_ug', category_key: 'micronutrients', domain_key: 'vitamin_a_ug', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients', period_capability: { min_days: 1, max_days: 365, custom_range: true } },
  { analysis_key: 'calcium_mg', category_key: 'micronutrients', domain_key: 'calcium_mg', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients', period_capability: { min_days: 1, max_days: 365, custom_range: true } },
  ...([
    ['fat_ratio_pct', 'body'], ['bmi', 'body'], ['fat_mass_kg', 'body'], ['muscle_mass_kg', 'body'],
    ['fat_free_mass_kg', 'body'], ['hydration_kg', 'body'], ['bone_mass_kg', 'body'],
    ['waist_circumference_cm', 'body'], ['hip_circumference_cm', 'body'],
    ['steps', 'activity'], ['distance_m', 'activity'], ['training', 'activity'],
    ['dietary_energy_kcal', 'nutrition'], ['protein_g', 'nutrition'], ['carbohydrates_g', 'nutrition'],
    ['fat_g', 'nutrition'], ['fiber_g', 'nutrition'], ['sugar_g', 'nutrition'], ['saturated_fat_g', 'nutrition'],
    ['water_intake_ml', 'fluid'],
  ] as const).map(([analysis_key, category_key]) => ({
    analysis_key, category_key, domain_key: analysis_key,
    status: category_key === 'nutrition' ? 'available' : 'coming_soon',
    card_capable: category_key === 'nutrition', detail_capable: category_key === 'nutrition',
    reader_key: category_key === 'nutrition' ? 'analytics.nutrition.analysis' : null,
    period_capability: category_key === 'nutrition'
      ? { min_days: 1, max_days: 365, custom_range: true } : null,
  })),
]

const mountedWrappers = new Set<VueWrapper>()
const disposeAnalysisAuthStores = new Set<() => void>()
let activeCardIds: string[]
let previousChartAmberStyle: string | null = null

function analysisPreferencesResponse() {
  return { active_card_ids: [...activeCardIds] }
}
let saveFailure = false
let registryFailuresRemaining = 0
let delayPreferenceWrite = false
let completePreferenceWrite: (() => void) | null = null
function setUser(userId = 'analysis-user') {
  for (const dispose of disposeAnalysisAuthStores) dispose()
  disposeAnalysisAuthStores.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  disposeAnalysisAuthStores.add(() => auth.$dispose())
  auth.user = {
    id: userId, username: 'owner', language: 'de', timezone: 'Europe/Berlin',
    week_starts_on: 0, preferred_weight_unit: 'kg', raw_payload_retention_days: 0, is_admin: false, is_active: true,
    deactivated_at: null,
  }
  return pinia
}

async function mountAnalysis(
  path: string,
  initialCards: string[] = [...DEFAULT_ACTIVE_CARD_IDS],
  userId = 'analysis-user',
) {
  activeCardIds = [...initialCards]
  const pinia = setUser(userId)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/analyse',
        component: AnalysisLayout,
        children: [
          { path: '', name: 'analysis-overview', component: AnalysisOverviewView },
          { path: 'katalog', name: 'analysis-catalog', component: AnalysisCatalogView },
          { path: 'koerper', name: 'analysis-category-body', component: AnalysisCatalogView, props: { categoryKey: 'body' } },
          { path: 'aktivitaet', name: 'analysis-category-activity', component: AnalysisActivityOverviewView },
          { path: 'ernaehrung', name: 'analysis-nutrition-overview', component: AnalysisNutritionOverviewView },
          { path: 'ernaehrung/:metric', name: 'analysis-nutrition-detail', component: AnalysisNutritionDetailView },
          { path: 'mikronaehrstoffe', name: 'analysis-category-micronutrients', component: AnalysisCatalogView, props: { categoryKey: 'micronutrients' } },
          { path: 'koerper/gewicht', name: 'analysis-weight-detail', component: AnalysisWeightDetailView },
          { path: 'aktivitaet/aktive-energie', name: 'analysis-active-energy', component: AnalysisActivityView },
          { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: AnalysisMicronutrientDetailView },
        ],
      },
      { path: '/ernaehrung', name: 'nutrition', component: { template: '<h1>Ernährung</h1>' } },
      { path: '/ernaehrung/eintrag/:eventId', name: 'nutrition-meal-detail', component: { template: '<h1>Ernährungseintrag</h1>' } },
      { path: '/aktivitaet', component: { template: '<h1>Aktivität</h1>' } },
      { path: '/gewicht', component: { template: '<h1>Gewicht</h1>' } },
      { path: '/trends', component: { template: '<h1>Trends</h1>' } },
      { path: '/mikronaehrstoffe', component: { template: '<h1>Mikronährstoffe</h1>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, {
    attachTo: document.body,
    global: {
      plugins: [pinia, router],
      stubs: {
        ChartPanel: {
          name: 'ChartPanel',
          props: ['option', 'title', 'empty'],
          template: '<section><h2><slot name="title">{{ title }}</slot></h2><slot name="header-actions" /></section>',
        },
      },
    },
  })
  mountedWrappers.add(wrapper)
  await flushPromises()
  return { router, wrapper }
}

async function expandReadings(wrapper: VueWrapper, selector: string, count: number) {
  const readings = wrapper.get(selector)
  const button = readings.get('button')
  expect(button.element.tagName).toBe('BUTTON')
  expect(button.text()).toBe(`Messwerte (${count})`)
  expect(button.attributes('aria-expanded')).toBe('false')
  const panelId = button.attributes('aria-controls')
  expect(panelId).toBeTruthy()
  const panel = readings.findAll('[id]').find((element) => element.attributes('id') === panelId)
  expect(panel).toBeDefined()
  expect(readings.findAll('table').every((table) => !table.isVisible())).toBe(true)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('true')
  expect(button.attributes('aria-controls')).toBe(panelId)
  expect(panel!.get('table').isVisible()).toBe(true)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('false')
  expect(button.attributes('aria-controls')).toBe(panelId)
  expect(panel!.get('table').isVisible()).toBe(false)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('true')
  expect(panel!.get('table').isVisible()).toBe(true)
}

function expectScreenReaderTableHeaders(wrapper: VueWrapper, selector: string, labels: string[]) {
  const headers = wrapper.findAll(`${selector} thead th`)
  expect(headers.map((header) => header.text())).toEqual(labels)
  for (const header of headers) {
    expect(header.attributes('scope')).toBe('col')
    expect(header.element.closest('.sr-only')?.closest('thead')).toBe(header.element.closest('thead'))
    expect(header.element.closest('[aria-hidden="true"], [hidden]')).toBeNull()
  }
  for (const row of wrapper.findAll(`${selector} tbody tr`)) {
    expect(row.element.closest('.sr-only, [aria-hidden="true"], [hidden]')).toBeNull()
    expect(row.isVisible()).toBe(true)
  }
}

function inclusiveDays(path: string): number {
  const url = new URL(path, 'https://calograph.test')
  return (Date.parse(`${url.searchParams.get('end')}T00:00:00Z`)
    - Date.parse(`${url.searchParams.get('start')}T00:00:00Z`)) / 86_400_000 + 1
}
function serveVitaminAData(): void {
  const fallback = apiMock.getMockImplementation()!
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (!path.startsWith('/analytics/nutrition/micronutrients?')) return fallback(path, options)
    const params = new URLSearchParams(path.split('?')[1] ?? '')
    const start = params.get('start') ?? '2026-03-25'
    const end = params.get('end') ?? start
    return Promise.resolve(fallback(path, options)).then((response) => {
      const data = response as AnalysisMicronutrientResponse
      return {
        ...data,
        start_date: start,
        end_date: end,
        period_days: inclusiveDays(path),
        metrics: data.metrics.map((item) => item.metric_key === 'vitamin_a_ug'
          ? { ...item, total: 1200, days_with_data: 12 }
          : item),
        series: params.get('metric_key') === 'vitamin_a_ug'
          ? Array.from({ length: 12 }, (_, index) => ({
            date: shiftIsoDate(start, index), provider_key: 'yazio', value: 100,
          }))
          : data.series,
      }
    })
  })
}


function cardOrder(wrapper: VueWrapper) {
  return wrapper.findAll('.analysis-card-list > li[data-card-key]').map((card) => card.attributes('data-card-key'))
}
async function openCardMenu(wrapper: VueWrapper, key: string): Promise<void> {
  await wrapper.get(`.analysis-card-list > li[data-card-key="${key}"] .analysis-overflow-trigger`).trigger('click')
}

function installDialogMethods(dialog: HTMLDialogElement): void {
  Object.defineProperty(dialog, 'showModal', {
    configurable: true,
    value(this: HTMLDialogElement) { this.setAttribute('open', '') },
  })
  Object.defineProperty(dialog, 'close', {
    configurable: true,
    value(this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    },
  })
}

async function openCardManager(wrapper: VueWrapper): Promise<void> {
  const dialog = wrapper.get('dialog').element as HTMLDialogElement
  installDialogMethods(dialog)
  if (!dialog.open) await wrapper.get('.analysis-add-card-button').trigger('click')
  if (!dialog.open) throw new Error('Card manager dialog did not open')
}

async function closeCardManager(wrapper: VueWrapper): Promise<void> {
  const dialog = wrapper.get('dialog').element as HTMLDialogElement
  if (dialog.open) await wrapper.get('.analysis-manager-header button').trigger('click')
  if (dialog.open) throw new Error('Card manager dialog did not close')
}
async function clickManagerAction(wrapper: VueWrapper, key: string): Promise<void> {
  await openCardManager(wrapper)
  for (const category of ['body', 'activity', 'nutrition', 'micronutrients', 'fluid']) {
    const option = wrapper.find(`[data-hub-card-option="${key}"]`)
    if (option.exists()) {
      await option.get('button').trigger('click')
      return
    }
    await wrapper.get(`[data-manager-category="${category}"]`).trigger('click')
  }
  throw new Error(`Card manager has no actionable entry for ${key}`)
}

async function removeHubCard(wrapper: VueWrapper, key: string): Promise<void> {
  await openCardMenu(wrapper, key)
  await wrapper.get(`.analysis-card-list > li[data-card-key="${key}"] [data-card-remove]`).trigger('click')
}

function preferenceWrites() {
  return apiMock.mock.calls
    .filter(([path, options]) => path === '/settings/analysis-preferences'
      && (options as RequestInit | undefined)?.method === 'PUT')
    .map(([, options]) => JSON.parse(String((options as RequestInit).body)).active_card_ids)
}
function preferenceWriteBodies() {
  return apiMock.mock.calls
    .filter(([path, options]) => path === '/settings/analysis-preferences'
      && (options as RequestInit | undefined)?.method === 'PUT')
    .map(([, options]) => JSON.parse(String((options as RequestInit).body)) as {
      active_card_ids: string[]
    })
}


async function dragCard(
  wrapper: VueWrapper,
  sourceSelector: string,
  targetSelector: string,
  outcome: 'drop' | 'outside' | 'cancel' | 'origin-list' | 'origin-list-move' = 'drop',
  beforeFinish?: () => Promise<void>,
) {
  const source = wrapper.get(sourceSelector)
  const sourceElement = source.element as HTMLElement
  const target = wrapper.get(targetSelector).element as HTMLElement
  const originList = wrapper.get('.analysis-card-list').element
  const descriptor = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
  let hit: Element = target
  Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => hit })
  const cards = wrapper.findAll('.analysis-card-list > li[data-card-key]').map(({ element }) => element as HTMLElement)
  const visibleKeys = () => wrapper.findAll('.analysis-card-list > li[data-card-key]')
    .map(({ element }) => element as HTMLElement)
    .filter((card) => window.getComputedStyle(card).display !== 'none')
    .map((card) => card.dataset.cardKey!)
  const rectFor = (card: HTMLElement) => {
    const index = visibleKeys().indexOf(card.dataset.cardKey!)
    const left = index * 110
    const top = 0
    return {
      left, top, width: 100, height: 100, right: left + 100, bottom: top + 100,
      x: left, y: top, toJSON: () => ({}),
    } as DOMRect
  }
  for (const card of cards) {
    vi.spyOn(card, 'getBoundingClientRect').mockImplementation(() => rectFor(card))
    Object.defineProperty(card, 'offsetWidth', { configurable: true, value: 100 })
    Object.defineProperty(card, 'offsetHeight', { configurable: true, value: 100 })
  }
  const startBounds = rectFor(sourceElement)
  const targetBounds = rectFor(target)
  const start = { x: startBounds.left + 50, y: startBounds.top + 50 }
  const horizontal = targetBounds.left - startBounds.left
  const vertical = targetBounds.top - startBounds.top
  const drop = {
    x: horizontal > 0 ? targetBounds.left - 49 : horizontal < 0 ? targetBounds.right + 49 : start.x,
    y: vertical > 0
      ? targetBounds.top - 43
      : vertical < 0 ? targetBounds.bottom + 55 : start.y,
  }
  let pointer = start
  const dispatch = (type: string, point = pointer) => {
    pointer = point
    const event = new Event(type)
    Object.defineProperties(event, {
      pointerId: { value: 19 }, clientX: { value: point.x }, clientY: { value: point.y },
    })
    window.dispatchEvent(event)
  }
  try {
    const pointerDown = new Event('pointerdown', { bubbles: true, cancelable: true })
    Object.defineProperties(pointerDown, {
      pointerId: { value: 19 }, isPrimary: { value: true },
      clientX: { value: start.x }, clientY: { value: start.y },
    })
    source.get('.analysis-drag-handle').element.dispatchEvent(pointerDown)
    for (let step = 1; step <= 12; step += 1) {
      const progress = step / 12
      dispatch('pointermove', {
        x: start.x + (drop.x - start.x) * progress,
        y: start.y + (drop.y - start.y) * progress,
      })
      await flushPromises()
    }
    if (outcome === 'outside') hit = document.body
    else if (outcome === 'origin-list' || outcome === 'origin-list-move') {
      hit = originList
      if (outcome === 'origin-list-move') {
        dispatch('pointermove', { x: drop.x + 1, y: drop.y + 1 })
        await flushPromises()
      }
    }
    if (beforeFinish) await beforeFinish()
    dispatch(outcome === 'cancel' ? 'pointercancel' : 'pointerup')
    await flushPromises()
  } finally {
    if (descriptor) Object.defineProperty(document, 'elementFromPoint', descriptor)
    else Reflect.deleteProperty(document, 'elementFromPoint')
  }
}


beforeEach(() => {
  setLocale('de')
  setUser()
  saveFailure = false
  registryFailuresRemaining = 0
  delayPreferenceWrite = false
  completePreferenceWrite = null
  apiMock.mockReset()
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/analytics/registry') {
      if (registryFailuresRemaining > 0) {
        registryFailuresRemaining -= 1
        return Promise.reject(new Error('registry unavailable'))
      }
      return Promise.resolve({ analyses: registry })
    }
    if (path === '/settings/analysis-preferences' && options?.method === 'PUT') {
      if (saveFailure) return Promise.reject(new Error('write failed'))
      const body = JSON.parse(String(options.body)) as { active_card_ids: string[] }
      const nextIds = [...body.active_card_ids]
      if (delayPreferenceWrite) {
        return new Promise<{ active_card_ids: string[] }>((resolve) => {
          completePreferenceWrite = () => {
            activeCardIds = nextIds
            resolve(analysisPreferencesResponse())
          }
        })
      }
      activeCardIds = nextIds
      return Promise.resolve(analysisPreferencesResponse())
    }
    if (path === '/settings/analysis-preferences') {
      return Promise.resolve(analysisPreferencesResponse())
    }
    if (path.startsWith('/analytics/nutrition/micronutrients')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: { provider_key: 'yazio' }, period_days: 7, period_capabilities: { supports_all_data: false, all_data_start_date: null }, metrics: [
        { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
        { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
      ], series: [] })
    }
    if (path.startsWith('/analytics/weight')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: null, period_capabilities: { supports_all_data: false, all_data_start_date: null }, points: [], summary: { measurement_count: 0, latest: null, change_kg: null, average_kg: null, minimum_kg: null, maximum_kg: null } })
    }
    if (path.startsWith('/analytics/verification/activity')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', period_capabilities: { supports_all_data: false, all_data_start_date: null }, view: 'canonical', days: [] })
    }
    if (path.startsWith('/analytics/nutrition/analysis')) {
      return Promise.resolve({
        start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: null, period_days: 7,
        period_capabilities: { supports_all_data: false, all_data_start_date: null }, days: [],
      })
    }
    if (path.startsWith('/analytics/trends')) return Promise.resolve({ points: [], budget_balance: null })
    return Promise.resolve({})
  })
})

afterEach(() => {
  for (const wrapper of mountedWrappers) {
    if (wrapper.exists()) wrapper.unmount()
  }
  mountedWrappers.clear()
  for (const dispose of disposeAnalysisAuthStores) dispose()
  disposeAnalysisAuthStores.clear()
  vi.unstubAllGlobals()
  if (previousChartAmberStyle !== null) {
    if (previousChartAmberStyle) {
      document.documentElement.style.setProperty('--chart-series-amber', previousChartAmberStyle)
    } else {
      document.documentElement.style.removeProperty('--chart-series-amber')
    }
    previousChartAmberStyle = null
  }
  setThemePreference('system')
  setLocale('de')
})

describe('Analysis V2 hub and catalogue', () => {
  it('renders default cards and leaves no-data catalog metrics selectable', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse')
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(wrapper.text()).not.toContain('Weitere Analysen')
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.find('.analysis-add-card-button').exists()).toBe(true)
    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.get('[data-analysis-catalog-key="vitamin_a_ug"] [data-coverage-status="incomplete"] .analysis-catalog-status-icon').text()).toBe('!')
    expect(wrapper.get('[data-analysis-add="vitamin_a_ug"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-analysis-add="protein_g"]').exists()).toBe(true)
    expect(wrapper.get('[data-analysis-key="water_intake_ml"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.find('[data-analysis-add="weight_kg"]').exists()).toBe(false)
    await wrapper.get('[data-analysis-add="vitamin_a_ug"]').trigger('click')
    await flushPromises()
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'] }])
    wrapper.unmount()
  })
  it('hides only the Micronutrients collection card from the Hub without changing saved preferences', async () => {
    const { wrapper } = await mountAnalysis('/analyse')
    const hiddenCard = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"]')
    const visibleCardIds = cardOrder(wrapper).filter((key) => (
      window.getComputedStyle(wrapper.get(`.analysis-card-list > li[data-card-key="${key}"]`).element).display !== 'none'
    ))

    expect(window.getComputedStyle(hiddenCard.element).display).toBe('none')
    expect(visibleCardIds).toEqual(['weight', 'nutrition', 'activity'])
    expect(activeCardIds).toEqual([...DEFAULT_ACTIVE_CARD_IDS])
    expect(preferenceWrites()).toEqual([])
    wrapper.unmount()
  })
  it('summarizes available micronutrients without repeating daily coverage', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        return Promise.resolve({
          start_date: params.get('start'),
          end_date: params.get('end'),
          selected_provider: null,
          period_days: 7,
          metrics: [
            { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 35, days_with_data: 7, contributors: [] },
            { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
          ],
          series: [],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=7')
    const vitamins = wrapper.get('[data-nutrient-group="vitamin"]')

    expect(vitamins.find('.analysis-micronutrient-count').exists()).toBe(false)
    expect(vitamins.get('[data-nutrient-key="vitamin_a_ug"] .analysis-micronutrient-average').text()).toBe('5 µg')
    expect(wrapper.text()).not.toMatch(/\d+ von \d+ mit Daten|\d+ von \d+ Tagen mit Werten/)
    expect(wrapper.find('[data-analysis-catalog-key]').exists()).toBe(false)
    wrapper.unmount()
  })


  it('renders one mixed list and manages addable categories without leaving the hub', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse', ['weight', 'protein_g'])
    expect(wrapper.text()).toContain('Meine Analysen')
    expect(wrapper.text()).not.toContain('Weitere Analysen')
    expect(cardOrder(wrapper)).toEqual(['weight', 'protein_g'])
    const list = wrapper.get('.analysis-card-list')
    expect(list.element.lastElementChild?.matches('[data-analysis-add-card]')).toBe(true)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.find('.analysis-category-grid').exists()).toBe(false)
    expect(wrapper.find('a[href="/analyse/katalog"]').exists()).toBe(false)
    expect(wrapper.find('.analysis-legacy-links').exists()).toBe(false)
    await openCardManager(wrapper)
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(0)
    await wrapper.get('[data-manager-category="activity"]').trigger('click')
    expect(wrapper.get('[data-manager-category="activity"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(1)
    expect(wrapper.get('[data-manager-group]').attributes('data-manager-group')).toBe('activity')
    const weightOption = wrapper.find('[data-hub-card-option="weight"]')
    expect(weightOption.exists()).toBe(false)
    const activityOption = wrapper.get('[data-hub-card-option="activity"]')
    expect(activityOption.find('.analysis-manager-status').exists()).toBe(false)
    const stepsOption = wrapper.get('[data-hub-card-option="steps"]')
    expect(stepsOption.attributes('aria-disabled')).toBe('true')
    expect(stepsOption.find('button').exists()).toBe(false)
    await wrapper.get('[data-hub-card-option="activity"] button').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse')
    expect(cardOrder(wrapper)).toEqual(['weight', 'protein_g', 'activity'])
    expect(preferenceWrites()).toEqual([['weight', 'protein_g', 'activity']])
  })
  it('lists cards outside the Hub before active cards in each category', async () => {
    const { wrapper } = await mountAnalysis(
      '/analyse',
      ['nutrition', 'meals_today', 'dietary_energy_kcal', 'protein_g'],
    )
    await openCardManager(wrapper)
    await wrapper.get('[data-manager-category="nutrition"]').trigger('click')

    const options = wrapper.findAll('[data-manager-group="nutrition"] [data-hub-card-option]')
    expect(options.map((option) => option.attributes('data-hub-card-option'))).toEqual([
      'carbohydrates_g', 'fat_g', 'fiber_g', 'sugar_g', 'saturated_fat_g',
      'nutrition', 'meals_today', 'dietary_energy_kcal', 'protein_g',
    ])
    expect(options.slice(0, 5).map((option) => option.get('button').text()))
      .toEqual(['Hinzufügen', 'Hinzufügen', 'Hinzufügen', 'Hinzufügen', 'Hinzufügen'])
    expect(options.slice(5).map((option) => option.get('button').text()))
      .toEqual(['Entfernen', 'Entfernen', 'Entfernen', 'Entfernen'])
    wrapper.unmount()
  })

  it('labels the manager and its search as card management', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['weight'])
    await openCardManager(wrapper)
    expect(wrapper.get('#analysis-manager-title').text()).toBe('Karte hinzufügen')
    expect(wrapper.get('.analysis-add-card-button').text()).toBe('Karte hinzufügen')
    const search = wrapper.get('input[type="search"]')
    expect(search.attributes('aria-label')).toBe('Karten durchsuchen')
    expect(search.attributes('placeholder')).toBe('Karten durchsuchen …')
    wrapper.unmount()
  })

  it('shows category tiles, one selected panel, and global search results', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['weight', 'protein_g'])
    await openCardManager(wrapper)

    const categories = wrapper.findAll('.analysis-manager-category-tile')
    expect(categories.map((category) => category.attributes('data-manager-category')))
      .toEqual(['body', 'activity', 'nutrition', 'micronutrients', 'fluid'])
    expect(categories.map((category) => category.find('.analysis-manager-category-icon').attributes('data-icon')))
      .toEqual(['scale', 'activity', 'nutrition', 'atom', 'drop'])
    expect(categories.every((category) => category.find('.analysis-manager-category-icon svg').exists())).toBe(true)
    expect(categories.map((category) => category.attributes('aria-expanded')))
      .toEqual(['false', 'false', 'false', 'false', 'false'])
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(0)

    await wrapper.get('[data-manager-category="body"]').trigger('click')
    expect(wrapper.get('[data-manager-category="body"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(1)
    expect(wrapper.get('[data-manager-group="body"]').isVisible()).toBe(true)
    expect(wrapper.get('[data-hub-card-option="weight"]').isVisible()).toBe(true)
    expect(wrapper.find('[data-hub-card-option="protein_g"]').exists()).toBe(false)

    await wrapper.get('[data-manager-category="body"]').trigger('click')
    expect(wrapper.get('[data-manager-category="body"]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(0)

    await wrapper.get('[data-manager-category="micronutrients"]').trigger('click')
    expect(wrapper.get('[data-manager-category="micronutrients"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-manager-group="micronutrients"]').isVisible()).toBe(true)
    expect(wrapper.get('[data-hub-card-option="vitamin_a_ug"]').isVisible()).toBe(true)

    const search = wrapper.get('input[type="search"]')
    expect(search.attributes('placeholder')).toBe('Karten durchsuchen …')
    await search.setValue('PROTEIN')
    expect(wrapper.find('.analysis-manager-category-grid').exists()).toBe(false)
    expect(wrapper.findAll('[data-manager-group]').map((group) => group.attributes('data-manager-group')))
      .toEqual(['nutrition'])
    expect(wrapper.get('[data-hub-card-option="protein_g"]').isVisible()).toBe(true)
    expect(wrapper.find('[data-hub-card-option="weight"]').exists()).toBe(false)

    await search.setValue('')
    expect(wrapper.find('.analysis-manager-category-grid').exists()).toBe(true)
    expect(wrapper.get('[data-manager-category="micronutrients"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-manager-group="micronutrients"]').isVisible()).toBe(true)

    await search.setValue('BMI')
    expect(wrapper.find('.analysis-manager-category-grid').exists()).toBe(false)
    expect(wrapper.findAll('[data-manager-group]').map((group) => group.attributes('data-manager-group')))
      .toEqual(['body'])
    const unavailable = wrapper.get('[data-hub-card-option="bmi"]')
    expect(unavailable.attributes('aria-disabled')).toBe('true')
    expect(unavailable.get('.analysis-manager-unavailable').text()).toMatch(/nicht verfügbar/iu)
    expect(unavailable.find('button').exists()).toBe(false)

    await search.setValue('kein-treffer')
    expect(wrapper.findAll('[data-manager-group]')).toHaveLength(0)
    expect(wrapper.get('.analysis-manager-empty').text()).toContain('Keine passenden Analysen gefunden')
    await search.setValue('')
    expect(wrapper.find('.analysis-manager-category-grid').exists()).toBe(true)
    expect(wrapper.get('[data-manager-group="micronutrients"]').isVisible()).toBe(true)
    await closeCardManager(wrapper)
    wrapper.unmount()
  })

  it('routes Meals Today to the existing date-specific nutrition view', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const mealsCard = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')
    const title = mealsCard.get('.analysis-card-heading a')
    const today = isoDateInTimeZone('Europe/Berlin')

    expect(title.text()).toBe('Mahlzeiten heute')
    expect(title.attributes('href')).toBe(`/ernaehrung?date=${today}`)
    expect(mealsCard.findAll('a')).toHaveLength(1)
    await title.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/ernaehrung')
    expect(router.currentRoute.value.query.date).toBe(today)
    wrapper.unmount()
  })

  it('renders body and Activity analysis while keeping options in the general catalog', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper')
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))).toBe(false)

    for (const label of [
      'Gewicht', 'Körperfett', 'BMI', 'Muskelmasse', 'Fettfreie Masse',
      'Körperwasser', 'Knochenmasse', 'Taillenumfang', 'Hüftumfang',
    ]) expect(wrapper.text()).toContain(label)

    await router.push('/analyse/aktivitaet')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Aktivität')
    expect(wrapper.text()).not.toMatch(/Schritte|Distanz|Training/)

    await router.push('/analyse/katalog')
    await flushPromises()
    for (const label of [
      'Aktive Energie', 'Schritte', 'Distanz', 'Training',
      'Energie', 'Protein', 'Kohlenhydrate', 'Fett', 'Ballaststoffe', 'Zucker', 'Gesättigte Fettsäuren',
    ]) {
      expect(wrapper.text()).toContain(label)
    }


    await router.push('/analyse/mikronaehrstoffe')
    await flushPromises()
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))).toBe(true)
    expect(wrapper.text()).toContain('Vitamine')
    expect(wrapper.text()).toContain('Mineralstoffe')
    expect(wrapper.get('[data-nutrient-group="vitamin"] h2').text()).toBe('Vitamine')
    expect(wrapper.get('[data-nutrient-group="mineral"] h2').text()).toBe('Mineralstoffe')
    expect(wrapper.find('.analysis-micronutrient-count').exists()).toBe(false)
    for (const state of wrapper.findAll('.analysis-micronutrient-empty')) {
      expect(state.text()).toMatch(/keine Werte im Zeitraum/i)
    }
    expect(wrapper.findAll('[data-nutrient-key]')).toHaveLength(0)
    expect(wrapper.find('.analysis-catalog-search').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not duplicate a micronutrient summary read when category and period change together', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper?period=30')
    apiMock.mockClear()

    await router.push('/analyse/mikronaehrstoffe?period=7')
    await flushPromises()

    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    const summaries = requests.filter((path) => !new URLSearchParams(path.split('?')[1] ?? '').has('metric_key'))
    expect(summaries).toHaveLength(1)
    for (const path of requests) {
      const params = new URLSearchParams(path.split('?')[1])
      const rangeMs = Date.parse(`${params.get('end')}T00:00:00Z`) - Date.parse(`${params.get('start')}T00:00:00Z`)
      expect(rangeMs).toBe(6 * 86_400_000)
    }
    wrapper.unmount()
  })

  it('localizes analysis card labels and actions for English users', async () => {
    setLocale('en')
    const { router, wrapper } = await mountAnalysis('/analyse', ['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(wrapper.text()).toContain('My analyses')
    expect(wrapper.text()).not.toContain('More analyses')
    expect(wrapper.text()).toContain('Add card')
    await openCardManager(wrapper)
    expect(wrapper.get('#analysis-manager-title').text()).toBe('Add card')
    expect(wrapper.get('input[type="search"]').attributes('aria-label')).toBe('Search cards')
    expect(wrapper.get('input[type="search"]').attributes('placeholder')).toBe('Search cards …')
    await wrapper.get('[data-manager-category="body"]').trigger('click')
    expect(wrapper.find('[data-hub-card-option="weight"] .analysis-manager-status').exists()).toBe(true)
    expect(wrapper.get('[data-hub-card-option="weight"] button').text()).toBe('Remove')
    await closeCardManager(wrapper)

    const weightCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(weightCard.get('.analysis-card-heading').text()).toBe('Weight')
    expect(weightCard.get('.analysis-drag-handle').attributes('aria-label')).toBe('Move Weight')
    expect(weightCard.get('.analysis-card-heading a').text()).toBe('Weight')
    expect(weightCard.find('.analysis-card-details').exists()).toBe(false)
    expect(weightCard.get('.analysis-card-heading a').attributes('href')).toContain('/analyse/koerper/gewicht')
    await weightCard.get('.analysis-overflow-trigger').trigger('click')
    const menuActions = weightCard.findAll('.analysis-overflow-menu button')
    expect(menuActions.map((action) => action.text())).toEqual(['Move forward', 'Move back', 'Remove'])
    expect(menuActions.every((action) => action.find('svg').exists())).toBe(true)
    expect(menuActions[0]!.attributes('aria-label')).toBe('Move Weight forward')
    expect(menuActions[0]!.attributes('disabled')).toBeDefined()

    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="weight_kg"]').text()).toContain('Weight')
    expect(wrapper.get('[data-analysis-selected="weight"]').text()).toContain('In My analyses')
    expect(wrapper.get('[data-analysis-key="weight_kg"]').find('[data-analysis-add="weight_kg"]').exists()).toBe(false)
    wrapper.unmount()
  })



  it('loads selected-card previews after a successful workspace retry', async () => {
    registryFailuresRemaining = 1
    const { wrapper } = await mountAnalysis('/analyse', ['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/weight'))).toBe(false)

    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()

    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/weight'))).toBe(true)
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity'])
    wrapper.unmount()
  })

  it('keeps a pending selection visible when navigating from catalog to hub', async () => {
    delayPreferenceWrite = true
    const { router, wrapper } = await mountAnalysis('/analyse/katalog')

    await wrapper.get('[data-analysis-add="vitamin_a_ug"]').trigger('click')
    expect(completePreferenceWrite).toBeTypeOf('function')
    await router.push('/analyse')
    await flushPromises()

    completePreferenceWrite!()
    await flushPromises()

    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    expect(cardOrder(wrapper))
      .toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    wrapper.unmount()
  })


  it('keeps the shared 7/30-day period in the hub query string', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse?period=30')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const sevenDay = wrapper.findAll('.analysis-period-popover .analysis-period-option').find((button) => button.text() === '7 Tage')
    expect(sevenDay).toBeDefined()
    await sevenDay!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('7')
    wrapper.unmount()
  })
  it('rederives rolling Hub readers at the account-local date boundary', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=90&source=synthetic#analysis-category-fluid', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'],
    )
    try {
      const micronutrientRequests = () => apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      const initial = micronutrientRequests()
      expect(initial.length).toBeGreaterThan(0)
      expect(initial.every((path) => {
        const params = new URLSearchParams(path.split('?')[1])
        return params.get('start') === '2026-07-04' && params.get('end') === '2026-10-01'
      })).toBe(true)
      const beforeTimezoneChange = apiMock.mock.calls.length
      useAuthStore().user = { ...useAuthStore().user!, id: 'analysis-utc-user', timezone: 'UTC' }
      await flushPromises()
      const updated = apiMock.mock.calls.slice(beforeTimezoneChange).map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      expect(updated.length).toBeGreaterThan(0)
      expect(updated.every((path) => {
        const params = new URLSearchParams(path.split('?')[1])
        return params.get('start') === '2026-07-03' && params.get('end') === '2026-09-30'
      })).toBe(true)
      expect(router.currentRoute.value.query).toMatchObject({ period: '90', source: 'synthetic' })
      expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('rederives canonical account-local rolling ranges on timezone change and stops reading after logout', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const { router, wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe?period=90&source=synthetic#analysis-category-fluid',
    )
    try {
      const micronutrientRequests = () => apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      const initial = micronutrientRequests()
      expect(initial.length).toBeGreaterThan(0)
      expect(initial.every((path) => {
        const params = new URLSearchParams(path.split('?')[1])
        return params.get('start') === '2026-07-04' && params.get('end') === '2026-10-01'
      })).toBe(true)
      const beforeTimezoneChange = apiMock.mock.calls.length
      useAuthStore().user = { ...useAuthStore().user!, id: 'analysis-utc-user', timezone: 'UTC' }
      await flushPromises()
      const updated = apiMock.mock.calls.slice(beforeTimezoneChange).map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      expect(updated.length).toBeGreaterThan(0)
      expect(updated.every((path) => {
        const params = new URLSearchParams(path.split('?')[1])
        return params.get('start') === '2026-07-03' && params.get('end') === '2026-09-30'
      })).toBe(true)
      expect(router.currentRoute.value.query).toMatchObject({ period: '90', source: 'synthetic' })
      expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
      const readsBeforeLogout = micronutrientRequests().length
      useAuthStore().user = null
      await flushPromises()
      expect(micronutrientRequests()).toHaveLength(readsBeforeLogout)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps a 365-day canonical category range through reader error, retry and empty data', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const fallback = apiMock.getMockImplementation()!
    const summaryRequests: string[] = []
    let attempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key')) {
        summaryRequests.push(path)
        attempts += 1
        if (attempts === 1) return Promise.reject(new Error('synthetic summary outage'))
        const url = new URL(path, 'https://calograph.test')
        return Promise.resolve({
          start_date: url.searchParams.get('start'),
          end_date: url.searchParams.get('end'),
          selected_provider: { provider_key: 'yazio' },
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          period_days: inclusiveDays(path), metrics: [], series: [],
        })
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=365')
    try {
      const expected = '/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31'
      expect(router.currentRoute.value.query.period).toBe('365')
      expect(summaryRequests).toEqual([expected])
      expect(inclusiveDays(summaryRequests[0]!)).toBe(365)
      await wrapper.get('[role="alert"] button').trigger('click')
      await flushPromises()
      expect(summaryRequests).toEqual([expected, expected])
      expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('365')
      expect(wrapper.get('[data-nutrient-group="vitamin"] .analysis-micronutrient-empty').text())
        .toMatch(/keine Werte im Zeitraum/i)
      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('discards a stale canonical Overview response after changing from 365 to 90 days', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const fallback = apiMock.getMockImplementation()!
    const oldSummary = Promise.withResolvers<AnalysisMicronutrientResponse>()
    const summaryRequests: string[] = []
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const url = new URL(path, 'https://calograph.test')
        if (!url.searchParams.has('metric_key')) {
          summaryRequests.push(path)
          if (inclusiveDays(path) === 365) return oldSummary.promise
          return Promise.resolve({
            start_date: url.searchParams.get('start'),
            end_date: url.searchParams.get('end'),
            selected_provider: { provider_key: 'yazio' },
            period_capabilities: { supports_all_data: false, all_data_start_date: null },
            period_days: inclusiveDays(path),
            metrics: [
              { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 1, days_with_data: 1, contributors: [] },
              { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
            ],
            series: [],
          })
        }
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=365')
    try {
      await router.replace({ path: router.currentRoute.value.path, query: { period: '90' } })
      await flushPromises()
      expect(summaryRequests.map(inclusiveDays)).toEqual([365, 90])
      expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('90')
      expect(wrapper.get('#analysis-category-micronutrients .analysis-micronutrient-average').text().trim()).toBe('1 µg')
      oldSummary.resolve({
        start_date: '2025-04-01', end_date: '2026-03-31',
        selected_provider: { provider_key: 'yazio' },
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        period_days: 365,
        metrics: [
          { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 777, days_with_data: 77, contributors: [] },
          { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
        ],
        series: [],
      })
      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('90')
      expect(wrapper.get('#analysis-category-micronutrients .analysis-micronutrient-average').text().trim()).toBe('1 µg')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('keeps a longer destination period when the departed Overview load resolves', async () => {
    const registryRequest = Promise.withResolvers<{ analyses: AnalysisRegistryEntry[] }>()
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => (
      path === '/analytics/registry' ? registryRequest.promise : fallback(path, options)
    ))
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=90')
    try {
      await router.push({
        path: '/analyse/koerper/gewicht',
        query: { period: 'custom', start: '2020-01-01', end: '2026-03-31' },
      })
      await flushPromises()
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2020-01-01', end: '2026-03-31',
      })

      registryRequest.resolve({ analyses: registry as AnalysisRegistryEntry[] })
      await flushPromises()

      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2020-01-01', end: '2026-03-31',
      })
      expect(apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/weight?')))
        .toContain('/analytics/weight?start=2020-01-01&end=2026-03-31')
    } finally {
      wrapper.unmount()
    }
  })

  it('invalidates a Berlin rolling response before a UTC account switch reloads the category', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const fallback = apiMock.getMockImplementation()!
    const oldSummary = Promise.withResolvers<AnalysisMicronutrientResponse>()
    const summaryRequests: string[] = []
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key')) {
        summaryRequests.push(path)
        if (summaryRequests.length === 1) return oldSummary.promise
        const url = new URL(path, 'https://calograph.test')
        return Promise.resolve({
          start_date: url.searchParams.get('start'),
          end_date: url.searchParams.get('end'),
          selected_provider: { provider_key: 'yazio' },
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          period_days: inclusiveDays(path),
          metrics: [
            { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 1, days_with_data: 2, contributors: [] },
            { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
          ],
          series: [],
        })
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=90')
    try {
      const auth = useAuthStore()
      auth.user = { ...auth.user!, id: 'analysis-utc-user', timezone: 'UTC' }
      await flushPromises()
      expect(summaryRequests.map((path) => {
        const url = new URL(path, 'https://calograph.test')
        return [url.searchParams.get('start'), url.searchParams.get('end')]
      })).toEqual([
        ['2026-07-04', '2026-10-01'],
        ['2026-07-03', '2026-09-30'],
      ])
      expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('90')
      expect(wrapper.get('#analysis-category-micronutrients .analysis-micronutrient-average').text().trim()).toBe('0,5 µg')
      oldSummary.resolve({
        start_date: '2026-07-04', end_date: '2026-10-01',
        selected_provider: { provider_key: 'yazio' },
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        period_days: 90,
        metrics: [
          { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 777, days_with_data: 77, contributors: [] },
          { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
        ],
        series: [],
      })
      await flushPromises()
      expect(wrapper.get('#analysis-category-micronutrients .analysis-micronutrient-average').text().trim()).toBe('0,5 µg')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
  it('normalizes a mixed-reader all-data hub URL before the first micronutrient read', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'activity', 'vitamin_a_ug'])
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      .map((path) => new URLSearchParams(path.split('?')[1]))
    expect(requests).toHaveLength(2)
    const summaryRequests = requests.filter((params) => !params.has('metric_key'))
    const metricRequests = requests.filter((params) => params.get('metric_key') === 'vitamin_a_ug')
    expect(summaryRequests).toHaveLength(1)
    expect(metricRequests).toHaveLength(1)
    for (const params of requests) {
      expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -29))
    }
    expect(metricRequests[0]!.get('start')).toBe(summaryRequests[0]!.get('start'))
    expect(metricRequests[0]!.get('end')).toBe(summaryRequests[0]!.get('end'))
    expect(router.currentRoute.value.query.period).toBe('30')
    const picker = wrapper.get('.analysis-period-picker')
    expect(picker.findAll('button[aria-pressed="true"]')).toHaveLength(1)
    expect(picker.get('button[aria-pressed="true"]').text()).toBe('30 Tage')
    wrapper.unmount()
  })
  it('exposes immediately usable desktop quick actions with a selected state and custom access', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper/gewicht?period=90')
    const picker = wrapper.get('.analysis-period-picker')
    const ninetyDay = picker.get('.analysis-period-quick-button[data-period="90"]')
    expect(ninetyDay.attributes('aria-pressed')).toBe('true')
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.get('.analysis-period-custom-form').isVisible()).toBe(true)
    await picker.get('.analysis-period-trigger').trigger('click')
    await picker.get('.analysis-period-quick-button[data-period="7"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('7')
    expect(picker.get('.analysis-period-quick-button[data-period="7"]').attributes('aria-pressed')).toBe('true')
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight?'))
    const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -6))
    wrapper.unmount()
  })
  it('offers 365-day access on the canonical micronutrient category', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=30')
    try {
      const picker = wrapper.get('.analysis-period-picker')
      expect(picker.get('.analysis-period-quick-button[data-period="7"]').attributes('disabled')).toBeUndefined()
      expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
      expect((picker.get('.analysis-period-quick-button[data-period="90"]').element as HTMLButtonElement).disabled).toBe(false)
      expect(picker.getComponent(PeriodPicker).props('maxDays')).toBe(365)
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.get('.analysis-period-custom-form').isVisible()).toBe(true)
      expect(picker.find('.analysis-period-option[data-period="365"]').exists()).toBe(true)
      expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
      const dates = picker.findAll('.analysis-period-custom-form input[type="text"]')
      await dates[0]!.setValue('01.04.2025')
      await dates[1]!.setValue('31.03.2026')
      await picker.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2025-04-01', end: '2026-03-31',
      })
      const summaryRequest = apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?')
          && !new URL(path, 'https://calograph.test').searchParams.has('metric_key'))
        .at(-1)
      const params = new URLSearchParams(summaryRequest!.split('?')[1])
      expect(params.get('start')).toBe('2025-04-01')
      expect(params.get('end')).toBe('2026-03-31')
      expect(inclusiveDays(summaryRequest!)).toBe(365)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it.each([
    { label: 'Hub', path: '/analyse' },
    { label: 'canonical Overview', path: '/analyse/mikronaehrstoffe' },
    { label: 'canonical Detail', path: '/analyse/mikronaehrstoffe/vitamin_a_ug' },
  ])('does not apply an emitted 366-day draft on the $label', async ({ path }) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const { router, wrapper } = await mountAnalysis(
      `${path}?period=365&source=synthetic#analysis-category-fluid`,
    )
    try {
      await flushPromises()
      const picker = wrapper.getComponent(PeriodPicker)
      const committedQuery = { ...router.currentRoute.value.query }
      const committedBounds = { start: picker.props('start'), end: picker.props('end'), period: picker.props('period') }
      const requestsBeforeApply = apiMock.mock.calls.length
      picker.vm.$emit('apply', { period: 'custom', start: '2025-03-31', end: '2026-03-31' })
      await flushPromises()
      expect(router.currentRoute.value.query).toEqual(committedQuery)
      expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
      expect(picker.props()).toMatchObject(committedBounds)
      expect(apiMock.mock.calls).toHaveLength(requestsBeforeApply)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
  it.each([
    { label: 'rolling 90', query: '?period=90', start: '2026-01-01', end: '2026-03-31' },
    { label: 'rolling 180', query: '?period=180', start: '2025-10-03', end: '2026-03-31' },
    { label: 'rolling 365', query: '?period=365', start: '2025-04-01', end: '2026-03-31' },
    {
      label: 'custom 31-day leap range',
      query: '?period=custom&start=2024-02-29&end=2024-03-30',
      start: '2024-02-29', end: '2024-03-30',
    },
    {
      label: 'custom 365-day leap range',
      query: '?period=custom&start=2023-04-01&end=2024-03-30',
      start: '2023-04-01', end: '2024-03-30',
    },
  ])('keeps $label on canonical Overview and Detail direct remounts without a 30-day read', async ({
    query, start, end,
  }) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      const result = fallback(path, options)
      const url = new URL(path, 'https://calograph.test')
      const requestStart = url.searchParams.get('start')
      const requestEnd = url.searchParams.get('end')
      if (!requestStart || !requestEnd || !path.startsWith('/analytics/')) return result
      const days = (Date.parse(`${requestEnd}T00:00:00Z`) - Date.parse(`${requestStart}T00:00:00Z`)) / 86_400_000 + 1
      return Promise.resolve(result).then((response) => ({
        ...response,
        start_date: requestStart,
        end_date: requestEnd,
        ...(path.startsWith('/analytics/nutrition/micronutrients') ? { period_days: days } : {}),
      }))
    })
    try {
      for (const path of ['/analyse/mikronaehrstoffe', '/analyse/mikronaehrstoffe/vitamin_a_ug']) {
        apiMock.mockClear()
        const { router, wrapper } = await mountAnalysis(`${path}${query}`)
        try {
          expect(router.currentRoute.value.query).toMatchObject(
            query.includes('period=custom')
              ? { period: 'custom', start, end }
              : { period: query.slice('?period='.length) },
          )
          const requests = apiMock.mock.calls.map(([request]) => String(request))
            .filter((request) => request.startsWith('/analytics/nutrition/micronutrients?'))
          expect(requests.length).toBeGreaterThan(0)
          expect(requests.every((request) => {
            const params = new URLSearchParams(request.split('?')[1])
            return params.get('start') === start && params.get('end') === end
              && inclusiveDays(request) === (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1
          })).toBe(true)
        } finally {
          wrapper.unmount()
        }
      }
    } finally {
      vi.useRealTimers()
    }
  })
  it.each([
    { label: 'rolling 90-day', query: '?period=90', period: '90', start: '2026-01-01', end: '2026-03-31', preset: '90 Tage' },
    { label: 'rolling 180-day', query: '?period=180', period: '180', start: '2025-10-03', end: '2026-03-31', preset: '6 Monate' },
    { label: 'rolling 365-day', query: '?period=365', period: '365', start: '2025-04-01', end: '2026-03-31', preset: '1 Jahr' },
    {
      label: 'custom leap-year 365-day',
      query: '?period=custom&start=2023-04-01&end=2024-03-30',
      period: 'custom', start: '2023-04-01', end: '2024-03-30', preset: '01.04.2023',
    },
    {
      label: 'custom 31-day leap-year',
      query: '?period=custom&start=2024-02-29&end=2024-03-30',
      period: 'custom', start: '2024-02-29', end: '2024-03-30', preset: '29.02.2024',
    },
  ])('preserves $label range through Summary, category, Metric, breadcrumbs and history', async ({
    query, period, start, end, preset,
  }) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      const result = fallback(path, options)
      const url = new URL(path, 'https://calograph.test')
      const requestStart = url.searchParams.get('start')
      const requestEnd = url.searchParams.get('end')
      if (!requestStart || !requestEnd || !path.startsWith('/analytics/')) return result
      const days = (Date.parse(`${requestEnd}T00:00:00Z`) - Date.parse(`${requestStart}T00:00:00Z`)) / 86_400_000 + 1
      return Promise.resolve(result).then((response) => ({
        ...response,
        start_date: requestStart,
        end_date: requestEnd,
        ...(path.startsWith('/analytics/nutrition/analysis')
          || path.startsWith('/analytics/nutrition/micronutrients') ? { period_days: days } : {}),
        ...(path.startsWith('/analytics/nutrition/micronutrients') && !url.searchParams.has('metric_key')
          ? { metrics: response.metrics.map((metric: AnalysisMicronutrientResponse['metrics'][number]) =>
            metric.metric_key === 'vitamin_a_ug' ? { ...metric, total: 10, days_with_data: 1 } : metric) }
          : {}),
        ...(path.startsWith('/analytics/nutrition/micronutrients')
          ? { series: url.searchParams.has('metric_key')
            ? [
              { date: requestStart, provider_key: 'yazio', value: 10 },
              { date: requestEnd, provider_key: 'yazio', value: 20 },
            ] : [] }
          : {}),
      }))
    })
    const routeQuery = `${query}&source=synthetic#analysis-category-fluid`
    const { router, wrapper } = await mountAnalysis(`/analyse${routeQuery}`, ['weight', 'nutrition', 'activity', 'vitamin_a_ug'])
    const expectSelection = (
      expectedPeriod = period, expectedStart = start, expectedEnd = end, requestOffset: number | null = 0,
    ) => {
      expect(router.currentRoute.value.query).toMatchObject({ period: expectedPeriod, source: 'synthetic' })
      expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
      if (expectedPeriod === 'custom') {
        expect(router.currentRoute.value.query).toMatchObject({ start: expectedStart, end: expectedEnd })
      } else {
        expect(router.currentRoute.value.query.start).toBeUndefined()
        expect(router.currentRoute.value.query.end).toBeUndefined()
      }
      const expectedPreset = expectedPeriod === 'custom'
        ? preset
        : expectedPeriod === '90' ? '90 Tage' : expectedPeriod === '180' ? '6 Monate' : '1 Jahr'
      expect(wrapper.get('.analysis-period-trigger').text()).toContain(expectedPreset)
      if (requestOffset !== null) {
        const requests = apiMock.mock.calls.slice(requestOffset).map(([path]) => String(path))
          .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
        expect(requests.length).toBeGreaterThan(0)
        expect(requests.every((path) => {
          const params = new URLSearchParams(path.split('?')[1])
          return params.get('start') === expectedStart
            && params.get('end') === expectedEnd
            && inclusiveDays(path) === (expectedPeriod === 'custom'
              ? (Date.parse(`${expectedEnd}T00:00:00Z`) - Date.parse(`${expectedStart}T00:00:00Z`)) / 86_400_000 + 1
              : Number(expectedPeriod))
        })).toBe(true)
      }
    }
    try {
      expectSelection()
      await wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectSelection()
      await wrapper.get('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      expectSelection()
      await wrapper.get('a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectSelection()
      await wrapper.get('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      expectSelection()
      await wrapper.get('.analysis-breadcrumbs a[href^="/analyse?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse')
      expectSelection()
      router.back()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      expectSelection()
      router.forward()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse')
      expectSelection()

      await wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      const nextPeriod = period === '365' ? '90' : '365'
      await wrapper.get('.analysis-period-custom-button').trigger('click')
      const requestOffset = apiMock.mock.calls.length
      await wrapper.get(`.analysis-period-option[data-period="${nextPeriod}"]`).trigger('click')
      await flushPromises()
      const nextStart = nextPeriod === '90' ? '2026-01-01' : '2025-04-01'
      expectSelection(nextPeriod, nextStart, '2026-03-31', requestOffset)
      router.back()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse')
      expectSelection(period, start, end, null)
      router.forward()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectSelection(nextPeriod, nextStart, '2026-03-31', null)
      expect(router.currentRoute.value.query.period).toBe(nextPeriod)

    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it.each(['/analyse', '/analyse/mikronaehrstoffe', '/analyse/mikronaehrstoffe/vitamin_a_ug'])(
    'keeps picker Apply on the current history entry at %s', async (path) => {
      vi.useFakeTimers({ toFake: ['Date'] })
      vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
      const { router, wrapper } = await mountAnalysis(
        `${path}?period=90&source=synthetic#analysis-category-micronutrients`,
      )
      const push = vi.spyOn(router, 'push')
      const replace = vi.spyOn(router, 'replace')
      try {
        const picker = wrapper.getComponent(PeriodPicker)
        picker.vm.$emit('apply', { period: '365', ...rangeForDays('2026-03-31', 365) })
        await flushPromises()
        expect(router.currentRoute.value.query).toEqual({ period: '365', source: 'synthetic' })
        expect(router.currentRoute.value.hash).toBe('#analysis-category-micronutrients')
        expect(push).not.toHaveBeenCalled()
        expect(replace).toHaveBeenCalled()
        replace.mockClear()
        picker.vm.$emit('apply', { period: 'custom', start: '2023-04-01', end: '2024-03-30' })
        await flushPromises()
        expect(router.currentRoute.value.query).toEqual({
          period: 'custom', start: '2023-04-01', end: '2024-03-30', source: 'synthetic',
        })
        expect(router.currentRoute.value.hash).toBe('#analysis-category-micronutrients')
        expect(push).not.toHaveBeenCalled()
        expect(replace).toHaveBeenCalled()
      } finally {
        wrapper.unmount()
        push.mockRestore()
        replace.mockRestore()
        vi.useRealTimers()
      }
    },
  )

  it.each([
    { period: '365' as const, ...rangeForDays('2026-03-31', 365) },
    { period: 'custom' as const, start: '2023-04-01', end: '2024-03-30' },
  ])('restores URL-owned history after a 90-day Hub to Overview Apply: $period', async (selection) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const hash = '#analysis-category-micronutrients'
    serveVitaminAData()
    const { router, wrapper } = await mountAnalysis(`/analyse?period=90&source=synthetic${hash}`, ['weight', 'nutrition', 'activity', 'vitamin_a_ug'])
    const hubQuery = { ...router.currentRoute.value.query }
    const push = vi.spyOn(router, 'push')
    try {
      await wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      await wrapper.get('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      push.mockClear()
      const selectedRange = selection.period === 'custom'
        ? { start: selection.start, end: selection.end }
        : rangeForDays('2026-03-31', 365)
      apiMock.mockClear()
      wrapper.getComponent(PeriodPicker).vm.$emit('apply', selection)
      await flushPromises()
      expect(push).not.toHaveBeenCalled()
      const overviewQuery = {
        source: 'synthetic', period: selection.period,
        ...(selection.period === 'custom' ? { start: selection.start, end: selection.end } : {}),
      }
      expect(router.currentRoute.value.query).toEqual(overviewQuery)
      expect(router.currentRoute.value.hash).toBe(hash)
      const expectReaderBounds = (expected: { start: string; end: string }) => {
        const requests = apiMock.mock.calls.map(([path]) => String(path))
          .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
        expect(requests.length).toBeGreaterThan(0)
        expect(requests.every((path) => {
          const params = new URLSearchParams(path.split('?')[1])
          return params.get('start') === expected.start && params.get('end') === expected.end
        })).toBe(true)
      }
      expectReaderBounds(selectedRange)
      apiMock.mockClear()
      router.back()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expect(router.currentRoute.value.query).toEqual(hubQuery)
      expect(router.currentRoute.value.hash).toBe(hash)
      expectReaderBounds(rangeForDays('2026-03-31', 90))
      apiMock.mockClear()
      router.forward()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      expect(router.currentRoute.value.query).toEqual(overviewQuery)
      expect(router.currentRoute.value.hash).toBe(hash)
      expectReaderBounds(selectedRange)
    } finally {
      wrapper.unmount()
      push.mockRestore()
      vi.useRealTimers()
    }
  })

  it('preserves 365 days from the Nutrition Summary category to its Energy Metric', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=365&source=synthetic#analysis-category-fluid', ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal'],
    )
    try {
      await wrapper.get('.analysis-card-list > li[data-card-key="nutrition"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung')
      expect(router.currentRoute.value.query).toMatchObject({ period: '365', source: 'synthetic' })
      let nutritionRequest = apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/analysis?')).at(-1)
      expect(nutritionRequest).toContain('start=2025-04-01')
      expect(nutritionRequest).toContain('end=2026-03-31')
      router.back()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse')
      await wrapper.get('.analysis-card-list > li[data-card-key="dietary_energy_kcal"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung/dietary_energy_kcal')
      expect(router.currentRoute.value.query).toMatchObject({ period: '365', source: 'synthetic' })
      nutritionRequest = apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/analysis?')).at(-1)
      expect(nutritionRequest).toContain('start=2025-04-01')
      expect(nutritionRequest).toContain('end=2026-03-31')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it.each([
    { label: 'rolling', query: '?period=365', period: '365', start: '2025-04-01', end: '2026-03-31' },
    {
      label: 'absolute custom',
      query: '?period=custom&start=2023-04-01&end=2024-03-30',
      period: 'custom', start: '2023-04-01', end: '2024-03-30',
    },
  ])('preserves 365-day $label through Hub catalog and standalone Detail links', async ({
    query, period, start, end,
  }) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    serveVitaminAData()
    const { router, wrapper } = await mountAnalysis(`/analyse${query}&source=synthetic#analysis-category-fluid`, ['weight', 'nutrition', 'activity', 'vitamin_a_ug'])
    const expectRange = () => {
      expect(router.currentRoute.value.query).toMatchObject({ period, source: 'synthetic' })
      expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
      if (period === 'custom') expect(router.currentRoute.value.query).toMatchObject({ start, end })
      const requests = apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      expect(requests.length).toBeGreaterThan(0)
      expect(requests.every((path) => {
        const params = new URLSearchParams(path.split('?')[1])
        return params.get('start') === start && params.get('end') === end
      })).toBe(true)
    }
    try {
      await wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"] .analysis-card-heading a').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectRange()
      router.back()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse')
      expectRange()
      router.forward()
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectRange()
      await wrapper.get('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
      expectRange()
      await wrapper.get('a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug?"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expectRange()
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
  it('rejects a 366-day custom detail URL before reading micronutrients', async () => {
    const { router, wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2025-03-31&end=2026-03-31',
    )
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(requests).toHaveLength(1)
    const params = new URLSearchParams(requests[0]!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -29))
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(router.currentRoute.value.query.start).toBeUndefined()
    expect(wrapper.get('.analysis-period-trigger').text()).toContain('30 Tage')
    wrapper.unmount()
  })
  it('uses 31 days for the all-analysis catalog but permits 365 days in nutrition overview', async () => {
    const catalog = await mountAnalysis('/analyse/katalog?period=365')
    const catalogRequests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(catalogRequests).toHaveLength(1)
    const catalogParams = new URLSearchParams(catalogRequests[0]!.split('?')[1])
    expect(catalogParams.get('start')).toBe(shiftIsoDate(catalogParams.get('end')!, -29))
    expect(catalog.router.currentRoute.value.query.period).toBe('30')
    expect(catalog.wrapper.get('.analysis-period-picker button[aria-pressed="true"]').text()).toBe('30 Tage')
    catalog.wrapper.unmount()

    apiMock.mockClear()
    const nutrition = await mountAnalysis('/analyse/ernaehrung?period=365')
    expect(nutrition.router.currentRoute.value.query.period).toBe('365')
    expect(nutrition.wrapper.get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    await nutrition.wrapper.get('.analysis-period-custom-button').trigger('click')
    expect(nutrition.wrapper.find('.analysis-period-option[data-period="180"]').exists()).toBe(true)
    expect(nutrition.wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    nutrition.wrapper.unmount()
  })
  it.each([
    ['/analyse/katalog?period=90', 31, '30'],
    ['/analyse/katalog?period=custom&start=2026-03-01&end=2026-03-31', 31, 'custom'],
    ['/analyse/katalog?period=custom&start=2026-02-28&end=2026-03-31', 31, '30'],
    ['/analyse/ernaehrung?period=365', 365, '365'],
    ['/analyse/koerper?period=365', 3661, '365'],
    ['/analyse/aktivitaet?period=365', 3661, '365'],
    ['/analyse/mikronaehrstoffe?period=365', 365, '365'],
  ] as const)('keeps analysis destination scope on %s', async (path, maximum, expectedPeriod) => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
    const { router, wrapper } = await mountAnalysis(path)
    try {
      expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(maximum)
      expect(router.currentRoute.value.query.period).toBe(expectedPeriod)
      if (expectedPeriod === 'custom') {
        expect(router.currentRoute.value.query).toMatchObject({ start: '2026-03-01', end: '2026-03-31' })
      } else {
        expect(router.currentRoute.value.query.start).toBeUndefined()
        expect(router.currentRoute.value.query.end).toBeUndefined()
      }
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
  it('keeps Individuell visibly disabled when a selected available reader forbids custom ranges', async () => {
    const originalApi = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({
        analyses: registry.map((entry) => entry.analysis_key === 'vitamin_a_ug'
          ? { ...entry, period_capability: { min_days: 1, max_days: 365, custom_range: false } }
          : entry),
      })
      : originalApi(path, options))
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    const picker = wrapper.get('.analysis-period-picker')
    const custom = picker.get('.analysis-period-custom-button')
    expect(custom.isVisible()).toBe(true)
    expect(custom.attributes('disabled')).toBeDefined()
    expect(picker.get('.analysis-period-quick-button[data-period="7"]').attributes('disabled')).toBeUndefined()
    expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('disabled')).toBeUndefined()
    expect(picker.get('.analysis-period-quick-button[data-period="90"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('.analysis-period-trigger').trigger('click')
    expect(picker.find('.analysis-period-custom-form').exists()).toBe(false)
    wrapper.unmount()
  })
  it('limits the Hub range to fixed Summary and selected Metric capabilities', async () => {
    const { wrapper } = await mountAnalysis(
      '/analyse?period=30',
      ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'],
    )
    const trigger = wrapper.get('.analysis-period-trigger')
    await trigger.trigger('click')

    const picker = wrapper.get('.analysis-period-picker')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    expect(picker.text()).toContain('7 Tage')
    expect(picker.text()).toContain('30 Tage')
    expect(picker.get('.analysis-period-quick-button[data-period="90"]').attributes('disabled')).toBeUndefined()
    expect(picker.text()).not.toContain('Alle Daten')
    expect(picker.find('.analysis-period-custom-form').isVisible()).toBe(true)
    wrapper.unmount()
  })
  it('applies an inclusive 365-day capability-bounded custom hub range', async () => {
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=30',
      ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'],
    )
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.03.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('365')

    await dates[1]!.setValue('28.02.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2020-03-01', end: '2021-02-28',
    })
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(requests.at(-1)).toContain('start=2020-03-01')
    expect(requests.at(-1)).toContain('end=2021-02-28')
    wrapper.unmount()
  })
  it('keeps the fluid category hash when adding the default period query', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse#analysis-category-fluid')
    await flushPromises()
    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    expect(router.currentRoute.value.query.period).toBe('30')
    wrapper.unmount()
  })

  it('renders sparse micronutrient readings as bars at their real date positions', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const metricKey = params.get('metric_key')
        const start = params.get('start') ?? '2026-03-25'
        const dateAtOffset = (days: number) => new Date(Date.parse(`${start}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10)
        const metrics = metricKey === 'vitamin_a_ug'
          ? [{ metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 30, days_with_data: 3, contributors: [] }]
          : [
            { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
            { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
          ]
        return Promise.resolve({
          start_date: start, end_date: params.get('end') ?? dateAtOffset(6),
          selected_provider: { provider_key: 'yazio' }, period_days: 7, metrics,
          series: metricKey === 'vitamin_a_ug'
            ? [
              { date: dateAtOffset(0), provider_key: 'yazio', value: 10 },
              { date: dateAtOffset(6), provider_key: 'yazio', value: 20 },
            ]
            : [],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    expect(card.get('.analysis-metric-value').text()).toBe('10 ug')
    expect(card.text()).not.toMatch(/\d+ Tage mit Daten/i)
    expect(card.get('.analysis-card-heading .analysis-average-mark').attributes('title')).toBe('Durchschnitt pro Tag')
    expect(card.text()).not.toContain('Durchschnitt pro Tag')
    expect(card.findAll('.analysis-mini-chart polyline')).toHaveLength(0)
    const bars = card.findAll('.analysis-mini-chart rect')
    expect(bars).toHaveLength(2)
    const centers = bars.map((bar) => Number(bar.attributes('x')) + Number(bar.attributes('width')) / 2)
    expect(centers[1]! - centers[0]!).toBeCloseTo(120 * 6 / 7)
    const seriesRequests = apiMock.mock.calls.flatMap(([path]) => {
      if (!String(path).startsWith('/analytics/nutrition/micronutrients')) return []
      const key = new URLSearchParams(String(path).split('?')[1] ?? '').get('metric_key')
      return key ? [key] : []
    })
    expect(seriesRequests).toEqual(['vitamin_a_ug'])
    wrapper.unmount()
  })
  it('fills each real line segment to the plot baseline without crossing missing days', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [4, null, 0, 8],
        dates: ['2026-03-28', '2026-03-29', '2026-03-30', '2026-03-31'],
        dateRange: { start: '2026-03-28', end: '2026-03-31' },
        variant: 'line',
        colorRole: 'emerald',
        label: 'Vitamin A, 28. bis 31. März',
        area: true,
      },
    })
    const svg = wrapper.get('svg[role="img"]')
    const baselineY = Number(svg.attributes('viewBox')!.trim().split(/\s+/)[3])
    expect(svg.attributes('aria-label')).toContain('Vitamin A')
    const segments = wrapper.findAll('polyline').map((line) => line.attributes('points')!.split(' '))
    expect(segments).toHaveLength(1)
    expect(segments[0]).toHaveLength(2)
    const [zeroX, zeroY] = segments[0]![0]!.split(',').map(Number)
    const [lastX, lastY] = segments[0]![1]!.split(',').map(Number)
    const circles = wrapper.findAll('circle').map((point) => ({
      x: Number(point.attributes('cx')), y: Number(point.attributes('cy')),
    }))
    const first = circles.find(({ x }) => x < zeroX!)
    expect(first).toBeDefined()
    expect((zeroX! - first!.x) / (lastX! - zeroX!)).toBeCloseTo(2)
    expect(circles).toContainEqual({ x: zeroX!, y: zeroY! })
    expect(zeroY!).toBeGreaterThan(first!.y)
    expect(zeroY!).toBeGreaterThan(lastY!)
    expect(wrapper.findAll('polygon')).toHaveLength(1)
    const areaPoints = wrapper.get('polygon').attributes('points')!.trim().split(/\s+/)
    expect(areaPoints).toEqual([`${zeroX},${baselineY}`, ...segments[0]!, `${lastX},${baselineY}`])
    wrapper.unmount()
  })

  it('keeps shared bar chart defaults unchanged without overview options', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [4, 0, null],
        dates: ['2026-03-28', '2026-03-29', '2026-03-30'],
        dateRange: { start: '2026-03-28', end: '2026-03-30' },
        variant: 'bars',
        colorRole: 'amber',
        label: 'Aktive Energie pro Tag',
      },
    })
    const svg = wrapper.get('svg[role="img"]')
    expect(svg.findAll('rect')).toHaveLength(1)
    expect(svg.findAll('circle')).toHaveLength(1)
    expect(svg.find('rect').attributes('fill')).toBeUndefined()
    wrapper.unmount()
  })
  it('renders a real zero in a singleton Hub day slot without filling missing days', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [0, null, null],
        dates: ['2026-03-01', '2026-03-02', '2026-03-03'],
        dateRange: { start: '2026-03-01', end: '2026-03-03' },
        variant: 'bars',
        colorRole: 'emerald',
        label: 'Selen, Tageswerte',
        maxBarWidth: 5,
        zeroAsBar: true,
      },
    })
    const svg = wrapper.get('svg[role="img"]')
    const bars = svg.findAll('rect')
    expect(bars).toHaveLength(1)
    expect(Number(bars[0]!.attributes('width'))).toBe(5)
    expect(Number(bars[0]!.attributes('x'))).toBeCloseTo(17.5)
    expect(Number(bars[0]!.attributes('height'))).toBe(2)
    expect(bars[0]!.attributes('rx')).toBe('0')
    expect(svg.findAll('circle')).toHaveLength(0)
    wrapper.unmount()
  })


  it('keeps sparse overview bars in their daily slots across a year', () => {
    const dates = Array.from({ length: 365 }, (_, index) => (
      new Date(Date.UTC(2026, 0, 1 + index)).toISOString().slice(0, 10)
    ))
    const values = dates.map((_, index) => (
      index === 0 ? 0 : index === 1 ? 12 : index === 364 ? 3 : null
    ))
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values,
        dates,
        dateRange: { start: '2026-01-01', end: '2026-12-31' },
        variant: 'bars',
        colorRole: 'emerald',
        label: 'Vitamin D daily values',
        seriesColor: 'var(--analysis-micronutrient-lime)',
        zeroAsBar: true,
        barWidthRatio: 0.65,
      },
    })
    const svg = wrapper.get('svg[role="img"]')
    const bars = svg.findAll('rect')
    expect(bars).toHaveLength(3)
    expect(Number(bars[0]!.attributes('height'))).toBe(2)
    expect(bars[0]!.attributes('rx')).toBe('0')
    expect(bars[0]!.attributes('fill')).toBe('var(--analysis-micronutrient-lime)')
    expect(Number(bars[0]!.attributes('width'))).toBeLessThan(
      Number(bars[1]!.attributes('x')) - Number(bars[0]!.attributes('x')),
    )
    expect(Number(bars[2]!.attributes('x'))).toBeGreaterThan(119)
    expect(svg.findAll('circle')).toHaveLength(0)
    wrapper.unmount()
  })


  it('renders gap-separated daily bars with a closely aligned measured trend and no fade', () => {
    const dates = [
      '2026-03-28',
      '2026-03-29',
      '2026-03-30',
      '2026-03-31',
      '2026-04-02',
      '2026-04-03',
      '2026-04-04',
      '2026-04-05',
    ]
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [8, 4, 0, 6, 2, 9, null, 3],
        dates,
        dateRange: { start: '2026-03-28', end: '2026-04-05' },
        variant: 'bars',
        colorRole: 'emerald',
        label: 'Vitamin A daily values',
        seriesColor: 'var(--micronutrient-color)',
        zeroAsBar: true,
        barWidthRatio: 0.5,
        trendOverlay: true,
      },
    })
    const svg = wrapper.get('svg[role="img"]')
    const bars = svg.findAll('rect')
    const lines = svg.findAll('polyline')
    const areas = svg.findAll('polygon')
    const markers = svg.findAll('.analysis-mini-chart__trend-point')
    const halos = svg.findAll('.analysis-mini-chart__trend-point-halo')
    const barCenter = (index: number) => (
      Number(bars[index]!.attributes('x')) + Number(bars[index]!.attributes('width')) / 2
    )
    const lineSegments = lines.map((line) => line.attributes('points')!.trim().split(/\s+/))
    const lineCoordinates = lineSegments.map((segment) => segment.map((point) => point.split(',').map(Number)))
    const expectedIndices = [[0, 1, 2, 3], [4, 5]]
    const expectedValues = [[8, 4, 0, 6], [2, 9]]

    expect(bars).toHaveLength(7)
    expect(Number(bars[5]!.attributes('height'))).toBe(16)
    expect(Number(bars[5]!.attributes('y'))).toBe(16)
    expect(bars.every((bar) => Number(bar.attributes('y')) >= 16)).toBe(true)
    expect(bars.map((bar) => Number(bar.attributes('fill-opacity')))).toEqual([0.66, 0.36, 0.66, 0.36, 0.36, 0.66, 0.66])
    expect(Number(bars[2]!.attributes('height'))).toBe(2)
    expect(Number(bars[2]!.attributes('y'))).toBe(32)
    expect(bars[2]!.attributes('rx')).toBe('0')
    expect(lineSegments.map((segment) => segment.length)).toEqual([4, 2])
    for (const [segmentIndex, indices] of expectedIndices.entries()) {
      for (const [pointIndex, barIndex] of indices.entries()) {
        expect(lineCoordinates[segmentIndex]![pointIndex]![0]).toBeCloseTo(barCenter(barIndex!), 10)
        const lineY = lineCoordinates[segmentIndex]![pointIndex]![1]
        expect(lineY).toBeCloseTo(27.25 - (expectedValues[segmentIndex]![pointIndex]! / 9) * 16, 10)
        expect(Number(bars[barIndex]!.attributes('y')) - lineY).toBeCloseTo(4.75, 10)
      }
    }
    expect(areas).toHaveLength(0)
    expect(markers).toHaveLength(7)
    expect(halos).toHaveLength(7)
    const markerCoordinates = markers.map((marker) => `${marker.attributes('cx')},${marker.attributes('cy')}`)
    expect(markerCoordinates.slice(0, 6)).toEqual(lineSegments.flat())
    const singletonPoint = markerCoordinates[6]!.split(',').map(Number)
    expect(singletonPoint[0]).toBeCloseTo(barCenter(6), 10)
    expect(singletonPoint[1]).toBeCloseTo(27.25 - (3 / 9) * 16, 10)
    expect(halos.map((marker) => `${marker.attributes('cx')},${marker.attributes('cy')}`)).toEqual(markerCoordinates)
    expect(markers.every((marker) => marker.attributes('stroke-width') === '3' && marker.attributes('vector-effect') === 'non-scaling-stroke')).toBe(true)
    expect(halos.every((marker) => marker.attributes('stroke-width') === '5'
      && marker.attributes('stroke') === 'var(--surface)')).toBe(true)
    expect(svg.findAll('linearGradient')).toHaveLength(0)
    const renderedLayers = Array.from(svg.element.children)
    const layerIndex = (selector: string) => renderedLayers.findIndex((element) => element.matches(selector))
    expect(layerIndex('rect')).toBeLessThan(layerIndex('polyline'))
    expect(layerIndex('polyline')).toBeLessThan(layerIndex('.analysis-mini-chart__trend-point-halo'))
    expect(layerIndex('.analysis-mini-chart__trend-point-halo')).toBeLessThan(layerIndex('.analysis-mini-chart__trend-point'))
    expect(svg.findAll('line')).toHaveLength(0)
    wrapper.unmount()
  })
  it('renders only measured macro stacks in teal, blue and amber at their calendar slots', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [80, null, 33],
        dates: ['2026-03-28', '2026-03-29', '2026-03-30'],
        dateRange: { start: '2026-03-28', end: '2026-03-30' },
        variant: 'stacked-bars',
        colorRole: 'teal',
        label: 'Makronährstoffe vom 28. bis 30. März',
        series: [
          { key: 'protein_g', values: [20, null, 0], colorRole: 'teal' },
          { key: 'carbohydrates_g', values: [50, null, 25], colorRole: 'blue' },
          { key: 'fat_g', values: [10, null, 8], colorRole: 'amber' },
        ],
      },
    })
    expect(wrapper.get('svg[role="img"]').attributes('aria-label')).toContain('Makronährstoffe')
    const bars = wrapper.findAll('rect').map((bar) => ({
      x: Number(bar.attributes('x')),
      y: Number(bar.attributes('y')),
      height: Number(bar.attributes('height')),
      fill: bar.attributes('fill') ?? (bar.element as SVGRectElement).style.fill,
    }))
    const first = bars.filter(({ x }) => x < 60)
    const last = bars.filter(({ x }) => x > 60)
    expect(first).toHaveLength(3)
    expect(last).toHaveLength(2)
    expect(new Set(first.map(({ x }) => x)).size).toBe(1)
    expect(new Set(last.map(({ x }) => x)).size).toBe(1)
    expect(first.map(({ fill }) => fill)).toEqual([
      'var(--chart-series-teal)', 'var(--chart-series-blue)', 'var(--chart-series-amber)',
    ])
    expect(last.map(({ fill }) => fill)).toEqual([
      'var(--chart-series-blue)', 'var(--chart-series-amber)',
    ])
    expect(
      first.reduce((sum, bar) => sum + bar.height, 0)
      / last.reduce((sum, bar) => sum + bar.height, 0),
    ).toBeCloseTo(80 / 33)
    wrapper.unmount()
  })
  it('keeps annual macro glyphs inside disjoint calendar slots without filling missing days', () => {
    const dates = Array.from({ length: 365 }, (_, index) => (
      new Date(Date.UTC(2026, 0, 1 + index)).toISOString().slice(0, 10)
    ))
    const protein = dates.map((_, index) => (
      index === 0 || index === 364 ? 0 : index === 2 ? 20 : null
    ))
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: dates.map(() => null),
        dates,
        dateRange: { start: '2026-01-01', end: '2026-12-31' },
        variant: 'stacked-bars',
        colorRole: 'teal',
        label: 'Makronährstoffe für ein Jahr',
        series: [{ key: 'protein_g', values: protein, colorRole: 'teal' }],
      },
    })
    const glyphs = [
      ...wrapper.findAll('rect').map((glyph) => {
        const left = Number(glyph.attributes('x'))
        const right = left + Number(glyph.attributes('width'))
        return { left, right, center: (left + right) / 2 }
      }),
      ...wrapper.findAll('circle').map((glyph) => {
        const center = Number(glyph.attributes('cx'))
        const radius = Number(glyph.attributes('r'))
        return { left: center - radius, right: center + radius, center }
      }),
      ...wrapper.findAll('ellipse').map((glyph) => {
        const center = Number(glyph.attributes('cx'))
        const radius = Number(glyph.attributes('rx'))
        return { left: center - radius, right: center + radius, center }
      }),
    ].sort((left, right) => left.left - right.left)
    const slotWidth = 120 / dates.length
    expect(glyphs).toHaveLength(3)
    expect(glyphs[0]!.center).toBeCloseTo(slotWidth / 2)
    expect(glyphs[1]!.center).toBeCloseTo(slotWidth * 2.5)
    expect(glyphs[2]!.center).toBeCloseTo(slotWidth * 364.5)
    glyphs.forEach(({ left, right }, index) => {
      expect(left).toBeGreaterThanOrEqual(0)
      expect(right).toBeLessThanOrEqual(120)
      if (index > 0) expect(glyphs[index - 1]!.right).toBeLessThan(left)
    })
    wrapper.unmount()
  })


  it('renders calendar line points and separate filled segments without bridging gaps', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [70, null, 0, 69],
        dates: ['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04'],
        dateRange: { start: '2026-03-01', end: '2026-03-04' },
        label: 'Gewicht',
        variant: 'line',
        colorRole: 'violet',
        showPoints: true,
        area: true,
      },
    })
    expect(wrapper.findAll('polyline')).toHaveLength(1)
    expect(wrapper.findAll('circle')).toHaveLength(3)
    expect(wrapper.findAll('polygon')).toHaveLength(1)
    const polygon = wrapper.get('polygon')
    const fill = polygon.attributes('fill') ?? ''
    expect(fill).toMatch(/^url\(#.+\)$/)
    const gradient = wrapper.get('linearGradient')
    expect(gradient.attributes('id')).toBe(fill.slice(5, -1))
    expect(wrapper.findAll('stop').map((stop) => stop.attributes('stop-color')))
      .toEqual(['var(--chart-series-violet)', 'var(--chart-series-violet)', 'var(--chart-series-violet)'])
    const opacities = wrapper.findAll('stop').map((stop) => Number(stop.attributes('stop-opacity')))
    expect(opacities[0]).toBeGreaterThanOrEqual(0.18)
    expect(opacities[0]).toBeGreaterThan(opacities[1]!)
    expect(opacities[1]).toBeGreaterThan(opacities[2]!)
    expect(opacities[2]).toBeGreaterThanOrEqual(0.05)
    expect(wrapper.findAll('circle').map((point) => Number(point.attributes('cx')))).toEqual([0, 80, 120])
    wrapper.unmount()
  })
  it('closes each real Weight area segment at the plot baseline without baseline markers', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [71, 72, null, 69, 70],
        dates: ['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05'],
        dateRange: { start: '2026-03-01', end: '2026-03-05' },
        label: 'Gewicht',
        variant: 'line',
        colorRole: 'violet',
        showPoints: true,
        area: true,
      },
    })
    const segments = wrapper.findAll('polyline').map((line) => (
      line.attributes('points')!.trim().split(/\s+/).map((point) => point.split(',').map(Number))
    ))
    const areas = wrapper.findAll('polygon').map((polygon) => (
      polygon.attributes('points')!.trim().split(/\s+/).map((point) => point.split(',').map(Number))
    ))
    const markers = wrapper.findAll('circle').map((circle) => [
      Number(circle.attributes('cx')), Number(circle.attributes('cy')),
    ])
    const baselineY = Number(wrapper.get('svg').attributes('viewBox')!.trim().split(/\s+/)[3])

    expect(segments).toHaveLength(2)
    expect(areas).toHaveLength(2)
    expect(markers).toHaveLength(4)
    expect(markers).toEqual(segments.flat())
    areas.forEach((area, index) => {
      const segment = segments[index]!
      expect(area).toEqual([
        [segment[0]![0], baselineY],
        ...segment,
        [segment.at(-1)![0], baselineY],
      ])
    })
    expect(markers.every(([, y]) => y !== baselineY)).toBe(true)
    const gradient = wrapper.get('linearGradient')
    expect(gradient.attributes('gradientUnits')).toBe('userSpaceOnUse')
    expect([gradient.attributes('x1'), gradient.attributes('y1'), gradient.attributes('x2'), gradient.attributes('y2')])
      .toEqual(['0', '0', '0', String(baselineY)])
    expect(wrapper.findAll('stop').map((stop) => stop.attributes('offset')))
      .toEqual(['0', '0.6', '1'])
    const opacities = wrapper.findAll('stop').map((stop) => Number(stop.attributes('stop-opacity')))
    expect(opacities[0]).toBeGreaterThanOrEqual(0.18)
    expect(opacities[0]).toBeGreaterThan(opacities[1]!)
    expect(opacities[1]).toBeGreaterThan(opacities[2]!)
    expect(opacities[2]).toBeGreaterThanOrEqual(0.05)
    wrapper.unmount()
  })
  it('renders Weight with monotone smooth segments while preserving missing-day gaps', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [71, 73, 72, null, 69, 70, 70.5],
        dates: ['2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05', '2026-03-06', '2026-03-07'],
        dateRange: { start: '2026-03-01', end: '2026-03-07' },
        label: 'Gewicht',
        variant: 'line',
        colorRole: 'violet',
        showPoints: true,
        area: true,
        smoothLine: true,
      },
    })
    const lines = wrapper.findAll('.analysis-mini-chart__line')
    const areas = wrapper.findAll('.analysis-mini-chart__area')
    expect(lines).toHaveLength(2)
    expect(lines.every((line) => line.element.tagName.toLowerCase() === 'path')).toBe(true)
    expect(lines.every((line) => line.attributes('d')!.includes(' C '))).toBe(true)
    expect(wrapper.findAll('polyline')).toHaveLength(0)
    expect(areas).toHaveLength(2)
    expect(areas.every((area) => area.attributes('d')!.endsWith(' Z'))).toBe(true)
    const markers = wrapper.findAll('circle').map((circle) => Number(circle.attributes('cx')))
    expect(markers).toEqual([0, 20, 40, 80, 100, 120])

    for (const line of lines) {
      const path = line.attributes('d')!
      const startY = Number(path.match(/^M\s+[-+]?[\d.]+\s+([-+]?[\d.]+)/)![1])
      let currentY = startY
      const curves = [...path.matchAll(/C\s+([-+]?[\d.]+)\s+([-+]?[\d.]+),\s+([-+]?[\d.]+)\s+([-+]?[\d.]+),\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)/g)]
      expect(curves.length).toBeGreaterThan(0)
      for (const curve of curves) {
        const controlY1 = Number(curve[2])
        const controlY2 = Number(curve[4])
        const endY = Number(curve[6])
        const minY = Math.min(currentY, endY)
        const maxY = Math.max(currentY, endY)
        expect(controlY1).toBeGreaterThanOrEqual(minY)
        expect(controlY1).toBeLessThanOrEqual(maxY)
        expect(controlY2).toBeGreaterThanOrEqual(minY)
        expect(controlY2).toBeLessThanOrEqual(maxY)
        currentY = endY
      }
    }
    wrapper.unmount()
  })
  it('keeps Weight samples in the upper-middle plot with visible area headroom', () => {
    for (const samples of [[73.4, 75.4, 74.4], [73.4, 73.4], [73.4, 73.4001]]) {
      const wrapper = mount(AnalysisMiniChart, {
        props: {
          values: samples,
          label: 'Gewicht',
          variant: 'line',
          colorRole: 'violet',
          showPoints: true,
          area: true,
          padLineDomain: true,
        },
      })
      const linePoints = wrapper.get('polyline').attributes('points')!.trim().split(/\s+/)
        .map((point) => point.split(',').map(Number))
      const markers = wrapper.findAll('circle').map((circle) => [
        Number(circle.attributes('cx')), Number(circle.attributes('cy')),
      ])
      const markerY = markers.map(([, y]) => y)
      const areaPoints = wrapper.get('polygon').attributes('points')!.trim().split(/\s+/)
        .map((point) => point.split(',').map(Number))
      const baselineY = Number(areaPoints.at(-1)![1])
      const chartBottomY = Number(wrapper.get('svg').attributes('viewBox')!.trim().split(/\s+/)[3])

      expect(markers).toHaveLength(samples.length)
      expect(markers).toEqual(linePoints)
      expect(baselineY).toBe(chartBottomY)
      expect(baselineY - Math.max(...markerY)).toBeGreaterThanOrEqual(9)
      expect(Math.min(...markerY) - 6).toBeGreaterThanOrEqual(2)
      expect(areaPoints[0]).toEqual([linePoints[0]![0], baselineY])
      expect(areaPoints.slice(1, -1)).toEqual(linePoints)
      expect(areaPoints.at(-1)).toEqual([linePoints.at(-1)![0], baselineY])
      wrapper.unmount()
    }
  })
  it('uses the Hub line padding below and above the observed range', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [0, 100],
        label: 'Energy',
        variant: 'line',
        colorRole: 'violet',
        hubLineDomainPaddingStep: 1,
      },
    })
    const points = wrapper.get('polyline').attributes('points')!.trim().split(/\s+/)
    const yPositions = points.map((point) => Number(point.split(',')[1]))

    expect(yPositions[0]).toBeCloseTo(30)
    expect(yPositions[1]).toBeCloseTo(10)
    wrapper.unmount()
  })

  it('keeps nearly flat Hub line samples around the display-resolution floor', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [5, 5.00001],
        label: 'Protein',
        variant: 'line',
        colorRole: 'teal',
        hubLineDomainPaddingStep: 0.1,
      },
    })
    const points = wrapper.get('polyline').attributes('points')!.trim().split(/\s+/)
    const yPositions = points.map((point) => Number(point.split(',')[1]))

    expect(yPositions.every(Number.isFinite)).toBe(true)
    expect(Math.max(...yPositions) - Math.min(...yPositions)).toBeLessThan(0.1)
    expect(yPositions.every((y) => y > 6 && y < 32)).toBe(true)
    wrapper.unmount()
  })



  it('always renders sparse Weight readings as a line with area and small markers', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/weight')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-01-01'
        const nextDate = new Date(Date.parse(`${start}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        const end = params.get('end') ?? nextDate
        return Promise.resolve({
          start_date: start, end_date: end, selected_provider: { provider_key: 'apple_health' },
          points: [{ date: start, weight_kg: 71 }, { date: nextDate, weight_kg: 72 }],
          summary: { measurement_count: 2, latest: { date: nextDate, weight_kg: 72 }, change_kg: 1, average_kg: 71.5, minimum_kg: 71, maximum_kg: 72 },
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const chart = wrapper.get('.analysis-card-list > li[data-card-key="weight"] .analysis-mini-chart')

    expect(chart.findAll('rect')).toHaveLength(0)
    expect(chart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(chart.findAll('.analysis-mini-chart__area')).toHaveLength(1)
    expect(chart.findAll('circle')).toHaveLength(2)
    expect(chart.findAll('circle').every((point) => Number(point.attributes('r')) === 1.25)).toBe(true)
    wrapper.unmount()
  })
  it('renders sparse Activity Metric charts without technical coverage footers', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/verification/activity')) {
        const start = new URLSearchParams(path.split('?')[1] ?? '').get('start') ?? '2026-03-25'
        return Promise.resolve({
          start_date: start,
          end_date: start,
          view: 'canonical',
          days: [{ date: start, canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 100, record_count: 1, source_types: [] }, providers: [] }],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="activity"]')

    expect(card.text()).not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    expect(card.attributes('data-coverage-status')).toBeUndefined()
    expect(card.find('.analysis-mini-chart--amber').exists()).toBe(true)
    wrapper.unmount()
  })

  it('omits technical coverage footers for full-range Activity Metrics', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/verification/activity')) {
        const start = new URLSearchParams(path.split('?')[1] ?? '').get('start') ?? '2026-03-25'
        const days = Array.from({ length: 7 }, (_, index) => {
          const date = new Date(Date.parse(`${start}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10)
          return { date, canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: index, record_count: 1, source_types: [] }, providers: [] }
        })
        return Promise.resolve({ start_date: start, end_date: days[6]!.date, view: 'canonical', days })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="activity"]')

    expect(card.text()).not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    expect(card.attributes('data-coverage-status')).toBeUndefined()
    wrapper.unmount()
  })


  it('names the weight drag and overflow controls separately from decorative icons', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    const trigger = card.get('.analysis-overflow-trigger')
    const triggerElement = trigger.element as HTMLButtonElement
    expect(card.get('.analysis-card-icon').attributes('aria-hidden')).toBe('true')
    expect(card.get('.analysis-drag-handle').attributes('aria-label')).toMatch(/Gewicht/)
    expect(trigger.attributes('aria-label')).toMatch(/Gewicht/)
    triggerElement.focus()
    await trigger.trigger('click')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(triggerElement)
    wrapper.unmount()
  })

  it('filters localized nutrition and coming-soon body labels without enabling unavailable entries', async () => {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: registry.map((entry) => entry.analysis_key === 'protein_g'
        ? { ...entry, status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.analysis', period_capability: { min_days: 1, max_days: 365, custom_range: true } }
        : entry) })
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse/katalog', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const search = wrapper.get('input[type="search"]')
    await search.setValue('pRoTeIn')
    expect(wrapper.findAll('[data-analysis-catalog-key]').map((entry) => entry.attributes('data-analysis-catalog-key')))
      .toEqual(['protein_g'])
    expect(wrapper.findAll('.analysis-catalog-category-heading').map((heading) => heading.text()))
      .toEqual(['Ernährung'])
    expect(wrapper.get('[data-analysis-catalog-key="protein_g"] a').attributes('href')).toContain('/analyse/ernaehrung/protein_g')
    expect(wrapper.get('[data-analysis-add="protein_g"]').attributes('disabled')).toBeUndefined()
    await search.setValue('TÄGLICHE')
    expect(wrapper.find('[data-analysis-catalog-key="protein_g"]').exists()).toBe(true)
    await search.setValue('kÖrPeRfEtT')
    expect(wrapper.find('[data-analysis-catalog-key="protein_g"]').exists()).toBe(false)
    const unavailable = wrapper.get('[data-analysis-catalog-key="fat_ratio_pct"]')
    expect(unavailable.attributes('aria-disabled')).toBe('true')
    expect(unavailable.find('a, [data-analysis-add]').exists()).toBe(false)
    await search.setValue('no-such-analysis')
    expect(wrapper.findAll('[data-analysis-catalog-key]')).toHaveLength(0)
    expect(wrapper.findAll('.analysis-catalog-category-heading')).toHaveLength(0)
    expect(wrapper.findAll('[role="status"]').some((status) => status.text().includes('Keine passenden Analysen gefunden.')))
      .toBe(true)
    await search.setValue('')
    expect(wrapper.find('[data-analysis-selected="weight"]').exists()).toBe(true)
    expect(wrapper.findAll('.analysis-catalog-category-heading').map((heading) => heading.text()))
      .toEqual(['Körper', 'Aktivität', 'Ernährung', 'Mikronährstoffe', 'Flüssigkeit'])
  })

  it('never offers an add or detail action for unknown or unavailable registry entries', async () => {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: [...registry, {
        analysis_key: 'unknown_metric', category_key: 'body', domain_key: 'unknown_metric',
        status: 'available', card_capable: true, detail_capable: true, reader_key: null, period_capability: null,
      }] })
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse/katalog')
    for (const key of ['unknown_metric', 'steps', 'fat_ratio_pct', 'water_intake_ml']) {
      const entry = wrapper.find(`[data-analysis-catalog-key="${key}"]`)
      if (entry.exists()) expect(entry.findAll('a, [data-analysis-add]')).toHaveLength(0)
    }
    expect(wrapper.find('[data-analysis-catalog-key="absent_metric"]').exists()).toBe(false)
  })

  it('opens the real weight and activity details from their links but not from drag handles', async () => {
    const { wrapper, router } = await mountAnalysis('/analyse?period=custom&start=2026-03-25&end=2026-03-31', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const weight = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    await weight.get('.analysis-drag-handle').trigger('click')
    expect(router.currentRoute.value.path).toBe('/analyse')
    await weight.get('.analysis-card-heading a').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/koerper/gewicht')
    expect(wrapper.find('.weight-summary-primary').exists()).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-03-25', end: '2026-03-31' })
    await router.push('/analyse?period=custom&start=2026-03-25&end=2026-03-31')
    await flushPromises()
    await wrapper.get('.analysis-card-list > li[data-card-key="activity"] .analysis-card-heading a').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/aktivitaet/aktive-energie')
    expect(wrapper.find('.activity-summary').exists()).toBe(true)
  })

  it('disables all card controls while a mixed order write is pending', async () => {
    delayPreferenceWrite = true
    const { wrapper } = await mountAnalysis('/analyse', ['weight', 'protein_g', 'activity', 'micronutrients'])
    const activity = wrapper.get('.analysis-card-list > li[data-card-key="activity"]')
    await activity.get('.analysis-overflow-trigger').trigger('click')
    await activity.findAll('.analysis-overflow-menu button')[0]!.trigger('click')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['weight', 'activity', 'protein_g', 'micronutrients'])
    expect(wrapper.findAll('.analysis-drag-handle').every((handle) => handle.attributes('disabled') !== undefined)).toBe(true)
    await activity.get('.analysis-overflow-trigger').trigger('click')
    const move = activity.findAll('.analysis-overflow-menu button')[0]!
    expect(move.attributes('disabled')).toBeDefined()
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: ['weight', 'activity', 'protein_g', 'micronutrients'] }])
    completePreferenceWrite!()
    await flushPromises()
    expect(move.attributes('disabled')).toBeUndefined()
    expect(activeCardIds).toEqual(['weight', 'activity', 'protein_g', 'micronutrients'])
  })

  it('moves Nutrition relative to visible Hub cards while preserving the hidden collection preference', async () => {
    const { wrapper } = await mountAnalysis('/analyse')
    const nutrition = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"]')
    await nutrition.get('.analysis-overflow-trigger').trigger('click')

    const moveBackward = nutrition.findAll('.analysis-overflow-menu button')[1]!
    expect(moveBackward.attributes('disabled')).toBeUndefined()
    await moveBackward.trigger('click')
    await flushPromises()

    expect(activeCardIds).toEqual(['weight', 'activity', 'micronutrients', 'nutrition'])
    expect(cardOrder(wrapper).filter((key) => (
      window.getComputedStyle(wrapper.get(`.analysis-card-list > li[data-card-key="${key}"]`).element).display !== 'none'
    ))).toEqual(['weight', 'activity', 'nutrition'])
    expect(preferenceWriteBodies()).toEqual([{
      active_card_ids: ['weight', 'activity', 'micronutrients', 'nutrition'],
    }])
    wrapper.unmount()
  })

  it('restores focus to a visible Hub card after removing Nutrition', async () => {
    const { wrapper } = await mountAnalysis('/analyse')

    await removeHubCard(wrapper, 'nutrition')
    await flushPromises()

    expect(document.activeElement).toBe(
      wrapper.get('.analysis-card-list > li[data-card-key="activity"] .analysis-overflow-trigger').element,
    )
    wrapper.unmount()
  })

})

describe('Analysis V1 hub summaries', () => {
  const start = '2026-03-01'
  const end = '2026-03-30'
  const rangeQuery = `?period=custom&start=${start}&end=${end}`
  const availableRegistry: AnalysisRegistryEntry[] = [
    ...registry.map((entry) => entry.category_key === 'nutrition'
      ? {
        ...entry, status: 'available' as const, card_capable: true, detail_capable: true,
        reader_key: 'analytics.nutrition.analysis',
        period_capability: { min_days: 1, max_days: 365, custom_range: true },
      }
      : entry),
    {
      analysis_key: 'vitamin_c_mg', category_key: 'micronutrients', domain_key: 'vitamin_c_mg',
      status: 'available', card_capable: true, detail_capable: true,
      reader_key: 'analytics.nutrition.micronutrients',
      period_capability: { min_days: 1, max_days: 365, custom_range: true },
    },
  ] as AnalysisRegistryEntry[]
  const metric = (
    metric_key: string, label: string, category: 'vitamin' | 'mineral',
    unit: 'mg' | 'ug', total: number | null, days_with_data: number,
  ) => ({
    metric_key, label, category, unit, eu_nrv: null, total, days_with_data,
    contributors: days_with_data ? [{ provider_key: 'yazio', days_with_data }] : [],
  })
  const vitaminA = metric('vitamin_a_ug', 'Vitamin A', 'vitamin', 'ug', 1200, 12)
  const calcium = metric('calcium_mg', 'Calcium', 'mineral', 'mg', 300, 3)
  const vitaminC = metric('vitamin_c_mg', 'Vitamin C', 'vitamin', 'mg', null, 0)
  const weight: WeightResponse = {
    start_date: start, end_date: end, selected_provider: { provider_key: 'apple_health' },
    period_capabilities: { supports_all_data: false, all_data_start_date: null },
    points: Array.from({ length: 27 }, (_, index) => ({
      date: shiftIsoDate(start, index), weight_kg: index === 26 ? 71.4 : index === 0 ? 72.5 : 72,
    })),
    summary: {
      measurement_count: 27, latest: { date: '2026-03-27', weight_kg: 71.4 },
      change_kg: -1.1, average_kg: 72, minimum_kg: 71.4, maximum_kg: 72.5,
    },
  }
  const activity: VerificationActivityResponse = {
    start_date: start, end_date: end, view: 'canonical',
    period_capabilities: { supports_all_data: false, all_data_start_date: null },
    days: Array.from({ length: 27 }, (_, index) => ({
      date: shiftIsoDate(start, index),
      canonical: {
        provider_key: 'yazio' as const, status: 'available' as const,
        active_energy_kcal: index === 0 ? 0 : 120, record_count: 1,
        source_types: ['yazio_export_v1' as const],
      },
      providers: [{
        provider_key: 'yazio' as const, status: 'available' as const,
        active_energy_kcal: index === 0 ? 0 : 120, record_count: 1,
        source_types: ['yazio_export_v1' as const],
      }],
    })),
  }
  const nutrition: AnalysisNutritionResponse = {
    start_date: start, end_date: end, selected_provider: { provider_key: 'yazio' }, period_days: 30,
    period_capabilities: { supports_all_data: false, all_data_start_date: null },
    days: [
      {
        date: start, provider_key: 'yazio', dietary_energy_kcal: 410,
        protein_g: 20, carbohydrates_g: 50, fat_g: 10,
        fiber_g: 4, sugar_g: 6, saturated_fat_g: 2,
        meals: [{
          event_id: 'hub-oats', local_date: start, serving_amount: null, serving_unit: null,
          provider_key: 'yazio', local_time: '08:15:00', meal_type: 'breakfast',
          display_name: 'Haferflocken', dietary_energy_kcal: 410,
          protein_g: 20, carbohydrates_g: 50, fat_g: 10,
          fiber_g: 4, sugar_g: 6, saturated_fat_g: 2,
        }],
      },
      {
        date: '2026-03-03', provider_key: 'yazio', dietary_energy_kcal: 590,
        protein_g: 40, carbohydrates_g: 60, fat_g: 20,
        fiber_g: 6, sugar_g: 8, saturated_fat_g: 3, meals: [],
      },
      {
        date: '2026-03-04', provider_key: null, dietary_energy_kcal: null,
        protein_g: null, carbohydrates_g: null, fat_g: null,
        fiber_g: null, sugar_g: null, saturated_fat_g: null, meals: [],
      },
    ],
  }
  const micronutrientResponse = (metricKey: string | null): AnalysisMicronutrientResponse => ({
    start_date: start, end_date: end, selected_provider: { provider_key: 'yazio' },
    period_capabilities: { supports_all_data: false, all_data_start_date: null },
    period_days: 30,
    metrics: metricKey === 'vitamin_a_ug' ? [vitaminA]
      : metricKey === 'calcium_mg' ? [calcium]
        : metricKey === 'vitamin_c_mg' ? [vitaminC]
          : [vitaminA, calcium, vitaminC],
    // The unfiltered summary deliberately contains NO day series.
    series: metricKey === 'vitamin_a_ug'
      ? Array.from({ length: 12 }, (_, index) => ({
        date: shiftIsoDate(start, index), provider_key: 'yazio', value: 100,
      }))
      : metricKey === 'calcium_mg'
        ? [0, 2, 4].map((index) => ({
          date: shiftIsoDate(start, index), provider_key: 'yazio', value: 100,
        }))
        : [],
  })
  function serveSeededHub(strictRange = false) {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: availableRegistry })
      if (path.startsWith('/analytics/')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        if (strictRange && (params.get('start') !== start || params.get('end') !== end)) {
          return Promise.reject(new Error('Wrong analysis range'))
        }
      }
      if (path.startsWith('/analytics/weight?')) return Promise.resolve(structuredClone(weight))
      if (path.startsWith('/analytics/verification/activity?')) return Promise.resolve(structuredClone(activity))
      if (path.startsWith('/analytics/nutrition/analysis?')) return Promise.resolve(structuredClone(nutrition))
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const key = new URLSearchParams(path.split('?')[1] ?? '').get('metric_key')
        return Promise.resolve(micronutrientResponse(key))
      }
      return fallback(path, options)
    })
  }

  function mealsTodayResponse(
    today: string,
    meals: AnalysisNutritionResponse['days'][number]['meals'],
    total: number | null,
  ): AnalysisNutritionResponse {
    return {
      ...nutrition,
      start_date: today,
      end_date: today,
      period_days: 1,
      days: [{
        ...nutrition.days[0]!,
        date: today,
        dietary_energy_kcal: total,
        meals,
      }],
    }
  }

  function mealEntry(
    name: string | null,
    localTime: string | null,
    kcal: number | null,
    mealType: string | null = 'snack',
  ) {
    return {
      event_id: `synthetic-${localTime ?? 'untimed'}-${name ?? 'unnamed'}`,
      local_date: isoDateInTimeZone('Europe/Berlin'),
      serving_amount: null,
      serving_unit: null,
      provider_key: 'yazio',
      local_time: localTime,
      meal_type: mealType,
      display_name: name,
      dietary_energy_kcal: kcal,
      protein_g: null,
      carbohydrates_g: null,
      fat_g: null,
      fiber_g: null,
      sugar_g: null,
      saturated_fat_g: null,
    } satisfies AnalysisNutritionResponse['days'][number]['meals'][number]
  }

  function serveMealsToday(
    meals: AnalysisNutritionResponse['days'][number]['meals'],
    total: number | null,
  ): string {
    serveSeededHub()
    const today = isoDateInTimeZone('Europe/Berlin')
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/analysis?')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        if (params.get('start') === today && params.get('end') === today) {
          return Promise.resolve(mealsTodayResponse(today, meals, total))
        }
      }
      return fallback(path, options)
    })
    return today
  }

  it('groups today food entries into ordered meal occasions and keeps the daily total', async () => {
    serveMealsToday([
      mealEntry('Oatly Barista Edition Lighter', '12:00:00', 86, 'lunch'),
      mealEntry('Corny Protein Erdnuss', '08:15:00', 20, 'breakfast'),
      mealEntry(null, '08:30:00', 30, 'breakfast'),
      mealEntry('süssstoff', '08:00:00', 15, 'breakfast'),
    ], 250)
    const { wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')
    const groups = card.findAll('.analysis-meal-list > li')

    expect(card.get('.analysis-metric-value').text()).toBe('250 kcal')
    expect(card.get('.analysis-meal-count').text()).toBe('2 Mahlzeiten · 4 Einträge')
    expect(groups.map((group) => group.get('.analysis-meal-group-label').text()))
      .toEqual(['Frühstück', 'Mittagessen'])
    expect(groups.map((group) => group.get('.analysis-meal-entry-count').text()))
      .toEqual(['3 Einträge', '1 Eintrag'])
    expect(groups.map((group) => group.get('.analysis-meal-group-kcal').text()))
      .toEqual(['65 kcal', '86 kcal'])
    expect(card.text()).not.toContain('Oatly Barista Edition Lighter')
    expect(card.text()).not.toContain('Corny Protein Erdnuss')
    expect(card.text()).not.toContain('süssstoff')
    wrapper.unmount()
  })

  it('orders all normalized types and safely groups unknown types when calories are missing', async () => {
    serveMealsToday([
      mealEntry('Unknown meal', '21:00:00', 9, 'brunch'),
      mealEntry('Snack', '16:00:00', 10, 'snack'),
      mealEntry('Dinner', '19:00:00', 0, 'dinner'),
      mealEntry('Lunch', '12:00:00', null, 'lunch'),
      mealEntry('Breakfast B', '08:30:00', null, 'breakfast'),
      mealEntry(null, null, 3, null),
      mealEntry('Breakfast A', '08:00:00', 4, ' Breakfast '),
    ], 36)
    const { wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')
    const groups = card.findAll('.analysis-meal-list > li')

    expect(card.get('.analysis-meal-count').text()).toBe('5 Mahlzeiten · 7 Einträge')
    expect(groups.map((group) => group.get('.analysis-meal-group-label').text()))
      .toEqual(['Frühstück', 'Mittagessen', 'Abendessen', 'Snack', 'Ohne Kategorie'])
    expect(groups.map((group) => group.get('.analysis-meal-entry-count').text()))
      .toEqual(['2 Einträge', '1 Eintrag', '1 Eintrag', '1 Eintrag', '2 Einträge'])
    expect(groups[0]!.find('.analysis-meal-group-kcal').exists()).toBe(false)
    expect(groups[1]!.find('.analysis-meal-group-kcal').exists()).toBe(false)
    expect(groups[2]!.get('.analysis-meal-group-kcal').text()).toBe('0 kcal')
    expect(groups[3]!.get('.analysis-meal-group-kcal').text()).toBe('10 kcal')
    expect(groups[4]!.get('.analysis-meal-group-kcal').text()).toBe('12 kcal')
    expect(card.text()).not.toContain('brunch')
    wrapper.unmount()
  })

  it('localizes meal types and the uncategorized fallback in English', async () => {
    setLocale('en')
    serveMealsToday([
      mealEntry('Coffee', '08:00:00', 25, 'breakfast'),
      mealEntry(null, '12:00:00', 5, 'provider-special-type'),
    ], 30)
    const { wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')
    const groups = card.findAll('.analysis-meal-list > li')

    expect(groups.map((group) => group.get('.analysis-meal-group-label').text()))
      .toEqual(['Breakfast', 'Uncategorized'])
    expect(card.text()).not.toContain('provider-special-type')
    wrapper.unmount()
  })


  it.each([
    {
      locale: 'de' as const,
      meals: [mealEntry('Breakfast', '08:00:00', 10, 'breakfast')],
      expected: '1 Mahlzeit · 1 Eintrag',
    },
    {
      locale: 'de' as const,
      meals: [
        mealEntry('Breakfast', '08:00:00', 10, 'breakfast'),
        mealEntry('Lunch', '12:00:00', 20, 'lunch'),
      ],
      expected: '2 Mahlzeiten · 2 Einträge',
    },
    {
      locale: 'en' as const,
      meals: [mealEntry('Breakfast', '08:00:00', 10, 'breakfast')],
      expected: '1 meal · 1 entry',
    },
    {
      locale: 'en' as const,
      meals: [
        mealEntry('Breakfast', '08:00:00', 10, 'breakfast'),
        mealEntry('Lunch', '12:00:00', 20, 'lunch'),
      ],
      expected: '2 meals · 2 entries',
    },
  ])('localizes meal occasion and entry counts in $locale', async ({ locale, meals, expected }) => {
    setLocale(locale)
    serveMealsToday(meals, 30)
    const { wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')

    expect(card.get('.analysis-meal-count').text()).toBe(expected)
    wrapper.unmount()
  })

  it('shows a localized no-entry state while retaining the independent daily total', async () => {
    serveMealsToday([], 142)
    const { wrapper } = await mountAnalysis('/analyse', [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today'])
    const card = wrapper.get('.analysis-card-list > li[data-card-key="meals_today"]')

    expect(card.get('.analysis-metric-value').text()).toBe('142 kcal')
    expect(card.get('.analysis-meal-empty').text()).toBe('Keine Ernährungseinträge für heute.')
    expect(card.find('.analysis-meal-count').exists()).toBe(false)
    expect(card.find('.analysis-meal-list').exists()).toBe(false)
    wrapper.unmount()
  })



  function fixedReaderPaths() {
    const paths = apiMock.mock.calls.map(([path]) => String(path))
    const prefixes = [
      '/analytics/weight?',
      '/analytics/nutrition/analysis?',
      '/analytics/nutrition/micronutrients?',
      '/analytics/verification/activity?',
    ]
    return prefixes.map((prefix) => paths.find((path) => path.startsWith(prefix)
      && (prefix !== '/analytics/nutrition/micronutrients?'
        || !new URL(path, 'https://calograph.test').searchParams.has('metric_key')))
    )
  }


  it('loads all four fixed Summary readers with a 365-day URL and no Metric selection', async () => {
    serveSeededHub()
    const { router, wrapper } = await mountAnalysis('/analyse?period=365', [...DEFAULT_ACTIVE_CARD_IDS])
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    const requests = fixedReaderPaths()
    expect(requests).toHaveLength(4)
    for (const path of requests) {
      expect(path).toBeDefined()
      expect(inclusiveDays(path!)).toBe(365)
    }
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/weight?')))
      .toHaveLength(1)
    expect(preferenceWrites()).toEqual([])
    wrapper.unmount()
  })

  it.each([
    { label: 'no Metric cards', keys: [] },
    { label: 'Weight', keys: ['weight', 'nutrition', 'micronutrients', 'activity'] },
    { label: 'Nutrition', keys: ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'] },
    { label: 'Micronutrients', keys: ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'] },
  ])('keeps fixed Summary readers and a 365-day limit for $label selection', async ({ keys }) => {
    serveSeededHub()
    const { wrapper } = await mountAnalysis('/analyse?period=365', keys)
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    const requests = fixedReaderPaths()
    expect(requests).toHaveLength(4)
    for (const path of requests) {
      expect(path).toBeDefined()
      expect(inclusiveDays(path!)).toBe(365)
    }
    wrapper.unmount()
  })

  it('intersects selected Metric custom-range limits with the fixed Summary readers', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: availableRegistry.map((entry) => entry.analysis_key === 'protein_g'
        ? { ...entry, period_capability: { min_days: 1, max_days: 365, custom_range: false } }
        : entry) })
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    expect(wrapper.getComponent(PeriodPicker).props('customRange')).toBe(false)
    expect(fixedReaderPaths().every(Boolean)).toBe(true)
    wrapper.unmount()
  })

  it('includes an unselected fixed Summary representative in the capability intersection', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: availableRegistry.map((entry) => entry.analysis_key === 'weight_kg'
        ? { ...entry, period_capability: { min_days: 1, max_days: 90, custom_range: true } }
        : entry) })
      : fallback(path, options))
    const { router, wrapper } = await mountAnalysis('/analyse?period=90', [...DEFAULT_ACTIVE_CARD_IDS])
    expect(router.currentRoute.value.query.period).toBe('90')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(90)
    expect(fixedReaderPaths().every(Boolean)).toBe(true)
    wrapper.unmount()
  })

  it('ignores a stale Hub Summary response after the URL changes to 365 days', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const oldSummary = Promise.withResolvers<AnalysisMicronutrientResponse>()
    const currentSummary = Promise.withResolvers<AnalysisMicronutrientResponse>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const url = new URL(path, 'https://calograph.test')
        if (!url.searchParams.has('metric_key')) {
          const days = inclusiveDays(path)
          if (days === 90) return oldSummary.promise
          if (days === 365) return currentSummary.promise
        }
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=90', [...DEFAULT_ACTIVE_CARD_IDS])
    await router.push('/analyse?period=365')
    await flushPromises()
    const summaryRequests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key'))
    expect(summaryRequests.map(inclusiveDays)).toEqual([90, 365])
    const responseFor = (path: string, metrics: AnalysisMicronutrientResponse['metrics']) => {
      const url = new URL(path, 'https://calograph.test')
      const startDate = url.searchParams.get('start')!
      const endDate = url.searchParams.get('end')!
      return {
        ...micronutrientResponse(null), start_date: startDate, end_date: endDate,
        period_days: inclusiveDays(path), metrics,
      }
    }
    currentSummary.resolve(responseFor(summaryRequests[1]!, []))
    await flushPromises()
    oldSummary.resolve(responseFor(summaryRequests[0]!, [vitaminA]))
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    const staleMetricReads = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?')
        && new URL(path, 'https://calograph.test').searchParams.has('metric_key')
        && inclusiveDays(path) === 90)
    expect(staleMetricReads).toEqual([])
    wrapper.unmount()
  })

  it('a 365-day fixed Summary retry uses identical bounds', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    let attempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key')) {
        attempts += 1
        if (attempts === 1) return Promise.reject(new Error('summary offline'))
        const url = new URL(path, 'https://calograph.test')
        const startDate = url.searchParams.get('start')!
        const endDate = url.searchParams.get('end')!
        return Promise.resolve({
          ...micronutrientResponse(null), start_date: startDate, end_date: endDate,
          period_days: inclusiveDays(path), metrics: [], series: [],
        })
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=365', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    const summaryRequests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key'))
    expect(summaryRequests).toHaveLength(1)
    const first = new URL(summaryRequests[0]!, 'https://calograph.test')
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    expect(activeCardIds).toContain('vitamin_a_ug')
    const error = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"] [role="alert"]')
    await error.get('button').trigger('click')
    await flushPromises()
    const retriedPath = apiMock.mock.calls.map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URL(path, 'https://calograph.test').searchParams.has('metric_key')).at(-1)!
    const retried = new URL(retriedPath, 'https://calograph.test')
    expect(attempts).toBe(2)
    expect(retried.searchParams.get('start')).toBe(first.searchParams.get('start'))
    expect(retried.searchParams.get('end')).toBe(first.searchParams.get('end'))
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    expect(activeCardIds).toContain('vitamin_a_ug')
    wrapper.unmount()
  })

  it('ignores a stale Hub reader response after an account switch', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const oldWeight = Promise.withResolvers<WeightResponse>()
    let weightRequests = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/weight?') && weightRequests++ === 0) return oldWeight.promise
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity'])
    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: 'analysis-user-b' }
    await flushPromises()
    const card = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(card.text()).toContain('71,4 kg')
    oldWeight.resolve({
      ...structuredClone(weight),
      points: [{ date: start, weight_kg: 10 }, { date: end, weight_kg: 10 }],
      summary: {
        measurement_count: 2, latest: { date: end, weight_kg: 10 }, change_kg: 0,
        average_kg: 10, minimum_kg: 10, maximum_kg: 10,
      },
    })
    await flushPromises()
    expect(card.text()).toContain('71,4 kg')
    expect(card.text()).not.toContain('10 kg')
    wrapper.unmount()
  })

  it('invalidates loaded meals and account preferences after a cross-tab session change', async () => {
    const today = isoDateInTimeZone('Europe/Berlin')
    const initialCards = [...DEFAULT_ACTIVE_CARD_IDS, 'meals_today']
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const nextRegistry = Promise.withResolvers<{ analyses: AnalysisRegistryEntry[] }>()
    const lateWeight = Promise.withResolvers<WeightResponse>()
    let weightRequests = 0
    const restoredUser = Promise.withResolvers<{
      id: string
      username: string
      language: string
      timezone: string
      week_starts_on: number
    }>()
    const weightB: WeightResponse = {
      ...structuredClone(weight),
      points: [{ date: end, weight_kg: 68 }],
      summary: {
        measurement_count: 1, latest: { date: end, weight_kg: 68 }, change_kg: null,
        average_kg: 68, minimum_kg: 68, maximum_kg: 68,
      },
    }
    let registryRequests = 0
    const todayMeal: AnalysisNutritionResponse = {
      ...nutrition,
      start_date: today,
      end_date: today,
      period_days: 1,
      days: [{
        date: today,
        provider_key: 'yazio',
        dietary_energy_kcal: 425,
        protein_g: 18,
        carbohydrates_g: 51,
        fat_g: 12,
        fiber_g: 5,
        sugar_g: 7,
        saturated_fat_g: 3,
        meals: [{
          event_id: 'account-a-meal', local_date: today, serving_amount: null, serving_unit: null,
          provider_key: 'yazio',
          local_time: '12:30:00',
          meal_type: 'lunch',
          display_name: 'A-Konto Mahlzeit',
          dietary_energy_kcal: 425,
          protein_g: 18,
          carbohydrates_g: 51,
          fat_g: 12,
          fiber_g: 5,
          sugar_g: 7,
          saturated_fat_g: 3,
        }],
      }],
    }
    const todayMealB: AnalysisNutritionResponse = {
      ...todayMeal,
      days: todayMeal.days.map((day) => ({
        ...day,
        dietary_energy_kcal: 510,
        meals: day.meals.map((meal) => ({
          ...meal,
          meal_type: 'dinner',
          display_name: 'B-Konto Mahlzeit',
          dietary_energy_kcal: 510,
        })),
      })),
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      const auth = useAuthStore()
      if (path === '/auth/me') return restoredUser.promise
      if (path === '/settings/onboarding') {
        return Promise.resolve({ mode: 'full', required: true, completed: false, current_step: 'targets' })
      }
      if (path.startsWith('/analytics/weight?')) {
        if (auth.user?.id === 'analysis-user-b') return Promise.resolve(structuredClone(weightB))
        if (weightRequests++ === 1) return lateWeight.promise
      }
      if (path === '/analytics/registry' && registryRequests++ > 0) return nextRegistry.promise
      if (path === '/settings/analysis-preferences'
        && options?.method !== 'PUT' && auth.user?.id === 'analysis-user-b') {
        return Promise.resolve({ active_card_ids: initialCards })
      }
      const request = new URL(path, 'https://calograph.test')
      if (request.pathname === '/analytics/nutrition/analysis'
        && request.searchParams.get('start') === today && request.searchParams.get('end') === today) {
        return Promise.resolve(structuredClone(
          useAuthStore().user?.id === 'analysis-user-b' ? todayMealB : todayMeal,
        ))
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse', initialCards, 'analysis-user-a')
    const cardSelector = '.analysis-card-list > li[data-card-key="meals_today"]'
    expect(wrapper.get(cardSelector).text()).toContain('425 kcal')
    await router.push('/analyse?period=90')
    await flushPromises()
    expect(weightRequests).toBe(2)

    const auth = useAuthStore()
    const revisionKey = 'calograph_auth_session_revision'
    const oldRevision = localStorage.getItem(revisionKey)
    const nextRevision = `cross-tab-${Date.now()}`
    localStorage.setItem(revisionKey, nextRevision)
    window.dispatchEvent(new StorageEvent('storage', {
      key: revisionKey,
      oldValue: oldRevision,
      newValue: nextRevision,
      storageArea: localStorage,
    }))
    await flushPromises()

    expect(auth.user).toBeNull()
    expect(cardOrder(wrapper)).not.toContain('meals_today')
    expect(wrapper.get('.analysis-card-list > li[data-card-key="weight"]').text()).not.toContain('71,4 kg')

    restoredUser.resolve({
      id: 'analysis-user-b',
      username: 'owner',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
    })
    await flushPromises()
    nextRegistry.resolve({ analyses: availableRegistry })
    await flushPromises()

    expect(auth.user?.id).toBe('analysis-user-b')
    const mealsCardB = wrapper.get(cardSelector)
    expect(mealsCardB.text()).toContain('510 kcal')
    expect(mealsCardB.text()).toContain('Abendessen')
    expect(mealsCardB.text()).not.toContain('425 kcal')
    lateWeight.resolve({
      ...structuredClone(weight),
      points: [{ date: start, weight_kg: 10 }, { date: end, weight_kg: 10 }],
      summary: {
        measurement_count: 2, latest: { date: end, weight_kg: 10 }, change_kg: 0,
        average_kg: 10, minimum_kg: 10, maximum_kg: 10,
      },
    })
    await flushPromises()
    const weightCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(weightCard.text()).toContain('68 kg')
    expect(weightCard.text()).not.toContain('10 kg')
    wrapper.unmount()
  })

  it.each(['pending', 'failed'] as const)(
    'preserves the stored card set when removal is attempted while preferences are %s',
    async (loadState) => {
      serveSeededHub()
      const initialCards = [...DEFAULT_ACTIVE_CARD_IDS, 'protein_g']
      const fallback = apiMock.getMockImplementation()!
      const pendingPreferences = Promise.withResolvers<{ active_card_ids: string[] }>()
      apiMock.mockImplementation((path: string, options?: RequestInit) => {
        if (path === '/settings/analysis-preferences' && options?.method !== 'PUT') {
          return loadState === 'pending'
            ? pendingPreferences.promise
            : Promise.reject(new Error('preferences unavailable'))
        }
        return fallback(path, options)
      })
      const { wrapper } = await mountAnalysis('/analyse', initialCards, 'analysis-load-guard-user')
      if (loadState === 'failed') {
        await flushPromises()
        expect(wrapper.find('.analysis-workspace-error').exists()).toBe(true)
        expect(wrapper.find('[data-card-remove]').exists()).toBe(false)
        expect(preferenceWriteBodies()).toEqual([])
        wrapper.unmount()
        return
      }
      const card = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
      await card.get('.analysis-overflow-trigger').trigger('click')
      expect(card.find('[data-card-remove]').exists()).toBe(false)
      expect(preferenceWriteBodies()).toEqual([])
      pendingPreferences.resolve({ active_card_ids: initialCards })
      await flushPromises()
      expect(cardOrder(wrapper)).toEqual(initialCards)
      const loadedCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
      await loadedCard.get('.analysis-overflow-trigger').trigger('click')
      expect(loadedCard.find('[data-card-remove]').exists()).toBe(true)
      await loadedCard.get('[data-card-remove]').trigger('click')
      await flushPromises()
      expect(preferenceWriteBodies()).toEqual([{
        active_card_ids: initialCards.filter((key) => key !== 'weight'),
      }])
      wrapper.unmount()
    },
  )

  it('cancels a Hub card drag when the authenticated account changes', async () => {
    const fallback = apiMock.getMockImplementation()!
    const accountA = {
      active_card_ids: ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'],
    }
    const accountB = {
      active_card_ids: ['activity', 'weight', 'nutrition', 'micronutrients', 'fat_g'],
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/analysis-preferences' && options?.method !== 'PUT') {
        return Promise.resolve(useAuthStore().user?.id === 'analysis-user-b' ? accountB : accountA)
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse', ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
    try {
      const previousWriteCount = preferenceWriteBodies().length
      await dragCard(
        wrapper,
        '.analysis-card-list > li[data-card-key="micronutrients"]',
        '.analysis-card-list > li[data-card-key="weight"]',
        'drop',
        async () => {
          expect(cardOrder(wrapper))
            .toEqual(['micronutrients', 'weight', 'nutrition', 'activity', 'protein_g'])
          useAuthStore().user = { ...useAuthStore().user!, id: 'analysis-user-b' }
          await flushPromises()
        },
      )
      await flushPromises()
      expect(preferenceWriteBodies().slice(previousWriteCount)).toEqual([])
      expect(cardOrder(wrapper)).toEqual(['activity', 'weight', 'nutrition', 'micronutrients', 'fat_g'])
    } finally {
      wrapper.unmount()
    }
  })

  it('ignores a stale Hub workspace result after the authenticated account changes', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const oldRegistry = Promise.withResolvers<{ analyses: AnalysisRegistryEntry[] }>()
    let registryRequests = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/analytics/registry' && registryRequests++ === 0) return oldRegistry.promise
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=365', [...DEFAULT_ACTIVE_CARD_IDS])
    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: 'analysis-user-b' }
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    oldRegistry.resolve({
      analyses: availableRegistry.map((entry) => entry.period_capability
        ? { ...entry, period_capability: { ...entry.period_capability, max_days: 30 } }
        : entry),
    })
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    wrapper.unmount()
  })

  it('ignores stale Hub workspaces after logout without starting readers', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const oldRegistry = Promise.withResolvers<{ analyses: AnalysisRegistryEntry[] }>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? oldRegistry.promise : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse?period=90', [...DEFAULT_ACTIVE_CARD_IDS])
    useAuthStore().user = null
    oldRegistry.resolve({ analyses: availableRegistry })
    await flushPromises()
    const readers = apiMock.mock.calls.map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight?')
        || path.startsWith('/analytics/verification/activity?')
        || path.startsWith('/analytics/nutrition/analysis?')
        || path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(readers).toEqual([])
    expect(wrapper.find('.analysis-period-picker').exists()).toBe(false)
    wrapper.unmount()
  })


  it('keeps the Weight card compact and its full statistics on the existing detail route', async () => {
    serveSeededHub(true)
    const keys = ['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg', 'dietary_energy_kcal', 'protein_g', 'vitamin_c_mg', 'carbohydrates_g', 'vitamin_a_ug', 'fat_g']
    const { router, wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    const weightCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    const cardText = weightCard.text().replace(/\s+/g, ' ')
    expect(cardOrder(wrapper)).toEqual(keys)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(cardText).toMatch(/71,4 kg/)
    expect(cardText).toMatch(/[-−]1,1 kg/)
    expect(cardText).toContain('27 Messungen')
    expect(cardText).not.toMatch(/Durchschnitt|Minimum|Maximum/)
    expect(weightCard.find('.analysis-mini-chart__area').exists()).toBe(true)
    const addTile = wrapper.get('[data-analysis-add-card]')
    expect(addTile.element.parentElement?.lastElementChild).toBe(addTile.element)
    expect(addTile.attributes('data-card-key')).toBeUndefined()
    expect(addTile.get('button').text()).toBe('Karte hinzufügen')

    const historyTitle = weightCard.get('.analysis-card-heading a[href^="/analyse/koerper/gewicht?"]')
    await historyTitle.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/koerper/gewicht')
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2026-03-01', end: '2026-03-30',
    })
    const detailText = wrapper.get('.analysis-detail-page').text().replace(/\s+/g, ' ')
    expect(detailText).toMatch(/Letzte Messung\s*71,4 kg/)
    expect(detailText).toMatch(/Veränderung\s*[-−]1,1 kg/)
    expect(detailText).toMatch(/Durchschnitt\s*72 kg/)
    expect(detailText).toMatch(/Minimum\s*71,4 kg/)
    expect(detailText).toMatch(/Maximum\s*72,5 kg/)
    expect(detailText).toContain('Messwerte (27)')
    expect(detailText).not.toMatch(/\d+\s+von\s+\d+\s+Tagen|Tage mit Daten/i)
    await expandReadings(wrapper, '.weight-reading-list', 27)
    const readingRow = wrapper.get('.weight-reading-list time[datetime="2026-03-27"]').element.closest('tr')
    expect(readingRow?.querySelector('td:first-child time')?.textContent).toBe('27.03.2026')
    expect(readingRow?.querySelectorAll('td')[1]?.textContent).toBe('71,4 kg')
    wrapper.unmount()
  })

  it('uses semantically distinct Phosphor icon keys for analysis metrics', () => {
    expect({
      protein: localizedAnalysisPresentation('protein_g')?.icon,
      sugar: localizedAnalysisPresentation('sugar_g')?.icon,
      carbohydrates: localizedAnalysisPresentation('carbohydrates_g')?.icon,
      fat: localizedAnalysisPresentation('fat_g')?.icon,
      fiber: localizedAnalysisPresentation('fiber_g')?.icon,
      energy: localizedAnalysisPresentation('dietary_energy_kcal')?.icon,
      weight: localizedAnalysisPresentation('weight_kg')?.icon,
      activeEnergy: localizedAnalysisPresentation('active_energy_kcal')?.icon,
      steps: localizedAnalysisPresentation('steps')?.icon,
      vitamin: localizedAnalysisPresentation('vitamin_a_ug')?.icon,
      mineral: localizedAnalysisPresentation('calcium_mg')?.icon,
    }).toEqual({
      protein: 'protein', sugar: 'cube', carbohydrates: 'carbohydrates',
      fat: 'fat', fiber: 'fiber', energy: 'energy', weight: 'scale',
      activeEnergy: 'fire', steps: 'steps', vitamin: 'leaf', mineral: 'atom',
    })
  })

  it('preserves selected micronutrient values, calendar gaps and independent Metric controls', async () => {
    serveSeededHub(true)
    const keys = ['weight', 'nutrition', 'activity', 'vitamin_c_mg', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'protein_g']
    const { router, wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    const vitamin = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    const mineral = wrapper.get('.analysis-card-list > li[data-card-key="calcium_mg"]')
    const empty = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_c_mg"]')
    expect(cardOrder(wrapper)).toEqual(keys)
    expect(vitamin.get('.analysis-metric-value').text()).toBe('100 ug')
    expect(mineral.get('.analysis-metric-value').text()).toBe('100 mg')
    expect([vitamin.text(), mineral.text(), empty.text()].join(' ')).not.toMatch(/\d+ Tage mit Daten/i)
    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"]').text())
      .not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    expect(wrapper.get('.analysis-card-list > li[data-card-key="dietary_energy_kcal"]').text())
      .not.toMatch(/\d+ protokollierte Tage/i)
    expect(empty.text()).toMatch(/Keine Daten im Zeitraum/i)
    const energyChart = wrapper.get('.analysis-card-list > li[data-card-key="dietary_energy_kcal"] svg.analysis-mini-chart')
    expect(energyChart.findAll('rect')).toHaveLength(0)
    expect(energyChart.findAll('polyline')).toHaveLength(0)
    expect(energyChart.findAll('polygon')).toHaveLength(0)
    expect(energyChart.findAll('circle')).toHaveLength(2)
    expect(vitamin.get('svg[role="img"] polyline').attributes('points')!.split(' ')).toHaveLength(12)
    expect(mineral.findAll('svg.analysis-mini-chart polyline')).toHaveLength(0)
    const mineralBars = mineral.findAll('svg.analysis-mini-chart rect')
    expect(mineralBars).toHaveLength(3)
    const mineralCenters = mineralBars.map((bar) => Number(bar.attributes('x')) + Number(bar.attributes('width')) / 2)
    expect(mineralCenters[1]! - mineralCenters[0]!).toBeGreaterThan(0)
    expect(mineralCenters[2]! - mineralCenters[1]!).toBeGreaterThan(0)
    const nutritionKpis = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"] .analysis-nutrition-kpis')
    expect(nutritionKpis.text()).toContain('Eiweiß')
    expect(nutritionKpis.text()).toContain('30 g')
    expect(nutritionKpis.text()).toContain('Kohlenhydrate')
    expect(nutritionKpis.text()).toContain('55 g')
    expect(nutritionKpis.text()).toContain('Fett')
    expect(nutritionKpis.text()).toContain('15 g')
    expect(empty.find('svg[role="img"]').exists()).toBe(false)
    for (const key of ['vitamin_c_mg', 'vitamin_a_ug', 'calcium_mg']) {
      const card = wrapper.get(`.analysis-card-list > li[data-card-key="${key}"]`)
      const link = card.get(`a[href^="/analyse/mikronaehrstoffe/${key}?"]`)
      const params = new URLSearchParams(link.attributes('href')!.split('?')[1])
      expect([params.get('period'), params.get('start'), params.get('end')])
        .toEqual(['custom', '2026-03-01', '2026-03-30'])
      expect(card.find('.analysis-drag-handle, .analysis-overflow-trigger').exists()).toBe(true)
    }
    expect(activeCardIds).toEqual(keys)
    expect(apiMock.mock.calls.filter(([path, options]) =>
      path === '/settings/analysis-preferences' && (options as RequestInit | undefined)?.method === 'PUT'))
      .toHaveLength(0)

    await mineral.get('a[href^="/analyse/mikronaehrstoffe/calcium_mg?"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/calcium_mg')
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start, end })
    expect(wrapper.get('h1').text()).toContain('Calcium')
    wrapper.unmount()
  })

  it('keeps sparse Weight and Energy as lines while limiting density selection to nutrient cards', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const sparseEnergy: AnalysisNutritionResponse = {
      ...structuredClone(nutrition),
      days: [
        { ...structuredClone(nutrition.days[0]!), date: start },
        { ...structuredClone(nutrition.days[1]!), date: shiftIsoDate(start, 1) },
      ],
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) =>
      path.startsWith('/analytics/nutrition/analysis?')
        ? Promise.resolve(sparseEnergy) : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [
      'weight', 'nutrition', 'micronutrients', 'activity',
      'dietary_energy_kcal', 'vitamin_a_ug', 'calcium_mg',
    ])
    const weightChart = wrapper.get('.analysis-card-list [data-card-key="weight"] svg.analysis-mini-chart')
    const energyChart = wrapper.get('.analysis-card-list [data-card-key="dietary_energy_kcal"] svg.analysis-mini-chart')
    const activityChart = wrapper.get('.analysis-card-list [data-card-key="activity"] svg.analysis-mini-chart')
    const nutritionChart = wrapper.get('.analysis-card-list [data-card-key="nutrition"] svg.analysis-mini-chart')
    const vitaminChart = wrapper.get('.analysis-card-list [data-card-key="vitamin_a_ug"] svg.analysis-mini-chart')
    const calciumChart = wrapper.get('.analysis-card-list [data-card-key="calcium_mg"] svg.analysis-mini-chart')

    expect(weightChart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(weightChart.findAll('.analysis-mini-chart__area')).toHaveLength(1)
    expect(energyChart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(energyChart.findAll('.analysis-mini-chart__area')).toHaveLength(1)
    expect(activityChart.findAll('rect').length).toBeGreaterThan(0)
    expect(activityChart.findAll('polyline')).toHaveLength(0)
    expect(nutritionChart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(nutritionChart.findAll('rect')).toHaveLength(0)
    expect(vitaminChart.findAll('polyline').length).toBeGreaterThan(0)
    expect(calciumChart.findAll('rect')).toHaveLength(3)
    expect(calciumChart.findAll('polyline')).toHaveLength(0)
    wrapper.unmount()
  })

  it('renders isolated points in a dense nutrient line as small markers without fake segments or fade', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const isolatedVitaminSeries = [0, 1, 4, 6, 7].map((offset) => ({
      date: shiftIsoDate(start, offset), provider_key: 'yazio', value: 100,
    }))
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && new URLSearchParams(path.split('?')[1] ?? '').get('metric_key') === 'vitamin_a_ug') {
        return Promise.resolve({ ...micronutrientResponse('vitamin_a_ug'), series: isolatedVitaminSeries })
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['vitamin_a_ug'])
    const chart = wrapper.get('.analysis-card-list [data-card-key="vitamin_a_ug"] svg.analysis-mini-chart')

    expect(chart.findAll('rect')).toHaveLength(0)
    expect(chart.findAll('polyline')).toHaveLength(2)
    expect(chart.findAll('polygon')).toHaveLength(2)
    expect(chart.findAll('circle')).toHaveLength(1)
    expect(Number(chart.get('circle').attributes('r'))).toBe(1.25)
    wrapper.unmount()
  })

  it('keeps Eiweiß, Kohlenhydrate, and Fett averages in Nutrition Summary beside individual metric cards', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['nutrition', 'protein_g'])
    const kpis = wrapper.get('.analysis-card-list [data-card-key="nutrition"] .analysis-nutrition-kpis')

    expect(kpis.text()).toContain('Eiweiß')
    expect(kpis.text()).toContain('30 g')
    expect(kpis.text()).toContain('Kohlenhydrate')
    expect(kpis.text()).toContain('55 g')
    expect(kpis.text()).toContain('Fett')
    expect(kpis.text()).toContain('15 g')
    wrapper.unmount()
  })

  it.each(['missing metric', 'null response'] as const)(
    'keeps a selected empty nutrient as an independent Metric with zero coverage for a %s',
    async (responseKind) => {
      serveSeededHub(true)
      const fallback = apiMock.getMockImplementation()!
      apiMock.mockImplementation((path: string, options?: RequestInit) => {
        if (path.startsWith('/analytics/nutrition/micronutrients?')
          && new URLSearchParams(path.split('?')[1] ?? '').get('metric_key') === 'vitamin_c_mg') {
          return Promise.resolve(responseKind === 'null response'
            ? null : { ...micronutrientResponse('vitamin_c_mg'), metrics: [], series: [] })
        }
        return fallback(path, options)
      })
      const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_c_mg'])
      const summary = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"]')
      const card = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_c_mg"]')
      expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_c_mg'])
      expect(card.get('a[href^="/analyse/mikronaehrstoffe/vitamin_c_mg?"]').text()).toContain('Vitamin C')
      expect(card.text()).toMatch(/Keine Daten im Zeitraum/i)
      expect(card.text()).not.toMatch(/\d+ Tage mit Daten/i)
      expect(card.find('[role="status"]').exists()).toBe(false)
      expect(card.find('[role="alert"]').exists()).toBe(false)
      expect(card.find('.analysis-metric-value').exists()).toBe(false)
      expect(card.find('svg[role="img"]').exists()).toBe(false)
      await openCardMenu(wrapper, 'vitamin_c_mg')
      expect(card.get('[data-card-remove]').attributes('aria-label')).toBe('Vitamin C aus Analysen entfernen')
      expect(summary.get('.analysis-nutrient-count').text().trim()).toBe('2 von 3')
      expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_c_mg'])
      const linkParams = new URLSearchParams(card.get('a').attributes('href')!.split('?')[1])
      expect([linkParams.get('period'), linkParams.get('start'), linkParams.get('end')])
        .toEqual(['custom', '2026-03-01', '2026-03-30'])
      wrapper.unmount()
    },
  )

  it('keeps selected Metric cards available while the micronutrient Summary is pending or failed', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    let rejectSummary!: (error: Error) => void
    let summaryAttempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && !new URLSearchParams(path.split('?')[1] ?? '').has('metric_key')
        && summaryAttempts++ === 0) {
        return new Promise<AnalysisMicronutrientResponse>((_resolve, reject) => { rejectSummary = reject })
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg', 'vitamin_a_ug'])
    const summary = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"]')
    const calciumCard = wrapper.get('.analysis-card-list > li[data-card-key="calcium_mg"]')
    const vitaminCard = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    expect(summary.get('[role="status"]').text()).toMatch(/lädt|geladen/i)
    expect(summary.find('[role="alert"]').exists()).toBe(false)
    expect(calciumCard.text()).toContain('100 mg')
    expect(vitaminCard.text()).toContain('100 ug')
    rejectSummary(new Error('summary offline'))
    await flushPromises()
    expect(summary.get('[role="alert"]').text()).toMatch(/Analyse/)
    expect(summary.find('[role="status"]').exists()).toBe(false)
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg', 'vitamin_a_ug'])
    expect([calciumCard.text(), vitaminCard.text()].join(' ')).not.toMatch(/\d+ Tage mit Daten/i)
    expect(calciumCard.findAll('svg[role="img"]')).toHaveLength(1)
    expect(vitaminCard.findAll('svg[role="img"]')).toHaveLength(1)
    await summary.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(summary.find('[role="alert"]').exists()).toBe(false)
    expect(summary.get('.analysis-nutrient-count').text().trim()).toBe('2 von 3')
    expect(calciumCard.text()).toContain('100 mg')
    expect(vitaminCard.text()).toContain('100 ug')
    await summary.get('.analysis-card-heading a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start, end })
    expect(wrapper.get('h1').text()).toContain('Mikronährstoffe')
    wrapper.unmount()
  })

  it('distinguishes a pending or failed Metric from an empty Metric without hiding the others', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    let rejectMetric!: (error: Error) => void
    let metricAttempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')
        && new URLSearchParams(path.split('?')[1] ?? '').get('metric_key') === 'calcium_mg'
        && metricAttempts++ === 0) {
        return new Promise<AnalysisMicronutrientResponse>((_resolve, reject) => { rejectMetric = reject })
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'activity', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg'])
    const mineral = wrapper.get('.analysis-card-list > li[data-card-key="calcium_mg"]')
    const empty = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_c_mg"]')
    const vitamin = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    expect(mineral.get('[role="status"]').text()).toMatch(/lädt|geladen/i)
    expect(mineral.find('[role="alert"]').exists()).toBe(false)
    expect(mineral.text()).not.toMatch(/Keine Daten im Zeitraum/i)
    expect(empty.text()).toMatch(/Keine Daten im Zeitraum/i)
    expect(empty.find('[role="status"]').exists()).toBe(false)
    expect(empty.find('[role="alert"]').exists()).toBe(false)
    expect(vitamin.text()).toContain('100 ug')
    rejectMetric(new Error('calcium offline'))
    await flushPromises()
    expect(mineral.get('[role="alert"]').text()).toMatch(/Analyse/)
    expect(mineral.find('[role="status"]').exists()).toBe(false)
    expect(mineral.text()).not.toMatch(/Keine Daten im Zeitraum/i)
    expect(mineral.get('a[href^="/analyse/mikronaehrstoffe/calcium_mg?"]').text()).toContain('Calcium')
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'activity', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg'])
    await mineral.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(mineral.find('[role="alert"]').exists()).toBe(false)
    expect(mineral.text()).toContain('100 mg')
    const sparseChart = mineral.get('svg[role="img"]')
    expect(sparseChart.findAll('rect')).toHaveLength(3)
    expect(sparseChart.findAll('polyline')).toHaveLength(0)
    expect(sparseChart.findAll('circle')).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps a failed fourth selected micronutrient as a visible retryable Metric', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const folate = metric('folate_ug', 'Folat', 'vitamin', 'ug', 720, 3)
    let folateAttempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/analytics/registry') {
        return Promise.resolve({ analyses: [...availableRegistry, {
          analysis_key: 'folate_ug', category_key: 'micronutrients', domain_key: 'folate_ug',
          status: 'available', card_capable: true, detail_capable: true,
          reader_key: 'analytics.nutrition.micronutrients',
          period_capability: { min_days: 1, max_days: 365, custom_range: true },
        }] })
      }
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const key = new URLSearchParams(path.split('?')[1] ?? '').get('metric_key')
        if (!key) return Promise.resolve({
          ...micronutrientResponse(null), metrics: [vitaminA, calcium, vitaminC, folate],
        })
        if (key === 'folate_ug') {
          folateAttempts += 1
          if (folateAttempts === 1) return Promise.reject(new Error('synthetic folate reader offline'))
          return Promise.resolve({
            ...micronutrientResponse(key), metrics: [folate],
            series: [
              { date: start, provider_key: 'yazio', value: 120 },
              { date: '2026-03-02', provider_key: 'yazio', value: 240 },
              { date: '2026-03-03', provider_key: 'yazio', value: 360 },
            ],
          })
        }
      }
      return fallback(path, options)
    })
    const keys = ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'calcium_mg', 'vitamin_c_mg', 'folate_ug']
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    const summary = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"]')
    const folateCard = wrapper.get('.analysis-card-list > li[data-card-key="folate_ug"]')
    expect(summary.get('.analysis-nutrient-count').text().trim()).toBe('3 von 4')
    expect(cardOrder(wrapper)).toEqual(keys)
    expect(folateAttempts).toBe(1)
    expect(folateCard.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]').text()).toContain('100 ug')
    await folateCard.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(folateAttempts).toBe(2)
    expect(folateCard.find('[role="alert"]').exists()).toBe(false)
    expect(folateCard.text()).toContain('240 ug')
    expect(folateCard.text()).not.toMatch(/\d+ Tage mit Daten/i)
    expect(summary.get('.analysis-nutrient-count').text().trim()).toBe('3 von 4')
    expect(wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]').text()).toContain('100 ug')
    expect(activeCardIds).toEqual(keys)
    expect(preferenceWrites()).toEqual([])
    wrapper.unmount()
  })






  it('keeps the Weight card honest when the period has no readings', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/analytics/weight?')
      ? Promise.resolve({
        ...weight, points: [],
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        summary: {
          measurement_count: 0, latest: null, change_kg: null,
          average_kg: null, minimum_kg: null, maximum_kg: null,
        },
      } satisfies WeightResponse)
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity'])
    const weightCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(weightCard.get('.analysis-card-state').text()).toBe('Keine Messungen im Zeitraum')
    expect(weightCard.find('.analysis-mini-chart').exists()).toBe(false)
    expect(weightCard.text()).not.toMatch(/\d+(?:,\d+)?\s*kg/)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a single real weight reading without inventing a period change', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/analytics/weight?')
      ? Promise.resolve({
        ...weight, points: [{ date: '2026-03-15', weight_kg: 73.2 }],
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        summary: {
          measurement_count: 1, latest: { date: '2026-03-15', weight_kg: 73.2 },
          change_kg: null, average_kg: 73.2, minimum_kg: 73.2, maximum_kg: 73.2,
        },
      } satisfies WeightResponse)
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity'])
    const weightCard = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    const text = weightCard.text().replace(/\s+/g, ' ')
    expect(text).toContain('73,2 kg')
    expect(text).toContain('1 Messung')
    expect(text).toMatch(/Veränderung\s*[–—]/)
    expect(text).not.toMatch(/(?:[+−-]\s*\d+(?:,\d+)?|0)\s*kg/)
    expect(weightCard.findAll('.analysis-mini-chart circle')).toHaveLength(1)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    wrapper.unmount()
  })


  it('filters the full micronutrient catalog and keeps chart density, zero, gaps and period links correct', async () => {
    const longRangeStart = '2025-04-01'
    const longRangeEnd = '2026-03-31'
    const longRangeQuery = `?period=custom&start=${longRangeStart}&end=${longRangeEnd}`
    serveSeededHub(true)
    const catalog = [
      ['vitamin_a_ug', 'vitamin', 'ug'], ['thiamin_mg', 'vitamin', 'mg'],
      ['riboflavin_mg', 'vitamin', 'mg'], ['niacin_mg', 'vitamin', 'mg'],
      ['pantothenic_acid_mg', 'vitamin', 'mg'], ['vitamin_b6_mg', 'vitamin', 'mg'],
      ['biotin_ug', 'vitamin', 'ug'], ['folate_ug', 'vitamin', 'ug'],
      ['vitamin_b12_ug', 'vitamin', 'ug'], ['vitamin_c_mg', 'vitamin', 'mg'],
      ['vitamin_d_ug', 'vitamin', 'ug'], ['vitamin_e_mg', 'vitamin', 'mg'],
      ['vitamin_k_ug', 'vitamin', 'ug'], ['calcium_mg', 'mineral', 'mg'],
      ['iron_mg', 'mineral', 'mg'], ['potassium_mg', 'mineral', 'mg'],
      ['magnesium_mg', 'mineral', 'mg'], ['phosphorus_mg', 'mineral', 'mg'],
      ['zinc_mg', 'mineral', 'mg'], ['copper_mg', 'mineral', 'mg'],
      ['manganese_mg', 'mineral', 'mg'], ['selenium_ug', 'mineral', 'ug'],
      ['iodine_ug', 'mineral', 'ug'], ['fluoride_mg', 'mineral', 'mg'],
      ['chloride_mg', 'mineral', 'mg'], ['choline_mg', 'mineral', 'mg'],
    ] as const
    const recorded: Record<string, { total: number | null; days: number }> = {
      vitamin_a_ug: { total: 1000, days: 4 },
      folate_ug: { total: 30, days: 2 },
      vitamin_d_ug: { total: 0, days: 1 },
      thiamin_mg: { total: null, days: 1 },
      riboflavin_mg: { total: 0, days: 0 },
    }
    const summaryMetrics = catalog.map(([key, category, unit]) => {
      const value = recorded[key]
      return metric(key, key, category, unit, value?.total ?? null, value?.days ?? 0)
    })
    const seriesByKey: Record<string, { offsets: number[]; values: number[] }> = {
      vitamin_a_ug: { offsets: [0, 1, 3, 4], values: [100, 200, 300, 400] },
      folate_ug: { offsets: [0, 364], values: [10, 20] },
      vitamin_d_ug: { offsets: [3], values: [0] },
    }
    const registryTemplate = availableRegistry.find((entry) => entry.analysis_key === 'vitamin_a_ug')!
    const nutrientRegistryEntries = summaryMetrics.map(({ metric_key }) => ({
      ...registryTemplate, analysis_key: metric_key, domain_key: metric_key,
    }))
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/analytics/registry') {
        return Promise.resolve({ analyses: nutrientRegistryEntries })
      }
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const metricKey = params.get('metric_key')
        const requestStart = params.get('start') ?? start
        const requestEnd = params.get('end') ?? end
        const periodDays = (Date.parse(`${requestEnd}T00:00:00Z`)
          - Date.parse(`${requestStart}T00:00:00Z`)) / 86_400_000 + 1
        const definition = metricKey ? seriesByKey[metricKey] : undefined
        const series = definition?.offsets.flatMap((offset, index) => (
          offset < periodDays
            ? [{
              date: shiftIsoDate(requestStart, offset),
              provider_key: 'yazio',
              value: definition.values[index]!,
            }]
            : []
        )) ?? []
        return Promise.resolve({
          ...micronutrientResponse(metricKey),
          start_date: requestStart,
          end_date: requestEnd,
          period_days: periodDays,
          metrics: metricKey
            ? summaryMetrics.filter((item) => item.metric_key === metricKey)
            : summaryMetrics,
          series,
        })
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis(`/analyse/mikronaehrstoffe${longRangeQuery}`)
    try {
      const category = wrapper.get('#analysis-category-micronutrients')
      const filter = category.get('[role="group"][aria-label="Mikronährstoffe anzeigen"]')
      expect(filter.findAll('button')).toHaveLength(2)
      expect(filter.get('button[aria-pressed="true"]').text()).toBe('Mit Daten')
      expect(wrapper.get('h1').text()).toContain('Mikronährstoffe')
      expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('custom')
      expect(category.findAll('[data-nutrient-key]')).toHaveLength(3)
      expect(category.get('[data-nutrient-group="vitamin"]').findAll('[data-nutrient-key]')).toHaveLength(3)
      expect(category.get('[data-nutrient-group="mineral"]').findAll('[data-nutrient-key]')).toHaveLength(0)
      expect(category.find('[data-nutrient-key="vitamin_e_mg"]').exists()).toBe(false)
      expect(category.text()).not.toMatch(/\d+ von \d+/)

      const vitaminARow = category.get('[data-nutrient-key="vitamin_a_ug"]')
      expect(vitaminARow.get('.analysis-micronutrient-average').text()).toContain('250 µg')
      const customLink = new URL(vitaminARow.get('a').attributes('href')!, 'https://calograph.test')
      expect(customLink.pathname).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
      expect([customLink.searchParams.get('period'), customLink.searchParams.get('start'), customLink.searchParams.get('end')])
        .toEqual(['custom', longRangeStart, longRangeEnd])
      const denseChart = vitaminARow.get('svg.analysis-mini-chart')
      expect(denseChart.findAll('.analysis-mini-chart__line')).toHaveLength(2)
      expect(denseChart.findAll('.analysis-mini-chart__area')).toHaveLength(2)
      expect(denseChart.findAll('rect')).toHaveLength(0)

      const sparseChart = category.get('[data-nutrient-key="folate_ug"] svg.analysis-mini-chart')
      expect(sparseChart.findAll('rect')).toHaveLength(2)
      expect(sparseChart.findAll('.analysis-mini-chart__line')).toHaveLength(0)
      expect(Number(sparseChart.find('rect').attributes('width'))).toBeGreaterThan(0)
      expect(Number(sparseChart.findAll('rect')[0]!.attributes('width'))).toBe(2.5)

      const zeroRow = category.get('[data-nutrient-key="vitamin_d_ug"]')
      expect(zeroRow.get('.analysis-micronutrient-average').text()).toContain('0 µg')
      expect(zeroRow.get('svg.analysis-mini-chart').findAll('rect')).toHaveLength(1)
      expect(Number(zeroRow.get('svg.analysis-mini-chart rect').attributes('height'))).toBe(2)
      expect(Number(zeroRow.get('svg.analysis-mini-chart rect').attributes('width'))).toBe(5)

      await filter.get('button[aria-pressed="false"]').trigger('click')
      expect(filter.get('button[aria-pressed="true"]').text()).toBe('Alle')
      expect(category.findAll('[data-nutrient-key]')).toHaveLength(26)
      expect(category.get('[data-nutrient-group="vitamin"]').findAll('[data-nutrient-key]')).toHaveLength(13)
      expect(category.get('[data-nutrient-group="mineral"]').findAll('[data-nutrient-key]')).toHaveLength(13)
      for (const key of ['vitamin_e_mg', 'thiamin_mg', 'riboflavin_mg']) {
        const row = category.get(`[data-nutrient-key="${key}"]`)
        expect(row.text()).toContain('Keine Daten')
        expect(row.find('svg.analysis-mini-chart').exists()).toBe(false)
      }
      expect(category.find('[data-nutrient-key="vitamin_e_mg"] .analysis-micronutrient-icon svg').exists())
        .toBe(true)
      const noDataLink = new URL(
        category.get('[data-nutrient-key="vitamin_e_mg"] a').attributes('href')!,
        'https://calograph.test',
      )
      expect(noDataLink.pathname).toBe('/analyse/mikronaehrstoffe/vitamin_e_mg')
      expect([noDataLink.searchParams.get('period'), noDataLink.searchParams.get('start'), noDataLink.searchParams.get('end')])
        .toEqual(['custom', longRangeStart, longRangeEnd])

      await wrapper.getComponent(PeriodPicker)
        .get('.analysis-period-quick-button[data-period="7"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('7')
      expect(filter.get('button[aria-pressed="true"]').text()).toBe('Alle')
      expect(category.findAll('[data-nutrient-key]')).toHaveLength(26)
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps a failed nutrient trend visible as an error, not as no-data', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const metricKey = new URLSearchParams(path.split('?')[1] ?? '').get('metric_key')
        if (metricKey === 'vitamin_a_ug') return Promise.reject(new Error('Trend unavailable'))
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis(`/analyse/mikronaehrstoffe${rangeQuery}`)
    try {
      const row = wrapper.get('[data-nutrient-key="vitamin_a_ug"]')
      expect(row.get('.analysis-micronutrient-average').text()).toContain('100 µg')
      expect(row.text()).not.toContain('Keine Daten')
      expect(row.get('[role="alert"]').text()).toBeTruthy()
    } finally {
      wrapper.unmount()
    }
  })

  it('reloads the active category range when navigation overtakes its initial filtered nutrient trend', async () => {
    const marchSummary: AnalysisMicronutrientResponse = {
      ...micronutrientResponse(null),
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      metrics: [
        vitaminA,
        metric('calcium_mg', 'Calcium', 'mineral', 'mg', null, 0),
        vitaminC,
      ],
    }
    const aprilSummary: AnalysisMicronutrientResponse = {
      start_date: '2026-04-01', end_date: '2026-04-07',
      selected_provider: { provider_key: 'yazio' }, period_days: 7,
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      metrics: [
        metric('vitamin_a_ug', 'Vitamin A', 'vitamin', 'ug', 900, 2),
        metric('calcium_mg', 'Calcium', 'mineral', 'mg', 60, 1),
        vitaminC,
      ],
      series: [],
    }
    let resolveMarchTrend!: (response: AnalysisMicronutrientResponse) => void
    const pendingMarchTrend = new Promise<AnalysisMicronutrientResponse>((resolve) => {
      resolveMarchTrend = resolve
    })
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: availableRegistry })
      if (!path.startsWith('/analytics/nutrition/micronutrients?')) return fallback(path, options)
      const params = new URLSearchParams(path.split('?')[1])
      const key = params.get('metric_key')
      if (params.get('start') === '2026-03-01' && params.get('end') === '2026-03-30') {
        if (key === 'vitamin_a_ug') return pendingMarchTrend
        return Promise.resolve(structuredClone(marchSummary))
      }
      if (params.get('start') === '2026-04-01' && params.get('end') === '2026-04-07') {
        if (!key) return Promise.resolve(structuredClone(aprilSummary))
        return Promise.resolve({
          ...aprilSummary,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          metrics: aprilSummary.metrics.filter((item) => item.metric_key === key),
          series: key === 'vitamin_a_ug'
            ? [
              { date: '2026-04-01', provider_key: 'yazio', value: 400 },
              { date: '2026-04-02', provider_key: 'yazio', value: 500 },
            ]
            : [{ date: '2026-04-07', provider_key: 'yazio', value: 60 }],
        } satisfies AnalysisMicronutrientResponse)
      }
      return Promise.reject(new Error('Unexpected micronutrient range'))
    })
    const { router, wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe?period=custom&start=2026-03-01&end=2026-03-30',
    )
    expect(apiMock.mock.calls.some(([path]) => {
      const request = String(path)
      if (!request.startsWith('/analytics/nutrition/micronutrients?')) return false
      const params = new URLSearchParams(request.split('?')[1])
      return params.get('start') === '2026-03-01' && params.get('end') === '2026-03-30'
        && params.get('metric_key') === 'vitamin_a_ug'
    })).toBe(true)

    await router.push({
      query: { period: 'custom', start: '2026-04-01', end: '2026-04-07' },
    })
    await flushPromises()
    resolveMarchTrend(micronutrientResponse('vitamin_a_ug'))
    await flushPromises()

    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2026-04-01', end: '2026-04-07',
    })
    const summaryRanges = apiMock.mock.calls.flatMap(([path]) => {
      const request = String(path)
      if (!request.startsWith('/analytics/nutrition/micronutrients?')) return []
      const params = new URLSearchParams(request.split('?')[1])
      return params.has('metric_key') ? [] : [[params.get('start'), params.get('end')]]
    })
    expect(summaryRanges).toContainEqual(['2026-04-01', '2026-04-07'])
    expect(wrapper.get('.analysis-period-trigger').text()).toContain('01.04.2026')
    const category = wrapper.get('#analysis-category-micronutrients')
    expect(category.find('.analysis-micronutrient-count').exists()).toBe(false)
    expect(category.text()).not.toMatch(/\d+ von \d+ mit Daten|\d+ von \d+ Tagen|data-coverage-status/)
    const vitaminRow = category.get('[data-nutrient-key="vitamin_a_ug"]')
    expect(vitaminRow.get('.analysis-micronutrient-average').text()).toBe('450 µg')
    expect(vitaminRow.findAll('svg[role="img"] rect')).toHaveLength(2)
    wrapper.unmount()
  })

  it('keeps sparse Weight as Line and Area with a smooth curve across a long selected period', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const longStart = '2020-01-01'
    const end = shiftIsoDate(longStart, 364)
    const sparseWeight: WeightResponse = {
      ...structuredClone(weight),
      start_date: longStart,
      end_date: end,
      points: [
        { date: longStart, weight_kg: 72 },
        { date: end, weight_kg: 69 },
      ],
      summary: {
        measurement_count: 2, latest: { date: end, weight_kg: 69 },
        change_kg: -3, average_kg: 70.5, minimum_kg: 69, maximum_kg: 72,
      },
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) =>
      path.startsWith('/analytics/weight?') ? Promise.resolve(sparseWeight) : fallback(path, options))
    const { wrapper } = await mountAnalysis(`/analyse?period=custom&start=${longStart}&end=${end}`)
    const chart = wrapper.get('.analysis-card-list [data-card-key="weight"] svg.analysis-mini-chart')
    const points = chart.findAll('circle').map((point) => Number(point.attributes('cx')))

    expect(chart.findAll('rect')).toHaveLength(0)
    expect(chart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(points).toEqual([0, 120])
    wrapper.unmount()
  })
  it('connects sparse Weight readings at real date positions with Line and Area', async () => {
    serveSeededHub(true)
    const sparseWeight: WeightResponse = {
      ...structuredClone(weight),
      points: [
        { date: shiftIsoDate(start, 1), weight_kg: 72 },
        { date: shiftIsoDate(start, 4), weight_kg: 70 },
        { date: shiftIsoDate(start, 10), weight_kg: 69 },
      ],
      summary: {
        measurement_count: 3,
        latest: { date: shiftIsoDate(start, 10), weight_kg: 69 },
        change_kg: -3, average_kg: 211 / 3, minimum_kg: 69, maximum_kg: 72,
      },
    }
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) =>
      path.startsWith('/analytics/weight?') ? Promise.resolve(sparseWeight) : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery)
    const chart = wrapper.get('.analysis-card-list [data-card-key="weight"] svg.analysis-mini-chart')
    const points = chart.findAll('circle').map((point) => Number(point.attributes('cx')))

    expect(chart.findAll('rect')).toHaveLength(0)
    expect(chart.findAll('.analysis-mini-chart__line')).toHaveLength(1)
    expect(points).toEqual([0, 40, 120])
    expect(chart.findAll('circle')).toHaveLength(3)
    wrapper.unmount()
  })
  it('uses only finite positive real Weight samples in Hub line positions and summaries', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const startDate = weight.start_date
    const rawPoints = [
      { date: shiftIsoDate(startDate, 0), weight_kg: 72 },
      { date: shiftIsoDate(startDate, 1), weight_kg: null },
      { date: shiftIsoDate(startDate, 2) },
      { date: shiftIsoDate(startDate, 3), weight_kg: 0 },
      { date: shiftIsoDate(startDate, 4), weight_kg: -1 },
      { date: shiftIsoDate(startDate, 5), weight_kg: 69 },
    ] as unknown as WeightResponse['points']
    const response: WeightResponse = {
      ...structuredClone(weight),
      points: rawPoints,
      summary: {
        measurement_count: 6,
        latest: { date: shiftIsoDate(startDate, 3), weight_kg: 0 },
        change_kg: 0,
        average_kg: 0,
        minimum_kg: 0,
        maximum_kg: 72,
      },
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) =>
      path.startsWith('/analytics/weight?') ? Promise.resolve(response) : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery)
    const card = wrapper.get('.analysis-card-list [data-card-key="weight"]')
    const chart = card.get('svg.analysis-mini-chart')
    const points = chart.findAll('circle').map((point) => Number(point.attributes('cx')))

    expect(card.get('.analysis-metric-value').text()).toBe('69 kg')
    expect(card.get('.analysis-card-footer-copy').text()).toBe('2 Messungen')
    expect(chart.findAll('rect')).toHaveLength(0)
    expect(points).toEqual([0, 120])
    expect(chart.findAll('circle')).toHaveLength(2)
    expect(chart.findAll('circle').every((point) => Number(point.attributes('r')) === 1.25)).toBe(true)
    expect(rawPoints.map((point) => point.weight_kg)).toEqual([72, null, undefined, 0, -1, 69])
    wrapper.unmount()
  })


  it('uses domain-specific empty states across built-in and metric Hub cards', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const emptyWeight: WeightResponse = {
      ...structuredClone(weight),
      points: [],
      summary: {
        measurement_count: 0, latest: null, change_kg: null,
        average_kg: null, minimum_kg: null, maximum_kg: null,
      },
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/weight?')) return Promise.resolve(emptyWeight)
      if (path.startsWith('/analytics/verification/activity?')) return Promise.resolve({ ...activity, days: [] })
      if (path.startsWith('/analytics/nutrition/analysis?')) return Promise.resolve({ ...nutrition, days: [] })
      if (path.startsWith('/analytics/nutrition/micronutrients?')) {
        const key = new URLSearchParams(path.split('?')[1] ?? '').get('metric_key')
        return Promise.resolve({ ...micronutrientResponse(key), metrics: [], series: [] })
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'vitamin_a_ug'])

    expect(wrapper.get('.analysis-card-list [data-card-key="weight"]').text()).toContain('Keine Messungen im Zeitraum')
    expect(wrapper.get('.analysis-card-list [data-card-key="nutrition"]').text()).toContain('Keine Daten im Zeitraum')
    expect(wrapper.get('.analysis-card-list [data-card-key="micronutrients"]').text()).toContain('Keine Daten im Zeitraum')
    expect(wrapper.get('.analysis-card-list [data-card-key="activity"]').text()).toContain('Keine Daten im Zeitraum')
    for (const key of ['nutrition', 'micronutrients', 'activity']) {
      expect(wrapper.find(`.analysis-card-list [data-card-key="${key}"] .analysis-metric-value`).exists()).toBe(false)
    }
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'vitamin_a_ug'])
    expect(wrapper.get('.analysis-card-list [data-card-key="dietary_energy_kcal"]').text()).toContain('Keine Daten im Zeitraum')
    expect(wrapper.get('.analysis-card-list [data-card-key="vitamin_a_ug"]').text()).toContain('Keine Daten im Zeitraum')
    expect(wrapper.get('.analysis-card-list [data-card-key="activity"]').text()).toContain('Keine Daten im Zeitraum')
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders built-in and selected metric cards from the same 30-day period', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'vitamin_a_ug'])
    const weightSummary = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(wrapper.find('.analysis-card-list > li[data-card-key="weight_kg"]').exists()).toBe(false)
    const nutritionSummary = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"]')
    const energyMetric = wrapper.get('.analysis-card-list > li[data-card-key="dietary_energy_kcal"]')
    const activitySummary = wrapper.get('.analysis-card-list > li[data-card-key="activity"]')
    expect(wrapper.find('.analysis-card-list > li[data-card-key="active_energy_kcal"]').exists()).toBe(false)
    expect(weightSummary.get('.analysis-card-heading a[href^="/analyse/koerper/gewicht?"]').attributes('href'))
      .toContain('start=2026-03-01')
    expect(weightSummary.text()).toContain('71,4 kg')
    expect(weightSummary.find('.analysis-mini-chart__area').exists()).toBe(true)
    const weightLineX = weightSummary.findAll('.analysis-mini-chart__line-point')
      .map((point) => Number(point.attributes('cx')))
    expect([weightLineX[0], weightLineX.at(-1)]).toEqual([0, 120])
    const weightArea = weightSummary.get('.analysis-mini-chart__area').attributes('d')!
    expect(weightArea).toMatch(/L 120 38 L 0 38 Z$/)

    expect(nutritionSummary.get('.analysis-card-heading a[href^="/analyse/ernaehrung?"]').attributes('href'))
      .toContain('end=2026-03-30')
    const kpis = nutritionSummary.findAll('.analysis-metric-value')
    expect(kpis.map((value) => value.text())).toEqual(['30 g', '55 g', '15 g'])
    expect(nutritionSummary.text()).not.toContain('Durchschnitt pro Tag')
    const averageMarks = nutritionSummary.findAll('abbr')
    expect(averageMarks).toHaveLength(1)
    expect(nutritionSummary.get('.analysis-card-heading abbr').attributes('title')).toBe('Durchschnitt pro Tag')
    expect(nutritionSummary.get('.analysis-card-heading abbr').attributes('aria-label')).toBe('Durchschnitt pro Tag')
    expect(energyMetric.get('.analysis-card-heading a').text()).toBe('Energie')
    expect(energyMetric.get('.analysis-card-heading .analysis-average-mark').attributes('title'))
      .toBe('Durchschnitt pro Tag')
    const macroTrends = nutritionSummary.findAll('.analysis-nutrition-macro-trend')
    expect(macroTrends).toHaveLength(3)
    expect(macroTrends.map((trend) => trend.text().trim())).toEqual(['', '', ''])
    const macroPreviews = macroTrends.map((trend) => trend.get('.analysis-nutrition-macro-trend-chart'))
    expect(macroPreviews[0]!.attributes('aria-label')).toContain('Eiweiß')
    expect(macroPreviews[1]!.attributes('aria-label')).toContain('Kohlenhydrate')
    expect(macroPreviews[2]!.attributes('aria-label')).toContain('Fett')
    expect(macroPreviews[0]!.classes()).toContain('analysis-mini-chart--teal')
    expect(macroPreviews[1]!.classes()).toContain('analysis-mini-chart--blue')
    expect(macroPreviews[2]!.classes()).toContain('analysis-mini-chart--amber')
    for (const preview of macroPreviews) {
      const points = preview.findAll('circle')
      expect(points).toHaveLength(2)
      expect(Number(points[0]!.attributes('cx'))).toBe(0)
      expect(Number(points[1]!.attributes('cx'))).toBe(120)
      expect(preview.findAll('.analysis-mini-chart__line')).toHaveLength(0)
      expect(preview.findAll('.analysis-mini-chart__area')).toHaveLength(0)
    }
    expect(nutritionSummary.text()).toMatch(/Fett/)
    expect(energyMetric.get('.analysis-card-heading a').attributes('href'))
      .toContain('/analyse/ernaehrung/dietary_energy_kcal?')
    expect(energyMetric.get('.analysis-metric-value').text()).toBe('500 kcal')
    expect(energyMetric.text()).not.toMatch(/\d+ protokollierte Tage/i)
    const energyPointX = energyMetric.findAll('.analysis-mini-chart circle')
      .map((point) => Number(point.attributes('cx')))
    expect(energyMetric.findAll('.analysis-mini-chart polyline')).toHaveLength(0)
    expect(energyPointX).toEqual([0, 120])

    expect(activitySummary.get('.analysis-card-heading a[href^="/analyse/aktivitaet/aktive-energie?"]').attributes('href'))
      .toContain('start=2026-03-01')
    expect(activitySummary.get('.analysis-metric-value').text()).toBe('116 kcal')
    expect(activitySummary.text()).not.toContain('Durchschnitt pro Tag')
    expect(activitySummary.get('.analysis-card-heading .analysis-average-mark').attributes('title')).toBe('Durchschnitt pro Tag')
    expect(activitySummary.text()).toContain('3.120 kcal im Zeitraum')
    expect(activitySummary.text()).not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    expect(activitySummary.findAll('.analysis-mini-chart rect')).toHaveLength(26)
    const activityBars = activitySummary.findAll('.analysis-mini-chart rect')
    expect(Math.max(...activityBars.map((bar) => (
      Number(bar.attributes('x')) + Number(bar.attributes('width'))
    )))).toBeGreaterThan(119)

    const vitaminMetric = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    const nutrients = wrapper.get('.analysis-card-list > li[data-card-key="micronutrients"]')
    expect(nutrients.text()).toMatch(/2\s*(von|\/)\s*3/)
    expect(nutrients.get('[data-nutrient-group="vitamin"]').text()).toMatch(/1\s*(von|\/)\s*2/)
    expect(nutrients.get('[data-nutrient-group="mineral"]').text()).toMatch(/1\s*(von|\/)\s*1/)
    expect(vitaminMetric.get('.analysis-card-heading a').text()).toBe('Vitamin A')
    expect(vitaminMetric.get('.analysis-card-heading .analysis-average-mark').attributes('title'))
      .toBe('Durchschnitt pro Tag')
    expect(nutrients.text()).toContain('Calcium')
    expect(vitaminMetric.text()).not.toMatch(/\d+ Tage mit Daten/i)
    expect(nutrients.get('.analysis-nutrient-trends').text()).toContain('Calcium')
    expect(nutrients.get('.analysis-card-heading a[href^="/analyse/mikronaehrstoffe?"]').attributes('href'))
      .toContain('end=2026-03-30')
    expect(vitaminMetric.get('.analysis-card-heading a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug?"]').attributes('href'))
      .toContain('start=2026-03-01')
    expect(wrapper.text()).not.toMatch(/\d+\s+von\s+\d+\s+Tagen(?: mit Werten)?/i)
    expect(nutrients.findAll('svg[role="img"]').some((chart) => chart.attributes('aria-label')?.includes('Vitamin A'))).toBe(false)
    const vitaminTrend = vitaminMetric.get('svg[role="img"]')
    const calciumTrend = nutrients.findAll('svg[role="img"]')
      .find((chart) => chart.attributes('aria-label')?.includes('Calcium'))
    expect(vitaminTrend?.get('polyline').attributes('points')!.split(' ')).toHaveLength(12)
    expect(vitaminTrend?.findAll('polygon')).toHaveLength(1)
    const vitaminLineX = vitaminTrend?.get('polyline').attributes('points')!.trim()
      .split(/\s+/).map((point) => Number(point.split(',')[0]))
    expect([vitaminLineX?.[0], vitaminLineX?.at(-1)]).toEqual([0, 120])
    const lineCharts = [
      [weightSummary, 'var(--chart-series-violet)'],
      [energyMetric, 'var(--chart-series-violet)'],
      [vitaminMetric, 'var(--chart-series-emerald)'],
    ] as const
    for (const [card, color] of lineCharts) {
      const chart = card.get('.analysis-mini-chart')
      const chartBottomY = Number(chart.attributes('viewBox')!.trim().split(/\s+/)[3])
      const gradient = chart.get('linearGradient')
      const stops = chart.findAll('linearGradient stop')
      expect(chart.findAll('.analysis-mini-chart__area')).toHaveLength(chart.findAll('.analysis-mini-chart__line').length)
      expect(gradient.attributes('gradientUnits')).toBe('userSpaceOnUse')
      expect([gradient.attributes('x1'), gradient.attributes('y1'), gradient.attributes('x2'), gradient.attributes('y2')])
        .toEqual(['0', '0', '0', String(chartBottomY)])
      expect(stops.map((stop) => stop.attributes('stop-color'))).toEqual([color, color, color])
      expect(stops.map((stop) => stop.attributes('offset'))).toEqual(['0%', '60%', '100%'])
      expect(stops.map((stop) => Number(stop.attributes('stop-opacity')))).toEqual([0.22, 0.2, 0.03])
      for (const area of chart.findAll('.analysis-mini-chart__area')) {
        if (area.element.tagName.toLowerCase() === 'polygon') {
          const points = area.attributes('points')!.trim().split(/\s+/).map((point) => point.split(',').map(Number))
          expect(points[0]![1]).toBe(chartBottomY)
          expect(points.at(-1)![1]).toBe(chartBottomY)
        } else {
          expect(area.attributes('d')).toMatch(new RegExp(`L [\\d.]+ ${chartBottomY} L [\\d.]+ ${chartBottomY} Z$`))
        }
      }
    }
    const vitaminAreaPoints = vitaminTrend?.get('polygon').attributes('points')!.trim().split(/\s+/)
      .map((point) => point.split(',').map(Number))
    const vitaminBaselineY = Number(vitaminTrend.attributes('viewBox')!.trim().split(/\s+/)[3])
    const vitaminLinePoints = vitaminTrend?.get('polyline').attributes('points')!.trim().split(/\s+/)
      .map((point) => point.split(',').map(Number))
    expect(vitaminAreaPoints?.[0]).toEqual([vitaminLinePoints?.[0]![0], vitaminBaselineY])
    expect(vitaminAreaPoints?.slice(1, -1)).toEqual(vitaminLinePoints)
    expect(vitaminAreaPoints?.at(-1)).toEqual([vitaminLinePoints?.at(-1)![0], vitaminBaselineY])
    expect(calciumTrend?.findAll('polygon')).toHaveLength(0)
    expect(activitySummary.findAll('.analysis-mini-chart polygon')).toHaveLength(0)
    expect(wrapper.text()).not.toMatch(/8\.942\s*Schritte|Stabil/)
    wrapper.unmount()
  })
  it('formats all Hub kcal values as locale integers without changing source precision', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    const today = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Berlin', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date())
    const activityDay = structuredClone(activity.days[0]!)
    const fractionalActivity: VerificationActivityResponse = {
      ...structuredClone(activity),
      days: [{
        ...activityDay,
        canonical: { ...activityDay.canonical!, active_energy_kcal: 289.7 },
        providers: activityDay.providers.map((provider) => ({ ...provider, active_energy_kcal: 289.7 })),
      }],
    }
    const rangeNutrition: AnalysisNutritionResponse = {
      ...structuredClone(nutrition),
      days: [{ ...structuredClone(nutrition.days[0]!), date: start, dietary_energy_kcal: 1634.3 }],
    }
    const mealsToday: AnalysisNutritionResponse = {
      ...structuredClone(nutrition),
      start_date: today,
      end_date: today,
      period_days: 1,
      days: [{
        ...structuredClone(nutrition.days[0]!),
        date: today,
        dietary_energy_kcal: 1634.3,
        meals: [{
          ...nutrition.days[0]!.meals[0]!,
          dietary_energy_kcal: 289.7,
        }],
      }],
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/verification/activity?')) return Promise.resolve(fractionalActivity)
      if (path.startsWith('/analytics/nutrition/analysis?')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        return Promise.resolve(params.get('start') === today ? mealsToday : rangeNutrition)
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [
      'weight', 'nutrition', 'micronutrients', 'activity',
      'dietary_energy_kcal', 'meals_today',
    ])

    expect(wrapper.get('[data-card-key="activity"] .analysis-metric-value').text()).toBe('290 kcal')
    expect(wrapper.get('[data-card-key="activity"]').text()).toContain('290 kcal im Zeitraum')
    expect(wrapper.get('[data-card-key="dietary_energy_kcal"] .analysis-metric-value').text()).toBe('1.634 kcal')
    const mealsCard = wrapper.get('[data-card-key="meals_today"]')
    expect(mealsCard.get('.analysis-metric-value').text()).toBe('1.634 kcal')
    expect(mealsCard.get('.analysis-meal-list').text()).toContain('290 kcal')
    expect(fractionalActivity.days[0]!.canonical!.active_energy_kcal).toBe(289.7)
    expect(mealsToday.days[0]!.dietary_energy_kcal).toBe(1634.3)
    wrapper.unmount()
  })



  it('keeps removable default cards in one grid with a final Add tile', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery)
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(wrapper.text()).not.toContain('Weitere Analysen')
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.find('.analysis-add-card-button').exists()).toBe(true)
    for (const card of wrapper.findAll('.analysis-card-list > li[data-card-key]')) {
      expect(card.find('.analysis-drag-handle').exists()).toBe(true)
      expect(card.find('.analysis-overflow').exists()).toBe(true)
      expect(card.find('.analysis-card-heading a').exists()).toBe(true)
      await openCardMenu(wrapper, card.attributes('data-card-key')!)
      expect(card.find('[data-card-remove]').exists()).toBe(true)
    }
    await removeHubCard(wrapper, 'weight')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['nutrition', 'micronutrients', 'activity'])
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.get('.analysis-card-list').element.lastElementChild?.matches('[data-analysis-add-card]')).toBe(true)
    await clickManagerAction(wrapper, 'weight')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['nutrition', 'micronutrients', 'activity', 'weight'])
    expect(wrapper.get('.analysis-card-list').element.lastElementChild?.matches('[data-analysis-add-card]')).toBe(true)
    expect(preferenceWrites()).toEqual([
      ['nutrition', 'micronutrients', 'activity'],
      ['nutrition', 'micronutrients', 'activity', 'weight'],
    ])
    wrapper.unmount()
  })
  it('returns focus to the Add tile after closing the card manager', async () => {
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery)
    const trigger = wrapper.get('.analysis-add-card-button')
    ;(trigger.element as HTMLButtonElement).focus()
    await openCardManager(wrapper)
    await closeCardManager(wrapper)
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })

  it('keeps hub cards and the final Add tile visible while workspace requests are pending', async () => {
    const registryRequest = Promise.withResolvers<{ analyses: AnalysisRegistryEntry[] }>()
    const preferencesRequest = Promise.withResolvers<{ active_card_ids: string[] }>()
    apiMock.mockImplementation((path: string) => path === '/analytics/registry'
      ? registryRequest.promise
      : path === '/settings/analysis-preferences'
        ? preferencesRequest.promise
        : Promise.resolve({}))
    const { wrapper } = await mountAnalysis('/analyse?period=30')
    expect(wrapper.findAll('.analysis-card-list > li[data-card-key]')).toHaveLength(4)
    expect(wrapper.get('.analysis-card-list').element.lastElementChild?.matches('[data-analysis-add-card]')).toBe(true)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.find('.analysis-card-list [data-card-key="weight"] [role="status"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('does not persist Summary reordering before stored active cards are loaded', async () => {
    const preferencesRequest = Promise.withResolvers<{
      active_card_ids: string[]
    }>()
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => (
      path === '/settings/analysis-preferences' && options?.method !== 'PUT'
        ? preferencesRequest.promise : fallback(path, options)
    ))
    const { wrapper } = await mountAnalysis('/analyse')
    try {
      const nutrition = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"]')
      const dragHandle = nutrition.get('.analysis-drag-handle')
      await nutrition.get('.analysis-overflow-trigger').trigger('click')
      const move = nutrition.get('.analysis-overflow-menu button')
      await move.trigger('click')
      expect(preferenceWriteBodies()).toEqual([])
      expect(move.attributes('disabled')).toBeDefined()
      expect(dragHandle.attributes('disabled')).toBeDefined()

      preferencesRequest.resolve({
        active_card_ids: ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'],
      })
      await flushPromises()
      expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
      expect(dragHandle.attributes('disabled')).toBeUndefined()
    } finally {
      wrapper.unmount()
    }
  })

  it('renders active-card errors after workspace failure', async () => {
    registryFailuresRemaining = 1
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    const selectedMetric = wrapper.get('.analysis-card-list > li[data-card-key="vitamin_a_ug"]')
    expect(selectedMetric.find('.analysis-card-error').exists()).toBe(true)
    expect(selectedMetric.find('.analysis-card-error button').exists()).toBe(true)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.findAll('[role="alert"] button').length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('renders every selected available Metric separately in its stored order', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    const extraKeys = ['folate_ug', 'vitamin_d_ug', 'vitamin_b12_ug']
    const extraEntries = extraKeys.map((analysis_key) => ({
      ...availableRegistry.find((entry) => entry.analysis_key === 'vitamin_a_ug')!,
      analysis_key,
      domain_key: analysis_key,
    }))
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: [...availableRegistry, ...extraEntries] })
      : fallback(path, options))
    const keys = ['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg', 'protein_g', 'vitamin_a_ug', 'vitamin_c_mg', 'folate_ug', 'vitamin_d_ug', 'vitamin_b12_ug']
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    expect(cardOrder(wrapper))
      .toEqual(keys)
    expect(wrapper.findAll('.analysis-card-list > li[data-card-key]')).toHaveLength(keys.length)
    expect(wrapper.find('.analysis-card-list .analysis-card .analysis-card').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Weitere Analysen')
    await clickManagerAction(wrapper, 'sugar_g')
    await flushPromises()
    expect(cardOrder(wrapper))
      .toEqual([...keys, 'sugar_g'])
    await clickManagerAction(wrapper, 'sugar_g')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(keys)
    expect(activeCardIds).toEqual(keys)
    wrapper.unmount()
  })

  it('persists menu moves across visible card types while retaining hidden preferences', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'protein_g', 'micronutrients', 'activity'])
    await openCardMenu(wrapper, 'protein_g')
    await flushPromises()
    const proteinMoves = wrapper.get('.analysis-card-list > li[data-card-key="protein_g"]').findAll('.analysis-overflow-menu button')
    expect(proteinMoves).toHaveLength(3)
    await proteinMoves[1]!.trigger('click')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['weight', 'activity', 'micronutrients', 'protein_g'])
    await openCardMenu(wrapper, 'activity')
    await flushPromises()
    const activityMoves = wrapper.get('.analysis-card-list > li[data-card-key="activity"]').findAll('.analysis-overflow-menu button')
    expect(activityMoves).toHaveLength(3)
    await activityMoves[0]!.trigger('click')
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['activity', 'weight', 'micronutrients', 'protein_g'])
    expect(preferenceWrites()).toEqual([
      ['weight', 'activity', 'micronutrients', 'protein_g'],
      ['activity', 'weight', 'micronutrients', 'protein_g'],
    ])
    wrapper.unmount()
  })

  it('persists a valid cross-type insertion released over the originating list root', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'protein_g', 'activity', 'micronutrients'])
    await dragCard(wrapper, '.analysis-card-list > li[data-card-key="activity"]', '.analysis-card-list > li[data-card-key="protein_g"]', 'origin-list')
    expect(cardOrder(wrapper)).toEqual(['weight', 'activity', 'protein_g', 'micronutrients'])
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: ['weight', 'activity', 'protein_g', 'micronutrients'] }])
    wrapper.unmount()
  })
  it('keeps a cross-type insertion when a later pointermove hits an empty list gap', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [
      'weight', 'vitamin_a_ug', 'protein_g', 'nutrition', 'micronutrients', 'activity',
    ])
    await dragCard(
      wrapper,
      '.analysis-card-list > li[data-card-key="weight"]',
      '.analysis-card-list > li[data-card-key="protein_g"]',
      'origin-list-move',
    )
    const expectedOrder = [
      'vitamin_a_ug', 'protein_g', 'weight', 'nutrition', 'micronutrients', 'activity',
    ]
    expect(cardOrder(wrapper)).toEqual(expectedOrder)
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: expectedOrder }])
    wrapper.unmount()
  })


  it('persists cross-type drops but not outside-list or cancelled pointer drops', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    await dragCard(wrapper, '.analysis-card-list > li[data-card-key="vitamin_a_ug"]', '.analysis-card-list > li[data-card-key="weight"]')
    const mixedOrder = ['vitamin_a_ug', 'weight', 'nutrition', 'micronutrients', 'activity']
    expect(cardOrder(wrapper)).toEqual(mixedOrder)
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: mixedOrder }])
    await dragCard(wrapper, '.analysis-card-list > li[data-card-key="activity"]', '.analysis-card-list > li[data-card-key="vitamin_a_ug"]', 'outside')
    await dragCard(wrapper, '.analysis-card-list > li[data-card-key="weight"]', '.analysis-card-list > li[data-card-key="vitamin_a_ug"]', 'cancel')
    expect(cardOrder(wrapper)).toEqual(mixedOrder)
    expect(activeCardIds).toEqual(mixedOrder)
    expect(preferenceWriteBodies()).toEqual([{ active_card_ids: mixedOrder }])
    wrapper.unmount()
  })

  it('keeps macro Summary values and per-day previews separate from the selected energy Metric', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal'])
    const summary = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"]')
    const metric = wrapper.get('.analysis-card-list > li[data-card-key="dietary_energy_kcal"]')
    expect(summary.get('.analysis-card-heading a').attributes('href')).toContain('/analyse/ernaehrung?')
    expect(metric.get('.analysis-card-heading a').attributes('href')).toContain('/analyse/ernaehrung/dietary_energy_kcal?')
    expect(summary.findAll('.analysis-metric-value').map((value) => value.text()))
      .toEqual(['30 g', '55 g', '15 g'])
    expect(summary.findAll('.analysis-nutrition-macro-trend')).toHaveLength(3)
    expect(metric.text()).not.toMatch(/\d+ protokollierte Tage/i)
    expect(metric.get('.analysis-metric-value').text()).toBe('500 kcal')
    wrapper.unmount()
  })

  it('shows a neutral Weight delta from the reader summary without a selected Weight Metric', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [...DEFAULT_ACTIVE_CARD_IDS])
    const weightSummary = wrapper.get('.analysis-card-list > li[data-card-key="weight"]')
    expect(weightSummary.text()).toContain('71,4 kg')
    expect(weightSummary.text()).toMatch(/[-−]1,1 kg/)
    expect(wrapper.find('.analysis-weight-section').exists()).toBe(false)
    expect(weightSummary.text()).toContain('27 Messungen')
    expect(weightSummary.find('.analysis-weight-change--positive, .analysis-weight-change--negative, .text-success, .text-danger').exists())
      .toBe(false)
    expect(weightSummary.find('.analysis-mini-chart__area').exists()).toBe(true)
    wrapper.unmount()
  })





  it('appends Metric additions and preserves relative order when removing keys', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g', 'vitamin_a_ug'])
    await clickManagerAction(wrapper, 'carbohydrates_g')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g'])
    expect(cardOrder(wrapper)).toEqual(activeCardIds)
    await clickManagerAction(wrapper, 'dietary_energy_kcal')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g'])
    expect(cardOrder(wrapper)).toEqual(activeCardIds)
    await clickManagerAction(wrapper, 'calcium_mg')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g', 'calcium_mg'])
    await clickManagerAction(wrapper, 'vitamin_a_ug')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'carbohydrates_g', 'calcium_mg'])
    expect(cardOrder(wrapper)).toEqual(activeCardIds)
    expect(preferenceWrites()).toEqual([
      ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g'],
      ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g'],
      ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'carbohydrates_g', 'calcium_mg'],
      ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'carbohydrates_g', 'calcium_mg'],
    ])
  })

  it('adds and removes a nutrition key without regrouping interleaved Metric selections', async () => {
    serveSeededHub(true)
    const { router, wrapper } = await mountAnalysis('/analyse/katalog' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg'])
    expect(preferenceWrites()).toEqual([])
    await wrapper.get('[data-analysis-catalog-key="fat_g"] [data-analysis-add="fat_g"]').trigger('click')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'fat_g'])
    await wrapper.get('[data-analysis-catalog-key="protein_g"] [aria-label*="aus Analysen entfernen"]').trigger('click')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'fat_g'])
    expect(preferenceWrites()).toEqual([
      ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'fat_g'],
      ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'fat_g'],
    ])
    await router.push('/analyse' + rangeQuery)
    await flushPromises()
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'dietary_energy_kcal', 'calcium_mg', 'fat_g'])
    expect(preferenceWrites()).toHaveLength(2)
  })

  it.each([
    ['Hub-Kartenliste', '/analyse'],
    ['Full-CatalogView', '/analyse/katalog'],
  ] as const)('preserves Metric order when %s removes its first interleaved selection', async (_surface, path) => {
    serveSeededHub(true)
    const { router, wrapper } = await mountAnalysis(path + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'calcium_mg'])
    expect(preferenceWrites()).toEqual([])
    if (path === '/analyse') {
      expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'calcium_mg'])
    }
    if (path === '/analyse') await removeHubCard(wrapper, 'vitamin_a_ug')
    else await wrapper.get('[data-analysis-catalog-key="vitamin_a_ug"] [aria-label*="aus Analysen entfernen"]').trigger('click')
    await flushPromises()
    expect(activeCardIds).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg'])
    expect(preferenceWrites()).toEqual([['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg']])
    if (path === '/analyse/katalog') {
      await router.push('/analyse' + rangeQuery)
      await flushPromises()
    }
    expect(cardOrder(wrapper)).toEqual(['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg'])
    expect(preferenceWrites()).toEqual([['weight', 'nutrition', 'micronutrients', 'activity', 'calcium_mg']])
  })

  it('distinguishes pending nutrition from failed nutrition and recovers its actual values on retry', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    let rejectFirst!: (error: Error) => void
    let attempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/analysis?') && attempts++ === 0) {
        return new Promise<AnalysisNutritionResponse>((_resolve, reject) => { rejectFirst = reject })
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal'])
    const card = wrapper.get('[data-analysis-key="dietary_energy_kcal"]')
    expect(card.get('[role="status"]').text()).toMatch(/lädt|geladen/i)
    expect(card.find('[role="alert"]').exists()).toBe(false)
    rejectFirst(new Error('nutrition offline'))
    await flushPromises()
    const error = card.get('[role="alert"]')
    expect(error.text()).toMatch(/Analyse/)
    expect(card.find('[role="status"]').exists()).toBe(false)
    await error.get('button').trigger('click')
    await flushPromises()
    expect(card.text()).toContain('500 kcal')
    expect(card.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('ignores a late nutrition response from an older period after the new period has rendered', async () => {
    serveSeededHub()
    const fallback = apiMock.getMockImplementation()!
    let resolveOld!: (value: AnalysisNutritionResponse) => void
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/nutrition/analysis?')) {
        const query = new URLSearchParams(path.split('?')[1] ?? '')
        if (query.get('start') === '2026-03-01') {
          return new Promise<AnalysisNutritionResponse>((resolve) => { resolveOld = resolve })
        }
        return Promise.resolve({
          ...nutrition, start_date: '2026-03-08', end_date: '2026-03-14', period_days: 7,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          days: [{ ...nutrition.days[0]!, date: '2026-03-09', dietary_energy_kcal: 730 }],
        } satisfies AnalysisNutritionResponse)
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=custom&start=2026-03-01&end=2026-03-07', ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal'],
    )
    await router.push('/analyse?period=custom&start=2026-03-08&end=2026-03-14')
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="dietary_energy_kcal"]').text()).toContain('730 kcal')
    resolveOld({ ...nutrition, start_date: '2026-03-01', end_date: '2026-03-07', period_days: 7 })
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="dietary_energy_kcal"]').text()).toContain('730 kcal')
    expect(wrapper.get('[data-analysis-key="dietary_energy_kcal"]').text()).not.toContain('500 kcal')
    wrapper.unmount()
  })

  it('keeps selected Metrics and fixed Summary readers on 365 days', async () => {
    serveSeededHub()
    const { router, wrapper } = await mountAnalysis('/analyse?period=365', ['weight', 'nutrition', 'micronutrients', 'activity', 'dietary_energy_kcal', 'vitamin_a_ug'])
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.getComponent(PeriodPicker).props('maxDays')).toBe(365)
    const fixedRequests = fixedReaderPaths()
    expect(fixedRequests).toHaveLength(4)
    expect(fixedRequests.every(Boolean)).toBe(true)
    const vitaminRequest = apiMock.mock.calls.map(([path]) => String(path)).find((path) =>
      path.startsWith('/analytics/nutrition/micronutrients?')
      && new URL(path, 'https://calograph.test').searchParams.get('metric_key') === 'vitamin_a_ug')
    expect(vitaminRequest).toBeDefined()
    expect(inclusiveDays(vitaminRequest!)).toBe(365)
    wrapper.unmount()
  })
})

describe('Analysis V2 detail views', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const detailCases = [
    { path: '/analyse/koerper/gewicht', reader: '/analytics/weight', category: '/analyse/koerper' },
    { path: '/analyse/aktivitaet/aktive-energie', reader: '/analytics/verification/activity', category: '/analyse/aktivitaet' },
    { path: '/analyse/ernaehrung/protein_g', reader: '/analytics/nutrition/analysis', category: '/analyse/ernaehrung' },
    { path: '/analyse/mikronaehrstoffe/vitamin_a_ug', reader: '/analytics/nutrition/micronutrients', category: '/analyse/mikronaehrstoffe' },
  ]
  function serveDetailRanges(allStart: string | null = null) {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      const detail = detailCases.find(({ reader }) => path.startsWith(`${reader}?`))
      if (!detail) return fallback(path, options)
      const params = new URLSearchParams(path.split('?')[1])
      const start = params.get('start')!
      const end = params.get('end')!
      const range = {
        start_date: start, end_date: end,
        period_capabilities: { supports_all_data: allStart !== null, all_data_start_date: allStart },
      }
      const periodDays = (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1
      if (detail.reader === '/analytics/weight') return Promise.resolve({
        ...range, selected_provider: null, points: [],
        summary: { measurement_count: 0, latest: null, change_kg: null, average_kg: null, minimum_kg: null, maximum_kg: null },
      } satisfies WeightResponse)
      if (detail.reader === '/analytics/verification/activity') return Promise.resolve({
        ...range, view: 'canonical',
        days: [{
          date: start,
          canonical: {
            provider_key: 'yazio', status: 'available', active_energy_kcal: 0,
            record_count: 1, source_types: ['yazio_export_v1'],
          },
          providers: [{
            provider_key: 'yazio', status: 'available', active_energy_kcal: 0,
            record_count: 1, source_types: ['yazio_export_v1'],
          }],
        }],
      } satisfies VerificationActivityResponse)
      if (detail.reader === '/analytics/nutrition/analysis') return Promise.resolve({
        ...range, selected_provider: { provider_key: 'yazio' }, period_days: periodDays,
        days: [{
          date: start, provider_key: 'yazio', dietary_energy_kcal: null, protein_g: 0,
          carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null, meals: [],
        }],
      } satisfies AnalysisNutritionResponse)
      return Promise.resolve({
        ...range, selected_provider: { provider_key: 'yazio' }, period_days: periodDays,
        metrics: [{
          metric_key: params.get('metric_key')!, label: 'Vitamin A', category: 'vitamin', unit: 'ug',
          eu_nrv: 800, total: 0, days_with_data: 1, contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
        }],
        series: [{ date: start, provider_key: 'yazio', value: 0 }],
      } satisfies AnalysisMicronutrientResponse)
    })
  }

  it.each(detailCases.filter(({ reader }) => reader !== '/analytics/nutrition/analysis'))(
    'does not promote a departed initial All probe or alter its destination on $path',
    async ({ path, reader }) => {
      serveDetailRanges('2026-03-20')
      const respond = apiMock.getMockImplementation()!
      let probeHeld = false
      const probe = Promise.withResolvers<void>()
      apiMock.mockImplementation((request: string, options?: RequestInit) => {
        if (!request.startsWith(`${reader}?`) || probeHeld) return respond(request, options)
        probeHeld = true
        return probe.promise.then(() => respond(request, options))
      })
      const readerRequests = () => apiMock.mock.calls.map(([request]) => String(request))
        .filter((request) => request.startsWith(`${reader}?`))

      const { wrapper, router } = await mountAnalysis(`${path}?period=all`)
      expect(router.currentRoute.value.query.period).toBe('30')
      expect(readerRequests()).toHaveLength(1)
      const probeParams = new URLSearchParams(readerRequests()[0]!.split('?')[1])
      expect([probeParams.get('start'), probeParams.get('end')]).toEqual(['2026-03-02', '2026-03-31'])
      expect(wrapper.findComponent(PeriodPicker).exists()).toBe(true)

      await router.push('/trends?period=7#departed')
      await flushPromises()
      expect(router.currentRoute.value.fullPath).toBe('/trends?period=7#departed')
      expect(wrapper.get('h1').text()).toBe('Trends')
      expect(wrapper.findComponent(PeriodPicker).exists()).toBe(false)

      probe.resolve()
      await flushPromises()
      await flushPromises()

      expect.soft(router.currentRoute.value.fullPath).toBe('/trends?period=7#departed')
      expect.soft(router.currentRoute.value.path).toBe('/trends')
      expect.soft(router.currentRoute.value.query).toEqual({ period: '7' })
      expect.soft(router.currentRoute.value.hash).toBe('#departed')
      expect.soft(readerRequests()).toHaveLength(1)
    },
  )

  it.each(['', '?period=all'])('defaults Analysis activity to 30 days before reading %s', async (query) => {
    serveDetailRanges()
    const { wrapper, router } = await mountAnalysis(`/analyse/aktivitaet/aktive-energie${query}`)
    expect(router.currentRoute.value.query.period).toBe('30')
    const requests = apiMock.mock.calls.map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/verification/activity?'))
    expect(requests).toEqual(['/analytics/verification/activity?start=2026-03-02&end=2026-03-31&view=canonical'])
    const picker = wrapper.getComponent(PeriodPicker)
    expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    const chart = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as { xAxis: { data: string[] } }
    expect(chart.xAxis.data).toHaveLength(30)
    expect([chart.xAxis.data[0], chart.xAxis.data.at(-1)]).toEqual(['2026-03-02', '2026-03-31'])
  })

  it.each([
    ['180', '2025-10-03'],
    ['365', '2025-04-01'],
  ])('accepts the bounded %s-day Analysis activity URL', async (period, start) => {
    serveDetailRanges()
    const { wrapper, router } = await mountAnalysis(`/analyse/aktivitaet/aktive-energie?period=${period}`)
    expect(router.currentRoute.value.query.period).toBe(period)
    const chart = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as { xAxis: { data: string[] } }
    expect(chart.xAxis.data).toHaveLength(Number(period))
    expect([chart.xAxis.data[0], chart.xAxis.data.at(-1)]).toEqual([start, '2026-03-31'])
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.get(`.analysis-period-option[data-period="${period}"]`).attributes('aria-pressed')).toBe('true')
  })

  it.each(detailCases)('rejects future custom input without changing the applied range on $path', async ({ path, reader }) => {
    serveDetailRanges()
    const query = '?period=custom&start=2026-03-01&end=2026-03-31'
    const { wrapper, router } = await mountAnalysis(`${path}${query}`)
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    const dates = picker.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.04.2026')
    await dates[1]!.setValue('02.04.2026')
    await picker.get('.analysis-period-custom-form').trigger('submit')
    await flushPromises()
    expect(picker.find('[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2026-03-01', end: '2026-03-31',
    })
    expect(picker.get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    const requests = apiMock.mock.calls.map(([request]) => String(request)).filter((request) => request.startsWith(`${reader}?`))
    expect(requests).toHaveLength(1)
    const params = new URLSearchParams(requests[0]!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2026-03-01', '2026-03-31'])
  })

  it.each(detailCases)('retains a 31-day custom range in reader and breadcrumb routes on $path', async ({ path, reader, category }) => {
    serveDetailRanges()
    const { wrapper, router } = await mountAnalysis(`${path}?period=30&source=synthetic#detail`)
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    const dates = picker.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('30.01.2024')
    await dates[1]!.setValue('29.02.2024')
    await picker.get('.analysis-period-custom-form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2024-01-30', end: '2024-02-29', source: 'synthetic',
    })
    expect(router.currentRoute.value.hash).toBe('#detail')
    const requests = apiMock.mock.calls.map(([request]) => String(request)).filter((request) => request.startsWith(`${reader}?`))
    const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2024-01-30', '2024-02-29'])
    for (const target of ['/analyse', category]) {
      const link = wrapper.findAll('.analysis-breadcrumbs a').find((link) =>
        new URL(link.attributes('href')!, 'https://calograph.test').pathname === target)
      expect(link).toBeDefined()
      const query = new URL(link!.attributes('href')!, 'https://calograph.test').searchParams
      expect([query.get('period'), query.get('start'), query.get('end')]).toEqual(['custom', '2024-01-30', '2024-02-29'])
    }
    expect(picker.get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
  })

  it.each(detailCases)('uses the genuine All boundary rather than maxDays on $path', async ({ path, reader }) => {
    serveDetailRanges('2026-03-20')
    const { wrapper, router } = await mountAnalysis(`${path}?period=30`)
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    await picker.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    const requests = apiMock.mock.calls.map(([request]) => String(request)).filter((request) => request.startsWith(`${reader}?`))
    expect(requests).toHaveLength(2)
    const params = new URLSearchParams(requests[1]!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2026-03-20', '2026-03-31'])
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.get('.analysis-period-option[data-period="all"]').attributes('aria-pressed')).toBe('true')
    expect(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['20.03.2026', '31.03.2026'])
  })

  it.each(detailCases)('does not offer All outside the reader boundary on $path', async ({ path }) => {
    serveDetailRanges('2000-01-01')
    const { wrapper, router } = await mountAnalysis(`${path}?period=30`)
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
  })

  it('uses the 30-day Analysis activity default on a same-reader route change', async () => {
    serveDetailRanges()
    const { wrapper, router } = await mountAnalysis('/analyse/aktivitaet/aktive-energie?period=7')
    await router.push('/analyse/aktivitaet/aktive-energie?source=synthetic#detail')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#detail')
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    const chart = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as { xAxis: { data: string[] } }
    expect(chart.xAxis.data).toHaveLength(30)
    expect([chart.xAxis.data[0], chart.xAxis.data.at(-1)]).toEqual(['2026-03-02', '2026-03-31'])
  })

  it('accepts the activity maximum as Custom, never as permission for All', async () => {
    serveDetailRanges()
    const { wrapper, router } = await mountAnalysis('/analyse/aktivitaet/aktive-energie?period=custom&start=2000-01-01&end=2010-01-08')
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start: '2000-01-01', end: '2010-01-08' })
    const chart = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as { xAxis: { data: string[] } }
    expect(chart.xAxis.data).toHaveLength(3661)
    expect([chart.xAxis.data[0], chart.xAxis.data.at(-1)]).toEqual(['2000-01-01', '2010-01-08'])
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    const dates = picker.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[1]!.setValue('09.01.2010')
    await picker.get('.analysis-period-custom-form').trigger('submit')
    expect(picker.find('[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query.end).toBe('2010-01-08')
    const retainedChart = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as typeof chart
    expect(retainedChart.xAxis.data).toEqual(chart.xAxis.data)
  })

  it('retokens populated detail charts on theme changes without rereading data', async () => {
    const colors: Record<string, readonly [string, string]> = {
      '--chart-background': ['#111d2f', '#ffffff'],
      '--chart-grid': ['#263449', '#d6deea'],
      '--chart-axis': ['#60738d', '#64748b'],
      '--chart-label': ['#bac5d3', '#475569'],
      '--chart-tooltip-bg': ['#111d30', '#ffffff'],
      '--chart-tooltip-border': ['#324157', '#bac5d5'],
      '--chart-tooltip-ink': ['#f3f6fb', '#182132'],
      '--chart-series-violet': ['#8b5cf6', '#6d28d9'],
      '--chart-series-teal': ['#2dd4bf', '#08776f'],
      '--chart-series-blue': ['#5b8ff9', '#315fc3'],
      '--chart-series-amber': ['#f6c445', '#905400'],
      '--chart-series-emerald': ['#34d399', '#047857'],
      '--chart-series-baseline': ['#64748b', '#526071'],
      '--chart-series-lilac': ['#d19bff', '#7446af'],
    }
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      getPropertyValue: (name: string) => {
        const values = colors[name]
        return values?.[element.getAttribute('data-theme') === 'light' ? 1 : 0] ?? ''
      },
    }) as CSSStyleDeclaration)
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/weight')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        const nextDate = new Date(Date.parse(`${start}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        return Promise.resolve({
          start_date: start, end_date: params.get('end') ?? nextDate, selected_provider: null,
          points: [{ date: start, weight_kg: 71 }, { date: nextDate, weight_kg: 72 }],
          summary: { measurement_count: 2, latest: { date: nextDate, weight_kg: 72 }, change_kg: 1, average_kg: 71.5, minimum_kg: 71, maximum_kg: 72 },
        })
      }
      if (path.startsWith('/analytics/verification/activity')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        return Promise.resolve({
          start_date: start, end_date: params.get('end') ?? start, view: 'canonical',
          days: [{ date: start, canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 200, record_count: 1, source_types: [] }, providers: [] }],
        })
      }
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        const nextDate = new Date(Date.parse(`${start}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        return Promise.resolve({
          start_date: start, end_date: params.get('end') ?? nextDate, selected_provider: null, period_days: 7,
          metrics: [{ metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: 30, days_with_data: 2, contributors: [] }],
          series: [{ date: start, provider_key: 'yazio', value: 10 }, { date: nextDate, provider_key: 'yazio', value: 20 }],
        })
      }
      return Promise.resolve({})
    })
    setThemePreference('dark')

    const cases = [
      { path: '/analyse/aktivitaet/aktive-energie', key: 'active_energy_kcal', seriesColor: '#905400' },
      { path: '/analyse/koerper/gewicht', key: 'weight_kg', seriesColor: '#6d28d9' },
      { path: '/analyse/mikronaehrstoffe/vitamin_a_ug', key: 'vitamin_a_ug', seriesColor: '#047857' },
    ]
    for (const chartCase of cases) {
      const { wrapper } = await mountAnalysis(chartCase.path, [chartCase.key])
      const chart = wrapper.findComponent({ name: 'ChartPanel' })
      expect(chart.exists()).toBe(true)
      type ThemeOption = {
        backgroundColor: string
        tooltip: { backgroundColor: string; borderColor: string; textStyle: { color: string } }
        xAxis: { axisLine: { lineStyle: { color: string } }; axisLabel: { color: string } }
        yAxis: { axisLine: { lineStyle: { color: string } }; axisLabel: { color: string }; splitLine: { lineStyle: { color: string } } }
        series: Array<{
          name: string
          type: string
          smooth?: boolean | number
          smoothMonotone?: 'x' | 'y' | 'none'
          connectNulls?: boolean
          showSymbol?: boolean
          areaStyle?: { color?: { colorStops?: Array<{ color: string }> } }
          data: unknown[]
          lineStyle?: { color: string }
          itemStyle?: { color: string }
        }>
      }
      const before = chart.props('option') as ThemeOption
      expect(before.series[0]!.data.length).toBeGreaterThan(0)
      const apiCallCount = apiMock.mock.calls.length

      setThemePreference('light')
      await flushPromises()
      const after = chart.props('option') as ThemeOption

      expect(after.backgroundColor).toBe('#ffffff')
      expect(after.tooltip).toMatchObject({ backgroundColor: '#ffffff', borderColor: '#bac5d5', textStyle: { color: '#182132' } })
      expect(after.xAxis.axisLine.lineStyle.color).toBe('#64748b')
      expect(after.xAxis.axisLabel.color).toBe('#475569')
      expect(after.yAxis.axisLine.lineStyle.color).toBe('#64748b')
      expect(after.yAxis.axisLabel.color).toBe('#475569')
      expect(after.yAxis.splitLine.lineStyle.color).toBe('#d6deea')
      if (chartCase.key === 'active_energy_kcal') expect(after.series[0]!.type).toBe('bar')
      if (chartCase.key === 'weight_kg') {
        expect(after.series[0]!.smooth).toBe(0.4)
        expect(after.series[0]!.smoothMonotone).toBe('x')
      }
      if (chartCase.key === 'vitamin_a_ug') {
        expect(after.series[0]!.connectNulls).toBe(false)
        const beforeGradient = before.series[0]!.areaStyle?.color?.colorStops?.map(({ color }) => color)
        const afterGradient = after.series[0]!.areaStyle?.color?.colorStops?.map(({ color }) => color)
        expect(beforeGradient).toBeDefined()
        expect(afterGradient).toEqual([
          'rgba(4, 120, 87, 0.24)',
          'rgba(4, 120, 87, 0.1)',
          'rgba(4, 120, 87, 0.03)',
          'rgba(4, 120, 87, 0)',
        ])
        expect(afterGradient).not.toEqual(beforeGradient)
        expect(after.series[0]!.lineStyle?.color).toBe(chartCase.seriesColor)
      }
      expect(apiMock.mock.calls).toHaveLength(apiCallCount)
      wrapper.unmount()
      setThemePreference('dark')
    }
  })
  it.each(detailCases)('opens custom fields and dismisses the picker on $path', async ({ path }) => {
    serveDetailRanges()
    const { wrapper } = await mountAnalysis(path)
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    expect(dates.map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['02.03.2026', '31.03.2026'])
    const trigger = wrapper.get('.analysis-period-trigger')
    await trigger.trigger('keydown', { key: 'Escape' })
    expect(trigger.attributes('aria-expanded')).toBe('false')
    await trigger.trigger('click')
    document.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    wrapper.unmount()
  })
  it('opens the compact mobile picker, rejects invalid 366-day custom dates, and restores trigger focus on Escape', async () => {
    vi.stubGlobal('innerWidth', 375)
    window.dispatchEvent(new Event('resize'))
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=30')
    const trigger = wrapper.get('.analysis-period-trigger')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    await trigger.trigger('click')
    const popover = wrapper.get('.analysis-period-popover')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(popover.find('.analysis-period-option[data-period="90"]').exists()).toBe(true)
    expect(popover.get('.analysis-period-option[data-period="30"]').attributes('aria-pressed')).toBe('true')
    const dates = popover.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.03.2021')
    await popover.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(popover.find('[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients?')))
      .toHaveLength(1)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger.element)
    wrapper.unmount()
  })
  it('keeps desktop quick-action focus and restores focus to the custom popover opener', async () => {
    const { wrapper } = await mountAnalysis('/analyse/koerper/gewicht?period=30')
    const quickAction = wrapper.findAll('.analysis-period-quick-button')
      .find((button) => button.text() === '7 Tage')!
    const quickElement = quickAction.element as HTMLButtonElement
    quickElement.focus()
    await quickAction.trigger('click')
    await flushPromises()
    expect(document.activeElement).toBe(quickElement)

    const customAction = wrapper.get('.analysis-period-custom-button')
    const customElement = customAction.element as HTMLButtonElement
    customElement.focus()
    await customAction.trigger('click')
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(document.activeElement).toBe(customElement)
    wrapper.unmount()
  })

  it('keeps the activity response when the current period is selected again', async () => {
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie?period=7')
    const activityCalls = () => apiMock.mock.calls
      .filter(([path]) => String(path).startsWith('/analytics/verification/activity'))
    expect(activityCalls()).toHaveLength(1)
    await wrapper.get('.analysis-period-trigger').trigger('click')
    await wrapper.get('.analysis-period-popover .analysis-period-option[aria-pressed="true"]').trigger('click')
    await flushPromises()
    expect(activityCalls()).toHaveLength(1)
    expect(wrapper.find('.dashboard-loading').exists()).toBe(false)
    wrapper.unmount()
  })
  it('applies the one-year preset as the latest 365 rolling days', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper/gewicht')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    await wrapper.get('.analysis-period-popover .analysis-period-option[data-period="365"]').trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight'))
    expect(calls).toHaveLength(2)
    const params = new URLSearchParams(calls[1]!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -364))
    expect(router.currentRoute.value.query.period).toBe('365')
    await wrapper.get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.get('.analysis-period-option[data-period="365"]').attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('applies an inclusive 365-day micronutrient custom range only after validation', async () => {
    const { wrapper, router } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.03.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.find('.analysis-period-error[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients')))
      .toHaveLength(1)

    await dates[1]!.setValue('28.02.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients'))
    expect(calls).toHaveLength(2)
    expect(calls[1]).toContain('start=2020-03-01')
    expect(calls[1]).toContain('end=2021-02-28')
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2020-03-01', end: '2021-02-28',
    })
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })
  it('allows at most 3661 inclusive days for a weight custom range', async () => {
    const { wrapper, router } = await mountAnalysis('/analyse/koerper/gewicht')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    const today = wrapper.find('.analysis-period-custom-form input[type="date"]').attributes('max')!
    await dates[0]!.setValue(formatGermanDate(shiftIsoDate(today, -3661)))
    await dates[1]!.setValue(formatGermanDate(today))
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.find('.analysis-period-error[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query.period).toBe('30')
    await dates[0]!.setValue(formatGermanDate(shiftIsoDate(today, -3660)))
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/analytics/weight'))
    expect(calls).toHaveLength(2)
    expect(calls[1]).toContain(`start=${shiftIsoDate(today, -3660)}`)
    expect(calls[1]).toContain(`end=${today}`)
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: shiftIsoDate(today, -3660), end: today,
    })
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('visually hides semantic Weight list headers without losing dated readings', async () => {
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/weight')
      ? Promise.resolve({
        start_date: '2026-03-01', end_date: '2026-03-31',
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        selected_provider: { provider_key: 'apple_health' },
        points: [{ date: '2026-03-02', weight_kg: 70 }, { date: '2026-03-29', weight_kg: 69.5 }],
        summary: {
          measurement_count: 2, latest: { date: '2026-03-29', weight_kg: 69.5 },
          change_kg: -0.5, average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70,
        },
      } satisfies WeightResponse)
      : Promise.resolve({ analyses: registry, active_card_ids: [] }))
    const { wrapper } = await mountAnalysis('/analyse/koerper/gewicht?period=30')
    await expandReadings(wrapper, '.weight-reading-list', 2)
    expectScreenReaderTableHeaders(wrapper, '.weight-reading-list', ['Datum', 'Gewicht'])
    const rows = wrapper.findAll('.weight-reading-list tbody tr')
    expect(rows.map((row) => row.findAll('td').map((cell) => cell.text()))).toEqual([
      ['02.03.2026', '70 kg'], ['29.03.2026', '69,5 kg'],
    ])
    expect(rows.map((row) => row.get('time').attributes('datetime'))).toEqual([
      '2026-03-02', '2026-03-29',
    ])
    wrapper.unmount()
  })
  it('renders sparse weight points and reports summary fields from the weight reader', async () => {
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/weight')
      ? Promise.resolve({
        start_date: '2026-03-01', end_date: '2026-03-31', selected_provider: { provider_key: 'apple_health' },
        points: [{ date: '2026-03-01', weight_kg: 70 }, { date: '2026-03-29', weight_kg: 69.5 }],
        summary: { measurement_count: 2, latest: { date: '2026-03-29', weight_kg: 69.5 }, change_kg: -0.5, average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70 },
      })
      : Promise.resolve({ analyses: registry, active_card_ids: [] }))
    const { wrapper } = await mountAnalysis('/analyse/koerper/gewicht')
    expect(wrapper.text()).toContain('Messwerte (2)')
    expect(wrapper.text()).not.toMatch(/\d+\s+von\s+\d+\s+Tagen|Tage mit Daten/i)
    expect(wrapper.text()).toContain('69,5 kg')
    expect(wrapper.get('.page-heading').find('.analysis-period-picker').exists()).toBe(true)
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    expect(chart.exists()).toBe(true)

    const option = chart.props('option') as {
      xAxis: { type: string }
      series: Array<{
        type: string; smooth?: boolean | number; smoothMonotone?: 'x' | 'y' | 'none'
        showSymbol?: boolean; data: Array<[number, number]>; lineStyle?: object
      }>
    }
    expect(option.xAxis.type).toBe('time')
    expect(option.series).toHaveLength(1)
    expect(option.series[0]!.type).toBe('line')
    expect(option.series[0]!.smooth).toBe(0.4)
    expect(option.series[0]!.smoothMonotone).toBe('x')
    expect(option.series[0]!.showSymbol).toBe(true)
    expect(option.series[0]!.lineStyle).toBeDefined()
    expect(option.series[0]!.data).toEqual([
      [Date.parse('2026-03-01T00:00:00Z'), 70],
      [Date.parse('2026-03-29T00:00:00Z'), 69.5],
    ])
    wrapper.unmount()
  })

  it('shows an explicit no-data state for a canonical micronutrient detail', async () => {
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(wrapper.get('h1').text()).toContain('Vitamin A')
    expect(wrapper.get('.micronutrient-empty').text()).toMatch(/keine Werte|keine Daten/i)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/demnächst|nicht verfügbar/i)
    expect(wrapper.findAll('.page-heading .analysis-period-picker')).toHaveLength(1)
    expect(wrapper.findAll('.micronutrient-empty')).toHaveLength(1)
    expect(wrapper.findAll('.micronutrient-summary')).toHaveLength(0)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
  })
  it('renders nulls between sparse canonical micronutrient points in the detail chart', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start')!
        const end = params.get('end')!
        return Promise.resolve({
          start_date: start, end_date: end, selected_provider: null, period_days: 7,
          metrics: [{
            metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin',
            unit: 'ug', eu_nrv: 800, total: 30, days_with_data: 2, contributors: [],
          }],
          series: [
            { date: start, provider_key: 'yazio', value: 10 },
            { date: shiftIsoDate(start, 6), provider_key: 'yazio', value: 20 },
          ],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2026-03-25&end=2026-03-31',
    )
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    const option = chart.props('option') as {
      xAxis: { data: string[] }
      series: Array<{ connectNulls?: boolean; data: Array<number | null> }>
    }
    expect(option.xAxis.data).toHaveLength(7)
    expect(option.series[0]!.connectNulls).toBe(false)
    expect(option.series[0]!.data).toEqual([10, null, null, null, null, null, 20])
    wrapper.unmount()
  })

  it('reads active energy only from canonical days and leaves missing values as gaps', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/verification/activity')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        const nextDate = new Date(Date.parse(`${start}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        return Promise.resolve({
          start_date: start,
          end_date: params.get('end') ?? nextDate,
          view: 'canonical',
          days: [
            { date: start, canonical: { provider_key: 'google_health', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] },
            { date: nextDate, canonical: null, providers: [] },
          ],
        })
      }
      return Promise.resolve({ analyses: registry, active_card_ids: [] })
    })
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    expect(wrapper.find('.activity-coverage').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Google Health')
    expect(wrapper.get('.page-heading').find('.analysis-period-picker').exists()).toBe(true)
    expect(wrapper.text()).not.toMatch(/\d+\s+von\s+\d+\s+Tagen mit Messwerten/)
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    const option = chart.props('option') as {
      xAxis: { data: string[] }
      yAxis: { min: number; scale: boolean }
      series: Array<{ type: string; data: Array<number | null> }>
    }
    expect(option.series[0]!.type).toBe('bar')
    expect(option.yAxis).toMatchObject({ min: 0, scale: false })
    expect(option.xAxis.data).toHaveLength(30)
    expect(option.series[0]!.data[0]).toBe(0)
    expect(option.series[0]!.data[1]).toBeNull()
    expect(option.series[0]!.data).toHaveLength(30)
    expect(wrapper.get('.activity-reading-list h2').text()).toBe('Tageswerte')
    wrapper.unmount()
  })
  it('rounds Active Energy detail values to whole kcal without changing chart precision', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({
          start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical',
          days: [{
            date: '2026-03-25',
            canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 289.5, record_count: 1, source_types: [] },
            providers: [],
          }],
        })
      }
      return Promise.resolve({ analyses: registry, active_card_ids: [] })
    })
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie?period=7')
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      yAxis: { axisLabel: { formatter: (value: number) => string } }
      tooltip: { valueFormatter: (value: number) => string }
      series: Array<{ data: Array<number | null> }>
    }
    expect(wrapper.get('.activity-summary-primary > strong').text()).toBe('290 kcal')
    expect(wrapper.get('.activity-reading-list strong').text()).toBe('290 kcal')
    expect(wrapper.find('.activity-coverage').exists()).toBe(false)
    expect(option.series[0]!.data[0]).toBe(289.5)
    expect(option.yAxis.axisLabel.formatter(289.5)).toBe('290')
    expect(option.tooltip.valueFormatter(289.5)).toBe('290 kcal')
    wrapper.unmount()
  })
  it('does not mutate the canonical Active Energy response while deriving chart values', async () => {
    let activityResponse: {
      start_date: string
      end_date: string
      view: 'canonical'
      days: Array<{ date: string; canonical: { provider_key: string; status: string; active_energy_kcal: number; record_count: number; source_types: string[] } | null; providers: unknown[] }>
    } | null = null
    let originalResponse: unknown = null
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/verification/activity')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        const nextDate = new Date(Date.parse(`${start}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10)
        activityResponse = {
          start_date: start,
          end_date: nextDate,
          view: 'canonical',
          days: [
            { date: start, canonical: { provider_key: 'google_health', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] },
            { date: nextDate, canonical: null, providers: [] },
          ],
        }
        originalResponse = structuredClone(activityResponse)
        return Promise.resolve(activityResponse)
      }
      return Promise.resolve({ analyses: registry, active_card_ids: [] })
    })

    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    expect(activityResponse).not.toBeNull()
    expect(activityResponse).toEqual(originalResponse)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).props('option').series[0].data.slice(0, 2)).toEqual([0, null])
    wrapper.unmount()
  })


  it('uses the canonical available-day mean for the Active Energy card preview', async () => {
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/verification/activity')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start') ?? '2026-03-25'
        const dateAtOffset = (days: number) => new Date(Date.parse(`${start}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10)
        return Promise.resolve({
          start_date: start, end_date: params.get('end') ?? dateAtOffset(6), view: 'canonical',
          days: [
            { date: dateAtOffset(0), canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 200, record_count: 1, source_types: [] }, providers: [] },
            { date: dateAtOffset(1), canonical: null, providers: [] },
            { date: dateAtOffset(2), canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 100, record_count: 1, source_types: [] }, providers: [] },
          ],
        })
      }
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences' && options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as {
          active_card_ids: string[]
        }
        activeCardIds = body.active_card_ids
        return Promise.resolve(analysisPreferencesResponse())
      }
      if (path === '/settings/analysis-preferences') {
        return Promise.resolve(analysisPreferencesResponse())
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'])

    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"] .analysis-metric-value').text()).toBe('150 kcal')
    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"]').text())
      .not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    const bars = wrapper.get('.analysis-card-list > li[data-card-key="activity"]').findAll('.analysis-mini-chart rect')
    expect(bars).toHaveLength(2)
    expect(Number(bars[1].attributes('x')) - Number(bars[0].attributes('x'))).toBeCloseTo(80)
    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"] .analysis-average-mark').attributes('title'))
      .toBe('Durchschnitt pro Tag')
    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"]').text())
      .not.toContain('Durchschnitt pro Tag')
    wrapper.unmount()
  })
  it('rounds a 219.8 kcal Active Energy average to 220 kcal', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({
          start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical',
          days: [{
            date: '2026-03-25',
            canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 219.8, record_count: 1, source_types: [] },
            providers: [],
          }],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'])

    expect(wrapper.get('.analysis-card-list > li[data-card-key="activity"] .analysis-metric-value').text()).toBe('220 kcal')
    wrapper.unmount()
  })

  it('keeps an exact-zero canonical value in the Active Energy card preview', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({
          start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical',
          days: [{ date: '2026-03-25', canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] }],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'])
    const activityCard = wrapper.get('.analysis-card-list > li[data-card-key="activity"]')
    expect(activityCard.get('.analysis-metric-value').text()).toBe('0 kcal')
    expect(activityCard.text()).toContain('0 kcal im Zeitraum')
    expect(activityCard.text()).not.toMatch(/\d+ Tage mit Aktivitätsdaten/i)
    const chart = activityCard.get('.analysis-mini-chart--amber')
    expect(chart.findAll('rect')).toHaveLength(0)
    const zeroMarkers = chart.findAll('circle')
    expect(zeroMarkers).toHaveLength(1)
    expect(zeroMarkers[0]!.attributes('cy')).toBe('34')
    wrapper.unmount()
  })
  it('pairs the active-energy heading with its amber decorative activity icon', async () => {
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    try {
      const icon = wrapper.get('.analysis-detail-icon--activity')
      expect(icon.attributes('aria-hidden')).toBe('true')
      expect(icon.text()).toBe('↗')
      expect(wrapper.get('h1').text()).toContain('Aktive Energie')
      expect(wrapper.get('h1').element.contains(icon.element)).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps zero energy distinguishable from a missing day on an amber zero-origin detail axis', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/verification/activity')) return Promise.resolve({
        start_date: '2026-03-28', end_date: '2026-03-30', view: 'canonical',
        days: [
          { date: '2026-03-28', canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] },
          { date: '2026-03-29', canonical: null, providers: [] },
          { date: '2026-03-30', canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 120, record_count: 1, source_types: [] }, providers: [] },
        ],
      })
      return Promise.resolve({ analyses: registry, active_card_ids: [] })
    })
    const { wrapper } = await mountAnalysis(
      '/analyse/aktivitaet/aktive-energie?period=custom&start=2026-03-28&end=2026-03-30',
    )
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { data: string[] }; yAxis: { min: number }
      series: Array<{
        type: string; data: (number | null)[]; itemStyle: { color: string }
        markPoint?: {
          symbol?: string; symbolSize?: number; itemStyle?: { color: string }
          data: Array<{
            coord: [string, number]; symbol?: string; symbolSize?: number
            itemStyle?: { color: string }
          }>
        }
      }>
    }
    expect(option.xAxis.data).toEqual(['2026-03-28', '2026-03-29', '2026-03-30'])
    expect(option.yAxis.min).toBe(0)
    expect(option.series[0]).toMatchObject({
      type: 'bar', data: [0, null, 120], itemStyle: { color: chartColors.value.amber },
    })
    const zeroMarker = option.series[0]?.markPoint
    expect(zeroMarker?.data.map(({ coord }) => coord)).toEqual([['2026-03-28', 0]])
    expect(zeroMarker?.symbol).not.toBe('none')
    expect(zeroMarker?.data[0]?.symbol).not.toBe('none')
    expect(zeroMarker?.data[0]?.symbolSize ?? zeroMarker?.symbolSize ?? 10).toBeGreaterThan(0)
    expect(zeroMarker?.data[0]?.itemStyle?.color ?? zeroMarker?.itemStyle?.color)
      .toBe(chartColors.value.amber)
    const zeroDay = wrapper.get('.activity-reading-list time[datetime="2026-03-28"]').element.closest('li')
    expect(zeroDay?.textContent).toContain('0 kcal')
    expect(wrapper.find('.activity-reading-list time[datetime="2026-03-29"]').exists()).toBe(false)
    expect(wrapper.find('.activity-coverage').exists()).toBe(false)
    expect(wrapper.get('.activity-summary-primary > strong').text()).toBe('60 kcal')
    expect(wrapper.findAll('.activity-reading-list li').map((row) => row.get('strong').text()))
      .toEqual(['0 kcal', '120 kcal'])
    wrapper.unmount()
  })

  it('renders an Activity category overview from canonical daily values and preserves its range', async () => {
    const start = '2026-03-25'
    const end = '2026-03-31'
    const values: (number | null)[] = [100.4, null, 0, 200.2, null, 300.3, null]
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/verification/activity?')) {
        return Promise.resolve({
          start_date: start,
          end_date: end,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          view: 'canonical',
          days: values.map((value, index) => ({
            date: shiftIsoDate(start, index),
            canonical: {
              provider_key: 'yazio' as const,
              status: value === null ? 'no_data' as const : 'available' as const,
              active_energy_kcal: value,
              record_count: value === null ? 0 : 1,
              source_types: [],
            },
            providers: [],
          })),
        } satisfies VerificationActivityResponse)
      }
      return fallback(path, options)
    })
    const { wrapper } = await mountAnalysis(
      '/analyse/aktivitaet?period=custom&start=2026-03-25&end=2026-03-31',
    )

    expect(wrapper.find('.analysis-activity-overview').exists()).toBe(true)
    expect(wrapper.get('.activity-overview-kpi').text()).toContain('150 kcal')
    expect(wrapper.get('.activity-overview-kpi').text()).toContain('Durchschnitt pro Tag')
    expect(wrapper.get('.activity-overview-total').text()).toContain('601 kcal')
    expect(wrapper.text()).not.toMatch(/\d+\s+von\s+\d+\s+Tagen|Aktivitätsdaten|yazio|analysis_demo|provider_key/i)
    expect(wrapper.get('.activity-overview-total').text()).toContain('Summe im Zeitraum')
    const statisticCards = wrapper.findAll('article.card')
    expect(statisticCards.map((card) => card.get('h2').text())).toEqual(['Minimum', 'Maximum'])
    expect(statisticCards.map((card) => card.get('strong').text())).toEqual(['0 kcal', '300 kcal'])
    expect(wrapper.text()).not.toMatch(/Schritte|Distanz|Training/)

    const detailUrl = new URL(
      wrapper.get('.activity-overview-kpi').attributes('href')!,
      'https://calograph.test',
    )
    expect(detailUrl.pathname).toBe('/analyse/aktivitaet/aktive-energie')
    expect(detailUrl.searchParams.toString()).toBe(
      'period=custom&start=2026-03-25&end=2026-03-31',
    )

    const request = apiMock.mock.calls.map(([path]) => String(path))
      .find((path) => path.startsWith('/analytics/verification/activity?'))
    expect(request).toBe(
      '/analytics/verification/activity?start=2026-03-25&end=2026-03-31&view=canonical',
    )

    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { data: string[] }
      yAxis: { min: number; axisLabel: { formatter: (value: number) => string } }
      tooltip: { valueFormatter: (value: number) => string }
      series: Array<{ type: string; data: (number | null)[]; itemStyle: { color: string } }>
    }
    expect(option.xAxis.data).toEqual(Array.from({ length: 7 }, (_, index) => shiftIsoDate(start, index)))
    expect(option.yAxis.min).toBe(0)
    expect(option.yAxis.axisLabel.formatter(289.5)).toBe('290')
    expect(option.tooltip.valueFormatter(289.5)).toBe('290 kcal')
    expect(option.series).toHaveLength(1)
    expect(option.series[0]).toMatchObject({
      type: 'bar',
      data: values,
      itemStyle: { color: chartColors.value.amber },
    })
    wrapper.unmount()
  })

  it('switches Activity chart view with an accessible switch and restores both modes for the same account', async () => {
    const start = '2026-03-25'
    const end = '2026-03-31'
    const values: (number | null)[] = [100, null, 0, 220, null, 90, 180]
    const userId = 'activity-chart-view-test'
    const otherUserId = `${userId}-other`
    const preferenceKey = `calograph.analysis.activity.chart-view.v1.${encodeURIComponent(userId)}`
    const otherPreferenceKey = `calograph.analysis.activity.chart-view.v1.${encodeURIComponent(otherUserId)}`
    localStorage.removeItem(preferenceKey)
    localStorage.removeItem(otherPreferenceKey)
    const path = `/analyse/aktivitaet?period=custom&start=${start}&end=${end}`
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((requestPath: string, options?: RequestInit) => {
      if (requestPath.startsWith('/analytics/verification/activity?')) {
        return Promise.resolve({
          start_date: start,
          end_date: end,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          view: 'canonical',
          days: values.map((value, index) => ({
            date: shiftIsoDate(start, index),
            canonical: value === null ? null : {
              provider_key: 'yazio' as const,
              status: 'available' as const,
              active_energy_kcal: value,
              record_count: 1,
              source_types: [],
            },
            providers: [],
          })),
        } satisfies VerificationActivityResponse)
      }
      return fallback(requestPath, options)
    })

    previousChartAmberStyle = document.documentElement.style.getPropertyValue('--chart-series-amber')
    document.documentElement.style.setProperty('--chart-series-amber', '#d97706')
    setThemePreference('light')
    const first = await mountAnalysis(path, [...DEFAULT_ACTIVE_CARD_IDS], userId)
    const viewSwitch = (wrapper: VueWrapper) =>
      wrapper.getComponent({ name: 'ChartPanel' }).get('input[role="switch"]')
    const readOption = (wrapper: VueWrapper) =>
      wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
        xAxis: { data: string[] }
        series: Array<{
          type: string
          data: (number | null)[]
          smooth?: boolean | number
          smoothMonotone?: 'x' | 'y' | 'none'
          connectNulls?: boolean
          showSymbol?: boolean
          symbol?: string
          symbolSize?: number
          itemStyle?: { color?: string }
          lineStyle?: { color?: string; width?: number }
          areaStyle?: {
            origin?: string
            color?: { colorStops?: Array<{ offset: number; color: string }> }
          }
        }>
      }

    expect(viewSwitch(first.wrapper).attributes('aria-label')).toBe('Linienansicht für aktive Energie')
    expect((viewSwitch(first.wrapper).element as HTMLInputElement).checked).toBe(false)
    expect(readOption(first.wrapper).series[0]).toMatchObject({
      type: 'bar', data: values, itemStyle: { color: chartColors.value.amber },
    })
    const requestCountBeforeToggle = apiMock.mock.calls.filter(([requestPath]) =>
      String(requestPath).startsWith('/analytics/verification/activity?')).length

    await viewSwitch(first.wrapper).setValue(true)
    await flushPromises()

    expect((viewSwitch(first.wrapper).element as HTMLInputElement).checked).toBe(true)
    expect(readOption(first.wrapper).xAxis.data)
      .toEqual(Array.from({ length: values.length }, (_, index) => shiftIsoDate(start, index)))
    const lineSeries = readOption(first.wrapper).series[0]!
    expect(lineSeries).toMatchObject({
      type: 'line',
      data: values,
      smooth: 0.4,
      smoothMonotone: 'x',
      connectNulls: true,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: 8,
      itemStyle: { color: chartColors.value.amber },
      lineStyle: { color: chartColors.value.amber, width: 2.5 },
      areaStyle: { origin: 'start' },
    })
    expect(lineSeries).not.toHaveProperty('markPoint')
    const gradientStops = lineSeries.areaStyle?.color?.colorStops ?? []
    expect(gradientStops.map(({ offset }) => offset)).toEqual([0, 0.45, 0.75, 1])
    const alphas = gradientStops.map(({ color }) =>
      Number(color.slice(color.lastIndexOf(',') + 1, -1).trim()))
    expect(alphas).toEqual([0.2, 0.1, 0.035, 0])
    expect(apiMock.mock.calls.filter(([requestPath]) =>
      String(requestPath).startsWith('/analytics/verification/activity?')).length)
      .toBe(requestCountBeforeToggle)

    const reopenedLine = await mountAnalysis(path, [...DEFAULT_ACTIVE_CARD_IDS], userId)
    expect((viewSwitch(reopenedLine.wrapper).element as HTMLInputElement).checked).toBe(true)
    expect(readOption(reopenedLine.wrapper).series[0]).toMatchObject({ type: 'line', data: values })

    const otherAccount = await mountAnalysis(path, [...DEFAULT_ACTIVE_CARD_IDS], otherUserId)
    expect((viewSwitch(otherAccount.wrapper).element as HTMLInputElement).checked).toBe(false)
    expect(readOption(otherAccount.wrapper).series[0]).toMatchObject({ type: 'bar', data: values })

    await viewSwitch(reopenedLine.wrapper).setValue(false)
    await flushPromises()
    const reopenedBars = await mountAnalysis(path, [...DEFAULT_ACTIVE_CARD_IDS], userId)
    expect((viewSwitch(reopenedBars.wrapper).element as HTMLInputElement).checked).toBe(false)
    expect(readOption(reopenedBars.wrapper).series[0]).toMatchObject({
      type: 'bar', data: values, itemStyle: { color: chartColors.value.amber },
    })
    localStorage.removeItem(preferenceKey)
    localStorage.removeItem(otherPreferenceKey)
  })

  it('shows an empty Activity state rather than zero KPIs when no days have readings', async () => {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/verification/activity?')) {
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start')!
        const end = params.get('end')!
        const days = Array.from({ length: inclusiveDays(path) }, (_, index) => ({
          date: shiftIsoDate(start, index),
          canonical: {
            provider_key: 'yazio' as const,
            status: 'no_data' as const,
            active_energy_kcal: null,
            record_count: 0,
            source_types: [],
          },
          providers: [],
        }))
        return Promise.resolve({
          start_date: start, end_date: end,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          view: 'canonical', days,
        } satisfies VerificationActivityResponse)
      }
      return fallback(path, options)
    })
    const { wrapper, router } = await mountAnalysis('/analyse/aktivitaet?source=synthetic#activity')
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#activity')
    const request = apiMock.mock.calls.map(([path]) => String(path))
      .find((path) => path.startsWith('/analytics/verification/activity?'))
    expect(request).toBeDefined()
    const requestParams = new URLSearchParams(request!.split('?')[1])
    expect([requestParams.get('start'), requestParams.get('end')]).toEqual(['2026-03-02', '2026-03-31'])

    expect(wrapper.find('.analysis-activity-overview').exists()).toBe(true)
    expect(wrapper.get('[role="status"]').text()).toContain('Aktivitätswerte')
    expect(wrapper.find('.activity-overview-kpi').exists()).toBe(false)
    expect(wrapper.find('.activity-overview-total').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
    expect(wrapper.findComponent(PeriodPicker).exists()).toBe(true)
    wrapper.unmount()
  })

  it('retries Activity reader errors without showing a zero or empty summary', async () => {
    let attempts = 0
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/analytics/verification/activity?')) {
        attempts += 1
        if (attempts === 1) return Promise.reject(new Error('synthetic reader outage'))
        const params = new URLSearchParams(path.split('?')[1] ?? '')
        const start = params.get('start')!
        const end = params.get('end')!
        const days = Array.from({ length: inclusiveDays(path) }, (_, index) => {
          const value = index === 2 ? 150 : null
          return {
            date: shiftIsoDate(start, index),
            canonical: {
              provider_key: 'yazio' as const,
              status: value === null ? 'no_data' as const : 'available' as const,
              active_energy_kcal: value,
              record_count: value === null ? 0 : 1,
              source_types: [],
            },
            providers: [],
          }
        })
        return Promise.resolve({
          start_date: start, end_date: end,
          period_capabilities: { supports_all_data: false, all_data_start_date: null },
          view: 'canonical', days,
        } satisfies VerificationActivityResponse)
      }
      return fallback(path, options)
    })
    const { wrapper, router } = await mountAnalysis('/analyse/aktivitaet?period=7')

    expect(wrapper.find('.analysis-activity-overview').exists()).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toBeTruthy()
    expect(wrapper.find('.activity-overview-kpi').exists()).toBe(false)
    expect(wrapper.find('.activity-overview-total').exists()).toBe(false)
    expect(router.currentRoute.value.query.period).toBe('7')

    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()

    expect(attempts).toBe(2)
    expect(wrapper.get('.activity-overview-kpi').text()).toContain('150 kcal')
    wrapper.unmount()
  })

})

describe('Analysis V2 nutrition detail', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const nutritionResponse: AnalysisNutritionResponse = {
    start_date: '2020-03-01',
    end_date: '2020-03-03',
    selected_provider: { provider_key: 'yazio' },
    period_capabilities: { supports_all_data: false, all_data_start_date: null },
    period_days: 3,
    days: [
      {
        date: '2020-03-01', provider_key: 'yazio',
        dietary_energy_kcal: 390, protein_g: 0, carbohydrates_g: 18,
        fat_g: 12, fiber_g: 4, sugar_g: 6, saturated_fat_g: 2,
        meals: [],
      },
      {
        date: '2020-03-02', provider_key: 'yazio',
        dietary_energy_kcal: 430, protein_g: null, carbohydrates_g: 24,
        fat_g: 16, fiber_g: 5, sugar_g: 8, saturated_fat_g: 3,
        meals: [{
          event_id: 'oats-uuid', local_date: '2020-03-02', serving_amount: null, serving_unit: null,
          provider_key: 'yazio', local_time: '08:15:00', meal_type: 'breakfast',
          display_name: 'Haferflocken', dietary_energy_kcal: 180, protein_g: null,
          carbohydrates_g: 21, fat_g: 5, fiber_g: 3, sugar_g: 4, saturated_fat_g: 1,
        }],
      },
      {
        date: '2020-03-03', provider_key: null,
        dietary_energy_kcal: null, protein_g: null, carbohydrates_g: null,
        fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [],
      },
    ],
  }
  const rangeQuery = '?period=custom&start=2020-03-01&end=2020-03-03'
  const nutritionRequests = () => apiMock.mock.calls.map(([path]) => String(path))
    .filter((path) => path.startsWith('/analytics/nutrition/analysis'))

  function serveNutrition(response: AnalysisNutritionResponse = nutritionResponse) {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/analysis')) return Promise.resolve(structuredClone(response))
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      return Promise.resolve({})
    })
  }

  const mealFixture = {
    event_id: 'fixture-uuid', local_date: '2020-03-01', serving_amount: null, serving_unit: null,
    provider_key: 'yazio', local_time: null, meal_type: null, display_name: null,
    dietary_energy_kcal: null, protein_g: null, carbohydrates_g: null, fat_g: null,
    fiber_g: null, sugar_g: null, saturated_fat_g: null,
  } satisfies AnalysisNutritionResponse['days'][number]['meals'][number]

  function dailyMealLinks(wrapper: VueWrapper) {
    return wrapper.findAll('a[data-meal-entry]')
  }

  it.each([
    { label: 'Energie', key: 'dietary_energy_kcal', chart: 0 },
    { label: 'Protein', key: 'protein_g', chart: 1 },
    { label: 'Kohlenhydrate', key: 'carbohydrates_g', chart: 1 },
    { label: 'Fett', key: 'fat_g', chart: 1 },
  ])('navigates the $label chart title or macro legend through a native period-preserving detail link', async ({ label, key, chart }) => {
    serveNutrition()
    const { wrapper, router } = await mountAnalysis(
      `/analyse/ernaehrung${rangeQuery}&source=synthetic&tag=one&tag=two#nutrition`,
    )
    const panel = wrapper.findAllComponents({ name: 'ChartPanel' })[chart]!
    const link = panel.findAll(chart === 0 ? 'h2 a' : 'a').find((candidate) => candidate.text() === label)
    expect(link).toBeDefined()
    const target = new URL(link!.attributes('href')!, 'https://calograph.test')
    expect(target.pathname).toBe(`/analyse/ernaehrung/${key}`)
    expect(target.searchParams.get('period')).toBe('custom')
    expect(target.searchParams.get('start')).toBe('2020-03-01')
    expect(target.searchParams.get('end')).toBe('2020-03-03')
    expect(target.searchParams.get('source')).toBe('synthetic')
    expect(target.searchParams.getAll('tag')).toEqual(['one', 'two'])
    expect(target.hash).toBe('#nutrition')
    const anchor = link!.element as HTMLAnchorElement
    expect(anchor.tabIndex).toBe(0)
    anchor.focus()
    expect(document.activeElement).toBe(anchor)
    await link!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe(`/analyse/ernaehrung/${key}`)
    expect(router.currentRoute.value.query).toEqual({
      period: 'custom', start: '2020-03-01', end: '2020-03-03',
      source: 'synthetic', tag: ['one', 'two'],
    })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(wrapper.get('h1').text()).toBe(label)
    wrapper.unmount()
  })

  it('sorts newest meal occasions chronologically with stable time ties and missing times last', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [
        { ...nutritionResponse.days[0]!, meals: [
          { ...mealFixture, event_id: 'older-porridge', display_name: 'Older porridge', local_time: '23:59:00' },
        ] },
        { ...nutritionResponse.days[2]!, meals: [
          { ...mealFixture, event_id: 'midday-soup', display_name: 'Midday soup', local_time: '12:00:00', meal_type: 'lunch', dietary_energy_kcal: 240 },
          { ...mealFixture, event_id: 'first-dinner', display_name: 'First tied dinner', local_time: '19:00:00', meal_type: 'dinner', dietary_energy_kcal: 410 },
          { ...mealFixture, event_id: 'untimed-apple', display_name: 'Untimed apple' },
          { ...mealFixture, event_id: 'second-dinner', display_name: 'Second tied dinner', local_time: '19:00:00', meal_type: 'dinner', dietary_energy_kcal: 420 },
          { ...mealFixture, event_id: 'latest-snack', display_name: 'Latest snack', local_time: '21:00:00', meal_type: 'snack', dietary_energy_kcal: 0 },
          { ...mealFixture, event_id: 'blank-one', display_name: '   ', local_time: '23:59:00' },
          { ...mealFixture, event_id: 'blank-two', display_name: '', local_time: '23:58:00' },
          { ...mealFixture, event_id: 'blank-three', display_name: null, local_time: '23:57:00' },
        ] },
        { ...nutritionResponse.days[1]!, meals: [
          { ...mealFixture, event_id: 'previous-lunch', display_name: 'Previous lunch', local_time: '12:30:00' },
          { ...mealFixture, event_id: 'previous-dinner', display_name: 'Previous dinner', local_time: '20:00:00', meal_type: 'dinner', dietary_energy_kcal: 510 },
          { ...mealFixture, event_id: 'previous-breakfast', display_name: 'Previous breakfast', local_time: '08:00:00' },
        ] },
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    const links = dailyMealLinks(wrapper)
    expect(links).toHaveLength(7)
    for (const [index, name] of [
      'Midday soup', 'First tied dinner', 'Second tied dinner',
      'Latest snack', 'Untimed apple', 'Previous lunch', 'Previous dinner',
    ].entries()) {
      expect(links[index]!.text()).toContain(name)
    }
    expect(links.map((link) => link.attributes('href'))).toEqual([
      '/ernaehrung/eintrag/midday-soup?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/first-dinner?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/second-dinner?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/latest-snack?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/untimed-apple?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/previous-lunch?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
      '/ernaehrung/eintrag/previous-dinner?period=custom&start=2020-03-01&end=2020-03-03&from=analysis',
    ])
    expect(wrapper.findAll('.nutrition-meal-date').map((heading) => heading.text()))
      .toEqual(['03.03.2020', '02.03.2020'])
    expect(wrapper.get('.nutrition-meal-occasion[data-meal-type="snack"] .nutrition-meal-occasion-title').text())
      .toContain('21:00')
    expect(links[0]!.text()).toContain('240 kcal')
    expect(links[1]!.text()).toContain('410 kcal')
    expect(links[2]!.text()).toContain('420 kcal')
    expect(links[3]!.text()).toContain('0 kcal')
    expect(links[3]!.text()).not.toContain('21:00')
    expect(links[6]!.text()).toContain('510 kcal')
    expect(wrapper.text()).not.toMatch(/Older porridge|Previous breakfast|yazio|provider/i)
    expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
      '/analytics/nutrition/analysis?start=2020-03-01&end=2020-03-03',
    ])
    wrapper.unmount()
  })

  it.each([
    { locale: 'de' as const, labels: ['Frühstück', 'Mittagessen', 'Abendessen', 'Snack'] },
    { locale: 'en' as const, labels: ['Breakfast', 'Lunch', 'Dinner', 'Snack'] },
  ])('localizes actual meal types in $locale without exposing provider IDs', async ({ locale, labels }) => {
    setLocale(locale)
    serveNutrition({
      ...nutritionResponse,
      days: [{ ...nutritionResponse.days[0]!, meals: [
        { ...mealFixture, event_id: 'oats', display_name: 'Oats', meal_type: 'breakfast', local_time: '08:00:00', dietary_energy_kcal: 100 },
        { ...mealFixture, event_id: 'soup', display_name: 'Soup', meal_type: 'lunch', local_time: '12:00:00', dietary_energy_kcal: 200 },
        { ...mealFixture, event_id: 'rice', display_name: 'Rice', meal_type: 'dinner', local_time: '19:00:00', dietary_energy_kcal: 300 },
        { ...mealFixture, event_id: 'apple', display_name: 'Apple', meal_type: 'snack', local_time: '21:00:00', dietary_energy_kcal: 50 },
      ] }],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    const links = dailyMealLinks(wrapper)
    expect(links).toHaveLength(4)
    expect(wrapper.findAll('.nutrition-meal-occasion-title').map((header) => header.text()))
      .toEqual(labels.map((label, index) => `${label} · ${['08:00', '12:00', '19:00', '21:00'][index]}`))
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text()))
      .toEqual(['Oats', 'Soup', 'Rice', 'Apple'])
    expect(wrapper.text()).not.toMatch(/yazio|provider/i)
    wrapper.unmount()
  })

  it('renders real meals without macro aggregates and keeps missing metadata distinct from zero kcal', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [{ ...nutritionResponse.days[2]!, meals: [
        { ...mealFixture, event_id: 'tea', display_name: 'Zero calorie tea', local_time: '10:00:00', meal_type: 'snack', dietary_energy_kcal: 0 },
        { ...mealFixture, event_id: 'unspecified-meal', display_name: 'Unspecified meal' },
      ] }],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    const links = dailyMealLinks(wrapper)
    expect(links).toHaveLength(2)
    const snackOccasion = wrapper.get('.nutrition-meal-occasion[data-meal-type="snack"]')
    expect(snackOccasion.get('.nutrition-meal-occasion-title').text()).toBe('Snack · 10:00')
    const zeroCalorieEntry = snackOccasion.get('a[data-meal-entry]')
    expect(zeroCalorieEntry.text()).toContain('Zero calorie tea')
    expect(zeroCalorieEntry.text()).toContain('0 kcal')
    expect(zeroCalorieEntry.text()).not.toContain('10:00')
    expect(links[1]!.text()).toContain('Unspecified meal')
    expect(wrapper.get('.nutrition-meal-date').text()).toBe('03.03.2020')
    expect(links[1]!.text()).not.toMatch(/0 kcal|00:00|Frühstück|Mittagessen|Abendessen|Snack|null|undefined|NaN/)
    expect(wrapper.find('.nutrition-empty').exists()).toBe(false)
    expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
      '/analytics/nutrition/analysis?start=2020-03-01&end=2020-03-03',
    ])
    wrapper.unmount()
  })

  it('does not invent meal rows from daily totals or blank meal names', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [{ ...nutritionResponse.days[0]!, meals: [
        { ...mealFixture, display_name: null, meal_type: 'breakfast', dietary_energy_kcal: 100 },
        { ...mealFixture, display_name: '', meal_type: 'lunch', dietary_energy_kcal: 200 },
        { ...mealFixture, display_name: '   ', meal_type: 'dinner', dietary_energy_kcal: 90 },
      ] }],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    expect(dailyMealLinks(wrapper)).toHaveLength(0)
    expect(wrapper.get('.nutrition-overview-kpi-value').text()).toBe('390 kcal')
    const dailyAccess = wrapper.get('.nutrition-meals-empty a')
    expect(dailyAccess).toBeDefined()
    expect(dailyAccess!.attributes('href')).toBe('/ernaehrung?date=2020-03-03')
    expect((dailyAccess!.element as HTMLAnchorElement).tabIndex).toBe(0)
    wrapper.unmount()
  })

  it('opens an actual meal by UUID preserving Analysis context and restores the origin through browser Back', async () => {
    serveNutrition()
    const { wrapper, router } = await mountAnalysis(
      `/analyse/ernaehrung${rangeQuery}&source=synthetic#nutrition`,
    )
    const links = dailyMealLinks(wrapper)
    expect(links).toHaveLength(1)
    expect(links[0]!.text()).toContain('Haferflocken')
    expect(links[0]!.attributes('href')).toBe('/ernaehrung/eintrag/oats-uuid?period=custom&start=2020-03-01&end=2020-03-03&source=synthetic&from=analysis')
    expect((links[0]!.element as HTMLAnchorElement).tabIndex).toBe(0)
    await links[0]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/ernaehrung/eintrag/oats-uuid')
    expect(router.currentRoute.value.query).toEqual({
      period: 'custom', start: '2020-03-01', end: '2020-03-03', source: 'synthetic', from: 'analysis',
    })
    expect(router.currentRoute.value.hash).toBe('')
    expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
      '/analytics/nutrition/analysis?start=2020-03-01&end=2020-03-03',
    ])
    router.back()
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(`/analyse/ernaehrung${rangeQuery}&source=synthetic#nutrition`)
    wrapper.unmount()
  })

  it('renders the four available nutrition averages as period-preserving detail links', async () => {
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}&source=synthetic#nutrition`)
    const kpis = wrapper.findAll('a.nutrition-overview-kpi')

    expect(kpis).toHaveLength(4)
    expect(wrapper.get('h1').text()).toBe('Ernährung')
    expect(wrapper.get('h1').findComponent(PhForkKnife).exists()).toBe(true)
    expect(wrapper.find('.nutrition-coverage').exists()).toBe(false)

    const averageMarkers = kpis.map((kpi) => kpi.get('.nutrition-overview-kpi-caption'))
    expect(averageMarkers.map((marker) => marker.text())).toEqual(['⌀', '⌀', '⌀', '⌀'])
    expect(averageMarkers.map((marker) => marker.attributes('title'))).toEqual(Array(4).fill('Durchschnitt pro Tag'))
    expect(averageMarkers.map((marker) => marker.attributes('aria-label'))).toEqual(Array(4).fill('Durchschnitt pro Tag'))
    expect(kpis.map((kpi) => kpi.get('.nutrition-overview-kpi-value').text())).toEqual([
      '410 kcal', '0 g', '21 g', '14 g',
    ])
    for (const [index, key] of [
      'dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g',
    ].entries()) {
      const target = new URL(kpis[index]!.attributes('href')!, 'http://calograph.test')
      expect(target.pathname).toBe(`/analyse/ernaehrung/${key}`)
      expect(target.searchParams.get('period')).toBe('custom')
      expect(target.searchParams.get('start')).toBe('2020-03-01')
      expect(target.searchParams.get('end')).toBe('2020-03-03')
      expect(target.searchParams.get('source')).toBe('synthetic')
      expect(target.hash).toBe('#nutrition')
    }
    wrapper.unmount()
  })

  it('rounds displayed average energy to whole kcal without rounding trend readings', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [
        { ...nutritionResponse.days[0]!, dietary_energy_kcal: 1626 },
        { ...nutritionResponse.days[1]!, dietary_energy_kcal: 1627 },
        { ...nutritionResponse.days[2]!, dietary_energy_kcal: null },
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    const energyKpi = wrapper.findAll('a.nutrition-overview-kpi')
      .find((link) => link.attributes('href')?.includes('dietary_energy_kcal'))!
    const energyChart = wrapper.findAllComponents({ name: 'ChartPanel' })[0]!
    const option = energyChart.props('option') as { series: Array<{ data: Array<number | null> }> }

    expect(energyKpi.get('.nutrition-overview-kpi-value').text()).toBe('1.627 kcal')
    expect(option.series[0]!.data).toEqual([1626, 1627, null])
    wrapper.unmount()
  })

  it.each(['dark', 'light'] as const)('renders separate energy and macro trends with semantic colors and real gaps in %s mode', async (theme) => {
    const colors: Record<string, readonly [string, string]> = {
      '--chart-series-violet': ['#8b5cf6', '#7c3aed'],
      '--chart-series-teal': ['#2dd4bf', '#278979'],
      '--chart-series-blue': ['#5b8ff9', '#2563eb'],
      '--chart-series-amber': ['#f6c445', '#d97706'],
    }
    vi.stubGlobal('getComputedStyle', (element: Element) => ({
      getPropertyValue: (name: string) => colors[name]?.[element.getAttribute('data-theme') === 'light' ? 1 : 0] ?? '',
    }) as CSSStyleDeclaration)
    setThemePreference(theme === 'dark' ? 'light' : 'dark')
    setThemePreference(theme)
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    const charts = wrapper.findAllComponents({ name: 'ChartPanel' })

    expect(charts).toHaveLength(2)
    const energy = charts[0]!.props('option') as {
      yAxis: { min: number }
      series: Array<{
        name: string
        data: Array<number | null>
        connectNulls: boolean
        lineStyle: { color: string }
        areaStyle: { color: { colorStops: Array<{ offset: number; color: string }> } }
        symbol: string
      }>
    }
    expect(energy.yAxis.min).toBe(0)
    expect(energy.series[0]).toMatchObject({
      name: 'Energie',
      data: [390, 430, null],
      connectNulls: false,
      lineStyle: { color: chartColors.value.violet },
    })
    expect(energy.series).toHaveLength(1)
    expect(energy.series[0]!.areaStyle).toMatchObject({ origin: 'start' })
    expect(energy.series[0]!.areaStyle.color.colorStops.map(({ offset }) => offset))
      .toEqual([0, 0.45, 0.75, 1])

    const macros = charts[1]!.props('option') as {
      yAxis: { min: number; name: string }
      series: Array<{
        name: string
        data: Array<number | null>
        connectNulls: boolean
        lineStyle: { color: string }
        areaStyle: { color: { colorStops: Array<{ offset: number; color: string }> } }
      }>
    }
    expect(macros.yAxis).toMatchObject({ min: 0, name: 'g' })
    expect(macros.series).toHaveLength(3)
    expect(macros.series.map(({ name, data, connectNulls, lineStyle }) => [
      name, data, connectNulls, lineStyle.color,
    ])).toEqual([
      ['Protein', [0, null, null], false, chartColors.value.teal],
      ['Kohlenhydrate', [18, 24, null], false, chartColors.value.blue],
      ['Fett', [12, 16, null], false, chartColors.value.amber],
    ])
    const energyAlpha = Number(energy.series[0]!.areaStyle.color.colorStops[0]!.color.match(/,\s*([\d.]+)\)$/)?.[1])
    const macroAlphas = macros.series.map(({ areaStyle }) => (
      Number(areaStyle.color.colorStops[0]!.color.match(/,\s*([\d.]+)\)$/)?.[1])
    ))
    expect(energyAlpha).toBeGreaterThan(0.1)
    expect(macroAlphas.every((alpha) => alpha > 0 && alpha <= energyAlpha / 4)).toBe(true)
    wrapper.unmount()
  })

  it('keeps unavailable chart groups empty while counting an explicit zero as nutrition data', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: nutritionResponse.days.map((day) => ({
        ...day, dietary_energy_kcal: null, protein_g: day.date === '2020-03-01' ? 0 : null,
        carbohydrates_g: null, fat_g: null,
      })),
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    expect(wrapper.findAll('.nutrition-overview-kpi-value').map((value) => value.text()))
      .toEqual(['–', '0 g', '–', '–'])
    expect(wrapper.findAllComponents({ name: 'ChartPanel' }).map((chart) => chart.props('empty')))
      .toEqual([true, false])
    expect(wrapper.find('.nutrition-empty').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the latest overview period when an older response arrives late', async () => {
    const pending: Array<(response: AnalysisNutritionResponse) => void> = []
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/nutrition/analysis')
      ? new Promise<AnalysisNutritionResponse>((resolve) => { pending.push(resolve) })
      : Promise.resolve({}))
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}&source=synthetic#nutrition`)
    await router.push('/analyse/ernaehrung?period=custom&start=2020-03-01&end=2020-03-02&source=synthetic#nutrition')
    await flushPromises()
    expect(pending).toHaveLength(2)
    pending[1]!({
      ...nutritionResponse,
      end_date: '2020-03-02', period_days: 2,
      days: [
        { ...nutritionResponse.days[0]!, dietary_energy_kcal: 20 },
        { ...nutritionResponse.days[1]!, dietary_energy_kcal: 60 },
      ],
    })
    await flushPromises()
    expect(wrapper.get('.nutrition-overview-kpi-value').text()).toBe('40 kcal')
    pending[0]!(structuredClone(nutritionResponse))
    await flushPromises()
    expect(wrapper.get('.nutrition-overview-kpi-value').text()).toBe('40 kcal')
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start: '2020-03-01', end: '2020-03-02', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    wrapper.unmount()
  })

  it('loads the response-owned All range and keeps All in its metric links', async () => {
    apiMock.mockImplementation((path: string) => {
      if (!path.startsWith('/analytics/nutrition/analysis')) return Promise.resolve({})
      const params = new URLSearchParams(path.split('?')[1])
      return Promise.resolve({
        ...structuredClone(nutritionResponse),
        start_date: params.get('start'), end_date: params.get('end'),
        period_capabilities: { supports_all_data: true, all_data_start_date: '2026-03-20' },
        days: [{ ...nutritionResponse.days[0]!, date: '2026-03-20' }],
      })
    })
    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung?period=all&source=synthetic#nutrition')
    expect(router.currentRoute.value.query).toEqual({ period: 'all', source: 'synthetic' })
    expect(nutritionRequests()).toEqual([
      '/analytics/nutrition/analysis?start=2026-03-02&end=2026-03-31',
      '/analytics/nutrition/analysis?start=2026-03-20&end=2026-03-31',
    ])
    const target = new URL(wrapper.get('a.nutrition-overview-kpi').attributes('href')!, 'http://calograph.test')
    expect(target.searchParams.get('period')).toBe('all')
    expect(target.searchParams.get('start')).toBeNull()
    expect(target.searchParams.get('end')).toBeNull()
    expect(target.searchParams.get('source')).toBe('synthetic')
    expect(target.hash).toBe('#nutrition')
    wrapper.unmount()
  })

  it('does not promote a delayed overview All probe after leaving the page', async () => {
    let resolveProbe!: (response: AnalysisNutritionResponse) => void
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/nutrition/analysis')
      ? new Promise<AnalysisNutritionResponse>((resolve) => { resolveProbe = resolve })
      : Promise.resolve({}))
    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung?period=all')
    await router.push('/trends?period=7#departed')
    await flushPromises()
    resolveProbe({
      ...nutritionResponse,
      end_date: '2026-03-31',
      period_capabilities: { supports_all_data: true, all_data_start_date: '2026-03-20' },
    })
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/trends?period=7#departed')
    expect(nutritionRequests()).toHaveLength(1)
    expect(wrapper.findComponent(AnalysisNutritionOverviewView).exists()).toBe(false)
    wrapper.unmount()
  })

  it('uses only each metric’s finite daily readings and leaves missing days and metrics empty', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [
        { ...nutritionResponse.days[0]!, dietary_energy_kcal: 0, protein_g: 30, carbohydrates_g: null, fat_g: Number.NaN },
        { ...nutritionResponse.days[2]!, dietary_energy_kcal: 600, protein_g: Number.POSITIVE_INFINITY, carbohydrates_g: null, fat_g: 10 },
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    expect(wrapper.findAll('.nutrition-overview-kpi-value').map((value) => value.text()))
      .toEqual(['300 kcal', '30 g', '–', '10 g'])
    const charts = wrapper.findAllComponents({ name: 'ChartPanel' })
    const energy = charts[0]!.props('option') as { xAxis: { data: string[] }; series: Array<{ data: Array<number | null> }> }
    expect(energy.xAxis.data).toEqual(['2020-03-01', '2020-03-02', '2020-03-03'])
    expect(energy.series[0]!.data).toEqual([0, null, 600])
    const macros = charts[1]!.props('option') as { series: Array<{ data: Array<number | null> }> }
    expect(macros.series.map(({ data }) => data)).toEqual([[30, null, null], [null, null, null], [null, null, 10]])
    wrapper.unmount()
  })

  it('localizes the overview heading, averages and chart labels in English', async () => {
    setLocale('en')
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung${rangeQuery}`)
    expect(wrapper.get('h1').text()).toBe('Nutrition')
    expect(wrapper.findAll('.nutrition-overview-kpi-label').map((label) => label.text()))
      .toEqual(['Energy', 'Protein', 'Carbohydrates', 'Fat'])
    const averageMarkers = wrapper.findAll('.nutrition-overview-kpi-caption')
    expect(averageMarkers.map((marker) => marker.text())).toEqual(['⌀', '⌀', '⌀', '⌀'])
    expect(averageMarkers.map((marker) => marker.attributes('title'))).toEqual(Array(4).fill('Daily average'))
    expect(averageMarkers.map((marker) => marker.attributes('aria-label'))).toEqual(Array(4).fill('Daily average'))
    expect(wrapper.findAllComponents({ name: 'ChartPanel' }).map((chart) => chart.props('title')))
      .toEqual(['Energy', 'Macronutrients'])
    wrapper.unmount()
  })
  it('keeps seven metric destinations, the general catalog and daily nutrition separate from the overview', async () => {
    const macroKeys = [
      'dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g',
      'fiber_g', 'sugar_g', 'saturated_fat_g',
    ]
    for (const key of macroKeys) expect(analysisHref(key)).toBe(`/analyse/ernaehrung/${key}`)
    for (const entry of registry) {
      if (!macroKeys.includes(entry.analysis_key)) {
        expect(analysisHref(entry.analysis_key)).not.toMatch(/^\/analyse\/ernaehrung\//)
      }
    }
    expect(analysisHref('unknown_nutrition_key')).toBe('/analyse/katalog')


    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung')
    expect(router.currentRoute.value.name).toBe('analysis-nutrition-overview')
    expect(wrapper.get('h1').text()).toContain('Ernährung')
    expect(wrapper.findComponent(AnalysisNutritionOverviewView).exists()).toBe(true)
    expect(wrapper.findComponent(AnalysisCatalogView).exists()).toBe(false)
    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.findComponent(AnalysisCatalogView).exists()).toBe(true)
    expect(wrapper.get('[data-analysis-catalog-key="protein_g"] a').attributes('href'))
      .toContain('/analyse/ernaehrung/protein_g')
    await router.push('/ernaehrung')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Ernährung')
    wrapper.unmount()
  })


  it('promotes a pending initial All probe after same-reader metric navigation without a redundant request', async () => {
    const allResponse: AnalysisNutritionResponse = {
      ...nutritionResponse,
      start_date: '2026-03-20', end_date: '2026-03-31', period_days: 12,
      period_capabilities: { supports_all_data: true, all_data_start_date: '2026-03-20' },
      days: [{ ...nutritionResponse.days[0]!, date: '2026-03-20' }],
    }
    serveNutrition(allResponse)
    const respond = apiMock.getMockImplementation()!
    const probe = Promise.withResolvers<AnalysisNutritionResponse>()
    let probeHeld = false
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (!path.startsWith('/analytics/nutrition/analysis') || probeHeld) return respond(path, options)
      probeHeld = true
      return probe.promise
    })

    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung/protein_g?period=all&source=synthetic#detail')
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#detail')
    expect(nutritionRequests()).toEqual(['/analytics/nutrition/analysis?start=2026-03-02&end=2026-03-31'])
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-quick-button[data-period="30"]')
      .attributes('aria-pressed')).toBe('true')

    await router.push({
      path: '/analyse/ernaehrung/carbohydrates_g',
      query: { ...router.currentRoute.value.query },
      hash: router.currentRoute.value.hash,
    })
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung/carbohydrates_g')
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#detail')
    expect(wrapper.get('h1').text()).toContain('Kohlenhydrate')
    expect(nutritionRequests()).toHaveLength(1)

    probe.resolve({ ...allResponse, start_date: '2026-03-02', period_days: 30 })
    await flushPromises()
    await flushPromises()

    expect.soft(router.currentRoute.value.path).toBe('/analyse/ernaehrung/carbohydrates_g')
    expect.soft(router.currentRoute.value.query).toEqual({ period: 'all', source: 'synthetic' })
    expect.soft(router.currentRoute.value.hash).toBe('#detail')
    expect(wrapper.get('h1').text()).toContain('Kohlenhydrate')
    expect(wrapper.get('.analysis-detail-title').text()).toContain('Kohlenhydrate')
    expect(wrapper.find('.nutrition-metric-list').exists()).toBe(false)
    expect.soft(nutritionRequests()).toHaveLength(2)
    expect.soft(nutritionRequests().map((request) => {
      const params = new URLSearchParams(request.split('?')[1])
      return [params.get('start'), params.get('end')]
    })).toEqual([
      ['2026-03-02', '2026-03-31'],
      ['2026-03-20', '2026-03-31'],
    ])
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect.soft(picker.get('.analysis-period-option[data-period="all"]').attributes('aria-pressed')).toBe('true')
    expect.soft(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['20.03.2026', '31.03.2026'])
    wrapper.unmount()
  })


  it.each(['', '?period=all'])('uses 30 days before the first nutrition reader for %s', async (query) => {
    serveNutrition()
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${query}`)
    expect(nutritionRequests()).toEqual(['/analytics/nutrition/analysis?start=2026-03-02&end=2026-03-31'])
    expect(router.currentRoute.value.query.period).toBe('30')
    const picker = wrapper.getComponent(PeriodPicker)
    expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['02.03.2026', '31.03.2026'])
    wrapper.unmount()
  })

  it.each([
    ['180', '2025-10-03', '03.10.2025'],
    ['365', '2025-04-01', '01.04.2025'],
  ])('renders exactly %s inclusive nutrition days for URL and picker Apply', async (period, start, displayStart) => {
    serveNutrition({
      ...nutritionResponse,
      start_date: start, end_date: '2026-03-31', period_days: Number(period),
      days: [{ ...nutritionResponse.days[0]!, date: start }],
    })
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g?period=${period}&source=synthetic#detail`)
    expect(router.currentRoute.value.query.period).toBe(period)
    expect(nutritionRequests()).toEqual([`/analytics/nutrition/analysis?start=${start}&end=2026-03-31`])
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { data: string[] }; series: Array<{ data: Array<number | null> }>
    }
    expect(option.xAxis.data).toHaveLength(Number(period))
    expect([option.xAxis.data[0], option.xAxis.data.at(-1)]).toEqual([start, '2026-03-31'])
    expect(option.series[0]!.data).toEqual([0, ...Array(Number(period) - 1).fill(null)])
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    expect(picker.get(`.analysis-period-option[data-period="${period}"]`).attributes('aria-pressed')).toBe('true')
    expect(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
      .toEqual([displayStart, '31.03.2026'])
    await picker.get('.analysis-period-option[data-period="7"]').trigger('click')
    await flushPromises()
    await picker.get('.analysis-period-custom-button').trigger('click')
    await picker.get(`.analysis-period-option[data-period="${period}"]`).trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ period, source: 'synthetic' })
    expect(router.currentRoute.value.query.start).toBeUndefined()
    expect(router.currentRoute.value.query.end).toBeUndefined()
    expect(router.currentRoute.value.hash).toBe('#detail')
    expect(nutritionRequests().at(-1)).toBe(`/analytics/nutrition/analysis?start=${start}&end=2026-03-31`)
    const applied = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as typeof option
    expect(applied.xAxis.data).toEqual(option.xAxis.data)
    wrapper.unmount()
  })
  it('accepts a 365-day custom range but rejects 366 inclusive days before requesting nutrition', async () => {
    serveNutrition()
    const { wrapper, router } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.03.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.find('.analysis-period-error[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start: '2020-03-01', end: '2020-03-03' })
    expect(nutritionRequests()).toHaveLength(1)

    await dates[1]!.setValue('28.02.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    expect(nutritionRequests()).toHaveLength(2)
    const params = new URLSearchParams(nutritionRequests()[1]!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2020-03-01', '2021-02-28'])
    expect(router.currentRoute.value.query).toMatchObject({ period: 'custom', start: '2020-03-01', end: '2021-02-28' })
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('visually hides semantic Nutrition Metric list headers while preserving zero readings and missing calendar days', async () => {
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    await expandReadings(wrapper, '.nutrition-reading-list', 1)
    const rows = wrapper.findAll('.nutrition-reading-list tbody tr')
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      series: Array<{ data: Array<number | null> }>
    }

    expect(wrapper.get('.nutrition-reading-list h2').text()).toBe('Messwerte (1)')
    expectScreenReaderTableHeaders(wrapper, '.nutrition-reading-list', ['Datum', 'Wert'])
    expect(rows).toHaveLength(1)
    expect(rows[0]!.findAll('td').map((cell) => cell.text())).toEqual(['01.03.2020', '0 g'])
    expect(rows[0]!.get('time').attributes('datetime')).toBe('2020-03-01')
    expect(wrapper.findAll('.nutrition-statistics strong').map((value) => value.text()))
      .toEqual(['0 g', '0 g'])
    expect(option.series[0]!.data).toEqual([0, null, null])
    expect(wrapper.find('.nutrition-reading-list time[datetime="2020-03-02"]').exists()).toBe(false)
    expect(nutritionRequests()).toHaveLength(1)
    wrapper.unmount()
  })


  it('renders one shared, metric-colored detail template for all seven Nutrition metrics', async () => {
    const colors: Record<string, string> = {
      '--chart-series-violet': '#8b5cf6',
      '--chart-series-teal': '#2dd4bf',
      '--chart-series-blue': '#5b8ff9',
      '--chart-series-amber': '#f6c445',
      '--chart-series-lilac': '#d19bff',
    }
    const nativeGetComputedStyle = window.getComputedStyle.bind(window)
    vi.stubGlobal('getComputedStyle', (element: Element) => {
      const computedStyle = nativeGetComputedStyle(element)
      return {
        display: computedStyle.display,
        visibility: computedStyle.visibility,
        opacity: computedStyle.opacity,
        getPropertyValue: (name: string) => colors[name] ?? computedStyle.getPropertyValue(name),
      } as CSSStyleDeclaration
    })
    setThemePreference('light')
    setThemePreference('dark')
    serveNutrition({
      ...nutritionResponse,
      days: [
        { ...nutritionResponse.days[0]!, dietary_energy_kcal: 390, protein_g: 80.2 },
        { ...nutritionResponse.days[1]!, dietary_energy_kcal: 430, protein_g: 80.6 },
        nutritionResponse.days[2]!,
      ],
    })
    const { router, wrapper } = await mountAnalysis(
      `/analyse/ernaehrung/dietary_energy_kcal${rangeQuery}&source=synthetic#detail`,
    )
    const selections = [
      { key: 'dietary_energy_kcal', label: 'Energie', unit: 'kcal', color: chartColors.value.violet, values: [390, 430, null], average: '410 kcal', minimum: '390 kcal', maximum: '430 kcal' },
      { key: 'protein_g', label: 'Protein', unit: 'g', color: chartColors.value.teal, values: [80.2, 80.6, null], average: '80,4 g', minimum: '80,2 g', maximum: '80,6 g' },
      { key: 'carbohydrates_g', label: 'Kohlenhydrate', unit: 'g', color: chartColors.value.blue, values: [18, 24, null], average: '21 g', minimum: '18 g', maximum: '24 g' },
      { key: 'fat_g', label: 'Fett', unit: 'g', color: chartColors.value.amber, values: [12, 16, null], average: '14 g', minimum: '12 g', maximum: '16 g' },
      { key: 'fiber_g', label: 'Ballaststoffe', unit: 'g', color: chartColors.value.lilac, values: [4, 5, null], average: '4,5 g', minimum: '4 g', maximum: '5 g' },
      { key: 'sugar_g', label: 'Zucker', unit: 'g', color: chartColors.value.lilac, values: [6, 8, null], average: '7 g', minimum: '6 g', maximum: '8 g' },
      { key: 'saturated_fat_g', label: 'Gesättigte Fettsäuren', unit: 'g', color: chartColors.value.lilac, values: [2, 3, null], average: '2,5 g', minimum: '2 g', maximum: '3 g' },
    ]

    for (const selection of selections) {
      if (router.currentRoute.value.params.metric !== selection.key) {
        await router.push({
          path: `/analyse/ernaehrung/${selection.key}`,
          query: router.currentRoute.value.query,
          hash: router.currentRoute.value.hash,
        })
        await flushPromises()
      }
      expect(wrapper.find('.nutrition-metric-list').exists()).toBe(false)
      expect(wrapper.get('.analysis-detail-title').text()).toContain(selection.label)
      expect(wrapper.getComponent(AnalysisIcon).props('icon'))
        .toBe(localizedAnalysisPresentation(selection.key)?.icon)
      expect(wrapper.get('.analysis-detail-page').attributes('style'))
        .toContain(`--nutrition-metric-color: ${selection.color}`)
      expect(wrapper.get('.analysis-breadcrumbs').text()).toContain(selection.label)
      expect(wrapper.get('.page-heading p').text()).toContain('im gewählten Zeitraum')
      expect(wrapper.get('.nutrition-summary h2').text()).toBe('Durchschnitt')
      expect(wrapper.get('.nutrition-summary strong').text()).toBe(selection.average)
      expect(wrapper.findAll('.nutrition-statistics h2').map((heading) => heading.text()))
        .toEqual(['Minimum', 'Maximum'])
      expect(wrapper.findAll('.nutrition-statistics strong').map((value) => value.text()))
        .toEqual([selection.minimum, selection.maximum])
      await expandReadings(wrapper, '.nutrition-reading-list', 2)
      expectScreenReaderTableHeaders(wrapper, '.nutrition-reading-list', ['Datum', 'Wert'])
      expect(wrapper.findAll('.nutrition-reading-list tbody tr')).toHaveLength(2)
      const valueFormatter = new Intl.NumberFormat('de-DE', {
        maximumFractionDigits: selection.unit === 'kcal' ? 0 : 1,
      })
      expect(wrapper.findAll('.nutrition-reading-list tbody tr')
        .map((row) => row.findAll('td').map((cell) => cell.text())))
        .toEqual(selection.values.slice(0, 2).map((value, index) => [
          formatGermanDate(`2020-03-0${index + 1}`),
          `${valueFormatter.format(value as number)} ${selection.unit}`,
        ]))
      expect(wrapper.text()).not.toMatch(/yazio|provider|Haferflocken|Benannte Mahlzeiten/i)

      const chart = wrapper.getComponent({ name: 'ChartPanel' })
      expect(chart.props('title')).toBe(selection.label)
      expect(chart.props('empty')).toBe(false)
      const option = chart.props('option') as {
        xAxis: { data: string[]; boundaryGap: boolean }
        yAxis: { min: number; name: string }
        series: Array<{
          type: string
          data: Array<number | null>
          connectNulls: boolean
          showSymbol: boolean
          lineStyle: { color: string }
          itemStyle: { color: string; borderColor: string }
          areaStyle: { origin: string; color: { colorStops: Array<{ offset: number }> } }
        }>
      }
      expect(option.xAxis).toMatchObject({
        data: ['2020-03-01', '2020-03-02', '2020-03-03'],
        boundaryGap: false,
      })
      expect(option.yAxis).toMatchObject({ min: 0, name: selection.unit })
      expect(option.series).toHaveLength(1)
      expect(option.series[0]).toMatchObject({
        type: 'line',
        data: selection.values,
        connectNulls: false,
        showSymbol: true,
        lineStyle: { color: selection.color },
        itemStyle: { color: selection.color },
        areaStyle: { origin: 'start' },
      })
      expect(option.series[0]!.areaStyle.color.colorStops.map(({ offset }) => offset))
        .toEqual([0, 0.45, 0.75, 1])
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2020-03-01', end: '2020-03-03', source: 'synthetic',
      })
    }

    expect(nutritionRequests()).toHaveLength(1)
    const categoryLink = wrapper.get('.analysis-breadcrumbs a[href*="/analyse/ernaehrung"]')
    await categoryLink.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung')
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2020-03-01', end: '2020-03-03', source: 'synthetic',
    })
    expect(router.currentRoute.value.hash).toBe('#detail')
    expect(nutritionRequests()).toHaveLength(2)
    wrapper.unmount()
  })
  it('rounds displayed Energy kcal and keeps only the raw Energy line in the detail', async () => {
    serveNutrition({
      ...nutritionResponse,
      days: [
        {
          ...nutritionResponse.days[0]!, dietary_energy_kcal: 1626.5,
          meals: [{
            ...nutritionResponse.days[1]!.meals[0]!, dietary_energy_kcal: 288.5,
          }],
        },
        { ...nutritionResponse.days[1]!, date: '2020-03-02', dietary_energy_kcal: 1627.4 },
        { ...nutritionResponse.days[2]!, date: '2020-03-03' },
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/dietary_energy_kcal${rangeQuery}`)
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      yAxis: { axisLabel: { formatter?: (value: number) => string } }
      series: Array<{
        type: string
        data: Array<number | null>
        connectNulls: boolean
        lineStyle: { color: string }
      }>
    }
    expect(wrapper.get('.nutrition-summary strong').text()).toBe('1.627 kcal')
    expect(wrapper.get('.nutrition-statistics strong').text()).toBe('1.627 kcal')
    await expandReadings(wrapper, '.nutrition-reading-list', 2)
    expect(wrapper.get('.nutrition-reading-list tbody tr').text()).toContain('1.627 kcal')
    expect(wrapper.text()).not.toMatch(/Haferflocken|Benannte Mahlzeiten|yazio|provider/i)
    expect(option.series).toHaveLength(1)
    expect(option.series[0]).toMatchObject({
      type: 'line', data: [1626.5, 1627.4, null], connectNulls: false,
      lineStyle: { color: chartColors.value.violet },
    })
    expect(option.yAxis.axisLabel.formatter?.(1626.5)).toBe('1.627')
    wrapper.unmount()
  })

  it('does not turn unnamed snapshots without recorded nutrients into data or phantom meals', async () => {
    const emptyDay = nutritionResponse.days[2]!
    serveNutrition({
      ...nutritionResponse,
      days: [
        {
          ...emptyDay, date: '2020-03-01', provider_key: 'yazio',
          meals: [null, '', '   '].map((display_name) => ({
            ...nutritionResponse.days[1]!.meals[0]!,
            display_name,
            dietary_energy_kcal: null, protein_g: null, carbohydrates_g: null,
            fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
          })),
        },
        { ...emptyDay, date: '2020-03-02' },
        emptyDay,
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    expect(wrapper.find('[role="status"]').exists()).toBe(true)
    expect(wrapper.get('[role="status"]').text()).toMatch(/keine Werte|keine Daten|keine Tageswerte/i)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
    expect(wrapper.findAll('.nutrition-meals li')).toHaveLength(0)
    expect(wrapper.text()).not.toMatch(/Haferflocken|Frühstück|0 g/)
    wrapper.unmount()
  })

  it('rejects an unknown nutrition metric without reading nutrition data', async () => {
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung/not_a_metric${rangeQuery}`)
    expect(nutritionRequests()).toHaveLength(0)
    expect(
      router.currentRoute.value.path === '/analyse/ernaehrung'
      || /nicht verfügbar|nicht gefunden|unbekannt/i.test(wrapper.get('[role="alert"]').text()),
    ).toBe(true)
    expect(wrapper.get('[role="alert"] a').attributes('href')).toBe(`/analyse/katalog${rangeQuery}`)
    wrapper.unmount()
  })

  it.each(['/analyse/ernaehrung', '/analyse/ernaehrung/protein_g'])('keeps loading, localized failure and same-period retry separate from successful nutrition on %s', async (path) => {
    let rejectFirst!: (reason: Error) => void
    const firstRequest = new Promise<never>((_resolve, reject) => { rejectFirst = reject })
    vi.mocked(localizeApiError).mockReturnValueOnce('Die Analyse konnte nicht geladen werden.')
    let reads = 0
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/analysis')) {
        reads += 1
        return reads === 1 ? firstRequest : Promise.resolve(structuredClone(nutritionResponse))
      }
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve(analysisPreferencesResponse())
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis(`${path}${rangeQuery}`)
    expect(wrapper.get('[role="status"]').text()).toMatch(/laden|lädt/i)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
    rejectFirst(new Error('nutrition unavailable'))
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Die Analyse konnte nicht geladen werden.')
    expect(wrapper.get('[role="alert"]').text()).toContain('Erneut laden')
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAllComponents({ name: 'ChartPanel' })).toHaveLength(path === '/analyse/ernaehrung' ? 2 : 1)
    expect(nutritionRequests()).toHaveLength(2)
    for (const request of nutritionRequests()) {
      const params = new URLSearchParams(request.split('?')[1])
      expect([params.get('start'), params.get('end')]).toEqual(['2020-03-01', '2020-03-03'])
    }
    wrapper.unmount()
  })

  it.each(['/analyse/ernaehrung', '/analyse/ernaehrung/protein_g'])('shows an empty API nutrition period without invented values on %s', async (path) => {
    const emptyNutritionResponse = {
      ...nutritionResponse,
      days: nutritionResponse.days.map((day) => ({
        ...day,
        provider_key: null,
        dietary_energy_kcal: null,
        protein_g: null,
        carbohydrates_g: null,
        fat_g: null,
        fiber_g: null,
        sugar_g: null,
        saturated_fat_g: null,
        meals: [],
      })),
    }
    serveNutrition(emptyNutritionResponse)
    const { wrapper } = await mountAnalysis(`${path}${rangeQuery}`)
    expect(wrapper.get('[role="status"]').text()).toMatch(/keine Werte|keine Daten|keine Tageswerte/i)
    expect(wrapper.findAll('.nutrition-overview-kpi-value')).toHaveLength(0)
    expect(wrapper.findAll('.nutrition-summary')).toHaveLength(0)
    expect(wrapper.findAll('.chart-card [role="img"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-meal-entry]')).toHaveLength(0)
    if (path === '/analyse/ernaehrung') {
      expect(wrapper.get('.nutrition-meals-empty').text()).toContain('Keine benannten Mahlzeiten verfügbar')
    }
    wrapper.unmount()
  })
})

describe('Analysis account and timezone invalidation', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  })
  afterEach(() => { vi.useRealTimers() })

  const accountRaceCases = [
    { path: '/analyse/koerper/gewicht', reader: '/analytics/weight', valueA: 71.1, valueB: 82.2, stale: 99.9, textA: '71,1 kg', textB: '82,2 kg', staleText: '99,9 kg' },
    { path: '/analyse/aktivitaet/aktive-energie', reader: '/analytics/verification/activity', valueA: 111, valueB: 222, stale: 999, textA: '111 kcal', textB: '222 kcal', staleText: '999 kcal' },
    { path: '/analyse/aktivitaet', reader: '/analytics/verification/activity', valueA: 111, valueB: 222, stale: 999, textA: '111 kcal', textB: '222 kcal', staleText: '999 kcal' },
    { path: '/analyse/ernaehrung/protein_g', reader: '/analytics/nutrition/analysis', valueA: 11.1, valueB: 22.2, stale: 99.9, textA: '11,1 g', textB: '22,2 g', staleText: '99,9 g' },
  ] as const

  function responseFor(reader: string, request: string, value: number) {
    const params = new URL(request, 'https://calograph.test').searchParams
    const start = params.get('start')!
    const end = params.get('end')!
    if (reader === '/analytics/weight') {
      return {
        start_date: start,
        end_date: end,
        selected_provider: null,
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        points: [{ date: start, weight_kg: value }],
        summary: {
          measurement_count: 1,
          latest: { date: start, weight_kg: value },
          change_kg: null,
          average_kg: value,
          minimum_kg: value,
          maximum_kg: value,
        },
      } satisfies WeightResponse
    }
    if (reader === '/analytics/nutrition/analysis') {
      return {
        start_date: start,
        end_date: end,
        selected_provider: { provider_key: 'yazio' },
        period_days: inclusiveDays(request),
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        days: [{
          date: start, provider_key: 'yazio', dietary_energy_kcal: value, protein_g: value,
          carbohydrates_g: value, fat_g: value, fiber_g: value, sugar_g: value,
          saturated_fat_g: value, meals: [],
        }],
      } satisfies AnalysisNutritionResponse
    }
    return {
      start_date: start,
      end_date: end,
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      view: 'canonical',
      days: [{
        date: start,
        canonical: {
          provider_key: 'yazio', status: 'available', active_energy_kcal: value,
          record_count: 1, source_types: ['yazio_export_v1'],
        },
        providers: [],
      }],
    } satisfies VerificationActivityResponse
  }

  it.each(accountRaceCases)(
    'clears account data and rejects a late prior-account response on $path',
    async ({ path, reader, valueA, valueB, stale, textA, textB, staleText }) => {
      const firstBResponse = Promise.withResolvers<void>()
      const staleAResponse = Promise.withResolvers<void>()
      const requests: Array<{ userId: string | undefined; path: string }> = []
      let deferFirstB = true
      let deferStaleA = false
      const fallback = apiMock.getMockImplementation()!
      apiMock.mockImplementation((request: string, options?: RequestInit) => {
        if (!request.startsWith(`${reader}?`)) return fallback(request, options)
        const userId = useAuthStore().user?.id
        requests.push({ userId, path: request })
        const start = new URL(request, 'https://calograph.test').searchParams.get('start')
        if (userId === 'analysis-user-b' && deferFirstB) {
          deferFirstB = false
          return firstBResponse.promise.then(() => responseFor(reader, request, valueB))
        }
        if (userId === 'analysis-user' && deferStaleA && start === '2026-03-02') {
          return staleAResponse.promise.then(() => responseFor(reader, request, stale))
        }
        return Promise.resolve(responseFor(reader, request, userId === 'analysis-user-b' ? valueB : valueA))
      })

      const { router, wrapper } = await mountAnalysis(`${path}?period=7`)
      expect(wrapper.text()).toContain(textA)

      const auth = useAuthStore()
      auth.user = { ...auth.user!, id: 'analysis-user-b' }
      await flushPromises()
      expect(wrapper.text()).not.toContain(textA)
      expect(requests.some(({ userId }) => userId === 'analysis-user-b')).toBe(true)
      firstBResponse.resolve()
      await flushPromises()
      expect(wrapper.text()).toContain(textB)

      auth.user = { ...auth.user!, id: 'analysis-user' }
      await flushPromises()
      expect(wrapper.text()).toContain(textA)
      deferStaleA = true
      await router.push(`${path}?period=30`)
      await flushPromises()
      expect(requests.some(({ userId, path: request }) => (
        userId === 'analysis-user'
        && new URL(request, 'https://calograph.test').searchParams.get('start') === '2026-03-02'
      ))).toBe(true)

      auth.user = { ...auth.user!, id: 'analysis-user-b' }
      await flushPromises()
      expect(wrapper.text()).toContain(textB)
      staleAResponse.resolve()
      await flushPromises()
      expect(wrapper.text()).toContain(textB)
      expect(wrapper.text()).not.toContain(staleText)
    },
  )

  const rollingCases = [
    { path: '/analyse/koerper/gewicht', reader: '/analytics/weight' },
    { path: '/analyse/aktivitaet/aktive-energie', reader: '/analytics/verification/activity' },
    { path: '/analyse/aktivitaet', reader: '/analytics/verification/activity' },
    { path: '/analyse/ernaehrung/protein_g', reader: '/analytics/nutrition/analysis' },
    { path: '/analyse/katalog', reader: '/analytics/nutrition/micronutrients' },
  ] as const

  it.each(rollingCases)(
    'recomputes rolling bounds after a timezone change on $path',
    async ({ path, reader }) => {
      vi.setSystemTime(new Date('2026-03-31T00:30:00Z'))
      const { router } = await mountAnalysis(`${path}?period=7`)
      const readerRequests = () => apiMock.mock.calls
        .map(([request]) => String(request))
        .filter((request) => request.startsWith(`${reader}?`))
      const before = readerRequests()
      expect(before.length).toBeGreaterThan(0)
      const bounds = (request: string) => {
        const params = new URL(request, 'https://calograph.test').searchParams
        return [params.get('start'), params.get('end')]
      }
      expect(bounds(before.at(-1)!)).toEqual(['2026-03-25', '2026-03-31'])

      const auth = useAuthStore()
      auth.user = { ...auth.user!, timezone: 'America/Los_Angeles' }
      await flushPromises()
      const after = readerRequests().slice(before.length)
      expect(after).toHaveLength(1)
      expect(bounds(after[0]!)).toEqual(['2026-03-24', '2026-03-30'])
      expect(router.currentRoute.value.query.period).toBe('7')
    },
  )

  it('invalidates Catalog registry and preference state on account changes in non-micronutrient categories', async () => {
    const pendingBRegistry = Promise.withResolvers<{ analyses: typeof registry }>()
    const staleAPreferences = Promise.withResolvers<{ active_card_ids: string[] }>()
    let accountAPreferenceReads = 0
    let accountBRegistryReads = 0
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((request: string, options?: RequestInit) => {
      const userId = useAuthStore().user?.id
      if (request === '/analytics/registry') {
        if (userId === 'analysis-user-b' && accountBRegistryReads++ === 0) return pendingBRegistry.promise
        return Promise.resolve({ analyses: registry })
      }
      if (request === '/settings/analysis-preferences' && options?.method !== 'PUT') {
        if (userId === 'analysis-user' && accountAPreferenceReads++ > 0) return staleAPreferences.promise
        return Promise.resolve({ active_card_ids: userId === 'analysis-user-b' ? ['activity'] : ['weight'] })
      }
      return fallback(request, options)
    })

    const { wrapper } = await mountAnalysis('/analyse/koerper?period=7', ['weight'])
    const weightRow = '[data-analysis-key="weight_kg"]'
    expect(wrapper.findAll(`${weightRow} [data-analysis-selected="weight"]`)).toHaveLength(1)

    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: 'analysis-user-b' }
    await flushPromises()
    expect(wrapper.findAll(weightRow)).toHaveLength(0)
    pendingBRegistry.resolve({ analyses: registry })
    await flushPromises()
    expect(wrapper.findAll(`${weightRow} [data-analysis-add="weight_kg"]`)).toHaveLength(1)

    auth.user = { ...auth.user!, id: 'analysis-user' }
    await flushPromises()
    auth.user = { ...auth.user!, id: 'analysis-user-b' }
    await flushPromises()
    staleAPreferences.resolve({ active_card_ids: ['weight'] })
    await flushPromises()
    expect(wrapper.findAll(`${weightRow} [data-analysis-add="weight_kg"]`)).toHaveLength(1)
  })

  it.each(['unknown_metric', 'toString', 'constructor', '__proto__'])(
    'keeps unknown micronutrient route %s routable and in its not-found state',
    async (metricKey) => {
      const path = `/analyse/mikronaehrstoffe/${metricKey}?period=30`
      const { router, wrapper } = await mountAnalysis(path)
      expect(router.currentRoute.value.path).toBe(`/analyse/mikronaehrstoffe/${metricKey}`)
      expect(wrapper.get('[role="alert"]').text()).toContain('Dieser Mikronährstoff ist nicht verfügbar.')
      expect(wrapper.findComponent(PeriodPicker).exists()).toBe(true)
    },
  )
})

describe('Analysis Hub and Catalog response-owned period contracts', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const yes = { supports_all_data: true, all_data_start_date: '2026-03-01' }
  const no = { supports_all_data: false, all_data_start_date: null }
  const readers = {
    weight: '/analytics/weight',
    activity: '/analytics/verification/activity',
    nutrition: '/analytics/nutrition/analysis',
    micronutrients: '/analytics/nutrition/micronutrients',
  } as const
  type Reader = keyof typeof readers
  const supportedHubAll = {
    weight: yes, activity: yes, nutrition: yes, micronutrients: yes,
  }

  function serveCapabilities(capabilities: Partial<Record<Reader | 'vitamin_a_ug' | 'calcium_mg', ReaderPeriodCapabilities | undefined>>) {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const response = await fallback(path, options)
      const reader = (Object.keys(readers) as Reader[]).find((key) => path.startsWith(`${readers[key]}?`))
      if (!reader) return response
      const metric = new URLSearchParams(path.split('?')[1]).get('metric_key')
      const key = metric === 'vitamin_a_ug' || metric === 'calcium_mg' ? metric : reader
      return { ...response, period_capabilities: capabilities[key] }
    })
  }

  function readerRanges(reader: Reader) {
    return apiMock.mock.calls.flatMap(([path]) => {
      if (!String(path).startsWith(`${readers[reader]}?`)) return []
      const params = new URLSearchParams(String(path).split('?')[1])
      return [[params.get('start'), params.get('end')]]
    })
  }
  it('preserves Catalog All and its URL after a summary failure without a 30-day retry', async () => {
    serveCapabilities({ micronutrients: yes })
    const fallback = apiMock.getMockImplementation()!
    const summaryRequests: string[] = []
    apiMock.mockImplementation((request: string, options?: RequestInit) => {
      if (request.startsWith(`${readers.micronutrients}?`)) {
        const params = new URL(request, 'https://calograph.test').searchParams
        if (!params.has('metric_key')) {
          summaryRequests.push(request)
          if (params.get('start') === '2026-03-01') {
            return Promise.reject(new Error('summary unavailable'))
          }
        }
      }
      return fallback(request, options)
    })

    const { router, wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe?period=30&source=synthetic#catalog',
    )
    const picker = wrapper.getComponent(PeriodPicker)
    await picker.get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query).toMatchObject({ period: 'all', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#catalog')
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(summaryRequests).toHaveLength(2)
    expect(readerRanges('micronutrients')).toEqual([
      ['2026-03-02', '2026-03-31'],
      ['2026-03-01', '2026-03-31'],
    ])
  })

  it('keeps a committed All range through a fixed reader error and retries identical bounds', async () => {
    serveCapabilities({ ...supportedHubAll })
    const fallback = apiMock.getMockImplementation()!
    let allNutritionAttempts = 0
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      const params = new URLSearchParams(path.split('?')[1] ?? '')
      if (path.startsWith(`${readers.nutrition}?`) && params.get('start') === '2026-03-01') {
        allNutritionAttempts += 1
        if (allNutritionAttempts === 1) return Promise.reject(new Error('nutrition offline'))
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.period).toBe('all')
    expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('all')
    const failedRequest = apiMock.mock.calls.map(([path]) => String(path))
      .find((path) => path.startsWith(`${readers.nutrition}?`)
        && new URLSearchParams(path.split('?')[1]).get('start') === '2026-03-01')!
    const failedBounds = new URLSearchParams(failedRequest.split('?')[1])
    const error = wrapper.get('.analysis-card-list > li[data-card-key="nutrition"] [role="alert"]')
    await error.get('button').trigger('click')
    await flushPromises()

    const retriedRequest = apiMock.mock.calls.map(([path]) => String(path))
      .filter((path) => path.startsWith(`${readers.nutrition}?`)
        && new URLSearchParams(path.split('?')[1]).get('start') === '2026-03-01').at(-1)!
    const retriedBounds = new URLSearchParams(retriedRequest.split('?')[1])
    expect(allNutritionAttempts).toBe(2)
    expect(retriedBounds.get('start')).toBe(failedBounds.get('start'))
    expect(retriedBounds.get('end')).toBe(failedBounds.get('end'))
    expect(router.currentRoute.value.query.period).toBe('all')
    wrapper.unmount()
  })


  it('uses the latest URL period when the workspace resolves after navigation', async () => {
    let resolveRegistry!: (response: { analyses: typeof registry }) => void
    const registryResponse = new Promise<{ analyses: typeof registry }>((resolve) => {
      resolveRegistry = resolve
    })
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => (
      path === '/analytics/registry' ? registryResponse : fallback(path, options)
    ))

    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=7', ['weight', 'nutrition', 'micronutrients', 'activity'],
    )
    await router.push('/analyse?period=90&source=synthetic#analysis-category-fluid')
    await flushPromises()
    resolveRegistry({ analyses: registry })
    await flushPromises()

    expect(router.currentRoute.value.query).toMatchObject({
      period: '90', source: 'synthetic',
    })
    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    expect(readerRanges('weight')[0]).toEqual(['2026-01-01', '2026-03-31'])
    wrapper.unmount()
  })


  it.each([
    { label: 'absent capability', capability: undefined },
    { label: 'explicit negative capability', capability: no },
    { label: 'missing boundary', capability: { supports_all_data: true, all_data_start_date: null } },
    { label: 'impossible boundary', capability: { supports_all_data: true, all_data_start_date: '2026-02-30' } },
    { label: 'future boundary', capability: { supports_all_data: true, all_data_start_date: '2026-04-01' } },
  ])('rejects initial and later Hub All when a selected reader has $label', async ({ capability }) => {
    serveCapabilities({ ...supportedHubAll, activity: capability })
    const { router, wrapper } = await mountAnalysis('/analyse?period=all&source=synthetic#analysis-category-fluid', ['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(router.currentRoute.value.query).toMatchObject({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    expect(readerRanges('weight')[0]).toEqual(['2026-03-02', '2026-03-31'])
    expect(readerRanges('activity')[0]).toEqual(['2026-03-02', '2026-03-31'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    await router.push('/analyse?period=all&source=synthetic#analysis-category-fluid')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(readerRanges('activity').every(([start, end]) => start === '2026-03-02' && end === '2026-03-31')).toBe(true)
  })

  it.each(['pending', 'error'] as const)('rejects Hub All while a selected reader is %s', async (state) => {
    serveCapabilities({ ...supportedHubAll })
    const respond = apiMock.getMockImplementation()!
    const pending = Promise.withResolvers<void>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (!path.startsWith(`${readers.activity}?`)) return respond(path, options)
      return state === 'pending'
        ? pending.promise.then(() => respond(path, options))
        : Promise.reject(new Error('synthetic activity reader failure'))
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'micronutrients', 'activity'])
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    if (state === 'pending') {
      pending.resolve()
      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('30')
    }
  })

  it.each(['unknown reader', 'missing static contract'] as const)('does not skip an available selected entry with a %s', async (reason) => {
    serveCapabilities({ ...supportedHubAll })
    const respond = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: registry.map((entry) => entry.analysis_key === 'protein_g'
        ? { ...entry, ...(reason === 'unknown reader'
          ? { reader_key: 'analytics.synthetic.missing' }
          : { period_capability: null }) }
        : entry) })
      : respond(path, options))
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
  })

  it.each([
    { label: 'missing selected metric capability', metric: undefined, summary: yes },
    { label: 'negative selected metric capability', metric: no, summary: yes },
    { label: 'invalid selected metric boundary', metric: { ...yes, all_data_start_date: '2026-02-30' }, summary: yes },
    { label: 'missing summary capability', metric: yes, summary: undefined },
  ])('requires both micronutrient summary and selected-card response: $label', async ({ metric, summary }) => {
    serveCapabilities({ weight: yes, activity: yes, nutrition: yes, micronutrients: summary, vitamin_a_ug: yes, calcium_mg: metric })
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'calcium_mg'])
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
  })

  it.each(['pending', 'error'] as const)('does not let a ready micronutrient summary substitute for a %s selected metric', async (state) => {
    serveCapabilities({ weight: yes, activity: yes, nutrition: yes, micronutrients: yes, vitamin_a_ug: yes, calcium_mg: yes })
    const respond = apiMock.getMockImplementation()!
    const metric = Promise.withResolvers<void>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.includes('metric_key=calcium_mg')
      ? state === 'pending'
        ? metric.promise.then(() => respond(path, options))
        : Promise.reject(new Error('synthetic calcium failure'))
      : respond(path, options))
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug', 'calcium_mg'])
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    if (state === 'pending') {
      metric.resolve()
      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('30')
    }
  })

  it('revokes Hub All while a newly selected micronutrient series is pending and does not restore it late', async () => {
    const march25 = { ...yes, all_data_start_date: '2026-03-25' }
    serveCapabilities({
      weight: march25, activity: march25, nutrition: march25, micronutrients: march25,
      vitamin_a_ug: yes,
    })
    const respond = apiMock.getMockImplementation()!
    const metric = Promise.withResolvers<void>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.includes('metric_key=vitamin_a_ug')
      ? metric.promise.then(() => respond(path, options))
      : respond(path, options))
    const { router, wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    for (const reader of Object.keys(readers) as Reader[]) {
      expect(readerRanges(reader).at(-1)).toEqual(['2026-03-25', '2026-03-31'])
    }
    await clickManagerAction(wrapper, 'vitamin_a_ug')
    await flushPromises()
    await closeCardManager(wrapper)
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    metric.resolve()
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('30')
  })

  it('applies the earliest response-owned union to every fixed reader at the exact 31-day boundary', async () => {
    serveCapabilities({
      weight: yes, activity: { ...yes, all_data_start_date: '2026-03-10' },
      nutrition: { ...yes, all_data_start_date: '2026-03-01' },
      micronutrients: { ...yes, all_data_start_date: '2026-03-05' },
      vitamin_a_ug: { ...yes, all_data_start_date: '2026-03-07' },
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity', 'vitamin_a_ug'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    for (const reader of ['weight', 'activity', 'nutrition', 'micronutrients'] as const) {
      expect(readerRanges(reader).at(-1)).toEqual(['2026-03-01', '2026-03-31'])
    }
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.findAll('.analysis-period-custom-form input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['01.03.2026', '31.03.2026'])
  })

  it('revokes Hub All when a selected micronutrient series rejects it', async () => {
    serveCapabilities({
      ...supportedHubAll,
      weight: { ...yes, all_data_start_date: '2026-02-28' },
      vitamin_a_ug: no,
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=30', ['weight', 'nutrition', 'micronutrients', 'activity'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    expect(readerRanges('weight').at(-1)).toEqual(['2026-02-28', '2026-03-31'])
    await clickManagerAction(wrapper, 'vitamin_a_ug')
    await flushPromises()
    await closeCardManager(wrapper)
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(readerRanges('micronutrients').at(-1)).toEqual(['2026-03-02', '2026-03-31'])
  })

  it.each([
    { label: 'empty', keys: [] as string[] },
    { label: 'coming-soon only', keys: ['steps', 'water_intake_ml'] },
  ])('uses fixed Summary capabilities without $label Metrics', async ({ keys }) => {
    serveCapabilities({ ...supportedHubAll })
    const { router, wrapper } = await mountAnalysis('/analyse?period=30', keys)
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    for (const reader of Object.keys(readers) as Reader[]) {
      expect(readerRanges(reader).at(-1)).toEqual(['2026-03-01', '2026-03-31'])
    }
  })

  it('ignores coming-soon entries when fixed Summary readers support All', async () => {
    serveCapabilities({ ...supportedHubAll })
    const { wrapper, router } = await mountAnalysis('/analyse?period=30', ['weight_kg', 'steps'])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    for (const reader of Object.keys(readers) as Reader[]) {
      expect(readerRanges(reader).at(-1)).toEqual(['2026-03-01', '2026-03-31'])
    }
    expect(apiMock.mock.calls.map(([path]) => String(path)).filter((path) => path.includes('metric_key='))).toEqual([])
  })

  const catalogPaths = ['/analyse/katalog', '/analyse/mikronaehrstoffe', '/analyse/ernaehrung', '/analyse/koerper']
  it.each(catalogPaths)('defaults %s to 30 without discarding unrelated query or hash', async (path) => {
    const { router, wrapper } = await mountAnalysis(`${path}?source=synthetic#analysis-category-fluid`)
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    const picker = wrapper.getComponent(PeriodPicker)
    expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    if (path === '/analyse/katalog' || path === '/analyse/mikronaehrstoffe') {
      expect(readerRanges('micronutrients')).toEqual([['2026-03-02', '2026-03-31']])
    } else if (path === '/analyse/ernaehrung') {
      expect(apiMock.mock.calls.map(([request]) => String(request))
        .filter((request) => request.startsWith('/analytics/nutrition/analysis?')))
        .toEqual(['/analytics/nutrition/analysis?start=2026-03-02&end=2026-03-31'])
    } else {
      expect(apiMock.mock.calls.map(([request]) => String(request)).filter((request) =>
        request.startsWith('/analytics/') && request !== '/analytics/registry')).toEqual([])
    }
  })

  it.each([
    { path: '/analyse/katalog', validStart: '2026-03-01', invalidStart: '2026-02-28' },
    { path: '/analyse/mikronaehrstoffe', validStart: '2025-04-01', invalidStart: '2025-03-31' },
    { path: '/analyse/ernaehrung', validStart: '2025-04-01', invalidStart: '2025-03-31' },
    { path: '/analyse/koerper', validStart: '2016-03-23', invalidStart: '2016-03-22' },
    { path: '/analyse/aktivitaet', validStart: '2016-03-23', invalidStart: '2016-03-22' },
  ])('accepts the inclusive static maximum but rejects one extra day in $path', async ({ path, validStart, invalidStart }) => {
    const { router, wrapper } = await mountAnalysis(`${path}?period=custom&start=${validStart}&end=2026-03-31&source=synthetic#bounds`)
    expect(router.currentRoute.value.query).toEqual({ period: 'custom', start: validStart, end: '2026-03-31', source: 'synthetic' })
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
    await router.push(`${path}?period=custom&start=${invalidStart}&end=2026-03-31&source=synthetic#bounds`)
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#bounds')
    if (path === '/analyse/katalog' || path === '/analyse/mikronaehrstoffe') {
      expect(readerRanges('micronutrients')).toEqual([[validStart, '2026-03-31'], ['2026-03-02', '2026-03-31']])
    }
  })

  it('does not derive Catalog All or load readers from the static Body catalog limit', async () => {
    const path = '/analyse/koerper'
    serveCapabilities({ weight: yes, activity: yes, micronutrients: yes })
    const { router, wrapper } = await mountAnalysis(`${path}?period=all`)
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(wrapper.find('.analysis-period-option[data-period="365"]').exists()).toBe(true)
    expect(apiMock.mock.calls.map(([request]) => String(request)).filter((request) =>
      request.startsWith('/analytics/') && request !== '/analytics/registry')).toEqual([])
  })

  it.each(['/analyse/katalog', '/analyse/mikronaehrstoffe'])('enables Catalog All only from the current micronutrient summary in %s', async (path) => {
    serveCapabilities({ micronutrients: yes })
    const respond = apiMock.getMockImplementation()!
    const summary = Promise.withResolvers<void>()
    let held = false
    apiMock.mockImplementation((request: string, options?: RequestInit) => {
      if (!request.startsWith(`${readers.micronutrients}?`) || held) return respond(request, options)
      held = true
      return summary.promise.then(() => respond(request, options))
    })
    const { router, wrapper } = await mountAnalysis(`${path}?period=all`)
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    summary.resolve()
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    expect(readerRanges('micronutrients').at(-1)).toEqual(['2026-03-01', '2026-03-31'])
    expect(readerRanges('weight')).toEqual([])
    expect(readerRanges('activity')).toEqual([])
  })

  it.each([
    { label: 'missing', capability: undefined },
    { label: 'negative', capability: no },
    { label: 'invalid', capability: { ...yes, all_data_start_date: '2026-02-30' } },
    { label: '366 inclusive days', capability: { ...yes, all_data_start_date: '2025-03-31' } },
  ])('does not offer Catalog All for a $label micronutrient capability', async ({ capability }) => {
    serveCapabilities({ micronutrients: capability })
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=all')
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(readerRanges('micronutrients')).toEqual([['2026-03-02', '2026-03-31']])
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
  })

  it('does not let a late micronutrient Catalog response enable All after a category change', async () => {
    serveCapabilities({ micronutrients: yes })
    const respond = apiMock.getMockImplementation()!
    const summary = Promise.withResolvers<void>()
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.startsWith(`${readers.micronutrients}?`)
      ? summary.promise.then(() => respond(path, options))
      : respond(path, options))
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=30')
    await router.push('/analyse/koerper?period=all&source=synthetic#body')
    await flushPromises()
    summary.resolve()
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#body')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(readerRanges('micronutrients')).toEqual([['2026-03-02', '2026-03-31']])
    expect(readerRanges('weight')).toEqual([])
  })

  it('drops a ready micronutrient Catalog capability when navigating to a category without a reader', async () => {
    serveCapabilities({ micronutrients: yes })
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    await wrapper.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')
    apiMock.mockClear()
    await router.push('/analyse/koerper?period=all&source=synthetic#body')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
    expect(router.currentRoute.value.hash).toBe('#body')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    expect(wrapper.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(apiMock.mock.calls.map(([request]) => String(request)).filter((request) =>
      request.startsWith('/analytics/') && request !== '/analytics/registry')).toEqual([])
  })
})

describe('Analysis selected nutrition reader capability', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it.each([
    { label: 'missing', capability: undefined, allowsAll: false },
    { label: 'negative', capability: { supports_all_data: false, all_data_start_date: null }, allowsAll: false },
    { label: 'genuine', capability: { supports_all_data: true, all_data_start_date: '2026-03-10' }, allowsAll: true },
  ])('requires the selected nutrition response to contribute a $label capability', async ({ capability, allowsAll }) => {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/analytics/registry') return {
        analyses: registry.map((entry) => entry.analysis_key === 'protein_g'
          ? { ...entry, status: 'available', card_capable: true, detail_capable: true,
            reader_key: 'analytics.nutrition.analysis', period_capability: { min_days: 1, max_days: 365, custom_range: true } }
          : entry),
      }
      const response = await fallback(path, options)
      if (path.startsWith('/analytics/nutrition/analysis?')) {
        return { ...response, period_capabilities: capability }
      }
      if ([
        '/analytics/weight?', '/analytics/verification/activity?',
        '/analytics/nutrition/micronutrients?',
      ].some((prefix) => path.startsWith(prefix))) return {
        ...response, period_capabilities: { supports_all_data: true, all_data_start_date: '2026-03-01' },
      }
      return response
    })
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight', 'nutrition', 'micronutrients', 'activity', 'protein_g'])
    expect(router.currentRoute.value.query.period).toBe('30')
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-custom-button').trigger('click')
    const all = wrapper.find('.analysis-period-option[data-period="all"]')
    expect(all.exists()).toBe(allowsAll)
    if (allowsAll) {
      await all.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('all')
      const requests = apiMock.mock.calls.map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/analysis?'))
      const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
      expect([params.get('start'), params.get('end')]).toEqual(['2026-03-01', '2026-03-31'])
    }
  })
})

describe('Analysis historical explicit interval handoff', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-01T10:00:00Z'))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  const surfaces = [
    { path: '/analyse', destination: '/analyse/mikronaehrstoffe/vitamin_a_ug' },
    { path: '/analyse/katalog', destination: '/analyse/mikronaehrstoffe/vitamin_a_ug' },
    { path: '/analyse/mikronaehrstoffe', destination: '/analyse/mikronaehrstoffe/vitamin_a_ug' },
    { path: '/analyse/ernaehrung/protein_g', destination: '/analyse' },
  ]

  function expectReaderBounds(start: string, end: string) {
    const requests = apiMock.mock.calls.map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/') && path.includes('?'))
    expect(requests.some((path) => path.startsWith('/analytics/nutrition/'))).toBe(true)
    for (const request of requests) {
      const params = new URLSearchParams(request.split('?')[1])
      expect([params.get('start'), params.get('end')]).toEqual([start, end])
    }
  }

  describe.each(surfaces)('$path', ({ path, destination }) => {
    it.each(['', '&period=30'])('keeps historical 30-day bounds through normalization and navigation with query "%s"', async (periodQuery) => {
      serveVitaminAData()
      const { router, wrapper } = await mountAnalysis(
        `${path}?start=2024-03-01&end=2024-03-30${periodQuery}&source=synthetic#historical`,
        ['weight', 'nutrition', 'activity', 'vitamin_a_ug'],
      )
      const expectedQuery = {
        period: 'custom', start: '2024-03-01', end: '2024-03-30', source: 'synthetic',
      }
      expect(router.currentRoute.value.query).toEqual(expectedQuery)
      expect(router.currentRoute.value.hash).toBe('#historical')
      expectReaderBounds('2024-03-01', '2024-03-30')
      expect(wrapper.getComponent(PeriodPicker).props()).toMatchObject({
        period: 'custom', start: '2024-03-01', end: '2024-03-30',
      })
      if (path === '/analyse/ernaehrung/protein_g') {
        for (const breadcrumb of wrapper.findAll('.analysis-breadcrumbs a')) {
          const href = new URL(breadcrumb.attributes('href')!, 'https://calograph.test')
          expect(Object.fromEntries(href.searchParams)).toEqual(expectedQuery)
          expect(href.hash).toBe('#historical')
        }
      }

      const link = wrapper.get(`a[href^="${destination}?"]`)
      const href = new URL(link.attributes('href')!, 'https://calograph.test')
      expect([href.searchParams.get('period'), href.searchParams.get('start'), href.searchParams.get('end')])
        .toEqual(['custom', '2024-03-01', '2024-03-30'])
      await link.trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.path).toBe(destination)
      expect(router.currentRoute.value.query).toEqual(expectedQuery)
      expect(router.currentRoute.value.hash).toBe('#historical')
      expectReaderBounds('2024-03-01', '2024-03-30')
    })

    it('keeps today-ending explicit 30-day bounds on the rolling preset path', async () => {
      serveVitaminAData()
      const { router, wrapper } = await mountAnalysis(
        `${path}?period=30&start=2026-09-02&end=2026-10-01&source=synthetic#rolling`,
        ['weight', 'nutrition', 'activity', 'vitamin_a_ug'],
      )
      expect(router.currentRoute.value.query).toEqual({ period: '30', source: 'synthetic' })
      expect(router.currentRoute.value.hash).toBe('#rolling')
      expectReaderBounds('2026-09-02', '2026-10-01')
      expect(wrapper.getComponent(PeriodPicker).props()).toMatchObject({
        period: '30', start: '2026-09-02', end: '2026-10-01',
      })
    })
  })
})
