import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Die Analyse konnte nicht gespeichert werden.'),
}))

import AnalysisActivityView from '../src/views/AnalysisActivityView.vue'
import AnalysisCatalogView from '../src/views/AnalysisCatalogView.vue'
import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisOverviewView from '../src/views/AnalysisOverviewView.vue'
import AnalysisWeightDetailView from '../src/views/AnalysisWeightDetailView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { setThemePreference } from '../src/theme'

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
    global: { plugins: [pinia, router], stubs: { ChartPanel: true } },
  })
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
    expect(weightCard.get('.analysis-card-description').text()).toBe('Actual weight measurements in the selected period.')
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
    const sevenDay = wrapper.findAll('button').find((button) => button.text() === '7 Tage')
    expect(sevenDay).toBeDefined()
    await sevenDay!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.period).toBe('7')
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
              { date: dateAtOffset(1), provider_key: 'yazio', value: null },
              { date: dateAtOffset(2), provider_key: 'yazio', value: 20 },
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
    expect(Number(circles[1].attributes('cx')) - Number(circles[0].attributes('cx'))).toBeCloseTo(40)
    const seriesRequests = apiMock.mock.calls.flatMap(([path]) => {
      if (!String(path).startsWith('/analytics/nutrition/micronutrients')) return []
      const key = new URLSearchParams(String(path).split('?')[1] ?? '').get('metric_key')
      return key ? [key] : []
    })
    expect(seriesRequests).toEqual(['vitamin_a_ug'])
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
    expect(card.find('.analysis-mini-chart--violet').exists()).toBe(true)
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
    const pointerUp = new Event('pointerup')
    Object.defineProperty(pointerUp, 'pointerId', { value: 24 })
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

  it('keeps the drag handle before card content and the overflow control on the right', async () => {
    const { wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    const card = wrapper.get('[data-analysis-card="weight_kg"]')

    expect(Array.from(card.element.children).map((element) => element.className)).toEqual([
      'analysis-drag-handle',
      'analysis-card-content',
      'analysis-card-tools',
    ])
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
      { path: '/analyse/aktivitaet/aktive-energie', key: 'active_energy_kcal', seriesColor: '#6d28d9' },
      { path: '/analyse/koerper/gewicht', key: 'weight_kg', seriesColor: '#6d28d9' },
      { path: '/analyse/mikronaehrstoffe/vitamin_a_ug', key: 'vitamin_a_ug', seriesColor: '#7446af' },
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
        series: Array<{ name: string; type: string; connectNulls?: boolean; data: unknown[]; lineStyle?: { color: string }; itemStyle?: { color: string } }>
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
        .toEqual(before.series.map((series) => ({ name: series.name, type: series.type, connectNulls: series.connectNulls, data: series.data })))
      const seriesColor = after.series[0]!.type === 'line'
        ? after.series[0]!.lineStyle?.color
        : after.series[0]!.itemStyle?.color
      expect(seriesColor).toBe(chartCase.seriesColor)
      expect(apiMock.mock.calls).toHaveLength(apiCallCount)
      wrapper.unmount()
      setThemePreference('dark')
    }
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
    expect(wrapper.get('.page-heading').find('.weight-period').exists()).toBe(true)
    expect(wrapper.get('.weight-summary-primary h2').text()).toBe('Letzte Messung')
    const primaryMeta = wrapper.get('.weight-summary-meta').text()
    expect(primaryMeta.indexOf('Veränderung')).toBeLessThan(primaryMeta.indexOf('2 Messungen'))
    expect(wrapper.get('.weight-summary-secondary').text()).toContain('Durchschnitt')
    expect(wrapper.get('.weight-reading-list h2').text()).toBe('Messwerte')
    expect(wrapper.find('[data-coverage-status]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('69,5 kg am 2. März')
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    expect(chart.exists()).toBe(true)

    const option = chart.props('option') as {
      xAxis: { type: string }
      series: Array<{ type: string; data: Array<[number, number]>; lineStyle?: object }>
    }
    expect(option.xAxis.type).toBe('time')
    expect(option.series).toHaveLength(1)
    expect(option.series[0]!.type).toBe('scatter')
    expect(option.series[0]!.lineStyle).toBeUndefined()
    expect(option.series[0]!.data).toEqual([
      [Date.parse('2026-03-01T00:00:00Z'), 70],
      [Date.parse('2026-03-29T00:00:00Z'), 69.5],
    ])
    wrapper.unmount()
  })

  it('shows an explicit no-data state for a canonical micronutrient detail', async () => {
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(wrapper.findAll('.page-heading .analytics-period-filter')).toHaveLength(1)
    expect(wrapper.findAll('.micronutrient-empty')).toHaveLength(1)
    expect(wrapper.findAll('.micronutrient-summary')).toHaveLength(0)
    expect(wrapper.findComponent({ name: 'ChartPanel' }).exists()).toBe(false)
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
    expect(wrapper.get('.page-heading').find('.activity-period').exists()).toBe(true)
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
})
