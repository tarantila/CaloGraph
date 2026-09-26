import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Provider nicht verfügbar'),
}))

import AnalysisNutritionView from '../src/views/AnalysisNutritionView.vue'
import AnalysisOverviewView from '../src/views/AnalysisOverviewView.vue'
import ChartPanel from '../src/components/ChartPanel.vue'
import { isoDateInTimeZone, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisMicronutrientsView from '../src/views/AnalysisMicronutrientsView.vue'
import AnalysisActivityView from '../src/views/AnalysisActivityView.vue'
import AnalysisBodyView from '../src/views/AnalysisBodyView.vue'
import type { AnalysisMicronutrientResponse, DailyPoint } from '../src/types'


function setUser() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = {
    id: 'analysis-user',
    username: 'owner',
    language: 'de',
    timezone: 'Europe/Berlin',
    week_starts_on: 0,
    raw_payload_retention_days: 0,
    is_admin: false,
    is_active: true,
    deactivated_at: null,
  }
  return pinia
}

async function mountAnalysis(path: string, pinia = setUser()) {
  setLocale('de')
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/analyse',
      component: { template: '<RouterView />' },
      children: [
        { path: '', name: 'analysis-overview', component: AnalysisOverviewView },
        { path: 'ernaehrung', name: 'analysis-nutrition', component: AnalysisNutritionView },
        { path: 'mikronaehrstoffe', name: 'analysis-micronutrients', component: AnalysisMicronutrientsView },
        { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: AnalysisMicronutrientDetailView },
        { path: 'aktivitaet', name: 'analysis-activity', component: AnalysisActivityView },
        { path: 'koerper', name: 'analysis-body', component: AnalysisBodyView },
      ],
    }],
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

const emptyMicronutrients = {
  start_date: '2026-03-31',
  end_date: '2026-03-31',
  selected_provider: { provider_key: 'yazio' },
  period_days: 1,
  metrics: [],
  series: [],
}
const nutritionPoints = [
  { date: '2026-03-30', calories_kcal: null, protein_g: null, carbs_g: null, fat_g: null },
  { date: '2026-03-31', calories_kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0 },
]

beforeEach(() => {
  setLocale('de')
  setUser()
  apiMock.mockReset()
  apiMock.mockImplementation((path: string) => {
    if (path.startsWith('/analytics/nutrition/micronutrients')) return Promise.resolve(emptyMicronutrients)
    if (path.startsWith('/analytics/trends')) return Promise.resolve({ points: nutritionPoints, budget_balance: null })
    if (path.startsWith('/analytics/verification/activity')) {
      return Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', view: 'canonical', days: [] })
    }
    if (path.startsWith('/analytics/weight')) {
      return Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', selected_provider: null, points: [] })
    }
    return Promise.resolve({})
  })
})

describe('Analysis nutrition and overview', () => {
  it('gates period trends on canonical readiness and preserves missing gaps and explicit zero', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-03-31T12:00:00Z'))
    try {
      const { wrapper } = await mountAnalysis('/analyse/ernaehrung')
      const calls = apiMock.mock.calls.map(([path]) => path as string)
      const gateIndex = calls.findIndex((path) => path.startsWith('/analytics/nutrition/micronutrients'))
      const trendsIndex = calls.findIndex((path) => path.startsWith('/analytics/trends'))
      expect(gateIndex).toBeGreaterThanOrEqual(0)
      expect(trendsIndex).toBeGreaterThan(gateIndex)
      expect(calls[gateIndex]).toContain(`start=${isoDateInTimeZone('Europe/Berlin')}`)
      expect(calls[gateIndex]).toContain(`end=${isoDateInTimeZone('Europe/Berlin')}`)

      const chart = wrapper.findComponent(ChartPanel)
      const option = chart.props('option') as { series: { name: string; data: (number | null)[] }[] }
      expect(option.series.map((series) => series.name)).toHaveLength(4)
      expect(option.series[0].data).toEqual([null, 0])
      expect(wrapper.text()).toContain('1 von 2 Tagen')
      expect(wrapper.text()).not.toContain('Körperfett')

      await wrapper.findAll('.analytics-period-button').find((button) => button.text() === '7 Tage')!.trigger('click')
      await flushPromises()
      const today = isoDateInTimeZone('Europe/Berlin')
      expect(apiMock.mock.calls.some(([path]) => path === `/analytics/trends?start=${shiftIsoDate(today, -6)}&end=${today}`)).toBe(true)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
  it('aligns Overview and Nutrition coverage for decimal-string canonical trends', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-03-31T12:00:00Z'))
    const today = isoDateInTimeZone('Europe/Berlin')
    const start = shiftIsoDate(today, -29)
    const points = Array.from({ length: 30 }, (_, index) => ({
      date: shiftIsoDate(start, index),
      calories_kcal: index === 29 ? '123.45' : null,
      protein_g: index === 29 ? '12.5' : null,
      carbs_g: index === 29 ? '0' : null,
      fat_g: null,
    }))
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        return Promise.resolve({ ...emptyMicronutrients, selected_provider: { provider_key: 'yazio' } })
      }
      if (path.startsWith('/analytics/trends')) return Promise.resolve({ points })
      return Promise.resolve({})
    })

    const overview = await mountAnalysis('/analyse')
    expect(overview.wrapper.get('a[href="/analyse/ernaehrung"] strong').text())
      .toBe('1 von 30 Tagen mit Werten')
    const overviewTrendCall = apiMock.mock.calls
      .map(([path]) => path as string)
      .find((path) => path.startsWith('/analytics/trends'))
    overview.wrapper.unmount()
    apiMock.mockClear()

    const nutrition = await mountAnalysis('/analyse/ernaehrung')
    const nutritionTrendCall = apiMock.mock.calls
      .map(([path]) => path as string)
      .find((path) => path.startsWith('/analytics/trends'))
    expect(nutritionTrendCall).toBe(overviewTrendCall)
    expect(nutrition.wrapper.text()).toContain('1 von 30 Tagen mit Einträgen')
    expect(nutrition.wrapper.get('.analysis-summary-card strong').text()).toBe('123,5 kcal')
    const chart = nutrition.wrapper.findComponent(ChartPanel)
    const option = chart.props('option') as { series: { data: (number | null)[] }[] }
    expect(option.series[0].data).toHaveLength(30)
    expect(option.series[0].data[29]).toBe(123.45)
    expect(option.series[1].data[29]).toBe(12.5)
    expect(option.series[2].data[29]).toBe(0)
    expect(option.series[3].data[29]).toBeNull()
    nutrition.wrapper.unmount()
  })


  it('shows a retryable provider error instead of empty data and does not call legacy-capable trends', async () => {
    apiMock.mockRejectedValueOnce(new Error('provider unavailable'))
    const { wrapper } = await mountAnalysis('/analyse/ernaehrung')

    expect(wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
    expect(wrapper.text()).not.toContain('Keine Daten')
    expect(apiMock.mock.calls.some(([path]) => (path as string).startsWith('/analytics/trends'))).toBe(false)

    await wrapper.get('.analysis-retry').trigger('click')
    await flushPromises()
    expect(apiMock.mock.calls.some(([path]) => (path as string).startsWith('/analytics/trends'))).toBe(true)
    wrapper.unmount()
  })

  it('ignores an earlier nutrition range response that arrives after the latest request', async () => {
    const firstGate = Promise.withResolvers<AnalysisMicronutrientResponse>()
    let gateRequests = 0
    let trendRequests = 0
    const currentPoints = [{ date: '2026-03-31', calories_kcal: 2, protein_g: 2, carbs_g: 2, fat_g: 2 }]
    const stalePoints = [{ date: '2026-03-31', calories_kcal: 900, protein_g: 900, carbs_g: 900, fat_g: 900 }]
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        gateRequests += 1
        return gateRequests === 1 ? firstGate.promise : Promise.resolve(emptyMicronutrients)
      }
      if (path.startsWith('/analytics/trends')) {
        trendRequests += 1
        return Promise.resolve({ points: trendRequests === 1 ? currentPoints : stalePoints })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse/ernaehrung')

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === '7 Tage')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('2 kcal')

    firstGate.resolve(emptyMicronutrients)
    await flushPromises()

    expect(wrapper.text()).toContain('2 kcal')
    expect(wrapper.text()).not.toContain('900 kcal')
    wrapper.unmount()
  })

  it('does not let a stale micronutrient catalog range replace the latest response', async () => {
    const firstResponse = Promise.withResolvers<AnalysisMicronutrientResponse>()
    let requests = 0
    const responseWithTotal = (total: number): AnalysisMicronutrientResponse => ({
      ...emptyMicronutrients,
      metrics: [{
        metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin',
        unit: 'ug', eu_nrv: 800, total, days_with_data: 1,
        contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
      }],
    })
    apiMock.mockImplementation((path: string) => {
      if (!path.startsWith('/analytics/nutrition/micronutrients')) return Promise.resolve({})
      requests += 1
      return requests === 1 ? firstResponse.promise : Promise.resolve(responseWithTotal(2))
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe')

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === '7 Tage')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('2 ug')

    firstResponse.resolve(responseWithTotal(900))
    await flushPromises()

    expect(wrapper.text()).toContain('2 ug')
    expect(wrapper.text()).not.toContain('900 ug')
    wrapper.unmount()
  })

  it('does not let a stale detail range replace the latest daily series', async () => {
    const firstResponse = Promise.withResolvers<AnalysisMicronutrientResponse>()
    let requests = 0
    function detailResponse(value: number): AnalysisMicronutrientResponse {
      return {
        start_date: '2026-03-31', end_date: '2026-03-31',
        selected_provider: { provider_key: 'yazio' }, period_days: 1,
        metrics: [{
          metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin',
          unit: 'ug', eu_nrv: 800, total: value, days_with_data: 1,
          contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
        }],
        series: [{ date: '2026-03-31', provider_key: 'yazio', value }],
      }
    }
    apiMock.mockImplementation((path: string) => {
      if (!path.startsWith('/analytics/nutrition/micronutrients')) return Promise.resolve({})
      requests += 1
      return requests === 1 ? firstResponse.promise : Promise.resolve(detailResponse(2))
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?start=2026-03-29&end=2026-03-31')

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === '7 Tage')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('2 ug')

    firstResponse.resolve(detailResponse(900))
    await flushPromises()

    expect(wrapper.text()).toContain('2 ug')
    expect(wrapper.text()).not.toContain('900 ug')
    wrapper.unmount()
  })

  it('preserves small nonzero nutrient values in catalog and detail displays', async () => {
    const response: AnalysisMicronutrientResponse = {
      ...emptyMicronutrients,
      metrics: [{
        metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug',
        eu_nrv: 800, total: 0.04, days_with_data: 1,
        contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
      }],
      series: [{ date: '2026-03-31', provider_key: 'yazio', value: 0.04 }],
    }
    apiMock.mockResolvedValue(response)
    const catalog = await mountAnalysis('/analyse/mikronaehrstoffe')

    expect(catalog.wrapper.text()).toContain('0,04 ug')
    catalog.wrapper.unmount()

    const detail = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(detail.wrapper.text()).toContain('0,04 ug')
    detail.wrapper.unmount()
  })

  it('shows micronutrient readiness while nutrition trends are still pending', async () => {
    const trendsResponse = Promise.withResolvers<{ points: DailyPoint[] }>()
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        return Promise.resolve({
          ...emptyMicronutrients,
          metrics: [{
            metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug',
            eu_nrv: 800, total: 1, days_with_data: 1, contributors: [],
          }],
        })
      }
      if (path.startsWith('/analytics/trends')) return trendsResponse.promise
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', view: 'canonical', days: [] })
      }
      if (path.startsWith('/analytics/weight')) {
        return Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', selected_provider: null, points: [] })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse')

    expect(wrapper.get('a[href="/analyse/mikronaehrstoffe"] strong').text()).toContain('1 von 1')
    expect(wrapper.get('a[href="/analyse/ernaehrung"] strong').text()).toBe('Wird geladen …')

    trendsResponse.resolve({ points: [] })
    await flushPromises()
    wrapper.unmount()
  })
  it('shows four data-backed overview areas and keeps other domains visible when nutrition is unavailable', async () => {
    apiMock.mockRejectedValueOnce(new Error('provider unavailable'))
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/verification/activity')) {
        return Promise.resolve({
          start_date: '2026-03-02', end_date: '2026-03-31', view: 'canonical',
          days: [{ date: '2026-03-31', canonical: {
            provider_key: 'yazio', status: 'available', active_energy_kcal: 400,
            record_count: 1, source_types: ['yazio_export_v1'],
          }, providers: [] }],
        })
      }
      if (path.startsWith('/analytics/weight')) {
        return Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', selected_provider: null,
          points: [{ date: '2026-03-29', weight_kg: 70.2 }] })
      }
      return Promise.reject(new Error('unexpected trend request'))
    })
    const { wrapper } = await mountAnalysis('/analyse')

    expect(wrapper.findAll('.analysis-domain-link')).toHaveLength(4)
    expect(wrapper.get('.analysis-domain-link[href="/analyse/ernaehrung"]').text()).toContain('Ernährung')
    expect(wrapper.text()).toContain('1 von 30 Tagen mit Werten')
    expect(wrapper.text()).not.toContain('400')
    expect(wrapper.text()).not.toContain('70,2')
    expect(wrapper.text()).toContain('Provider nicht verfügbar')
    expect(apiMock.mock.calls.some(([path]) => (path as string).startsWith('/analytics/trends'))).toBe(false)
    wrapper.unmount()
  })
  it('retries overview activity and body failures independently', async () => {
    let activityRequests = 0
    let bodyRequests = 0
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) return Promise.resolve(emptyMicronutrients)
      if (path.startsWith('/analytics/trends')) return Promise.resolve({ points: [] })
      if (path.startsWith('/analytics/verification/activity')) {
        activityRequests += 1
        return activityRequests === 1
          ? Promise.reject(new Error('activity unavailable'))
          : Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', view: 'canonical', days: [] })
      }
      if (path.startsWith('/analytics/weight')) {
        bodyRequests += 1
        return bodyRequests === 1
          ? Promise.reject(new Error('weight unavailable'))
          : Promise.resolve({ start_date: '2026-03-02', end_date: '2026-03-31', selected_provider: null, points: [] })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse')

    expect(wrapper.find('[data-analysis-retry=\"activity\"]').exists()).toBe(true)
    expect(wrapper.find('[data-analysis-retry=\"body\"]').exists()).toBe(true)
    await wrapper.get('[data-analysis-retry=\"activity\"]').trigger('click')
    await flushPromises()
    expect(activityRequests).toBe(2)
    expect(wrapper.find('[data-analysis-retry=\"activity\"]').exists()).toBe(false)
    expect(wrapper.find('[data-analysis-retry=\"body\"]').exists()).toBe(true)

    await wrapper.get('[data-analysis-retry=\"body\"]').trigger('click')
    await flushPromises()
    expect(bodyRequests).toBe(2)
    expect(wrapper.find('[data-analysis-retry=\"body\"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
describe('Analysis micronutrient pages', () => {
  it('groups evidence-bearing catalog entries, keeps explicit zero and hides missing entries', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        return Promise.resolve({
          ...emptyMicronutrients,
          period_days: 30,
          metrics: [
            { metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug',
              eu_nrv: 800, total: 0, days_with_data: 1,
              contributors: [{ provider_key: 'google_health', days_with_data: 1 }] },
            { metric_key: 'iron_mg', label: 'Eisen', category: 'mineral', unit: 'mg',
              eu_nrv: 14, total: null, days_with_data: 0, contributors: [] },
            { metric_key: 'magnesium_mg', label: 'Magnesium', category: 'mineral', unit: 'mg',
              eu_nrv: 375, total: 10, days_with_data: 2,
              contributors: [{ provider_key: 'yazio', days_with_data: 2 }] },
          ],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe')

    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.text()).toContain('Magnesium')
    expect(wrapper.text()).not.toContain('Eisen')
    expect(wrapper.findAll('.micronutrient-group')).toHaveLength(2)
    expect(wrapper.find('a[href^="/analyse/mikronaehrstoffe/vitamin_a_ug"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('%')
    expect(apiMock.mock.calls.some(([path]) => (path as string).startsWith('/analytics/micronutrients'))).toBe(false)
    wrapper.unmount()
  })

  it('keeps both catalog categories and shows an empty-category state', async () => {
    apiMock.mockImplementation((path: string) => path.startsWith('/analytics/nutrition/micronutrients')
      ? Promise.resolve({
        ...emptyMicronutrients,
        period_days: 30,
        metrics: [{
          metric_key: 'iron_mg', label: 'Eisen', category: 'mineral', unit: 'mg',
          eu_nrv: 14, total: 2, days_with_data: 1, contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
        }],
      })
      : Promise.resolve({}))
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe')
    expect(wrapper.findAll('.micronutrient-group')).toHaveLength(2)
    expect(wrapper.findAll('.micronutrient-group h2').map((heading) => heading.text())).toEqual([
      'Vitamine', 'Mineralstoffe',
    ])
    expect(wrapper.get('.micronutrient-group-empty').text()).toContain('Keine Werte in dieser Gruppe')
    expect(wrapper.get('.micronutrient-group:last-child h2').text()).toBe('Mineralstoffe')
    wrapper.unmount()
  })

  it('distinguishes a readable empty catalog from a retryable provider failure', async () => {
    apiMock.mockRejectedValueOnce(new Error('provider unavailable'))
    const failed = await mountAnalysis('/analyse/mikronaehrstoffe')
    expect(failed.wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
    expect(failed.wrapper.text()).not.toContain('Für diesen Zeitraum liegen keine Mikronährstoffwerte vor.')
    await failed.wrapper.get('.analysis-retry').trigger('click')
    await flushPromises()
    expect(failed.wrapper.text()).toContain('Für diesen Zeitraum liegen keine Mikronährstoffwerte vor.')
    expect(failed.wrapper.findAll('.micronutrient-group')).toHaveLength(0)
    failed.wrapper.unmount()
  })

  it('renders deep-linked daily values and actual per-day provider provenance', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/nutrition/micronutrients')) {
        return Promise.resolve({
          start_date: '2026-03-29',
          end_date: '2026-03-31',
          selected_provider: { provider_key: 'google_health' },
          period_days: 3,
          metrics: [{
            metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug',
            eu_nrv: 800, total: 900, days_with_data: 2,
            contributors: [
              { provider_key: 'google_health', days_with_data: 1 },
              { provider_key: 'yazio', days_with_data: 1 },
            ],
          }],
          series: [
            { date: '2026-03-29', provider_key: 'google_health', value: null },
            { date: '2026-03-30', provider_key: 'google_health', value: 0 },
            { date: '2026-03-31', provider_key: 'yazio', value: 900 },
          ],
        })
      }
      return Promise.resolve({})
    })
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?start=2026-03-29&end=2026-03-31')
    const calls = apiMock.mock.calls.map(([path]) => path as string)
    expect(calls.some((path) => path.includes('metric_key=vitamin_a_ug'))).toBe(true)
    expect(wrapper.text()).toContain('Vitamin A')
    expect(wrapper.text()).toContain('0 ug')
    expect(wrapper.text()).toContain('900 ug')
    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('YAZIO')
    expect(wrapper.text()).toContain('2 von 3 Tagen')
    expect(wrapper.text()).toContain('–')
    const chart = wrapper.findComponent(ChartPanel)
    const option = chart.props('option') as { series: { data: (number | null)[] }[] }
    expect(option.series[0].data).toEqual([null, 0, 900])
    expect(wrapper.find('ul[aria-label="Tägliche Werte"]').exists()).toBe(true)
    expect(calls.some((path) => path.startsWith('/analytics/micronutrients'))).toBe(false)
    wrapper.unmount()
  })

  it('refetches on a changed metric and clears stale details after a noncatalog error', async () => {
    apiMock.mockImplementation((path: string) => {
      if (!path.startsWith('/analytics/nutrition/micronutrients')) return Promise.resolve({})
      if (path.includes('metric_key=iron_mg')) return Promise.reject(new Error('not catalogued'))
      const metricKey = path.includes('metric_key=vitamin_a_ug') ? 'vitamin_a_ug' : 'iron_mg'
      return Promise.resolve({
        start_date: '2026-03-31', end_date: '2026-03-31',
        selected_provider: { provider_key: 'yazio' }, period_days: 1,
        metrics: [{
          metric_key: metricKey, label: metricKey === 'vitamin_a_ug' ? 'Vitamin A' : 'Eisen',
          category: metricKey === 'vitamin_a_ug' ? 'vitamin' : 'mineral', unit: 'mg',
          eu_nrv: null, total: 1, days_with_data: 1, contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
        }],
        series: [{ date: '2026-03-31', provider_key: 'yazio', value: 1 }],
      })
    })
    const { router, wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug')
    expect(wrapper.text()).toContain('Vitamin A')
    await router.push('/analyse/mikronaehrstoffe/iron_mg')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
    expect(wrapper.text()).not.toContain('Vitamin A')
    expect(apiMock.mock.calls.some(([path]) => (path as string).includes('metric_key=iron_mg'))).toBe(true)
    wrapper.unmount()
  })
})
afterEach(() => { vi.useRealTimers() })
describe('Analysis activity and body pages', () => {
  it('uses only the canonical activity record and counts an explicit zero', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-03-31T12:00:00Z'))
    apiMock.mockResolvedValue({
      start_date: '2026-03-02',
      end_date: '2026-03-31',
      view: 'canonical',
      days: [
        {
          date: '2026-03-30',
          canonical: null,
          providers: [{
            provider_key: 'yazio', status: 'available', active_energy_kcal: 400,
            record_count: 1, source_types: ['yazio_export_v1'],
          }],
        },
        {
          date: '2026-03-31',
          canonical: {
            provider_key: 'google_health', status: 'available', active_energy_kcal: 0,
            record_count: 1, source_types: ['google_health_activity_v4'],
          },
          providers: [],
        },
      ],
    })
    const { wrapper } = await mountAnalysis('/analyse/aktivitaet')
    const call = apiMock.mock.calls.find(([path]) => (path as string).startsWith('/analytics/verification/activity'))?.[0] as string
    const today = isoDateInTimeZone('Europe/Berlin')
    expect(call).toContain('view=canonical')
    expect(call).toContain(`start=${shiftIsoDate(today, -29)}`)
    expect(call).toContain(`end=${today}`)
    expect(wrapper.text()).toContain('1 von 30 Tagen mit Werten')
    expect(wrapper.text()).toContain('31.03.2026')
    expect(wrapper.text()).toContain('0 kcal')
    expect(wrapper.text()).not.toContain('400')
    expect(wrapper.text()).not.toContain('Körperfett')
    wrapper.unmount()
  })

  it('shows a real empty state and a retry control on activity errors', async () => {
    apiMock.mockResolvedValueOnce({
      start_date: '2026-03-02', end_date: '2026-03-31', view: 'canonical', days: [],
    })
    const empty = await mountAnalysis('/analyse/aktivitaet')
    expect(empty.wrapper.text()).toContain('Keine Aktivitätswerte für diesen Zeitraum.')
    empty.wrapper.unmount()

    apiMock.mockRejectedValueOnce(new Error('activity unavailable'))
    const failed = await mountAnalysis('/analyse/aktivitaet')
    expect(failed.wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
    expect(failed.wrapper.find('.analysis-retry').exists()).toBe(true)
    failed.wrapper.unmount()
  })

  it('shows latest actual weight and distinct-date coverage without derived body composition', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-03-31T12:00:00Z'))
    apiMock.mockResolvedValue({
      start_date: '2026-03-02',
      end_date: '2026-03-31',
      selected_provider: { provider_key: 'yazio' },
      points: [
        { date: '2026-03-29', weight_kg: 70.2 },
        { date: '2026-03-31', weight_kg: 69.8 },
      ],
    })
    const { wrapper } = await mountAnalysis('/analyse/koerper')
    expect(wrapper.text()).toContain('2 von 30 Tagen mit Werten')
    expect(wrapper.text()).toContain('29.03.2026')
    expect(wrapper.text()).toContain('31.03.2026')
    expect(wrapper.text()).toContain('69,8 kg')
    expect(wrapper.text()).not.toContain('Körperfett')
    expect(apiMock.mock.calls.some(([path]) => (path as string).startsWith('/analytics/weight?start='))).toBe(true)
    wrapper.unmount()
  })

  it('shows empty body data distinctly and offers retry after a request error', async () => {
    apiMock.mockResolvedValueOnce({
      start_date: '2026-03-02', end_date: '2026-03-31', selected_provider: null, points: [],
    })
    const empty = await mountAnalysis('/analyse/koerper')
    expect(empty.wrapper.text()).toContain('Keine Gewichtswerte für diesen Zeitraum.')
    empty.wrapper.unmount()

    apiMock.mockRejectedValueOnce(new Error('weight unavailable'))
    const failed = await mountAnalysis('/analyse/koerper')
    expect(failed.wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
    expect(failed.wrapper.find('.analysis-retry').exists()).toBe(true)
    failed.wrapper.unmount()
  })
})
