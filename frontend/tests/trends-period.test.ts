import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Fehler'),
}))

import AnalyticsPeriodFilter from '../src/components/AnalyticsPeriodFilter.vue'
import DateFilter from '../src/components/DateFilter.vue'
import TrendsView from '../src/views/TrendsView.vue'
import { isoDateInTimeZone, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const routes = [{ path: '/trends', name: 'trends', component: TrendsView }]

async function mountTrends(path = '/trends') {
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = {
    id: 'user-1',
    username: 'admin',
    language: 'de',
    timezone: 'Europe/Berlin',
    week_starts_on: 0,
    raw_payload_retention_days: 0,
    is_admin: true,
    is_active: true,
    deactivated_at: null,
  }
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(TrendsView, {
    global: {
      plugins: [router],
      stubs: { ChartPanel: true },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  setLocale('de')
  apiMock.mockReset()
  apiMock.mockResolvedValue({ points: [], budget_balance: null })
})

describe('trends period selection', () => {
  it('loads the default 90-day range and keeps it in the URL', async () => {
    const { wrapper, router } = await mountTrends()
    const today = isoDateInTimeZone('Europe/Berlin')

    expect(apiMock).toHaveBeenCalledWith(`/analytics/trends?start=${shiftIsoDate(today, -89)}&end=${today}`)
    expect(router.currentRoute.value.query).toEqual({ start: shiftIsoDate(today, -89), end: today })
    expect(wrapper.getComponent(DateFilter).get('select').element.value).toBe('90')
    expect(wrapper.getComponent(DateFilter).findAll('input[type="text"]').map((input) => input.attributes('placeholder'))).toEqual([
      'TT.MM.JJJJ',
      'TT.MM.JJJJ',
    ])
    expect(wrapper.getComponent(DateFilter).get('button.compact-apply').classes()).toContain('compact-action')
  })

  it('keeps existing Trends presets compact and persists custom mode', async () => {
    const { wrapper, router } = await mountTrends('/trends?start=2026-05-01&end=2026-05-31&period=custom')
    const filter = wrapper.getComponent(AnalyticsPeriodFilter)

    expect(filter.findAll('.analytics-period-button').map((button) => button.text())).toEqual([
      '30 Tage', '90 Tage', '6 Monate', '1 Jahr', 'Alle', 'Individuell',
    ])
    expect(filter.findAll('.analytics-period-button[aria-pressed="true"]')).toHaveLength(1)
    expect(filter.findAll('.analytics-period-custom-fields')).toHaveLength(1)

    await filter.findAll('.analytics-period-button').find((button) => button.text() === '30 Tage')!.trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.period).toBe('30')
  })
  it('reloads the analytics range when browser history changes only the query', async () => {
    const { wrapper, router } = await mountTrends('/trends?start=2026-05-01&end=2026-05-31')
    apiMock.mockClear()

    await router.push('/trends?start=2026-06-01&end=2026-06-30')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/analytics/trends?start=2026-06-01&end=2026-06-30')
    expect(router.currentRoute.value.query).toEqual({ start: '2026-06-01', end: '2026-06-30' })
    wrapper.unmount()
  })

  it.each([
    ['30', 29],
    ['180', 179],
  ])('loads the %s-day preset', async (preset, offset) => {
    const { wrapper } = await mountTrends()
    apiMock.mockClear()
    await wrapper.getComponent(DateFilter).get('select').setValue(preset)
    await flushPromises()

    const today = isoDateInTimeZone('Europe/Berlin')
    expect(apiMock).toHaveBeenCalledWith(`/analytics/trends?start=${shiftIsoDate(today, -offset)}&end=${today}`)
    wrapper.unmount()
  })

  it('loads a valid custom range and rejects an inverted range', async () => {
    const { wrapper } = await mountTrends()
    apiMock.mockClear()
    const inputs = wrapper.getComponent(DateFilter).findAll('input[type="text"]')
    await inputs[0].setValue('01.05.2026')
    await inputs[1].setValue('31.05.2026')
    await wrapper.getComponent(DateFilter).get('form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/analytics/trends?start=2026-05-01&end=2026-05-31')

    apiMock.mockClear()
    await inputs[0].setValue('01.06.2026')
    await inputs[1].setValue('31.05.2026')
    await wrapper.getComponent(DateFilter).get('form').trigger('submit')
    await flushPromises()
    expect(apiMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Von-Datum darf nicht nach')
    wrapper.unmount()
  })
})
