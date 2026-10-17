import { createPinia, setActivePinia } from 'pinia'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type RouteLocationRaw } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Fehler'),
}))

import PeriodPicker from '../src/components/PeriodPicker.vue'
import ActivityView from '../src/views/ActivityView.vue'
import DailyView from '../src/views/DailyView.vue'
import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import WeekdaysView from '../src/views/WeekdaysView.vue'
import type { DailyPoint } from '../src/types'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

enableAutoUnmount(afterEach)

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

async function mountView(component: Component, location: RouteLocationRaw = '/') {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component }] })
  await router.push(location)
  await router.isReady()
  const wrapper = mount(component, {
    global: { plugins: [router], stubs: { ChartPanel: true } },
  })
  await flushPromises()
  return { wrapper, router }
}

function requestFor(prefix: string): string {
  const call = apiMock.mock.calls.find(([path]) => typeof path === 'string' && path.startsWith(prefix))
  if (!call) throw new Error(`No API request matched ${prefix}`)
  return call[0] as string
}

function point(date: string, calories: number): DailyPoint {
  return {
    date, calories_kcal: calories, target_kcal: 2200, maintenance_kcal: null,
    deviation_kcal: calories - 2200, activity_mode: 'off', activity_source_type: null,
    active_energy_kcal: null, activity_credit_kcal: 0, activity_data_status: 'disabled',
    effective_budget_kcal: 2200, effective_maintenance_kcal: null,
    effective_deviation_kcal: calories - 2200, protein_g: 120, carbs_g: 200, fat_g: 70,
    tracking_status: 'complete', tracking_score: 8, tracking_reasons: [],
    average_7d: null, average_14d: null, average_28d: null,
  }
}

const analyticsViews = [
  { name: 'Daily', component: DailyView, endpoint: '/analytics/daily' },
  { name: 'Micronutrients', component: MicronutrientsView, endpoint: '/analytics/micronutrients' },
  { name: 'Weekdays', component: WeekdaysView, endpoint: '/analytics/weekdays' },
] as const

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
  setLocale('de')
  setUser()
  apiMock.mockReset()
  apiMock.mockImplementation((path: string) => {
    if (path === '/dashboard/summary') return Promise.resolve({ data_start_date: '2024-01-01' })
    if (path.startsWith('/analytics/micronutrients')) {
      return Promise.resolve({
        start_date: '2026-09-02',
        end_date: '2026-10-01',
        source: null,
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        available_sources: [{ source_type: 'yazio_export_v1', last_updated_at: null }],
        nutrients: [],
        recorded_days: 0,
        last_updated_at: null,
      })
    }
    if (path.startsWith('/analytics/weekdays')) return Promise.resolve({ weekdays: [] })
    if (path.startsWith('/analytics/daily')) return Promise.resolve([])
    if (path.startsWith('/analytics/verification/activity')) {
      return Promise.resolve({ start_date: '2026-08-25', end_date: '2026-08-31', view: 'canonical', days: [] })
    }
    return Promise.resolve({ points: [], budget_balance: null })
  })
})

afterEach(() => vi.useRealTimers())

describe('analytics page controlled period integrations', () => {
  it('requests ActivityView’s authenticated local seven-day canonical range', async () => {
    vi.setSystemTime(new Date('2026-08-31T12:00:00Z'))
    const { wrapper } = await mountView(ActivityView)
    expect(requestFor('/analytics/verification/activity')).toBe(
      '/analytics/verification/activity?start=2026-08-25&end=2026-08-31&view=canonical',
    )
    wrapper.unmount()
  })

  describe.each(analyticsViews)('$name reader', ({ component, endpoint }) => {
    it('defaults to 30 inclusive days in the authenticated timezone', async () => {
      const { wrapper, router } = await mountView(component)
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=2026-09-02&end=2026-10-01`)
      expect(router.currentRoute.value.query).toMatchObject({
        start: '2026-09-02', end: '2026-10-01', period: '30',
      })
      expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text()).toContain('30 Tage')
    })

    it('preserves a supported preset-only URL as 90 inclusive local days', async () => {
      const { wrapper, router } = await mountView(component, '/?period=90')
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=2026-07-04&end=2026-10-01`)
      expect(router.currentRoute.value.query).toEqual({
        start: '2026-07-04', end: '2026-10-01', period: '90',
      })
      expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text()).toContain('90 Tage')
    })

    it.each([
      { period: '60', start: '2026-08-03', end: '2026-10-01', label: '03.08.2026 – 01.10.2026' },
      { period: 'year', start: '2025-01-01', end: '2025-12-31', label: '01.01.2025 – 31.12.2025' },
      { period: 'custom', start: '2016-09-23', end: '2026-10-01', label: '23.09.2016 – 01.10.2026' },
    ])('preserves the explicit $period range as a visible custom selection', async ({ period, start, end, label }) => {
      const { wrapper, router } = await mountView(component, `/?start=${start}&end=${end}&period=${period}`)
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=${start}&end=${end}`)
      expect(router.currentRoute.value.query).toMatchObject({ start, end, period: 'custom' })
      const picker = wrapper.getComponent(PeriodPicker)
      expect(picker.get('.analysis-period-trigger').text()).toContain(label)
      expect(picker.get('.analysis-period-custom-button').attributes('aria-pressed')).toBe('true')
      expect(picker.find('form').exists()).toBe(false)
    })

    it('preserves an explicit range without a period query as the visible custom selection', async () => {
      const { wrapper, router } = await mountView(component, '/?start=2026-08-01&end=2026-08-31')
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=2026-08-01&end=2026-08-31`)
      expect(router.currentRoute.value.query).toMatchObject({
        start: '2026-08-01', end: '2026-08-31', period: 'custom',
      })
      expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text())
        .toContain('01.08.2026 – 31.08.2026')
    })

    it.each([
      { label: 'syntactically invalid start', query: { start: 'not-a-date', end: '2026-10-01' } },
      { label: 'impossible calendar date', query: { start: '2026-02-30', end: '2026-03-01' } },
      { label: 'inverted range', query: { start: '2026-10-01', end: '2026-09-01' } },
      { label: 'missing start', query: { end: '2026-10-01' } },
      { label: 'missing end', query: { start: '2026-09-01' } },
      { label: 'non-string start', query: { start: ['2026-09-01', '2026-09-02'], end: '2026-10-01' } },
      { label: '3662 inclusive days', query: { start: '2016-09-22', end: '2026-10-01' } },
    ])('falls back to 30 days for $label', async ({ query }) => {
      const { wrapper, router } = await mountView(component, { path: '/', query })
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=2026-09-02&end=2026-10-01`)
      expect(router.currentRoute.value.query).toMatchObject({
        start: '2026-09-02', end: '2026-10-01', period: '30',
      })
      expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text()).toContain('30 Tage')
    })

    it.each([
      { label: 'malformed start without end', query: { period: '90', start: 'not-a-date' } },
      { label: 'malformed end without start', query: { period: '90', end: 'not-a-date' } },
      { label: 'missing start', query: { period: '90', end: '2026-10-01' } },
      { label: 'missing end', query: { period: '90', start: '2026-07-04' } },
      { label: 'array-valued start', query: { period: '90', start: ['2026-07-04', '2026-07-05'], end: '2026-10-01' } },
      { label: 'array-valued end', query: { period: '90', start: '2026-07-04', end: ['2026-09-30', '2026-10-01'] } },
    ])('repairs a supported 90-day URL with $label to 30 local days', async ({ query }) => {
      const { wrapper, router } = await mountView(component, { path: '/', query })
      expect(requestFor(endpoint)).toBe(`${endpoint}?start=2026-09-02&end=2026-10-01`)
      expect(router.currentRoute.value.query).toEqual({
        start: '2026-09-02', end: '2026-10-01', period: '30',
      })
      expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-trigger').text()).toContain('30 Tage')
    })

    it('applies a future custom range supported by the general reader', async () => {
      const { wrapper, router } = await mountView(component, '/?note=keep#future')
      const picker = wrapper.getComponent(PeriodPicker)
      await picker.get('.analysis-period-custom-button').trigger('click')
      const fields = picker.findAll('input[type="text"]')
      await fields[0]!.setValue('02.10.2026')
      await fields[1]!.setValue('31.10.2026')
      await picker.get('form').trigger('submit')
      await flushPromises()
      expect(router.currentRoute.value.query).toEqual({
        start: '2026-10-02', end: '2026-10-31', period: 'custom', note: 'keep',
      })
      expect(router.currentRoute.value.hash).toBe('#future')
      expect(apiMock).toHaveBeenLastCalledWith(`${endpoint}?start=2026-10-02&end=2026-10-31`)
      expect(picker.get('.analysis-period-trigger').text()).toContain('02.10.2026 – 31.10.2026')
      expect(picker.find('form').exists()).toBe(false)
    })
  })

  it('applies daily custom drafts to rendered days only on submit without dropping filters or hash', async () => {
    const points = [point('2026-09-01', 1700), point('2026-09-30', 2100), point('2026-10-01', 2300)]
    apiMock.mockImplementation((path: string) => {
      const query = new URL(path, 'https://calograph.test').searchParams
      return Promise.resolve(points.filter((item) => item.date >= query.get('start')! && item.date <= query.get('end')!))
    })
    const { wrapper, router } = await mountView(DailyView,
      '/?start=2026-09-02&end=2026-10-01&source=apple_health_xml&tracking=complete,probably_complete&weekday=2&note=keep#records')
    const picker = wrapper.getComponent(PeriodPicker)
    expect(wrapper.get('.daily-table').text()).toContain('01.10.2026')
    expect(wrapper.get('.daily-table').text()).not.toContain('01.09.2026')
    expect(wrapper.get('[aria-label="Tageswerte filtern"]').text()).toContain('Datenstatus')
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0)
    await picker.get('.analysis-period-custom-button').trigger('click')
    const fields = picker.findAll('input[type="text"]')
    const requestsBeforeEditing = apiMock.mock.calls.length
    await fields[0]!.setValue('01.09.2026')
    await fields[1]!.setValue('30.09.2026')
    await flushPromises()
    expect(router.currentRoute.value.query.start).toBe('2026-09-02')
    expect(apiMock).toHaveBeenCalledTimes(requestsBeforeEditing)
    expect(wrapper.get('.daily-table').text()).toContain('01.10.2026')
    expect(wrapper.get('.daily-table').text()).not.toContain('01.09.2026')
    await picker.get('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-01', end: '2026-09-30', period: 'custom',
      source: 'apple_health_xml', tracking: 'complete,probably_complete', weekday: '2', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#records')
    expect(apiMock).toHaveBeenLastCalledWith(
      '/analytics/daily?start=2026-09-01&end=2026-09-30&source=apple_health_xml&tracking=complete%2Cprobably_complete&weekday=2',
    )
    expect(wrapper.get('.daily-table').text()).toContain('01.09.2026')
    expect(wrapper.get('.daily-table').text()).toContain('30.09.2026')
    expect(wrapper.get('.daily-table').text()).not.toContain('01.10.2026')
    expect(picker.find('form').exists()).toBe(false)
    expect(wrapper.findAll('input[type="text"]')).toHaveLength(0)
  })

  it.each([
    { start: 'not-a-date', end: '2026-10-01' },
    { start: '2026-10-01', end: '2026-09-01' },
    { start: '2016-09-22', end: '2026-10-01' },
  ])('keeps Daily filters and hash while repairing invalid range $start to $end', async (range) => {
    const { router } = await mountView(DailyView, {
      path: '/', query: { ...range, source: 'apple_health_xml', tracking: 'complete,probably_complete', weekday: '2', note: 'keep' },
      hash: '#records',
    })
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-02', end: '2026-10-01', period: '30',
      source: 'apple_health_xml', tracking: 'complete,probably_complete', weekday: '2', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#records')
    expect(requestFor('/analytics/daily')).toBe(
      '/analytics/daily?start=2026-09-02&end=2026-10-01&source=apple_health_xml&tracking=complete%2Cprobably_complete&weekday=2',
    )
  })

  describe.each([
    { name: 'Daily', component: DailyView, endpoint: '/analytics/daily' },
    { name: 'Weekdays', component: WeekdaysView, endpoint: '/analytics/weekdays' },
  ])('$name static reader capability', ({ component, endpoint }) => {
    it.each(['', '&source=apple_health_xml'])('never exposes All with source query "%s"', async (sourceQuery) => {
      const { wrapper, router } = await mountView(component,
        `/?start=2026-01-01&end=2026-10-01&period=all${sourceQuery}`)
      const picker = wrapper.getComponent(PeriodPicker)
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
      expect(router.currentRoute.value.query).toMatchObject({
        start: '2026-09-02', end: '2026-10-01', period: '30',
      })
      const request = new URL(requestFor(endpoint), 'https://calograph.test')
      expect(request.pathname).toBe(endpoint)
      expect(request.searchParams.has('period')).toBe(false)
    })
  })

  it('reloads rendered Weekdays when history changes the range and preserves unrelated query and hash', async () => {
    apiMock.mockResolvedValueOnce({ weekdays: [{
      weekday: 0, label: 'Montag', count: 1, mean_kcal: 1700, median_kcal: 1700,
      p25_kcal: 1700, p75_kcal: 1700, mean_deviation_kcal: -500, mean_protein_g: 100,
    }] }).mockResolvedValue({ weekdays: [{
      weekday: 1, label: 'Dienstag', count: 1, mean_kcal: 2400, median_kcal: 2400,
      p25_kcal: 2400, p75_kcal: 2400, mean_deviation_kcal: 200, mean_protein_g: 140,
    }] })
    const { wrapper, router } = await mountView(WeekdaysView, '/?start=2026-05-01&end=2026-05-31&period=custom')
    expect(wrapper.get('.table-card tbody').text()).toContain('Montag')
    await router.push('/?start=2026-06-01&end=2026-06-30&period=custom&source=apple_health_xml&note=keep#weekdays')
    await flushPromises()
    expect(wrapper.get('.table-card tbody').text()).toContain('Dienstag')
    expect(wrapper.get('.table-card tbody').text()).not.toContain('Montag')
    expect(apiMock).toHaveBeenLastCalledWith('/analytics/weekdays?start=2026-06-01&end=2026-06-30')
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-06-01', end: '2026-06-30', period: 'custom', source: 'apple_health_xml', note: 'keep',
    })
    expect(router.currentRoute.value.hash).toBe('#weekdays')
  })
})
