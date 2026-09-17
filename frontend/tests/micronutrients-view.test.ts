import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type RouteLocationRaw } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {},
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import { isoDateInTimeZone, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
function response(overrides: Record<string, unknown> = {}) {
  return {
    start_date: '2026-08-01',
    end_date: '2026-08-31',
    source: null,
    recorded_days: 0,
    last_updated_at: null,
    available_sources: [],
    nutrients: [],
    ...overrides,
  }
}

async function mountView(location: RouteLocationRaw) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: MicronutrientsView }],
  })
  await router.push(location)
  await router.isReady()
  const wrapper = mount(MicronutrientsView, {
    global: {
      plugins: [router],
      stubs: { AnalyticsPeriodFilter: true },
    },
  })
  await flushPromises()
  return { router, wrapper }
}

describe('MicronutrientsView source routing', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setActivePinia(createPinia())
    setLocale('de')
  })

  it('normalizes unsupported canonical All deep links to the safe default range', async () => {
    apiMock.mockResolvedValue(response())

    const { router, wrapper } = await mountView({
      path: '/',
      query: { start: '2026-08-01', end: '2026-08-31', period: 'all' },
    })

    const today = isoDateInTimeZone('Europe/Berlin')
    expect(apiMock).toHaveBeenCalledWith(
      `/analytics/micronutrients?start=${shiftIsoDate(today, -29)}&end=${today}`,
    )
    expect(router.currentRoute.value.query).toEqual({
      start: shiftIsoDate(today, -29),
      end: today,
    })
    wrapper.unmount()
  })

  it('preserves an explicit legacy source in the URL and API request', async () => {
    apiMock.mockResolvedValue(response({ source: 'apple_health_xml' }))

    const { router, wrapper } = await mountView({
      path: '/',
      query: {
        start: '2026-08-01',
        end: '2026-08-31',
        period: 'all',
        source: 'apple_health_xml',
      },
    })

    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/micronutrients?start=2026-08-01&end=2026-08-31&period=all&source=apple_health_xml',
    )
    expect(router.currentRoute.value.query.source).toBe('apple_health_xml')
    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('Apple Health')
    expect(wrapper.get('.micronutrient-source-filter').text()).not.toContain('YAZIO')
    wrapper.unmount()
  })

  it('uses selected_provider instead of a legacy source for display', async () => {
    apiMock.mockResolvedValue(response({
      source: 'apple_health_xml',
      selected_provider: {
        provider_key: 'google_health',
        latest_evidence_observed_at: null,
      },
    }))

    const { wrapper } = await mountView({ path: '/', query: { source: 'apple_health_xml' } })

    expect(wrapper.get('.micronutrient-source-filter').text()).toContain('Google Health Connect')
    expect(wrapper.get('.micronutrient-source-filter').text()).not.toContain('Apple Health')
    expect(wrapper.text()).not.toContain('YAZIO')
    wrapper.unmount()
  })
})
