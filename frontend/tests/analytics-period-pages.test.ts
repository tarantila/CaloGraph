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
import DailyView from '../src/views/DailyView.vue'
import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import WeekdaysView from '../src/views/WeekdaysView.vue'
import { isoDateInTimeZone, shiftIsoDate } from '../src/date-format'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
function setUser() {
  setActivePinia(createPinia())
  useAuthStore().user = {
    id: 'user-1',
    username: 'owner',
    language: 'de',
    timezone: 'Europe/Berlin',
    week_starts_on: 0,
    raw_payload_retention_days: 0,
    is_admin: false,
    is_active: true,
    deactivated_at: null,
  }
}

async function mountView(component: typeof DailyView, path = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component }] })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, {
    global: { plugins: [router], stubs: { ChartPanel: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setLocale('de')
  setUser()
  apiMock.mockReset()
  apiMock.mockImplementation((path: string) => {
    if (path === '/dashboard/summary') return Promise.resolve({ data_start_date: '2024-01-01' })
    if (path.startsWith('/analytics/micronutrients')) {
      return Promise.resolve({
        available_sources: [{ source_type: 'yazio_export_v1', last_updated_at: null }],
        nutrients: [],
        recorded_days: 0,
        last_updated_at: null,
      })
    }
    if (path.startsWith('/analytics/weekdays')) return Promise.resolve({ weekdays: [] })
    if (path.startsWith('/analytics/daily')) return Promise.resolve([])
    return Promise.resolve({ points: [], budget_balance: null })
  })
})

describe('analytics page responsive period integrations', () => {
  it('offers the compact period family on Micronutrients', async () => {
    const wrapper = await mountView(MicronutrientsView)
    const filter = wrapper.getComponent(AnalyticsPeriodFilter)

    expect(filter.findAll('.analytics-period-button').map((button) => button.text())).toEqual([
      '7 Tage', '30 Tage', '60 Tage', 'Alle', 'Individuell',
    ])
    await filter.get('.analytics-period-button').trigger('click')
    await flushPromises()

    const today = isoDateInTimeZone('Europe/Berlin')
    expect(apiMock).toHaveBeenCalledWith(`/analytics/micronutrients?start=${shiftIsoDate(today, -6)}&end=${today}&source=yazio_export_v1`)
  })

  it('keeps custom controls separate from Daily additional filters', async () => {
    const wrapper = await mountView(DailyView)
    const filter = wrapper.getComponent(AnalyticsPeriodFilter)

    expect(filter.find('.analytics-period-custom-fields').exists()).toBe(false)
    expect(wrapper.get('[aria-label="Tageswerte filtern"]').text()).toContain('Ernährungsquelle')

    await filter.findAll('.analytics-period-button').find((button) => button.text() === 'Individuell')!.trigger('click')

    expect(filter.find('.analytics-period-custom-fields').exists()).toBe(true)
    expect(wrapper.get('[aria-label="Tageswerte filtern"]').text()).toContain('Datenstatus')
  })

  it('keeps the Weekdays aggregation page on its own compact preset family', async () => {
    const wrapper = await mountView(WeekdaysView)
    const filter = wrapper.getComponent(AnalyticsPeriodFilter)

    expect(filter.findAll('.analytics-period-button').map((button) => button.text())).toEqual([
      '7 Tage', '30 Tage', '60 Tage', '6 Monate', 'Alle', 'Individuell',
    ])
    expect(filter.get('.analytics-period-button[aria-pressed="true"]').text()).toBe('6 Monate')
    expect(wrapper.text()).toContain('Wochentagsanalyse')
  })

  it('restores Individuell and Alle from deep-linked period URLs', async () => {
    const custom = await mountView(DailyView, '/?start=2026-07-28&end=2026-08-26&period=custom')
    const customFilter = custom.getComponent(AnalyticsPeriodFilter)
    expect(customFilter.get('.analytics-period-button[aria-pressed="true"]').text()).toBe('Individuell')
    expect(customFilter.find('.analytics-period-custom-fields').exists()).toBe(true)
    custom.unmount()

    const all = await mountView(DailyView, '/?start=2026-02-01&end=2026-08-26&period=all')
    const allFilter = all.getComponent(AnalyticsPeriodFilter)
    expect(allFilter.get('.analytics-period-button[aria-pressed="true"]').text()).toBe('Alle')
    expect(allFilter.find('.analytics-period-custom-fields').exists()).toBe(false)
  })

  it('keeps default desktop DateFilter presets aligned with their date ranges', async () => {
    const daily = await mountView(DailyView)
    expect(daily.getComponent(DateFilter).get('select').element.value).toBe('30')
    daily.unmount()

    const micronutrients = await mountView(MicronutrientsView)
    expect(micronutrients.getComponent(DateFilter).get('select').element.value).toBe('30')
    micronutrients.unmount()

    const weekdays = await mountView(WeekdaysView)
    expect(weekdays.getComponent(DateFilter).get('select').element.value).toBe('180')
  })
})
