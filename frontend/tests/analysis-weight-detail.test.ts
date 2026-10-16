import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Provider nicht verfügbar'),
}))

import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import AnalysisWeightDetailView from '../src/views/AnalysisWeightDetailView.vue'
import PeriodPicker from '../src/components/PeriodPicker.vue'
import WeightHistoryChart from '../src/components/WeightHistoryChart.vue'

import { setLocale } from '../src/i18n'
import { chartColors } from '../src/chart-theme'
import { setThemePreference, themePreference } from '../src/theme'
import { useAuthStore } from '../src/stores/auth'
import type { WeightResponse } from '../src/types'

const emptyWeightResponse: WeightResponse = {
  start_date: '2000-01-01',
  end_date: '2010-01-08',
  selected_provider: null,
  period_capabilities: { supports_all_data: false, all_data_start_date: null },
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
        { path: 'koerper', component: { template: '<h1>Körper</h1>' } },
        { path: 'koerper/gewicht', component: AnalysisWeightDetailView },
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
          template: '<section class="card chart-card" />',
        },
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

async function expandReadings(wrapper: VueWrapper, count: number) {
  const readings = wrapper.get('.weight-reading-list')
  const button = readings.get('button')
  expect(button.element.tagName).toBe('BUTTON')
  expect(button.text()).toBe(`Messwerte (${count})`)
  expect(button.attributes('aria-expanded')).toBe('false')
  const panelId = button.attributes('aria-controls')
  expect(panelId).toBeTruthy()
  const panel = readings.findAll('[id]').find((element) => element.attributes('id') === panelId)
  expect(panel).toBeDefined()
  expect(readings.findAll('table').every((table) => !table.isVisible())).toBe(true)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('true')
  expect(button.attributes('aria-controls')).toBe(panelId)
  expect(panel!.get('table').isVisible()).toBe(true)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('false')
  expect(button.attributes('aria-controls')).toBe(panelId)
  expect(panel!.get('table').isVisible()).toBe(false)

  await button.trigger('click')
  expect(button.attributes('aria-expanded')).toBe('true')
  expect(panel!.get('table').isVisible()).toBe(true)
}

let previousThemePreference: 'system' | 'light' | 'dark' = 'system'
let previousVioletToken = ''
beforeEach(() => {
  previousThemePreference = themePreference.value
  previousVioletToken = document.documentElement.style.getPropertyValue('--chart-series-violet')
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  apiMock.mockReset()
  apiMock.mockResolvedValue(emptyWeightResponse)
})

afterEach(() => {
  if (previousVioletToken) document.documentElement.style.setProperty('--chart-series-violet', previousVioletToken)
  else document.documentElement.style.removeProperty('--chart-series-violet')
  vi.useRealTimers()
  setThemePreference(previousThemePreference)
})

describe('Analysis weight detail range', () => {
  it.each(['', '?period=all'])('uses 30 days before the first weight read for %s', async (query) => {
    const { wrapper, router } = await mountWeight(`/analyse/koerper/gewicht${query}`)
    try {
      expect(router.currentRoute.value.query.period).toBe('30')
      expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/weight?'))
        .map(([path]) => path)).toEqual(['/analytics/weight?start=2026-03-02&end=2026-03-31'])
      const picker = wrapper.getComponent(PeriodPicker)
      expect(picker.get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
      expect(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
        .toEqual(['02.03.2026', '31.03.2026'])
    } finally {
      wrapper.unmount()
    }
  })

  it.each([
    ['180', '2025-10-03'],
    ['365', '2025-04-01'],
  ])('retains the supported %s-day weight URL and selection', async (period, start) => {
    const { wrapper, router } = await mountWeight(`/analyse/koerper/gewicht?period=${period}`)
    try {
      expect(router.currentRoute.value.query.period).toBe(period)
      expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/analytics/weight?'))
        .map(([path]) => path)).toEqual([`/analytics/weight?start=${start}&end=2026-03-31`])
      const picker = wrapper.getComponent(PeriodPicker)
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.get(`.analysis-period-option[data-period="${period}"]`).attributes('aria-pressed')).toBe('true')
      expect(picker.findAll('input[type="text"]').map((input) => (input.element as HTMLInputElement).value))
        .toEqual([period === '180' ? '03.10.2025' : '01.04.2025', '31.03.2026'])
    } finally {
      wrapper.unmount()
    }
  })

  it('selects the Weight-only Alle preset and restores its URL-backed 3661-day range', async () => {
    const { wrapper, router } = await mountWeight('/analyse/koerper/gewicht')
    try {
      const picker = wrapper.getComponent(PeriodPicker)
      await picker.get('.analysis-period-custom-button').trigger('click')
      const optionButtons = picker.findAll('button').filter((button) => button.attributes('data-period') !== undefined)
      const labels = optionButtons.map((button) => button.text().trim())
        .filter((label, index, allLabels) => allLabels.indexOf(label) === index)
      expect(labels).toEqual(['7 Tage', '30 Tage', '90 Tage', '6 Monate', '1 Jahr', 'Alle'])

      const allOption = optionButtons.find((button) => button.text().trim() === 'Alle')!
      await allOption.trigger('click')

      await flushPromises()
      expect(router.currentRoute.value.query.period).toBe('3661')
      expect(apiMock).toHaveBeenLastCalledWith('/analytics/weight?start=2016-03-23&end=2026-03-31')
      wrapper.unmount()

      const restored = await mountWeight('/analyse/koerper/gewicht?period=3661')
      try {
        expect(restored.router.currentRoute.value.query.period).toBe('3661')
        expect(apiMock).toHaveBeenLastCalledWith('/analytics/weight?start=2016-03-23&end=2026-03-31')
        const restoredPicker = restored.wrapper.getComponent(PeriodPicker)
        expect(restoredPicker.get('.analysis-period-trigger').text()).toContain('Alle')
        await restoredPicker.get('.analysis-period-custom-button').trigger('click')
        const restoredOption = restoredPicker.findAll('button')
          .find((button) => button.attributes('data-period') === '3661')
        expect(restoredOption?.attributes('aria-pressed')).toBe('true')
      } finally {
        restored.wrapper.unmount()
      }
    } finally {
      if (wrapper.element.isConnected) wrapper.unmount()
    }
  })

  it('accepts the inclusive 3661-day maximum and requests the full range', async () => {
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2000-01-01&end=2010-01-08')

    expect(apiMock).toHaveBeenCalledWith('/analytics/weight?start=2000-01-01&end=2010-01-08')
    expect(wrapper.get('.weight-summary-current h2').text()).toBe('Letzte Messung')
    expect(wrapper.get('.weight-summary-meta-label').text()).toBe('Veränderung')
    expect(wrapper.text()).not.toContain('0 Messungen')
    expect(wrapper.text()).toContain('Für diesen Zeitraum liegen keine Gewichtswerte vor.')
    wrapper.unmount()
  })

  it('rejects a custom weight URL beyond the inclusive maximum without retaining its boundaries', async () => {
    const { wrapper, router } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2000-01-01&end=2010-01-09')

    expect(router.currentRoute.value.query.period).toBe('30')
    expect(router.currentRoute.value.query.start).toBeUndefined()
    expect(router.currentRoute.value.query.end).toBeUndefined()
    const request = apiMock.mock.calls.find(([path]) => String(path).startsWith('/analytics/weight?'))?.[0] as string
    const params = new URLSearchParams(request.split('?')[1])
    const start = params.get('start')!
    const end = params.get('end')!
    expect([start, end]).toEqual(['2026-03-02', '2026-03-31'])
    expect(wrapper.getComponent(PeriodPicker).get('.analysis-period-quick-button[data-period="30"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('plots only actual weight measurements and connects sparse dates in the selected range', async () => {
    const wrapper = mount(WeightHistoryChart, {
      props: {
        points: [
          { date: '2026-03-01', weight_kg: 72 },
          { date: '2026-03-04', weight_kg: 69 },
        ],
        dateRange: { start: '2026-03-01', end: '2026-03-04' },
      },
      global: {
        stubs: {
          ChartPanel: { name: 'ChartPanel', props: ['option', 'title', 'empty'], template: '<div />' },
        },
      },
    })
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      series: Array<{
        data: Array<[number, number | null]>; connectNulls: boolean
        smooth: number; smoothMonotone: 'x'; lineStyle: { cap: string; join: string }
      }>
    }
    const series = option.series[0]!
    expect(series.data.map(([, value]) => value)).toEqual([72, 69])
    expect(series.data.map(([date]) => date)).toEqual([
      Date.parse('2026-03-01T00:00:00Z'),
      Date.parse('2026-03-04T00:00:00Z'),
    ])
    expect(series.connectNulls).toBe(true)
    expect(series.smooth).toBe(0.4)
    expect(series.smoothMonotone).toBe('x')
    expect(series.lineStyle).toMatchObject({ cap: 'round', join: 'round' })
    wrapper.unmount()
  })

  it('pads the Weight value axis above and below real measurements, including flat series', () => {
    const cases = [
      { weights: [72, 69], expected: [68.5, 72.5] },
      { weights: [70, 70], expected: [69.5, 70.5] },
      { weights: [50, 60], expected: [48.8, 61.2] },
    ]
    for (const { weights, expected } of cases) {
      const wrapper = mount(WeightHistoryChart, {
        props: {
          points: weights.map((weight_kg, index) => ({
            date: `2026-03-${String(index + 1).padStart(2, '0')}`,
            weight_kg,
          })),
        },
        global: {
          stubs: {
            ChartPanel: { name: 'ChartPanel', props: ['option', 'title', 'empty'], template: '<div />' },
          },
        },
      })
      const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
        yAxis: { min: number; max: number }
      }
      expect([option.yAxis.min, option.yAxis.max]).toEqual(expected)
      wrapper.unmount()
    }
  })
  it('plots sparse March 1, 10 and 11 measurements with a theme-based fade to the plot baseline', async () => {
    document.documentElement.style.setProperty('--chart-series-violet', '#7c3aed')
    setThemePreference('dark')
    setThemePreference('light')
    apiMock.mockResolvedValue({
      start_date: '2026-03-01', end_date: '2026-03-11',
      selected_provider: { provider_key: 'yazio' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      points: [
        { date: '2026-03-11', weight_kg: 70 },
        { date: '2026-03-01', weight_kg: 72 },
        { date: '2026-03-10', weight_kg: 71 },
      ],
      summary: {
        measurement_count: 3, latest: { date: '2026-03-11', weight_kg: 70 },
        change_kg: -2, average_kg: 71, minimum_kg: 70, maximum_kg: 72,
      },
    } satisfies WeightResponse)
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2026-03-01&end=2026-03-11')
    const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
      xAxis: { type: string }
      series: Array<{
        type: string; data: [number | string, number][]; showSymbol: boolean
        smooth: number; smoothMonotone: 'x'
        lineStyle: { color: string; cap: string; join: string }; itemStyle: { color: string }
        areaStyle?: {
          color: {
            type: string; x: number; y: number; x2: number; y2: number; global: boolean
            colorStops: Array<{ offset: number; color: string }>
          }
        }
      }>
    }
    expect(option.xAxis.type).toBe('time')
    const series = option.series[0]!
    expect(series.type).toBe('line')
    expect(series.showSymbol).toBe(true)
    expect(series.data.map(([, value]) => value)).toEqual([72, 71, 70])
    const [first, second, third] = series.data.map(([date]) => typeof date === 'number' ? date : Date.parse(date))
    expect((second! - first!) / (third! - second!)).toBe(9)
    expect(series.lineStyle.color).toBe(chartColors.value.violet)
    expect(series.itemStyle.color).toBe(chartColors.value.violet)
    expect(series.areaStyle?.color.colorStops.map((stop) => stop.offset)).toEqual([0, 0.6, 1])
    expect(series.areaStyle?.color.colorStops.map(({ color }) => Number(color.match(/,\s*([\d.]+)\s*\)$/)?.[1])))
      .toEqual([0.24, 0.14, 0.04])
    wrapper.unmount()
  })

  it('shows only two summary items and counts actual readings in the shared CaloGraph table', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-03-01', end_date: '2026-03-04', selected_provider: null,
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      points: [{ date: '2026-03-04', weight_kg: 69 }, { date: '2026-03-01', weight_kg: 72 }],
      summary: {
        measurement_count: 9, latest: { date: '2026-03-04', weight_kg: 69 },
        change_kg: -3, average_kg: 70.5, minimum_kg: 69, maximum_kg: 72,
      },
    } satisfies WeightResponse)
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2026-03-01&end=2026-03-04')
    try {
      expect(wrapper.findAll('.weight-summary-primary > *')).toHaveLength(2)
      expect(wrapper.get('.weight-summary-primary').text()).not.toContain('Messungen')
      expect(wrapper.get('.weight-summary-primary').text()).not.toContain('Tagen mit Werten')
      expect(wrapper.get('.weight-summary-current > strong').text()).toBe('69 kg')
      expect(wrapper.get('.weight-summary-meta strong').text()).toBe('-3 kg')
      expect(wrapper.findAll('.weight-summary-secondary article').map((card) => card.get('strong').text()))
        .toEqual(['70,5 kg', '69 kg', '72 kg'])
      await expandReadings(wrapper, 2)
      expect(wrapper.find('.weight-reading-list table').exists()).toBe(true)
      expect(wrapper.findAll('.weight-reading-list thead th').map((heading) => heading.text()))
        .toEqual(['Datum', 'Gewicht'])
      expect(wrapper.findAll('.weight-reading-list tbody tr').map((row) => ({
        date: row.get('time').attributes('datetime'), value: row.findAll('td')[1]!.text(),
      }))).toEqual([
        { date: '2026-03-01', value: '72 kg' },
        { date: '2026-03-04', value: '69 kg' },
      ])
      const detailBlocks = Array.from(wrapper.get('.analysis-detail-page').element.children).flatMap((element) => {
        if (element.matches('.weight-summary-primary')) return ['primary']
        if (element.matches('.chart-card')) return ['chart']
        if (element.matches('.weight-summary-secondary')) return ['statistics']
        if (element.matches('.weight-reading-list')) return ['readings']
        return []
      })
      expect(detailBlocks).toEqual(['primary', 'chart', 'statistics', 'readings'])
    } finally {
      wrapper.unmount()
    }
  })

  it('uses the same scale icon mapping as the Analysis Hub', async () => {
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht')
    try {
      const icon = wrapper.get('.analysis-detail-icon--weight')
      expect(icon.attributes('aria-hidden')).toBe('true')
      expect(wrapper.findComponent({ name: 'AnalysisIcon' }).props('icon')).toBe('scale')
      expect(icon.find('svg').exists()).toBe(true)
      expect(icon.text()).not.toContain('◉')
      expect(wrapper.get('h1').text()).toBe('Gewicht')
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps a recorded zero change distinct from an unavailable single-measurement change', async () => {
    for (const [points, change, expected] of [
      [[{ date: '2026-03-01', weight_kg: 70 }, { date: '2026-03-04', weight_kg: 70 }], 0, '0 kg'],
      [[{ date: '2026-03-04', weight_kg: 70 }], null, '–'],
    ] as const) {
      apiMock.mockResolvedValue({
        start_date: '2026-03-01', end_date: '2026-03-04', selected_provider: null,
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        points: [...points],
        summary: {
          measurement_count: points.length, latest: { date: '2026-03-04', weight_kg: 70 },
          change_kg: change, average_kg: 70, minimum_kg: 70, maximum_kg: 70,
        },
      } satisfies WeightResponse)
      const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2026-03-01&end=2026-03-04')
      try {
        expect(wrapper.get('.weight-summary-meta strong').text()).toBe(expected)
        await expandReadings(wrapper, points.length)
        expect(wrapper.findAll('.weight-reading-list tbody tr')).toHaveLength(points.length)
        expect(wrapper.get('.weight-reading-list h2').text()).toBe(`Messwerte (${points.length})`)
      } finally {
        wrapper.unmount()
      }
    }
  })

})
