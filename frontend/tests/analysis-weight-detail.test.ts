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

import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import AnalysisWeightDetailView from '../src/views/AnalysisWeightDetailView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import type { WeightResponse } from '../src/types'

const emptyWeightResponse: WeightResponse = {
  start_date: '2000-01-01',
  end_date: '2010-01-08',
  selected_provider: null,
  points: [],
  summary: {
    measurement_count: 0,
    latest: null,
    change_kg: null,
    average_kg: null,
    minimum_kg: null,
    maximum_kg: null,
  },
}

async function mountWeight(path: string) {
  setLocale('de')
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().user = {
    id: 'analysis-user', username: 'owner', language: 'de', timezone: 'Europe/Berlin',
    week_starts_on: 0, raw_payload_retention_days: 0, is_admin: false, is_active: true,
    deactivated_at: null,
  }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/analyse',
      component: AnalysisLayout,
      children: [
        { path: '', component: { template: '<h1>Analyse</h1>' } },
        { path: 'katalog', component: { template: '<h1>Katalog</h1>' } },
        { path: 'koerper/gewicht', component: AnalysisWeightDetailView },
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
  return wrapper
}

beforeEach(() => {
  apiMock.mockReset()
  apiMock.mockResolvedValue(emptyWeightResponse)
})

describe('Analysis weight detail range', () => {
  it('accepts the inclusive 3661-day maximum and requests the full range', async () => {
    const wrapper = await mountWeight('/analyse/koerper/gewicht?start=2000-01-01&end=2010-01-08')

    expect(apiMock).toHaveBeenCalledWith('/analytics/weight?start=2000-01-01&end=2010-01-08')
    expect(wrapper.text()).toContain('0 Messungen')
    expect(wrapper.text()).toContain('Für diesen Zeitraum liegen keine Gewichtswerte vor.')
    wrapper.unmount()
  })

  it('rejects 3662 inclusive days without issuing an analytics request', async () => {
    const wrapper = await mountWeight('/analyse/koerper/gewicht?start=2000-01-01&end=2010-01-09')

    expect(wrapper.get('[role="alert"]').text()).toContain('3661')
    expect(apiMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
