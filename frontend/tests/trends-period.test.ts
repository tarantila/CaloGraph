import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Fehler'),
}))

import ChartPanel from '../src/components/ChartPanel.vue'
import PeriodPicker from '../src/components/PeriodPicker.vue'
import TrendsView from '../src/views/TrendsView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import type { DailyPoint, ReaderPeriodCapabilities } from '../src/types'

const routes = [{ path: '/trends', name: 'trends', component: TrendsView }]
const unsupported: ReaderPeriodCapabilities = { supports_all_data: false, all_data_start_date: null }
const supported: ReaderPeriodCapabilities = { supports_all_data: true, all_data_start_date: '2026-01-01' }

interface TrendsResponse {
  points: DailyPoint[]
  budget_balance: null
  period_capabilities: ReaderPeriodCapabilities
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

function result(points: DailyPoint[] = [], capability = unsupported): TrendsResponse {
  return { points, budget_balance: null, period_capabilities: capability }
}

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
      stubs: {
        ChartPanel: {
          props: ['title', 'option', 'empty', 'height'],
          template: '<section><slot name="header-actions" /></section>',
        },
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

type TrendsWrapper = VueWrapper<InstanceType<typeof TrendsView>>
function calorieChart(wrapper: TrendsWrapper) {
  return wrapper.findAllComponents(ChartPanel)[0]!.props('option') as {
    xAxis: { data: string[] }
    series: Array<{ name: string; data: Array<number | null> }>
  }
}
async function openPicker(wrapper: TrendsWrapper) {
  const picker = wrapper.getComponent(PeriodPicker)
  if (!picker.find('.analysis-period-popover').exists()) {
    await picker.get('.analysis-period-custom-button').trigger('click')
  }
  return picker
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-10-01T12:00:00Z'))
  setLocale('de')
  apiMock.mockReset()
  apiMock.mockResolvedValue(result())
})
afterEach(() => {
  vi.useRealTimers()
})

describe('trends period selection', () => {
  it('defaults to 30 local inclusive days and applies custom dates only on submit', async () => {
    const { wrapper, router } = await mountTrends('/trends?include_incomplete=true&source=yazio#nutrition')
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-09-02', end: '2026-10-01', period: '30' })
    const picker = await openPicker(wrapper)
    const fields = picker.findAll('input[type="text"]')
    const requestsBeforeEditing = apiMock.mock.calls.length
    await fields[0]!.setValue('01.09.2026')
    await fields[1]!.setValue('30.09.2026')
    await flushPromises()
    expect(router.currentRoute.value.query.start).toBe('2026-09-02')
    expect(apiMock).toHaveBeenCalledTimes(requestsBeforeEditing)
    await picker.get('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-01', end: '2026-09-30', period: 'custom', include_incomplete: 'true', source: 'yazio',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(apiMock).toHaveBeenLastCalledWith('/analytics/trends?start=2026-09-01&end=2026-09-30')
    wrapper.unmount()
  })

  it('changes rendered trend range and chart data after 7-day selection and custom Apply', async () => {
    const points = [
      point('2026-08-10', 1500), point('2026-08-12', 1600),
      point('2026-09-02', 1800), point('2026-09-25', 2100),
      point('2026-09-27', 1900), point('2026-10-01', 2300),
    ]
    apiMock.mockImplementation((path: string) => {
      const query = new URL(path, 'https://calograph.test').searchParams
      return Promise.resolve(result(points.filter((item) => item.date >= query.get('start')! && item.date <= query.get('end')!)))
    })
    const { wrapper, router } = await mountTrends('/trends?source=yazio#nutrition')
    expect(wrapper.get('.chart-range').text()).toBe('02.09. – 01.10.2026')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1800, 2100, 1900, 2300])

    await wrapper.getComponent(PeriodPicker).get('.analysis-period-strip [data-period="7"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.chart-range').text()).toBe('25.09. – 01.10.2026')
    expect(calorieChart(wrapper).xAxis.data).toEqual(['25.09.', '27.09.', '01.10.'])
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2100, 1900, 2300])
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-09-25', end: '2026-10-01', period: '7', source: 'yazio' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')

    const picker = await openPicker(wrapper)
    const fields = picker.findAll('input[type="text"]')
    await fields[0]!.setValue('10.08.2026')
    await fields[1]!.setValue('12.08.2026')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2100, 1900, 2300])
    await picker.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.chart-range').text()).toBe('10.08. – 12.08.2026')
    expect(calorieChart(wrapper).xAxis.data).toEqual(['10.08.', '12.08.'])
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1500, 1600])
    wrapper.unmount()
  })

  it('reloads rendered analytics data when browser history changes only the query', async () => {
    const points = [point('2026-05-15', 1700), point('2026-06-15', 2400)]
    apiMock.mockImplementation((path: string) => {
      const query = new URL(path, 'https://calograph.test').searchParams
      return Promise.resolve(result(points.filter((item) => item.date >= query.get('start')! && item.date <= query.get('end')!)))
    })
    const { wrapper, router } = await mountTrends('/trends?start=2026-05-01&end=2026-05-31&period=custom')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1700])
    await router.push('/trends?start=2026-06-01&end=2026-06-30&period=custom&source=yazio#nutrition')
    await flushPromises()
    expect(wrapper.get('.chart-range').text()).toBe('01.06. – 30.06.2026')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2400])
    expect(router.currentRoute.value.query).toEqual({ start: '2026-06-01', end: '2026-06-30', period: 'custom', source: 'yazio' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    const requestsBeforeUnrelatedChange = apiMock.mock.calls.length
    await router.push({ query: { ...router.currentRoute.value.query, source: 'manual' }, hash: '#budget' })
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(requestsBeforeUnrelatedChange)
    wrapper.unmount()
  })

  it('rejects an inverted custom range without changing the displayed data or applied URL', async () => {
    apiMock.mockResolvedValue(result([point('2026-10-01', 2300)]))
    const { wrapper, router } = await mountTrends()
    const picker = await openPicker(wrapper)
    const inputs = picker.findAll('input[type="text"]')
    await inputs[0]!.setValue('01.06.2026')
    await inputs[1]!.setValue('31.05.2026')
    const requestsBeforeSubmit = apiMock.mock.calls.length
    await picker.get('form').trigger('submit')
    await flushPromises()
    expect(picker.find('[role="alert"]').exists()).toBe(true)
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-09-02', end: '2026-10-01', period: '30' })
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2300])
    expect(apiMock).toHaveBeenCalledTimes(requestsBeforeSubmit)
    wrapper.unmount()
  })

  it.each([
    { preset: '30', start: '2026-09-02', label: '02.09. – 01.10.2026', calories: [1800, 2300] },
    { preset: '180', start: '2026-04-05', label: '05.04. – 01.10.2026', calories: [1400, 1700, 1800, 2300] },
  ])('applies the $preset-day preset from a custom range', async ({ preset, start, label, calories }) => {
    const points = [
      point('2026-04-05', 1400), point('2026-05-01', 1700),
      point('2026-09-02', 1800), point('2026-10-01', 2300),
    ]
    apiMock.mockImplementation((path: string) => {
      const query = new URL(path, 'https://calograph.test').searchParams
      return Promise.resolve(result(points.filter((item) => item.date >= query.get('start')! && item.date <= query.get('end')!)))
    })
    const { wrapper, router } = await mountTrends('/trends?start=2026-05-01&end=2026-05-31&period=custom')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1700])
    if (preset === '180') {
      await (await openPicker(wrapper)).get('.analysis-period-option[data-period="180"]').trigger('click')
    } else {
      await wrapper.getComponent(PeriodPicker).get('.analysis-period-strip [data-period="30"]').trigger('click')
    }
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({ start, end: '2026-10-01', period: preset })
    expect(wrapper.get('.chart-range').text()).toBe(label)
    expect(calorieChart(wrapper).series[0]!.data).toEqual(calories)
    wrapper.unmount()
  })

  it.each([
    '/trends?source=yazio#nutrition',
    '/trends?period=all&source=yazio#nutrition',
  ])('ignores an old positive capability after a newer 7-day result from %s', async (path) => {
    let finishOld!: (value: TrendsResponse) => void
    apiMock.mockImplementationOnce(() => new Promise<TrendsResponse>((resolve) => { finishOld = resolve }))
    apiMock.mockResolvedValue(result([point('2026-09-25', 2100), point('2026-10-01', 2300)]))
    const { wrapper, router } = await mountTrends(path)
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-strip [data-period="7"]').trigger('click')
    await flushPromises()
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2100, 2300])
    finishOld(result([], supported))
    await flushPromises()
    const picker = await openPicker(wrapper)
    expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-09-25', end: '2026-10-01', period: '7', source: 'yazio' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([2100, 2300])
    wrapper.unmount()
  })

  it('revokes active All on the current false capability and falls back to exactly 30 days', async () => {
    let finishAll!: (value: TrendsResponse) => void
    apiMock.mockResolvedValueOnce(result([point('2026-10-01', 2300)], supported))
    apiMock.mockImplementationOnce(() => new Promise<TrendsResponse>((resolve) => { finishAll = resolve }))
    apiMock.mockResolvedValue(result([point('2026-09-02', 1800), point('2026-10-01', 2300)]))
    const { wrapper, router } = await mountTrends('/trends?include_incomplete=true&source=yazio#nutrition')
    const picker = await openPicker(wrapper)
    await picker.get('.analysis-period-option[data-period="all"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query).toMatchObject({ start: '2026-01-01', end: '2026-10-01', period: 'all' })
    finishAll(result())
    await flushPromises()
    expect(router.currentRoute.value.query).toEqual({
      start: '2026-09-02', end: '2026-10-01', period: '30', include_incomplete: 'true', source: 'yazio',
    })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(wrapper.get('.chart-range').text()).toBe('02.09. – 01.10.2026')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1800, 2300])
    expect(apiMock).toHaveBeenLastCalledWith('/analytics/trends?start=2026-09-02&end=2026-10-01')
    expect((await openPicker(wrapper)).find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not expose a previous positive All capability while the new request is pending or after it fails', async () => {
    let failCurrent!: (reason: Error) => void
    apiMock.mockResolvedValueOnce(result([], supported))
    apiMock.mockImplementationOnce(() => new Promise<TrendsResponse>((_resolve, reject) => { failCurrent = reject }))
    const { wrapper, router } = await mountTrends()
    expect((await openPicker(wrapper)).find('.analysis-period-option[data-period="all"]').exists()).toBe(true)
    await wrapper.getComponent(PeriodPicker).get('.analysis-period-strip [data-period="7"]').trigger('click')
    await flushPromises()
    expect((await openPicker(wrapper)).find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    const requestsBeforeFailure = apiMock.mock.calls.length
    failCurrent(new Error('synthetic request failure'))
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('Fehler')
    expect((await openPicker(wrapper)).find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    expect(router.currentRoute.value.query.period).toBe('7')
    expect(apiMock).toHaveBeenCalledTimes(requestsBeforeFailure)
    wrapper.unmount()
  })

  it.each([
    { name: 'false capability', capability: unsupported },
    { name: 'true capability without reader boundary', capability: { supports_all_data: true, all_data_start_date: null } },
  ])('settles initial URL All at 30 days for $name without sending unsupported All', async ({ capability }) => {
    apiMock.mockResolvedValue(result([point('2026-10-01', 2300)], capability))
    const { wrapper, router } = await mountTrends('/trends?period=all&start=2026-01-01&end=2026-10-01&source=yazio#nutrition')
    expect(apiMock.mock.calls.map(([path]) => new URL(path, 'https://calograph.test').searchParams.get('period'))).not.toContain('all')
    expect(router.currentRoute.value.query).toEqual({ start: '2026-09-02', end: '2026-10-01', period: '30', source: 'yazio' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(wrapper.get('.chart-range').text()).toBe('02.09. – 01.10.2026')
    expect((await openPicker(wrapper)).find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('honors initial URL All only after the 30-day probe proves a real reader boundary', async () => {
    apiMock.mockResolvedValueOnce(result([point('2026-10-01', 2300)], supported))
    apiMock.mockResolvedValue(result([point('2026-01-01', 1500), point('2026-10-01', 2300)], supported))
    const { wrapper, router } = await mountTrends('/trends?period=all&source=yazio#nutrition')
    const firstRequest = new URL(apiMock.mock.calls[0]![0], 'https://calograph.test').searchParams
    expect(firstRequest.get('start')).toBe('2026-09-02')
    expect(firstRequest.get('end')).toBe('2026-10-01')
    expect(firstRequest.has('period')).toBe(false)
    expect(router.currentRoute.value.query).toEqual({ start: '2026-01-01', end: '2026-10-01', period: 'all', source: 'yazio' })
    expect(router.currentRoute.value.hash).toBe('#nutrition')
    expect(wrapper.get('.chart-range').text()).toBe('01.01. – 01.10.2026')
    expect(calorieChart(wrapper).series[0]!.data).toEqual([1500, 2300])
    wrapper.unmount()
  })
})
