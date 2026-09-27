import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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

const registry = [
  { analysis_key: 'weight_kg', category_key: 'body', domain_key: 'weight_kg', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.weight', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'active_energy_kcal', category_key: 'activity', domain_key: 'active_energy_kcal', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.verification.activity', period_capability: { min_days: 1, max_days: 3661, custom_range: true } },
  { analysis_key: 'vitamin_a_ug', category_key: 'micronutrients', domain_key: 'vitamin_a_ug', status: 'available', card_capable: true, detail_capable: true, reader_key: 'analytics.nutrition.micronutrients', period_capability: { min_days: 1, max_days: 31, custom_range: true } },
  { analysis_key: 'protein_g', category_key: 'nutrition', domain_key: 'protein_g', status: 'coming_soon', card_capable: false, detail_capable: false, reader_key: null, period_capability: null },
  { analysis_key: 'water_intake_ml', category_key: 'fluid', domain_key: 'water_intake_ml', status: 'coming_soon', card_capable: false, detail_capable: false, reader_key: null, period_capability: null },
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
      return Promise.resolve({ start_date: '2026-03-25', end_date: '2026-03-31', selected_provider: { provider_key: 'yazio' }, period_days: 7, metrics: [{ metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug', eu_nrv: 800, total: null, days_with_data: 0, contributors: [] }], series: [] })
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

describe('Analysis V2 hub and catalogue', () => {
  it('starts empty, keeps supported no-data metrics selectable, and marks unsupported keys disabled', async () => {
    const { router, wrapper } = await mountAnalysis('/analyse')
    expect(wrapper.text()).toContain('Noch keine Analysen ausgewählt')
    expect(wrapper.findAll('.analysis-card')).toHaveLength(0)

    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.text()).toContain('Noch keine Werte im Zeitraum')
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
  it('localizes analysis card labels, descriptions, and actions for English users', async () => {
    setLocale('en')
    const { router, wrapper } = await mountAnalysis('/analyse', ['weight_kg'])
    expect(wrapper.get('#analysis-existing-views-title').text()).toBe('Other existing analyses')

    const weightCard = wrapper.get('[data-analysis-key="weight_kg"]')
    expect(weightCard.get('.analysis-card-heading').text()).toBe('Weight')
    expect(weightCard.get('.analysis-card-description').text()).toBe('Actual weight measurements in the selected period.')
    expect(weightCard.find('[aria-label="Move Weight forward"]').exists()).toBe(true)

    await router.push('/analyse/katalog')
    await flushPromises()
    expect(wrapper.get('[data-analysis-key="weight_kg"]').text()).toContain('Weight')
    expect(wrapper.get('[data-analysis-key="weight_kg"]').text()).toContain('Actual weight measurements in the selected period.')
    expect(wrapper.get('[data-analysis-add="weight_kg"]').attributes('aria-label')).toBe('Remove Weight from analyses')
    wrapper.unmount()
  })
  it('keeps direct links to existing analyses visible when workspace loading fails', async () => {
    registryFailuresRemaining = 1
    const { wrapper } = await mountAnalysis('/analyse')

    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.find('[aria-labelledby="analysis-existing-views-title"]').exists()).toBe(true)
    expect(wrapper.findAll('.analysis-legacy-links a').map((link) => link.attributes('href'))).toEqual([
      '/ernaehrung',
      '/aktivitaet',
      '/gewicht',
      '/trends',
      '/mikronaehrstoffe',
    ])
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
})

describe('Analysis V2 detail views', () => {
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
    expect(wrapper.text()).not.toContain('69,5 kg am 2. März')
    const chart = wrapper.findComponent({ name: 'ChartPanel' })
    if (chart.exists()) {
      const option = chart.props('option') as { series: { data: unknown[] }[] }
      expect(option.series[0].data).toHaveLength(2)
    }
    wrapper.unmount()
  })

  it('shows an explicit no-data state for a canonical micronutrient detail', async () => {
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(wrapper.text()).toContain('Für diesen Zeitraum liegen keine Werte vor')
    expect(wrapper.text()).not.toContain('0 µg')
    wrapper.unmount()
  })

  it('reads active energy only from canonical days and leaves missing values as gaps', async () => {
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/verification/activity')
      ? Promise.resolve({
        start_date: '2026-03-25', end_date: '2026-03-31', view: 'canonical',
        days: [
          { date: '2026-03-25', canonical: { provider_key: 'google_health', status: 'available', active_energy_kcal: 0, record_count: 1, source_types: [] }, providers: [] },
          { date: '2026-03-26', canonical: null, providers: [] },
        ],
      })
      : Promise.resolve({ analyses: registry, analysis_keys: [] }))
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet/aktive-energie')
    expect(wrapper.text()).toContain('1 von 7 Tagen')
    expect(wrapper.text()).not.toContain('Google Health')
    wrapper.unmount()
  })
})
