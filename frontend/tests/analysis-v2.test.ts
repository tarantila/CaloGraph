import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Die Analyse konnte nicht gespeichert werden.'),
}))

import { localizeApiError } from '../src/api'
import AnalysisMiniChart from '../src/components/AnalysisMiniChart.vue'
import { chartColors } from '../src/chart-theme'
import AnalysisActivityView from '../src/views/AnalysisActivityView.vue'
import AnalysisCatalogView from '../src/views/AnalysisCatalogView.vue'
import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisNutritionDetailView from '../src/views/AnalysisNutritionDetailView.vue'
import AnalysisOverviewView from '../src/views/AnalysisOverviewView.vue'
import AnalysisWeightDetailView from '../src/views/AnalysisWeightDetailView.vue'
import { analysisHref } from '../src/analysis-v2'
import { formatGermanDate, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { setThemePreference } from '../src/theme'
import type {
  AnalysisMicronutrientResponse, AnalysisNutritionResponse, AnalysisRegistryEntry,
  VerificationActivityResponse, WeightResponse,
} from '../src/types'

const registry = [
  { analysis_key: 'weight_kg', category_key: 'body', domain_key: 'weight_kg', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.weight', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'active_energy_kcal', category_key: 'activity', domain_key: 'active_energy_kcal', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.verification.activity', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'vitamin_a_ug', category_key: 'micronutrients', domain_key: 'vitamin_a_ug', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients', period_capability: { min_days: 1, max_days: 31, custom_range: true } },
  { analysis_key: 'calcium_mg', category_key: 'micronutrients', domain_key: 'calcium_mg', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients', period_capability: { min_days: 1, max_days: 31, custom_range: true } },
  ...([
    ['fat_ratio_pct', 'body'], ['bmi', 'body'], ['fat_mass_kg', 'body'], ['muscle_mass_kg', 'body'],
    ['fat_free_mass_kg', 'body'], ['hydration_kg', 'body'], ['bone_mass_kg', 'body'],
    ['waist_circumference_cm', 'body'], ['hip_circumference_cm', 'body'],
    ['steps', 'activity'], ['distance_m', 'activity'], ['training', 'activity'],
    ['dietary_energy_kcal', 'nutrition'], ['protein_g', 'nutrition'], ['carbohydrates_g', 'nutrition'],
    ['fat_g', 'nutrition'], ['fiber_g', 'nutrition'], ['sugar_g', 'nutrition'], ['saturated_fat_g', 'nutrition'],
    ['water_intake_ml', 'fluid'],
  ] as const).map(([analysis_key, category_key]) => ({
    analysis_key, category_key, domain_key: analysis_key, status: 'coming_soon',
    card_capable: false, detail_capable: false, reader_key: null, period_capability: null,
  })),
]

const mountedWrappers = new Set<VueWrapper>()
let selectedKeys: string[]
let saveFailure = false
let registryFailuresRemaining = 0
let delayPreferenceWrite = false
let completePreferenceWrite: (() => void) | null = null
function setUser() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = {
    id: 'analysis-user', username: 'owner', language: 'de', timezone: 'Europe/Berlin',
    week_starts_on: 0, raw_payload_retention_days: 0, is_admin: false, is_active: true,
    deactivated_at: null,
  }
  return pinia
}

async function mountAnalysis(path: string, initialSelection: string[] = []) {
  selectedKeys = [...initialSelection]
  const pinia = setUser()
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
          { path: 'aktivitaet', name: 'analysis-category-activity', component: AnalysisCatalogView, props: { categoryKey: 'activity' } },
          { path: 'ernaehrung', name: 'analysis-category-nutrition', component: AnalysisCatalogView, props: { categoryKey: 'nutrition' } },
          { path: 'ernaehrung/:metric', name: 'analysis-nutrition-detail', component: AnalysisNutritionDetailView },
          { path: 'mikronaehrstoffe', name: 'analysis-category-micronutrients', component: AnalysisCatalogView, props: { categoryKey: 'micronutrients' } },
          { path: 'koerper/gewicht', name: 'analysis-weight-detail', component: AnalysisWeightDetailView },
          { path: 'aktivitaet/aktive-energie', name: 'analysis-active-energy', component: AnalysisActivityView },
          { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: AnalysisMicronutrientDetailView },
        ],
      },
      { path: '/ernaehrung', component: { template: '<h1>Ernährung</h1>' } },
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
          template: '<div />',
        },
      },
    },
  })
  mountedWrappers.add(wrapper)
  await flushPromises()
  return { router, wrapper }
}

beforeEach(() => {
  setLocale('de')
  setUser()
  selectedKeys = []
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
      const nextKeys = JSON.parse(String(options.body)).analysis_keys as string[]
      if (delayPreferenceWrite) {
        return new Promise<{ analysis_keys: string[] }>((resolve) => {
          completePreferenceWrite = () => {
            selectedKeys = nextKeys
            resolve({ analysis_keys: nextKeys })
          }
        })
      }
      selectedKeys = nextKeys
      return Promise.resolve({ analysis_keys: selectedKeys })
    }
    if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
    if (path.startsWith('/analytics/nutrition/micronutrients')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: { provider_key: 'yazio' }, period_days: 7, metrics: [
        { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
        { metric_key: 'calcium_mg', label: 'Calcium', category: 'mineral', unit: 'mg', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] },
      ], series: [] })
    }
    if (path.startsWith('/analytics/weight')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: null, points: [], summary: { measurement_count: 0, latest: null, change_kg: null, average_kg: null, minimum_kg: null, maximum_kg: null } })
    }
    if (path.startsWith('/analytics/verification/activity')) {
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical', days: [] })
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
  vi.unstubAllGlobals()
  setThemePreference('system')
  setLocale('de')
})

describe('Analysis V2 hub and catalogue', () => {
  it('starts empty, keeps supported no-data metrics selectable, and marks unsupported keys disabled', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse')
    expect(wrapper.text()).toContain('Noch keine Analysen ausgewählt')
    expect(wrapper.findAll('.analysis-card')).toHaveLength(0)

    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.text()).toContain('Noch keine Werte im Zeitraum')
    expect(wrapper.get('[data-analysis-catalog-key="vitamin_a_ug"] [data-coverage-status="incomplete"] .analysis-catalog-status-icon').text()).toBe('!')
    expect(wrapper.get('[data-analysis-add="vitamin_a_ug"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-analysis-key="protein_g"]').attributes('aria-disabled')).toBe('true')
    expect(wrapper.get('[data-analysis-key="water_intake_ml"]').attributes('aria-disabled')).toBe('true')

    await wrapper.get('[data-analysis-add="weight_kg"]').trigger('click')
    await flushPromises()
    expect(apiMock.mock.calls.some(([path, options]) => path === '/settings/analysis-preferences'
      && (options as RequestInit | undefined)?.method === 'PUT'
      && JSON.stringify(JSON.parse(String((options as RequestInit).body))) === JSON.stringify({ analysis_keys: ['weight_kg'] }))).toBe(true)
    wrapper.unmount()
  })
  it('marks micronutrient catalog coverage complete only for every selected-period day', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
    const coverage = wrapper.get('[data-analysis-catalog-key="vitamin_a_ug"] [data-coverage-status="complete"]')

    expect(coverage.text()).toContain('7 von 7 Tagen')
    expect(coverage.get('.analysis-catalog-status-icon').text()).toBe('✓')
    wrapper.unmount()
  })


  it('renders selected analyses and addable categories directly on the hub', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    const hubText = wrapper.text()

    expect(hubText.indexOf('Meine Analysen')).toBeGreaterThanOrEqual(0)
    expect(hubText.indexOf('Alle Analysen')).toBeGreaterThan(hubText.indexOf('Meine Analysen'))
    expect(wrapper.find('a[href="/analyse/katalog"]').exists()).toBe(false)
    expect(wrapper.find('.analysis-legacy-links').exists()).toBe(false)
    expect(wrapper.find('[data-analysis-add="active_energy_kcal"]').exists()).toBe(true)
    expect(wrapper.find('[data-analysis-add="weight_kg"]').exists()).toBe(false)
    expect(wrapper.find('[data-analysis-selected="weight_kg"]').text()).toContain('In Meine Analysen')
    expect(wrapper.text()).toContain('Bald verfügbar')
    await wrapper.get('[data-analysis-add="active_energy_kcal"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse')
    expect(selectedKeys).toContain('active_energy_kcal')
    wrapper.unmount()
  })
  it('renders each requested category and separates micronutrient types', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper')
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))).toBe(false)

    for (const label of [
      'Gewicht', 'Körperfett', 'BMI', 'Muskelmasse', 'Fettfreie Masse',
      'Körperwasser', 'Knochenmasse', 'Taillenumfang', 'Hüftumfang',
    ]) expect(wrapper.text()).toContain(label)

    await router.push('/analyse/aktivitaet')
    await flushPromises()
    for (const label of ['Aktive Energie', 'Schritte', 'Distanz', 'Training']) expect(wrapper.text()).toContain(label)

    await router.push('/analyse/ernaehrung')
    await flushPromises()
    for (const label of ['Energie', 'Protein', 'Kohlenhydrate', 'Fett', 'Ballaststoffe', 'Zucker', 'Gesättigte Fettsäuren']) {
      expect(wrapper.text()).toContain(label)
    }

    await router.push('/analyse/mikronaehrstoffe')
    await flushPromises()
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))).toBe(true)
    expect(wrapper.text()).toContain('Vitamine')
    expect(wrapper.text()).toContain('Mineralstoffe')
    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.text()).toContain('Calcium')
    wrapper.unmount()
  })

  it('does not duplicate a micronutrient read when category and period change together', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper?period=30')
    apiMock.mockClear()

    await router.push('/analyse/mikronaehrstoffe?period=7')
    await flushPromises()

    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(requests).toHaveLength(1)
    const params = new URLSearchParams(requests[0]!.split('?')[1])
    const rangeMs = Date.parse(`${params.get('end')}T00:00:00Z`) - Date.parse(`${params.get('start')}T00:00:00Z`)
    expect(rangeMs).toBe(6 * 86_400_000)
    wrapper.unmount()
  })

  it('localizes analysis card labels and actions for English users', async () => {
    setLocale('en')
    const { router, wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    expect(wrapper.text()).toContain('My analyses')
    expect(wrapper.text()).toContain('All analyses')

    const weightCard = wrapper.get('[data-analysis-key="weight_kg"]')
    expect(weightCard.get('.analysis-card-heading').text()).toBe('Weight')
    expect(weightCard.find('.analysis-card-description').exists()).toBe(false)
    expect(weightCard.find('[aria-label="Move Weight forward"]').exists()).toBe(true)

    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="weight_kg"]').text()).toContain('Weight')
    expect(wrapper.get('[data-analysis-selected="weight_kg"]').text()).toContain('In My analyses')
    expect(wrapper.get('[data-analysis-key="weight_kg"]').find('[data-analysis-add="weight_kg"]').exists()).toBe(false)
    wrapper.unmount()
  })



  it('loads selected-card previews after a successful workspace retry', async () => {
    registryFailuresRemaining = 1
    const { wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/weight'))).toBe(false)

    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()

    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/weight'))).toBe(true)
    expect(wrapper.findAll('.analysis-card')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps a pending selection visible when navigating from catalog to hub', async () => {
    delayPreferenceWrite = true
    const { router, wrapper } = await mountAnalysis('/analyse/katalog')

    await wrapper.get('[data-analysis-add="weight_kg"]').trigger('click')
    expect(completePreferenceWrite).toBeTypeOf('function')
    await router.push('/analyse')
    await flushPromises()

    completePreferenceWrite!()
    await flushPromises()

    expect(selectedKeys).toEqual(['weight_kg'])
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['weight_kg'])
    wrapper.unmount()
  })

  it('does not persist a reorder when the pointer is released outside the card grid', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    const source = wrapper.get('[data-analysis-card="weight_kg"] .analysis-drag-handle')
    const target = wrapper.get('[data-analysis-card="active_energy_kcal"]').element
    const originalElementFromPoint = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
    let pointedElement: Element = target
    Object.defineProperty(document, 'elementFromPoint', {
      configurable: true,
      value: () => pointedElement,
    })
    const dispatchPointer = (type: string, clientX: number) => {
      const event = new Event(type)
      Object.defineProperties(event, {
        pointerId: { value: 17 },
        clientX: { value: clientX },
        clientY: { value: 0 },
      })
      window.dispatchEvent(event)
    }

    await source.trigger('pointerdown', { pointerId: 17, isPrimary: true })
    dispatchPointer('pointermove', -1)
    await flushPromises()
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['weight_kg', 'active_energy_kcal'])

    pointedElement = document.body
    dispatchPointer('pointerup', 0)
    await flushPromises()
    expect(selectedKeys).toEqual(['active_energy_kcal', 'weight_kg'])
    expect(apiMock.mock.calls.some(([path, options]) => (
      path === '/settings/analysis-preferences' && (options as RequestInit | undefined)?.method === 'PUT'
    ))).toBe(false)
    if (originalElementFromPoint) Object.defineProperty(document, 'elementFromPoint', originalElementFromPoint)
    else Reflect.deleteProperty(document, 'elementFromPoint')
    wrapper.unmount()
  })
  it('persists a pointer reorder released on a card', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    const source = wrapper.get('[data-analysis-card="weight_kg"] .analysis-drag-handle')
    const target = wrapper.get('[data-analysis-card="active_energy_kcal"]').element
    const originalElementFromPoint = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
    Object.defineProperty(document, 'elementFromPoint', {
      configurable: true,
      value: () => target,
    })
    const dispatchPointer = (type: string) => {
      const event = new Event(type)
      Object.defineProperties(event, {
        pointerId: { value: 19 },
        clientX: { value: -1 },
        clientY: { value: 0 },
      })
      window.dispatchEvent(event)
    }

    await source.trigger('pointerdown', { pointerId: 19, isPrimary: true })
    expect(wrapper.findAll('.analysis-card.is-dragging')).toHaveLength(1)
    dispatchPointer('pointermove')
    await flushPromises()
    dispatchPointer('pointerup')
    await flushPromises()

    expect(apiMock.mock.calls.some(([path, options]) => (
      path === '/settings/analysis-preferences' && (options as RequestInit | undefined)?.method === 'PUT'
    ))).toBe(true)
    expect(selectedKeys).toEqual(['weight_kg', 'active_energy_kcal'])
    if (originalElementFromPoint) Object.defineProperty(document, 'elementFromPoint', originalElementFromPoint)
    else Reflect.deleteProperty(document, 'elementFromPoint')
    wrapper.unmount()
  })
  it('uses explicit keyboard controls to persist order and removal as a full ordered list', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['active_energy_kcal', 'weight_kg'])

    await wrapper.get('[aria-label="Gewicht nach vorne verschieben"]').trigger('click')
    await flushPromises()
    expect(selectedKeys).toEqual(['weight_kg', 'active_energy_kcal'])

    await wrapper.get('[aria-label="Aktive Energie aus Analysen entfernen"]').trigger('click')
    await flushPromises()
    expect(selectedKeys).toEqual(['weight_kg'])
    wrapper.unmount()
  })

  it('rolls back an optimistic reorder when the complete preference write fails', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    saveFailure = true
    await wrapper.get('[aria-label="Gewicht nach vorne verschieben"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['active_energy_kcal', 'weight_kg'])
    expect(wrapper.get('[role="alert"]').text()).toContain('Die Analyse konnte nicht gespeichert werden.')
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
  it('normalizes a mixed-reader all-data hub URL before the first micronutrient read', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight_kg', 'vitamin_a_ug'])
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
    const actions = picker.findAll('button[aria-pressed]')
    expect(actions.map((button) => button.text())).toEqual([
      '7 Tage', '30 Tage', '90 Tage', '1 Jahr', 'Alle Daten',
    ])
    expect(actions.find((button) => button.text() === '90 Tage')?.attributes('aria-pressed')).toBe('true')
    expect(picker.findAll('button').some((button) => /benutzerdefiniert/i.test(button.text()))).toBe(true)
    await actions[0]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('7')
    expect(picker.get('button[aria-pressed="true"]').text()).toBe('7 Tage')
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight?'))
    const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -6))
    wrapper.unmount()
  })
  it('offers only safe quick actions and custom access for a 31-day category', async () => {
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe?period=30')
    const picker = wrapper.get('.analysis-period-picker')
    expect(picker.findAll('button[aria-pressed]').map((button) => button.text()))
      .toEqual(['7 Tage', '30 Tage'])
    expect(picker.get('button[aria-pressed="true"]').text()).toBe('30 Tage')
    expect(picker.findAll('button').some((button) => /benutzerdefiniert/i.test(button.text()))).toBe(true)
    wrapper.unmount()
  })
  it('keeps a valid 31-day leap-day range from the hub summary through the category, nutrient, and breadcrumbs', async () => {
    const query = '?period=custom&start=2024-02-29&end=2024-03-30'
    const { router, wrapper } = await mountAnalysis(
      `/analyse${query}`, ['vitamin_a_ug'],
    )
    const expectCustomSelection = () => {
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2024-02-29', end: '2024-03-30',
      })
      expect(wrapper.get('.analysis-period-trigger').text()).toContain('29.02.2024')
      expect(wrapper.get('.analysis-period-picker').findAll('.analysis-period-quick-button').map((button) => button.text()))
        .toEqual(['7 Tage', '30 Tage'])
      const requests = apiMock.mock.calls
        .map(([path]) => String(path))
        .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
      expect([params.get('start'), params.get('end')]).toEqual(['2024-02-29', '2024-03-30'])
    }
    expectCustomSelection()
    await wrapper.get('[role="region"] a[href^="/analyse/mikronaehrstoffe?"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
    expect(wrapper.get('h1').text()).toContain('Mikronährstoffe')
    expectCustomSelection()
    await wrapper.get('a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug?"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(wrapper.get('h1').text()).toContain('Vitamin A')
    expectCustomSelection()
    await wrapper.get('.analysis-breadcrumbs a[href^="/analyse/mikronaehrstoffe"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
    expectCustomSelection()
    await wrapper.get('.analysis-breadcrumbs a[href^="/analyse?"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse')
    expectCustomSelection()
    wrapper.unmount()
  })
  it('rejects an oversized custom detail URL before reading micronutrients', async () => {
    const { router, wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2020-03-01&end=2020-04-01',
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
  it('uses 31 days for the all-analysis catalog but permits 365 days in nutrition catalog', async () => {
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
    expect(nutrition.wrapper.get('.analysis-period-picker button[aria-pressed="true"]').text()).toBe('1 Jahr')
    expect(nutrition.wrapper.get('.analysis-period-picker').findAll('button[aria-pressed]').map((button) => button.text()))
      .toEqual(['7 Tage', '30 Tage', '90 Tage', '1 Jahr'])
    nutrition.wrapper.unmount()
  })
  it('hides custom entry when a selected available reader does not permit custom ranges', async () => {
    const originalApi = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({
        analyses: registry.map((entry) => entry.analysis_key === 'vitamin_a_ug'
          ? { ...entry, period_capability: { min_days: 1, max_days: 31, custom_range: false } }
          : entry),
      })
      : originalApi(path, options))
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight_kg', 'vitamin_a_ug'])
    const picker = wrapper.get('.analysis-period-picker')
    expect(picker.findAll('button[aria-pressed]').map((button) => button.text()))
      .toEqual(['7 Tage', '30 Tage'])
    expect(picker.findAll('button').some((button) => /benutzerdefiniert/i.test(button.text()))).toBe(false)
    await wrapper.get('.analysis-period-trigger').trigger('click')
    expect(picker.find('.analysis-period-custom-form').exists()).toBe(false)
    wrapper.unmount()
  })
  it('limits the hub picker to selected-reader capabilities while keeping custom and quick ranges visible', async () => {
    const { wrapper } = await mountAnalysis(
      '/analyse?period=30',
      ['weight_kg', 'active_energy_kcal', 'vitamin_a_ug'],
    )
    const trigger = wrapper.get('.analysis-period-trigger')
    await trigger.trigger('click')

    const picker = wrapper.get('.analysis-period-picker')
    expect(picker.text()).toContain('7 Tage')
    expect(picker.text()).toContain('30 Tage')
    expect(picker.text()).not.toContain('90 Tage')
    expect(picker.text()).not.toContain('Alle Daten')
    expect(picker.find('.analysis-period-custom-form').isVisible()).toBe(true)
    wrapper.unmount()
  })
  it('applies an inclusive capability-bounded custom hub range and preserves it in analysis links', async () => {
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=30',
      ['weight_kg', 'active_energy_kcal', 'vitamin_a_ug'],
    )
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.04.2020')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('31')

    await dates[1]!.setValue('31.03.2020')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2020-03-01', end: '2020-03-31',
    })
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
    expect(requests.at(-1)).toContain('start=2020-03-01')
    expect(requests.at(-1)).toContain('end=2020-03-31')
    const detailLink = wrapper.get('[data-analysis-card="vitamin_a_ug"] .analysis-card-content')
    await detailLink.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2020-03-01', end: '2020-03-31',
    })
    wrapper.unmount()
  })
  it('retains the shared 3661-day reader limit for the all-supported-range preset', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const allData = wrapper.findAll('.analysis-period-popover .analysis-period-option').find((option) => option.text() === 'Alle Daten')
    expect(allData).toBeDefined()
    await allData!.trigger('click')
    await flushPromises()
    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/verification/activity?'))
    const params = new URLSearchParams(requests.at(-1)!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -3660))
    expect(router.currentRoute.value.query.period).toBe('all')
    wrapper.unmount()
  })
  it('keeps the fluid category hash when adding the default period query', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse#analysis-category-fluid')
    await flushPromises()
    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    expect(router.currentRoute.value.query.period).toBe('30')
    expect(wrapper.find('#analysis-category-fluid').exists()).toBe(true)
    wrapper.unmount()
  })

  it('preserves a valid all-data hub period when opening the fluid section', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', ['weight_kg'])
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('all')

    await wrapper.get('#analysis-category-fluid .analysis-category-title a').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.hash).toBe('#analysis-category-fluid')
    expect(router.currentRoute.value.query.period).toBe('all')
    expect(router.currentRoute.value.query.start).toBeUndefined()
    expect(router.currentRoute.value.query.end).toBeUndefined()
    wrapper.unmount()
  })
  it('loads only selected micronutrient series and shows their daily average with gaps', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['vitamin_a_ug'])
    const card = wrapper.get('[data-analysis-card="vitamin_a_ug"]')
    expect(card.get('.analysis-metric-value').text()).toBe('10 ug')
    expect(card.text()).toContain('3 von 7 Tagen')
    expect(card.text()).toContain('Durchschnitt pro Tag')
    expect(card.findAll('.analysis-mini-chart polyline')).toHaveLength(0)
    expect(card.findAll('.analysis-mini-chart circle')).toHaveLength(2)
    const circles = card.findAll('.analysis-mini-chart circle')
    expect(circles).toHaveLength(2)
    expect(Number(circles[1].attributes('cx')) - Number(circles[0].attributes('cx'))).toBeCloseTo(120)
    const seriesRequests = apiMock.mock.calls.flatMap(([path]) => {
      if (!String(path).startsWith('/analytics/nutrition/micronutrients')) return []
      const key = new URLSearchParams(String(path).split('?')[1] ?? '').get('metric_key')
      return key ? [key] : []
    })
    expect(seriesRequests).toEqual(['vitamin_a_ug'])
    wrapper.unmount()
  })
  it('breaks the mini line at a missing day while keeping the zero marker on UTC calendar days across DST', () => {
    const wrapper = mount(AnalysisMiniChart, {
      props: {
        values: [4, null, 0, 8],
        dates: ['2026-03-28', '2026-03-29', '2026-03-30', '2026-03-31'],
        dateRange: { start: '2026-03-28', end: '2026-03-31' },
        variant: 'line',
        colorRole: 'emerald',
        label: 'Vitamin A, 28. bis 31. März',
      },
    })
    const svg = wrapper.get('svg[role="img"]')
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


  it('positions sparse weight card points using their dates across the selected range', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
    const { wrapper } = await mountAnalysis('/analyse?period=30', ['weight_kg'])
    const points = wrapper.get('[data-analysis-card="weight_kg"] .analysis-mini-chart polyline').attributes('points')?.split(' ') ?? []
    expect(points).toHaveLength(2)
    const xDelta = Number(points[1]!.split(',')[0]) - Number(points[0]!.split(',')[0])
    expect(xDelta).toBeGreaterThan(0)
    expect(xDelta).toBeLessThan(10)
    wrapper.unmount()
  })
  it('marks daily card coverage incomplete with an amber status marker', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['active_energy_kcal'])
    const card = wrapper.get('[data-analysis-card="active_energy_kcal"]')

    expect(card.get('[data-coverage-status="incomplete"]').text()).toContain('1 von 7 Tagen')
    expect(card.get('.analysis-card-coverage-icon').text()).toBe('!')
    expect(card.find('.analysis-mini-chart--amber').exists()).toBe(true)
    wrapper.unmount()
  })

  it('marks complete daily coverage only when every selected-period day has a value', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['active_energy_kcal'])
    const coverage = wrapper.get('[data-analysis-card="active_energy_kcal"] [data-coverage-status="complete"]')

    expect(coverage.text()).toContain('7 von 7 Tagen')
    expect(coverage.get('.analysis-card-coverage-icon').text()).toBe('✓')
    wrapper.unmount()
  })

  it('uses a single-column grid direction for pointer reorder semantics', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    const grid = wrapper.get('.analysis-card-list').element as HTMLElement
    grid.style.gridTemplateColumns = '100px'
    const source = wrapper.get('[data-analysis-card="weight_kg"] .analysis-drag-handle')
    const target = wrapper.get('[data-analysis-card="active_energy_kcal"]').element
    const originalElementFromPoint = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => target })
    vi.spyOn(target, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)
    const dispatchPointer = (type: string) => {
      const event = new Event(type)
      Object.defineProperties(event, {
        pointerId: { value: 23 },
        clientX: { value: 90 },
        clientY: { value: 10 },
      })
      window.dispatchEvent(event)
    }

    await source.trigger('pointerdown', { pointerId: 23, isPrimary: true })
    dispatchPointer('pointerup')
    await flushPromises()

    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['weight_kg', 'active_energy_kcal'])
    expect(wrapper.get('[data-analysis-card="weight_kg"]').attributes('data-drop-state')).toBe('dropped')
    if (originalElementFromPoint) Object.defineProperty(document, 'elementFromPoint', originalElementFromPoint)
    else Reflect.deleteProperty(document, 'elementFromPoint')
    wrapper.unmount()
  })
  it('does not show success feedback when a pointer reorder save fails', async () => {
    saveFailure = true
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    const target = wrapper.get('[data-analysis-card="active_energy_kcal"]').element
    const originalElementFromPoint = Object.getOwnPropertyDescriptor(document, 'elementFromPoint')
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: () => target })
    vi.spyOn(target, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 100, height: 100, right: 100, bottom: 100, x: 0, y: 0, toJSON: () => ({}) } as DOMRect)

    await wrapper.get('[data-analysis-card="weight_kg"] .analysis-drag-handle').trigger('pointerdown', { pointerId: 24, isPrimary: true })
    const pointerMove = new Event('pointermove')
    Object.defineProperties(pointerMove, {
      pointerId: { value: 24 }, clientX: { value: 1 }, clientY: { value: 1 },
    })
    window.dispatchEvent(pointerMove)
    await flushPromises()
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['weight_kg', 'active_energy_kcal'])
    const pointerUp = new Event('pointerup')
    Object.defineProperties(pointerUp, {
      pointerId: { value: 24 }, clientX: { value: 1 }, clientY: { value: 1 },
    })
    window.dispatchEvent(pointerUp)
    await flushPromises()

    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['active_energy_kcal', 'weight_kg'])
    expect(wrapper.findAll('[data-drop-state="dropped"]')).toHaveLength(0)
    expect(wrapper.get('.analysis-save-error').text()).toContain('Die Analyse konnte nicht gespeichert werden.')

    if (originalElementFromPoint) Object.defineProperty(document, 'elementFromPoint', originalElementFromPoint)
    else Reflect.deleteProperty(document, 'elementFromPoint')
    wrapper.unmount()
  })

  it('presents handle, themed icon, metric name and overflow in reading order', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    const card = wrapper.get('[data-analysis-card="weight_kg"]')
    const handle = card.get('.analysis-drag-handle').element
    const icon = card.get('.analysis-card-icon').element
    const name = card.get('h3').element
    const overflow = card.get('.analysis-overflow summary').element
    for (const [before, after] of [[handle, icon], [icon, name], [name, overflow]]) {
      expect(before!.compareDocumentPosition(after!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    }
    expect(card.get('.analysis-drag-handle').attributes('aria-label')).toMatch(/Gewicht/)
    expect(card.get('.analysis-overflow summary').attributes('aria-label')).toMatch(/Gewicht/)
    wrapper.unmount()
  })

  it('filters localized nutrition and coming-soon body labels without enabling unavailable entries', async () => {
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path === '/analytics/registry'
      ? Promise.resolve({ analyses: registry.map((entry) => entry.analysis_key === 'protein_g'
        ? { ...entry, status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.analysis', period_capability: { min_days: 1, max_days: 365, custom_range: true } }
        : entry) })
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse/katalog', ['weight_kg'])
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
    expect(wrapper.find('[data-analysis-selected="weight_kg"]').exists()).toBe(true)
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

  it('opens the real weight and activity details from the card body but not from the handle', async () => {
    const { wrapper, router } = await mountAnalysis('/analyse?period=custom&start=2026-03-25&end=2026-03-31', ['weight_kg', 'active_energy_kcal'])
    await wrapper.get('[data-analysis-card="weight_kg"] .analysis-drag-handle').trigger('click')
    expect(router.currentRoute.value.path).toBe('/analyse')
    await wrapper.get('[data-analysis-card="weight_kg"] .analysis-card-content').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/koerper/gewicht')
    expect(wrapper.find('.weight-summary-primary').exists()).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-03-25', end: '2026-03-31' })
    await router.push('/analyse?period=custom&start=2026-03-25&end=2026-03-31')
    await flushPromises()
    await wrapper.get('[data-analysis-card="active_energy_kcal"] .analysis-card-content').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/aktivitaet/aktive-energie')
    expect(wrapper.find('.activity-summary').exists()).toBe(true)
  })

  it('prevents conflicting reorder actions until the pending order has been saved', async () => {
    delayPreferenceWrite = true
    const { wrapper } = await mountAnalysis('/analyse', ['active_energy_kcal', 'weight_kg'])
    await wrapper.get('[aria-label="Gewicht nach vorne verschieben"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key')))
      .toEqual(['weight_kg', 'active_energy_kcal'])
    expect(wrapper.findAll('.analysis-drag-handle').every((handle) => handle.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.get('[aria-label="Gewicht nach hinten verschieben"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="Aktive Energie nach vorne verschieben"]').attributes('disabled')).toBeDefined()
    completePreferenceWrite!()
    await flushPromises()
    expect(wrapper.get('[aria-label="Gewicht nach hinten verschieben"]').attributes('disabled')).toBeUndefined()
    expect(selectedKeys).toEqual(['weight_kg', 'active_energy_kcal'])
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
      period_capability: { min_days: 1, max_days: 31, custom_range: true },
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
    days: [
      {
        date: start, provider_key: 'yazio', dietary_energy_kcal: 410,
        protein_g: 20, carbohydrates_g: 50, fat_g: 10,
        fiber_g: 4, sugar_g: 6, saturated_fat_g: 2,
        meals: [{
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

  function hubRegion(wrapper: VueWrapper, name: string) {
    return wrapper.findAll('section[aria-labelledby], section[aria-label], [role="region"]').find((region) => {
      const headingId = region.attributes('aria-labelledby')
      const heading = headingId ? region.find(`[id="${headingId}"]`) : null
      const accessibleName = region.attributes('aria-label') ?? (heading?.exists() ? heading.text() : '')
      return accessibleName === name
    })
  }

  it('places a distinct weight analysis after the selected summaries and before micronutrients with period-preserving details', async () => {
    serveSeededHub(true)
    const keys = ['dietary_energy_kcal', 'weight_kg', 'vitamin_a_ug', 'active_energy_kcal']
    const { router, wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    const summaries = wrapper.get('.analysis-card-list')
    expect(summaries.findAll('[data-analysis-card]').map((card) => card.attributes('data-analysis-card')))
      .toEqual(keys)
    const weightSection = hubRegion(wrapper, 'Gewichtsverlauf')
    const nutrients = wrapper.find('section[aria-labelledby="analysis-nutrient-summary-title"]')
    expect(weightSection, 'selected weight has its own named analysis section').toBeDefined()
    expect(nutrients).toBeDefined()
    expect(summaries.element.contains(weightSection!.element)).toBe(false)
    for (const [before, after] of [
      [summaries.element, weightSection!.element],
      [weightSection!.element, nutrients!.element],
      [nutrients!.element, wrapper.get('#analysis-catalog-title').element],
    ]) {
      expect(before!.compareDocumentPosition(after!) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0)
    }

    const text = weightSection!.text().replace(/\s+/g, ' ')
    expect(text).toMatch(/Letzte Messung\s*71,4 kg/)
    expect(text).toMatch(/Veränderung\s*[-−]1,1 kg/)
    expect(text).toMatch(/Durchschnitt\s*72 kg/)
    expect(text).toMatch(/Minimum\s*71,4 kg/)
    expect(text).toMatch(/Maximum\s*72,5 kg/)
    expect(text).toContain('27 von 30 Tagen')
    const details = weightSection!.get('a[href^="/analyse/koerper/gewicht?"]')
    expect(details.text()).toMatch(/Details/i)
    await details.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/koerper/gewicht')
    expect(router.currentRoute.value.query).toMatchObject({
      period: 'custom', start: '2026-03-01', end: '2026-03-30',
    })
    expect(wrapper.get('.weight-summary-primary > strong').text()).toBe('71,4 kg')
    expect(wrapper.get('.weight-reading-list time[datetime="2026-03-27"]').element.parentElement?.textContent)
      .toContain('71,4 kg')
    wrapper.unmount()
  })

  it('shows the weight analysis only while weight is selected without disturbing other summaries', async () => {
    serveSeededHub(true)
    const keys = ['vitamin_a_ug', 'active_energy_kcal']
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    expect(hubRegion(wrapper, 'Gewichtsverlauf')).toBeUndefined()
    await wrapper.get('[data-analysis-add="weight_kg"]').trigger('click')
    await flushPromises()
    const weightSection = hubRegion(wrapper, 'Gewichtsverlauf')
    expect(weightSection).toBeDefined()
    expect(weightSection!.text()).toContain('71,4 kg')
    await wrapper.get('[aria-label="Gewicht aus Analysen entfernen"]').trigger('click')
    await flushPromises()
    expect(hubRegion(wrapper, 'Gewichtsverlauf')).toBeUndefined()
    expect(wrapper.findAll('[data-analysis-card]').map((card) => card.attributes('data-analysis-card')))
      .toEqual(keys)
    expect(wrapper.find('section[aria-labelledby="analysis-nutrient-summary-title"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps the selected weight analysis honest when the period has no readings', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/analytics/weight?')
      ? Promise.resolve({
        ...weight, points: [],
        summary: {
          measurement_count: 0, latest: null, change_kg: null,
          average_kg: null, minimum_kg: null, maximum_kg: null,
        },
      } satisfies WeightResponse)
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight_kg'])
    const weightSection = hubRegion(wrapper, 'Gewichtsverlauf')
    expect(weightSection).toBeDefined()
    expect(weightSection!.get('[role="status"]').text()).toMatch(/keine Gewichtswerte|keine Werte/i)
    expect(weightSection!.text()).toContain('0 von 30 Tagen')
    expect(weightSection!.text()).not.toMatch(/\d+(?:,\d+)?\s*kg/)
    wrapper.unmount()
  })

  it('shows a single real weight reading without inventing a period change', async () => {
    serveSeededHub(true)
    const fallback = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => path.startsWith('/analytics/weight?')
      ? Promise.resolve({
        ...weight, points: [{ date: '2026-03-15', weight_kg: 73.2 }],
        summary: {
          measurement_count: 1, latest: { date: '2026-03-15', weight_kg: 73.2 },
          change_kg: null, average_kg: 73.2, minimum_kg: 73.2, maximum_kg: 73.2,
        },
      } satisfies WeightResponse)
      : fallback(path, options))
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['weight_kg'])
    const weightSection = hubRegion(wrapper, 'Gewichtsverlauf')
    expect(weightSection).toBeDefined()
    const text = weightSection!.text().replace(/\s+/g, ' ')
    expect(text).toMatch(/Letzte Messung\s*73,2 kg/)
    expect(text).toMatch(/Durchschnitt\s*73,2 kg/)
    expect(text).toMatch(/Minimum\s*73,2 kg/)
    expect(text).toMatch(/Maximum\s*73,2 kg/)
    expect(text).toContain('1 von 30 Tagen')
    expect(text).toMatch(/Veränderung\s*[–—]|weniger als zwei Messungen/i)
    expect(text).not.toMatch(/(?:[+−-]\s*\d+(?:,\d+)?|0)\s*kg/)
    wrapper.unmount()
  })

  it('shows canonical category presence, grouped definitions and sourced mini-trends even when the unfiltered series is empty', async () => {
    serveSeededHub(true)
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe' + rangeQuery)
    expect(router.currentRoute.value.path).toBe('/analyse/mikronaehrstoffe')
    expect(wrapper.get('h1').text()).toContain('Mikronährstoffe')
    const category = wrapper.get('#analysis-category-micronutrients')
    expect(category.get('.analysis-nutrient-count').text().trim()).toBe('2 von 3')
    const vitamins = category.findAll('section').find((section) => section.find('h3').exists() && section.get('h3').text() === 'Vitamine')
    const minerals = category.findAll('section').find((section) => section.find('h3').exists() && section.get('h3').text() === 'Mineralstoffe')
    expect(vitamins?.text()).toContain('Vitamin A')
    expect(vitamins?.text()).toContain('Vitamin C')
    expect(vitamins?.text()).not.toContain('Calcium')
    expect(minerals?.text()).toContain('Calcium')
    expect(minerals?.text()).not.toContain('Vitamin A')
    expect(category.text()).toContain('12 von 30 Tagen')
    expect(category.text()).toContain('3 von 30 Tagen')
    for (const key of ['vitamin_a_ug', 'vitamin_c_mg', 'calcium_mg']) {
      const link = category.get(`a[href^="/analyse/mikronaehrstoffe/${key}?"]`)
      const params = new URLSearchParams(link.attributes('href')!.split('?')[1])
      expect([params.get('period'), params.get('start'), params.get('end')])
        .toEqual(['custom', '2026-03-01', '2026-03-30'])
    }
    const charts = category.findAll('svg[role="img"]')
    const vitaminTrend = charts.find((chart) => chart.attributes('aria-label')?.includes('Vitamin A'))
    const calciumTrend = charts.find((chart) => chart.attributes('aria-label')?.includes('Calcium'))
    expect(vitaminTrend?.get('polyline').attributes('points')?.split(' ')).toHaveLength(12)
    expect(calciumTrend?.findAll('circle')).toHaveLength(3)
    expect(charts.length).toBeLessThanOrEqual(3)

    const requests = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients?'))
      .map((path) => new URLSearchParams(path.split('?')[1]))
    expect(requests.filter((params) => !params.has('metric_key'))).toHaveLength(1)
    const trendKeys = requests.flatMap((params) => params.has('metric_key') ? [params.get('metric_key')!] : [])
    expect(trendKeys).toEqual(expect.arrayContaining(['vitamin_a_ug', 'calcium_mg']))
    expect(trendKeys.length).toBeLessThanOrEqual(3)
    expect(new Set(trendKeys).size).toBe(trendKeys.length)
    expect(category.text()).not.toMatch(/Mangel|Unterversorgung|Überversorgung|Überdosierung/i)
    wrapper.unmount()
  })

  it('reloads the active category range when navigation overtakes its initial filtered nutrient trend', async () => {
    const marchSummary: AnalysisMicronutrientResponse = {
      ...micronutrientResponse(null),
      metrics: [
        vitaminA,
        metric('calcium_mg', 'Calcium', 'mineral', 'mg', null, 0),
        vitaminC,
      ],
    }
    const aprilSummary: AnalysisMicronutrientResponse = {
      start_date: '2026-04-01', end_date: '2026-04-07',
      selected_provider: { provider_key: 'yazio' }, period_days: 7,
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
    expect(category.get('.analysis-nutrient-count').text().trim()).toBe('2 von 3')
    expect(category.text()).toContain('2 von 7 Tagen')
    expect(category.text()).toContain('1 von 7 Tagen')
    expect(category.text()).not.toMatch(/12 von 7 Tagen|12 von 30 Tagen/)
    const vitaminTrend = category.findAll('.analysis-nutrient-trends > li')
      .find((item) => item.get('a').text() === 'Vitamin A')
    expect(vitaminTrend).toBeDefined()
    expect(vitaminTrend!.get('svg[role="img"] polyline').attributes('points')!.split(' ')).toHaveLength(2)
    wrapper.unmount()
  })

  it('renders preference-selected weight, nutrition, micronutrients and activity from the same 30-day period', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [
      'weight_kg', 'dietary_energy_kcal', 'vitamin_a_ug', 'active_energy_kcal',
    ])
    const weightCard = wrapper.get('[data-analysis-key="weight_kg"]')
    const nutritionCard = wrapper.get('[data-analysis-key="dietary_energy_kcal"]')
    const activityCard = wrapper.get('[data-analysis-key="active_energy_kcal"]')
    expect(weightCard.get('a[href^="/analyse/koerper/gewicht?"]').attributes('href')).toContain('start=2026-03-01')
    expect(weightCard.text()).toContain('71,4 kg')
    expect(weightCard.text()).toMatch(/[-−]1,1 kg/)
    expect(weightCard.text()).toContain('27 von 30 Tagen')
    expect(nutritionCard.get('a[href^="/analyse/ernaehrung/dietary_energy_kcal?"]').attributes('href')).toContain('end=2026-03-30')
    expect(nutritionCard.text()).toContain('500 kcal')
    expect(nutritionCard.text()).toContain('Eiweiß')
    expect(nutritionCard.text()).toContain('30 g')
    expect(nutritionCard.text()).toContain('2 von 30 Tagen')
    const macroBars = nutritionCard.findAll('.analysis-mini-chart rect')
    expect(macroBars).toHaveLength(6)
    const stackColors = macroBars.map((bar) => bar.attributes('fill') ?? (bar.element as SVGRectElement).style.fill)
    expect(stackColors.slice(0, 3)).toEqual([
      'var(--chart-series-teal)', 'var(--chart-series-blue)', 'var(--chart-series-amber)',
    ])
    expect(stackColors.slice(3)).toEqual(stackColors.slice(0, 3))

    expect(activityCard.get('a[href^="/analyse/aktivitaet/aktive-energie?"]').attributes('href')).toContain('start=2026-03-01')
    expect(activityCard.text()).toContain('115,6 kcal')
    expect(activityCard.text()).toContain('27 von 30 Tagen')
    expect(activityCard.text()).not.toMatch(/Schritte|8\.942|Stabil/)
    expect(activityCard.findAll('.analysis-mini-chart rect')).toHaveLength(26)

    const nutrients = wrapper.findAll('[role="region"]').find((region) => {
      const headingId = region.attributes('aria-labelledby')
      return headingId && region.find(`[id="${headingId}"]`).text().includes('Mikronährstoffe')
    })
    expect(nutrients).toBeDefined()
    expect(nutrients!.text()).toMatch(/2\s*(von|\/)\s*3/)
    expect(nutrients!.text()).toContain('Vitamin A')
    expect(nutrients!.text()).toContain('Calcium')
    expect(nutrients!.text()).toContain('12 von 30 Tagen')
    expect(nutrients!.text()).toContain('3 von 30 Tagen')
    expect(nutrients!.get('a[href^="/analyse/mikronaehrstoffe?"]').attributes('href')).toContain('end=2026-03-30')
    expect(wrapper.get('[data-analysis-key="vitamin_a_ug"] a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug?"]').attributes('href'))
      .toContain('start=2026-03-01')
    const vitaminTrend = nutrients!.findAll('svg[role="img"]')
      .find((chart) => chart.attributes('aria-label')?.includes('Vitamin A'))
    const calciumTrend = nutrients!.findAll('svg[role="img"]')
      .find((chart) => chart.attributes('aria-label')?.includes('Calcium'))
    expect(vitaminTrend?.findAll('polyline')).toHaveLength(1)
    expect(vitaminTrend?.get('polyline').attributes('points')?.split(' ')).toHaveLength(12)
    expect(calciumTrend?.findAll('circle')).toHaveLength(3)
    expect(wrapper.text()).not.toMatch(/8\.942\s*Schritte|Stabil/)
    wrapper.unmount()
  })

  it('keeps empty preferences genuinely empty with an available add action', async () => {
    serveSeededHub(true)
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, [])
    expect(wrapper.get('.analysis-empty-state').text()).toContain('Noch keine Analysen ausgewählt')
    expect(wrapper.findAll('.analysis-card')).toHaveLength(0)
    expect(wrapper.findAll('[role="region"]').some((region) => {
      const headingId = region.attributes('aria-labelledby')
      return Boolean(headingId && region.find(`[id="${headingId}"]`).text().includes('Mikronährstoffe'))
    })).toBe(false)
    await wrapper.get('[data-analysis-add="dietary_energy_kcal"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="dietary_energy_kcal"]').text()).toContain('500 kcal')
    wrapper.unmount()
  })

  it('keeps all seven selected nutrition metrics and each selected nutrient reachable without synthetic preference keys', async () => {
    serveSeededHub(true)
    const keys = [
      'dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g',
      'fiber_g', 'sugar_g', 'saturated_fat_g', 'vitamin_a_ug', 'calcium_mg',
    ]
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, keys)
    expect(wrapper.findAll('.analysis-card').map((card) => card.attributes('data-analysis-key'))).toEqual(keys)
    for (const key of keys.slice(0, 7)) {
      expect(wrapper.get(`[data-analysis-key="${key}"] a[href^="/analyse/ernaehrung/${key}?"]`).attributes('href'))
        .toContain('end=2026-03-30')
    }
    for (const key of keys.slice(7)) {
      expect(wrapper.get(`[data-analysis-key="${key}"] a[href^="/analyse/mikronaehrstoffe/${key}?"]`).attributes('href'))
        .toContain('start=2026-03-01')
    }
    expect(wrapper.find('[data-analysis-key="micronutrients"]').exists()).toBe(false)
    wrapper.unmount()
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
    const { wrapper } = await mountAnalysis('/analyse' + rangeQuery, ['dietary_energy_kcal'])
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
          days: [{ ...nutrition.days[0]!, date: '2026-03-09', dietary_energy_kcal: 730 }],
        } satisfies AnalysisNutritionResponse)
      }
      return fallback(path, options)
    })
    const { router, wrapper } = await mountAnalysis(
      '/analyse?period=custom&start=2026-03-01&end=2026-03-07', ['dietary_energy_kcal'],
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

  it('limits selected micronutrient cards and their grouped summary to at most 31 inclusive days', async () => {
    serveSeededHub()
    const { router, wrapper } = await mountAnalysis('/analyse?period=all', [
      'weight_kg', 'dietary_energy_kcal', 'vitamin_a_ug',
    ])
    expect(router.currentRoute.value.query.period).toBe('30')
    const picker = wrapper.get('.analysis-period-picker')
    expect(picker.findAll('button[aria-pressed]').map((button) => button.text())).toEqual(['7 Tage', '30 Tage'])
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2026')
    await dates[1]!.setValue('01.04.2026')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('31')
    expect(router.currentRoute.value.query.period).toBe('30')
    wrapper.unmount()
  })
})

describe('Analysis V2 detail views', () => {
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
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
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
      if (chartCase.key === 'vitamin_a_ug') {
        const coverage = wrapper.get('.micronutrient-coverage[data-coverage-status="incomplete"]')
        expect(coverage.get('.micronutrient-coverage-icon').text()).toBe('!')
      }
      type ThemeOption = {
        backgroundColor: string
        tooltip: { backgroundColor: string; borderColor: string; textStyle: { color: string } }
        xAxis: { axisLine: { lineStyle: { color: string } }; axisLabel: { color: string } }
        yAxis: { axisLine: { lineStyle: { color: string } }; axisLabel: { color: string }; splitLine: { lineStyle: { color: string } } }
        series: Array<{
          name: string
          type: string
          connectNulls?: boolean
          smooth?: boolean
          showSymbol?: boolean
          areaStyle?: { opacity?: number }
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
      expect(after.series.map((series) => ({ name: series.name, type: series.type, connectNulls: series.connectNulls, data: series.data })))
      if (chartCase.key === 'active_energy_kcal') expect(after.series[0]!.type).toBe('bar')
      if (chartCase.key === 'weight_kg') {
        expect(after.series[0]!.smooth).toBe(false)
        expect(after.series[0]!.showSymbol).toBe(true)
      }
      if (chartCase.key === 'vitamin_a_ug') {
        expect(after.series[0]!.connectNulls).toBe(false)
        expect(after.series[0]!.areaStyle?.opacity).toBeCloseTo(0.12)
      }
      expect(apiMock.mock.calls).toHaveLength(apiCallCount)
      wrapper.unmount()
      setThemePreference('dark')
    }
  })
  it('limits each shared period picker to its reader capability and shows custom fields immediately', async () => {
    const cases = [
      {
        path: '/analyse/koerper/gewicht?period=365',
        labels: ['7 Tage', '30 Tage', '90 Tage', '1 Jahr', 'Alle Daten'],
        custom: true,
      },
      {
        path: '/analyse/aktivitaet/aktive-energie',
        labels: ['7 Tage', '30 Tage', '90 Tage', '1 Jahr', 'Alle Daten'],
        custom: true,
      },
      {
        path: '/analyse/mikronaehrstoffe/vitamin_a_ug',
        labels: ['7 Tage', '30 Tage'],
        custom: true,
      },
    ]
    for (const pickerCase of cases) {
      const { wrapper } = await mountAnalysis(pickerCase.path)
      await wrapper.get('.analysis-period-trigger').trigger('click')
      expect(wrapper.findAll('.analysis-period-popover .analysis-period-option').map((option) => option.text()))
        .toEqual(pickerCase.labels)
      expect(wrapper.find('.analysis-period-custom-form').exists()).toBe(pickerCase.custom)
      if (pickerCase.custom) {
        expect(wrapper.findAll('.analysis-period-custom-form input[type="text"]')).toHaveLength(2)
      }
      const trigger = wrapper.get('.analysis-period-trigger')
      await trigger.trigger('keydown', { key: 'Escape' })
      expect(trigger.attributes('aria-expanded')).toBe('false')
      await trigger.trigger('click')
      document.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      await flushPromises()
      expect(trigger.attributes('aria-expanded')).toBe('false')
      wrapper.unmount()
    }
  })
  it('opens the compact mobile picker, rejects invalid custom dates, and restores trigger focus on Escape', async () => {
    vi.stubGlobal('innerWidth', 375)
    window.dispatchEvent(new Event('resize'))
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=30')
    const trigger = wrapper.get('.analysis-period-trigger')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    expect(trigger.text()).toContain('30 Tage')
    await trigger.trigger('click')
    const popover = wrapper.get('.analysis-period-popover')
    expect(trigger.attributes('aria-expanded')).toBe('true')
    expect(popover.findAll('.analysis-period-option').map((button) => button.text()))
      .toEqual(['7 Tage', '30 Tage'])
    expect(popover.get('.analysis-period-option[aria-pressed="true"]').text()).toBe('30 Tage')
    const dates = popover.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.04.2020')
    await popover.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(popover.get('[role="alert"]').text()).toContain('31')
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
    const yearPreset = wrapper.findAll('.analysis-period-popover .analysis-period-option').find((option) => option.text() === '1 Jahr')
    expect(yearPreset).toBeDefined()
    await yearPreset!.trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight'))
    expect(calls).toHaveLength(2)
    const params = new URLSearchParams(calls[1]!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -364))
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.get('.analysis-period-trigger').text()).toContain('1 Jahr')
    wrapper.unmount()
  })
  it('normalizes a stale six-month URL before its first weight request', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse/koerper/gewicht?period=180')
    const calls = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/weight?'))
    expect(calls).toHaveLength(1)
    const params = new URLSearchParams(calls[0]!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -364))
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.get('.analysis-period-trigger').text()).toContain('1 Jahr')
    expect(wrapper.findAll('.analysis-period-picker button').some((button) => button.text() === '6 Monate')).toBe(false)
    wrapper.unmount()
  })

  it('applies an inclusive 31-day micronutrient custom range only after validation', async () => {
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.04.2020')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('31')
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients')))
      .toHaveLength(1)

    await dates[1]!.setValue('31.03.2020')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls
      .map(([path]) => String(path))
      .filter((path) => path.startsWith('/analytics/nutrition/micronutrients'))
    expect(calls).toHaveLength(2)
    expect(calls[1]).toContain('start=2020-03-01')
    expect(calls[1]).toContain('end=2020-03-31')
    wrapper.unmount()
  })
  it('allows at most 3661 inclusive days for a weight custom range', async () => {
    const { wrapper } = await mountAnalysis('/analyse/koerper/gewicht')
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    const today = wrapper.find('.analysis-period-custom-form input[type="date"]').attributes('max')!
    await dates[0]!.setValue(formatGermanDate(shiftIsoDate(today, -3661)))
    await dates[1]!.setValue(formatGermanDate(today))
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('3661')
    await dates[0]!.setValue(formatGermanDate(shiftIsoDate(today, -3660)))
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    const calls = apiMock.mock.calls.map(([path]) => String(path)).filter((path) => path.startsWith('/analytics/weight'))
    expect(calls).toHaveLength(2)
    expect(calls[1]).toContain(`start=${shiftIsoDate(today, -3660)}`)
    expect(calls[1]).toContain(`end=${today}`)
    wrapper.unmount()
  })
  it('renders sparse weight points and reports summary fields from the weight reader', async () => {
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/weight')
      ? Promise.resolve({
        start_date: '2026-03-01', end_date: '2026-03-31', selected_provider: { provider_key: 'apple_health' },
        points: [{ date: '2026-03-01', weight_kg: 70 }, { date: '2026-03-29', weight_kg: 69.5 }],
        summary: { measurement_count: 2, latest: { date: '2026-03-29', weight_kg: 69.5 }, change_kg: -0.5, average_kg: 69.75, minimum_kg: 69.5, maximum_kg: 70 },
      })
      : Promise.resolve({ analyses: registry, analysis_keys: [] }))
    const { wrapper } = await mountAnalysis('/analyse/koerper/gewicht')
    expect(wrapper.text()).toContain('2 Messungen')
    expect(wrapper.text()).toContain('69,5 kg')
    expect(wrapper.get('.page-heading').find('.analysis-period-picker').exists()).toBe(true)
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    expect(chart.exists()).toBe(true)

    const option = chart.props('option') as {
      xAxis: { type: string }
      series: Array<{ type: string; smooth?: boolean; showSymbol?: boolean; data: Array<[number, number]>; lineStyle?: object }>
    }
    expect(option.xAxis.type).toBe('time')
    expect(option.series).toHaveLength(1)
    expect(option.series[0]!.type).toBe('line')
    expect(option.series[0]!.smooth).toBe(false)
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
      return Promise.resolve({ analyses: registry, analysis_keys: [] })
    })
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    expect(wrapper.text()).toContain('1 von 7 Tagen')
    expect(wrapper.text()).not.toContain('Google Health')
    expect(wrapper.get('.page-heading').find('.analysis-period-picker').exists()).toBe(true)
    expect(wrapper.get('.activity-coverage[data-coverage-status="incomplete"] .activity-coverage-icon').text()).toBe('!')
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    const option = chart.props('option') as {
      xAxis: { data: string[] }
      yAxis: { min: number; scale: boolean }
      series: Array<{ type: string; data: Array<number | null> }>
    }
    expect(option.series[0]!.type).toBe('bar')
    expect(option.yAxis).toMatchObject({ min: 0, scale: false })
    expect(option.xAxis.data).toHaveLength(7)
    expect(option.series[0]!.data[0]).toBe(0)
    expect(option.series[0]!.data[1]).toBeNull()
    expect(option.series[0]!.data).toHaveLength(7)
    expect(wrapper.get('.activity-reading-list h2').text()).toBe('Tageswerte')
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
      return Promise.resolve({ analyses: registry, analysis_keys: [] })
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
        const nextKeys = JSON.parse(String(options.body)).analysis_keys as string[]
        selectedKeys = nextKeys
        return Promise.resolve({ analysis_keys: nextKeys })
      }
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['active_energy_kcal'])

    expect(wrapper.get('[data-analysis-card="active_energy_kcal"] .analysis-metric-value').text()).toBe('150 kcal')
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"]').text()).toContain('2 von 7 Tagen')
    const bars = wrapper.get('[data-analysis-card="active_energy_kcal"]').findAll('.analysis-mini-chart rect')
    expect(bars).toHaveLength(2)
    expect(Number(bars[1].attributes('x')) - Number(bars[0].attributes('x'))).toBeCloseTo(240 / 7)
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"]').text()).toContain('Durchschnitt pro Tag')
    wrapper.unmount()
  })
  it('keeps an exact-zero canonical value in the Active Energy card preview', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/analytics/registry') return Promise.resolve({ analyses: registry })
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({
          start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical',
          days: [{ date: '2026-03-25', canonical: { provider_key: 'yazio', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] }],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse?period=7', ['active_energy_kcal'])
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"] .analysis-metric-value').text()).toBe('0 kcal')
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"]').text()).toContain('1 von 7 Tagen')
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"]').text()).toContain('Durchschnitt pro Tag')
    expect(wrapper.get('[data-analysis-card="active_energy_kcal"]').findAll('.analysis-mini-chart circle')).toHaveLength(1)
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
      return Promise.resolve({ analyses: registry, analysis_keys: [] })
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
    expect(wrapper.get('.activity-coverage').text()).toContain('2 von 3 Tagen')
    expect(wrapper.get('.activity-summary-primary > strong').text()).toBe('60 kcal')
    expect(wrapper.findAll('.activity-reading-list li').map((row) => row.get('strong').text()))
      .toEqual(['0 kcal', '120 kcal'])
    wrapper.unmount()
  })

})

describe('Analysis V2 nutrition detail', () => {
  const nutritionResponse: AnalysisNutritionResponse = {
    start_date: '2020-03-01',
    end_date: '2020-03-03',
    selected_provider: { provider_key: 'yazio' },
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
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
      return Promise.resolve({})
    })
  }

  it('links exactly the seven supported macro keys to details without replacing the nutrition catalog', async () => {
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
    expect(router.currentRoute.value.name).toBe('analysis-category-nutrition')
    expect(wrapper.get('h1').text()).toContain('Ernährung')
    await router.push('/ernaehrung')
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Ernährung')
    wrapper.unmount()
  })

  it('switches all seven German metric headings, units and chart values from one period response while preserving the query', async () => {
    serveNutrition()
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    const selections = [
      { key: 'protein_g', label: 'Protein', unit: 'g', values: [0, null, null] },
      { key: 'dietary_energy_kcal', label: 'Energie', unit: 'kcal', values: [390, 430, null] },
      { key: 'carbohydrates_g', label: 'Kohlenhydrate', unit: 'g', values: [18, 24, null] },
      { key: 'fat_g', label: 'Fett', unit: 'g', values: [12, 16, null] },
      { key: 'fiber_g', label: 'Ballaststoffe', unit: 'g', values: [4, 5, null] },
      { key: 'sugar_g', label: 'Zucker', unit: 'g', values: [6, 8, null] },
      { key: 'saturated_fat_g', label: 'Gesättigte Fettsäuren', unit: 'g', values: [2, 3, null] },
    ]
    for (const selection of selections) {
      expect(wrapper.get('h1').text()).toContain(selection.label)
      expect(wrapper.get(`time[datetime="2020-03-01"]`).element.closest('li, article, section')?.textContent)
        .toContain(`${selection.values[0]} ${selection.unit}`)
      const activeLink = wrapper.get(`a[href*="/analyse/ernaehrung/${selection.key}"][aria-current="page"]`)
      expect(activeLink.text()).toContain(selection.label)
      const chart = wrapper.getComponent({ name: 'ChartPanel' })
      const option = chart.props('option') as { xAxis: { data: string[] }; series: Array<{ data: Array<number | null> }> }
      expect(option.xAxis.data).toEqual(['2020-03-01', '2020-03-02', '2020-03-03'])
      expect(option.series[0]!.data).toEqual(selection.values)
      const next = selections[selections.indexOf(selection) + 1]
      if (next) {
        await wrapper.get(`a[href*="/analyse/ernaehrung/${next.key}"]`).trigger('click')
        await flushPromises()
        expect(router.currentRoute.value.path).toBe(`/analyse/ernaehrung/${next.key}`)
        expect(router.currentRoute.value.query).toMatchObject({
          period: 'custom', start: '2020-03-01', end: '2020-03-03',
        })
      }
    }
    expect(nutritionRequests()).toHaveLength(1)
    const params = new URLSearchParams(nutritionRequests()[0]!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2020-03-01', '2020-03-03'])
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/nutrition/')))
      .toHaveLength(1)
    await router.back()
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung/sugar_g')
    expect(router.currentRoute.value.query.start).toBe('2020-03-01')
    wrapper.unmount()
  })

  it('stacks exact protein, carbohydrate and fat readings without inventing missing macros or calorie stacks', async () => {
    const distinctSeriesColors: Record<string, string> = {
      '--chart-series-violet': '#8b5cf6',
      '--chart-series-amber': '#f6c445',
    }
    vi.stubGlobal('getComputedStyle', () => ({
      getPropertyValue: (name: string) => distinctSeriesColors[name] ?? '',
    }) as CSSStyleDeclaration)
    serveNutrition({
      ...nutritionResponse,
      days: [
        { ...nutritionResponse.days[0]!, dietary_energy_kcal: 410, protein_g: 20, carbohydrates_g: 50, fat_g: 10 },
        { ...nutritionResponse.days[2]!, date: '2020-03-02' },
        { ...nutritionResponse.days[0]!, date: '2020-03-03', dietary_energy_kcal: 180, protein_g: 0, carbohydrates_g: 25, fat_g: 8 },
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/dietary_energy_kcal${rangeQuery}`)
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { data: string[] }
      series: Array<{ name: string; stack?: string; type: string; data: (number | null)[]; itemStyle: { color: string } }>
    }
    expect(option.xAxis.data).toEqual(['2020-03-01', '2020-03-02', '2020-03-03'])
    const stacks = option.series.filter((series) => series.stack != null)
    expect(stacks.map(({ name, data }) => [name, data])).toEqual([
      ['Eiweiß', [20, null, 0]],
      ['Kohlenhydrate', [50, null, 25]],
      ['Fett', [10, null, 8]],
    ])
    expect(new Set(stacks.map(({ stack }) => stack)).size).toBe(1)
    expect(stacks.map(({ type, itemStyle }) => [type, itemStyle.color])).toEqual([
      ['bar', chartColors.value.teal],
      ['bar', chartColors.value.blue],
      ['bar', chartColors.value.amber],
    ])
    expect(option.series.filter(({ type }) => type === 'bar')).toHaveLength(3)
    expect(option.series.find(({ name }) => name === 'Energie')).toMatchObject({
      type: 'line', data: [410, null, 180], itemStyle: { color: chartColors.value.violet },
    })
    wrapper.unmount()
  })

  it('clamps an all-data nutrition detail URL to 365 days before its first reader request', async () => {
    serveNutrition()
    const { router, wrapper } = await mountAnalysis('/analyse/ernaehrung/protein_g?period=all')
    expect(nutritionRequests()).toHaveLength(1)
    const params = new URLSearchParams(nutritionRequests()[0]!.split('?')[1])
    expect(params.get('start')).toBe(shiftIsoDate(params.get('end')!, -364))
    expect(router.currentRoute.value.query.period).toBe('365')
    expect(wrapper.get('.analysis-period-trigger').text()).toContain('1 Jahr')
    wrapper.unmount()
  })
  it('accepts a 365-day custom range but rejects 366 inclusive days before requesting nutrition', async () => {
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    await wrapper.get('.analysis-period-trigger').trigger('click')
    const dates = wrapper.findAll('.analysis-period-custom-form input[type="text"]')
    await dates[0]!.setValue('01.03.2020')
    await dates[1]!.setValue('01.03.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    expect(wrapper.get('.analysis-period-error').text()).toContain('365')
    expect(nutritionRequests()).toHaveLength(1)

    await dates[1]!.setValue('28.02.2021')
    await wrapper.get('.analysis-period-custom-form button[type="submit"]').trigger('click')
    await flushPromises()
    expect(nutritionRequests()).toHaveLength(2)
    const params = new URLSearchParams(nutritionRequests()[1]!.split('?')[1])
    expect([params.get('start'), params.get('end')]).toEqual(['2020-03-01', '2021-02-28'])
    wrapper.unmount()
  })

  it('keeps the named meal on its response date, an exact-zero day, and distinct null/missing-day values', async () => {
    serveNutrition()
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    const dayTime = (date: string) => wrapper.get(`time[datetime="${date}"]`).element
    const dayGroup = (date: string) => dayTime(date).closest('li, article, section')
    expect(dayGroup('2020-03-01')?.textContent).toContain('0 g')
    expect(dayGroup('2020-03-01')?.textContent).not.toContain('Haferflocken')
    expect(dayGroup('2020-03-02')?.textContent).toContain('Haferflocken')
    expect(dayGroup('2020-03-02')?.textContent).toContain('–')
    expect(dayGroup('2020-03-02')?.textContent).not.toContain('0 g')
    expect(dayGroup('2020-03-03')?.textContent).toContain('–')
    expect(dayGroup('2020-03-03')?.textContent).not.toContain('0 g')
    expect(dayGroup('2020-03-01')?.textContent).toContain('Keine benannten Mahlzeiten verfügbar')
    expect(dayGroup('2020-03-03')?.textContent).toContain('Keine benannten Mahlzeiten verfügbar')
    expect(nutritionRequests()).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps each winning-provider meal and its nutrient value attached to the correct day', async () => {
    serveNutrition({
      ...nutritionResponse,
      selected_provider: { provider_key: 'yazio' },
      days: [
        {
          ...nutritionResponse.days[0]!,
          provider_key: 'google_health', protein_g: 12,
          meals: [{
            ...nutritionResponse.days[1]!.meals[0]!,
            provider_key: 'google_health', display_name: 'Gemüsesuppe', protein_g: 12,
          }],
        },
        {
          ...nutritionResponse.days[1]!,
          protein_g: 24,
          meals: [{
            ...nutritionResponse.days[1]!.meals[0]!, protein_g: 24,
          }],
        },
        nutritionResponse.days[2]!,
      ],
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    const dayGroup = (date: string) => wrapper.get(`time[datetime="${date}"]`).element.closest('li, article, section')
    expect(dayGroup('2020-03-01')?.textContent).toContain('12 g')
    expect(dayGroup('2020-03-01')?.textContent).toContain('Gemüsesuppe')
    expect(dayGroup('2020-03-01')?.textContent).not.toContain('Haferflocken')
    expect(dayGroup('2020-03-02')?.textContent).toContain('24 g')
    expect(dayGroup('2020-03-02')?.textContent).toContain('Haferflocken')
    expect(dayGroup('2020-03-02')?.textContent).not.toContain('Gemüsesuppe')
    expect(dayGroup('2020-03-01')?.querySelector('.nutrition-meals li')?.textContent).toContain('12 g')
    expect(dayGroup('2020-03-02')?.querySelector('.nutrition-meals li')?.textContent).toContain('24 g')
    expect(dayGroup('2020-03-03')?.textContent).not.toMatch(/Gemüsesuppe|Haferflocken/)
    expect(wrapper.text()).not.toMatch(/Mangel|Unterversorgung|Überversorgung|Überdosierung/i)
    wrapper.unmount()
  })

  it('counts only measured days for the selected nutrition metric, including a recorded zero', async () => {
    serveNutrition()
    const { router, wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    expect(wrapper.text()).toContain('1 von 3 Tagen')
    await wrapper.get('a[href*="/analyse/ernaehrung/dietary_energy_kcal"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/analyse/ernaehrung/dietary_energy_kcal')
    expect(wrapper.text()).toContain('2 von 3 Tagen')
    expect(wrapper.text()).not.toContain('1 von 3 Tagen')
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
    wrapper.unmount()
  })

  it('keeps loading, localized failure and same-period retry separate from the successful detail', async () => {
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
      if (path === '/settings/analysis-preferences') return Promise.resolve({ analysis_keys: selectedKeys })
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
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
    expect(wrapper.findAllComponents({ name: 'ChartPanel' })).toHaveLength(1)
    expect(nutritionRequests()).toHaveLength(2)
    for (const request of nutritionRequests()) {
      const params = new URLSearchParams(request.split('?')[1])
      expect([params.get('start'), params.get('end')]).toEqual(['2020-03-01', '2020-03-03'])
    }
    wrapper.unmount()
  })

  it('shows an empty API period with a complete date series and no invented meals or chart points', async () => {
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
    const { wrapper } = await mountAnalysis(`/analyse/ernaehrung/protein_g${rangeQuery}`)
    expect(wrapper.get('[role="status"]').text()).toMatch(/keine Werte|keine Daten|keine Tageswerte/i)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
    expect(wrapper.text()).not.toContain('0 g')
    expect(wrapper.text()).not.toContain('Keine benannten Mahlzeiten verfügbar')
    wrapper.unmount()
  })
})
