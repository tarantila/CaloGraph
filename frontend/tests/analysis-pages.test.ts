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
        { path: 'mikronaehrstoffe', name: 'analysis-category-micronutrients', component: { template: '<h1>Mikronährstoffe</h1>' } },
        { path: 'mikronaehrstoffe/:metric', name: 'analysis-micronutrient-detail', component: AnalysisMicronutrientDetailView },
      ],
    }],
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
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2026-03-01&end=2026-03-31',
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
    expect(wrapper.text()).not.toMatch(/Mangel|Unterversorgung|Überversorgung|Überdosierung/i)
    wrapper.unmount()
  })

  it('plots every calendar date with a real null break, exact zero and sourced daily reading', async () => {
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      start_date: '2026-03-28',
      end_date: '2026-03-31',
      period_days: 4,
      metrics: [{
        ...micronutrientResponse.metrics[0]!,
        total: 12, days_with_data: 3,
      }],
      series: [
        { date: '2026-03-28', provider_key: 'google_health', value: 4 },
        { date: '2026-03-30', provider_key: 'yazio', value: 0 },
        { date: '2026-03-31', provider_key: 'google_health', value: 8 },
      ],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2026-03-28&end=2026-03-31',
    )
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { type: string; data: string[] }
      series: Array<{ type: string; data: (number | null)[]; connectNulls: boolean }>
    }
    expect(option.xAxis).toMatchObject({
      type: 'category',
      data: ['2026-03-28', '2026-03-29', '2026-03-30', '2026-03-31'],
    })
    expect(option.series[0]).toMatchObject({
      type: 'line', data: [4, null, 0, 8], connectNulls: false,
    })
    const zeroReading = wrapper.get('.micronutrient-daily-card time[datetime="2026-03-30"]').element.closest('li')
    expect(zeroReading?.textContent).toContain('YAZIO')
    expect(zeroReading?.textContent).toContain('0 ug')
    expect(wrapper.find('.micronutrient-daily-card time[datetime="2026-03-29"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('normalizes an oversized custom micronutrient range to a supported 30-day request', async () => {
    const { wrapper, router } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2026-02-28&end=2026-03-31',
    )

    expect(router.currentRoute.value.query.period).toBe('30')
    const request = apiMock.mock.calls.find(([path]) => String(path).startsWith('/analytics/nutrition/micronutrients?'))?.[0] as string
    const params = new URLSearchParams(request.split('?')[1])
    const start = params.get('start')!
    const end = params.get('end')!
    expect((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1).toBe(30)
    expect(start).not.toBe('2026-02-28')
    expect(params.get('metric_key')).toBe('vitamin_a_ug')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
