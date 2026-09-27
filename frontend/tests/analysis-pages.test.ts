import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Provider nicht verfügbar'),
}))

import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import ChartPanel from '../src/components/ChartPanel.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import type { AnalysisMicronutrientResponse } from '../src/types'

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

async function mountAnalysis(path: string) {
  setLocale('de')
  const pinia = setUser()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/analyse',
      component: AnalysisLayout,
      children: [
        { path: '', name: 'analysis-overview', component: { template: '<h1>Analyse</h1>' } },
        { path: 'katalog', name: 'analysis-catalog', component: { template: '<h1>Katalog</h1>' } },
        { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: AnalysisMicronutrientDetailView },
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
  return { wrapper, router }
}

const micronutrientResponse: AnalysisMicronutrientResponse = {
  start_date: '2026-03-01',
  end_date: '2026-03-31',
  selected_provider: { provider_key: 'google_health' },
  period_days: 31,
  metrics: [{
    metric_key: 'vitamin_a_ug', label: 'Vitamin A', category: 'vitamin', unit: 'ug',
    eu_nrv: 800, total: 900, days_with_data: 2,
    contributors: [
      { provider_key: 'google_health', days_with_data: 1 },
      { provider_key: 'yazio', days_with_data: 1 },
    ],
  }],
  series: [
    { date: '2026-03-01', provider_key: 'google_health', value: null },
    { date: '2026-03-31', provider_key: 'yazio', value: 0 },
  ],
}

beforeEach(() => {
  setLocale('de')
  setUser()
  apiMock.mockReset()
  apiMock.mockResolvedValue(micronutrientResponse)
})

describe('Analysis micronutrient detail', () => {
  it('preserves the inclusive 31-day range, actual zero readings, and per-day provenance', async () => {
    const { wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?start=2026-03-01&end=2026-03-31',
    )

    const request = apiMock.mock.calls.find(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))?.[0] as string
    expect(request).toContain('start=2026-03-01')
    expect(request).toContain('end=2026-03-31')
    expect(request).toContain('metric_key=vitamin_a_ug')
    expect(wrapper.text()).toContain('0 ug')
    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('YAZIO')
    expect(wrapper.text()).toContain('2 von 31 Tagen')
    expect(wrapper.find('ul[aria-label="Tägliche Werte"]').exists()).toBe(true)
    expect(wrapper.findComponent(ChartPanel).exists()).toBe(true)
    wrapper.unmount()
  })

  it('rejects a range longer than 31 inclusive days before reading data', async () => {
    const { wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?start=2026-02-28&end=2026-03-31',
    )

    expect(wrapper.get('[role="alert"]').text()).toContain('31')
    expect(apiMock.mock.calls.some(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients'))).toBe(false)
    wrapper.unmount()
  })
})
