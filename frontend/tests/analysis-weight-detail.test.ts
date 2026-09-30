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
import { chartColors } from '../src/chart-theme'
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
          template: '<div />',
        },
      },
    },
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  apiMock.mockReset()
  apiMock.mockResolvedValue(emptyWeightResponse)
})

describe('Analysis weight detail range', () => {
  it('accepts the inclusive 3661-day maximum and requests the full range', async () => {
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2000-01-01&end=2010-01-08')

    expect(apiMock).toHaveBeenCalledWith('/analytics/weight?start=2000-01-01&end=2010-01-08')
    expect(wrapper.text()).toContain('0 Messungen')
    expect(wrapper.text()).toContain('Für diesen Zeitraum liegen keine Gewichtswerte vor.')
    wrapper.unmount()
  })

  it('normalizes an oversized custom range to a supported 365-day weight request', async () => {
    const { wrapper, router } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2000-01-01&end=2010-01-09')

    expect(router.currentRoute.value.query.period).toBe('365')
    const request = apiMock.mock.calls.find(([path]) => String(path).startsWith('/analytics/weight?'))?.[0] as string
    const params = new URLSearchParams(request.split('?')[1])
    const start = params.get('start')!
    const end = params.get('end')!
    expect((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1).toBe(365)
    expect(start).not.toBe('2000-01-01')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('plots sparse March 1, 10 and 11 measurements on a real violet time axis with a restrained fill', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-03-01', end_date: '2026-03-11',
      selected_provider: { provider_key: 'yazio' },
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
        lineStyle: { color: string }; itemStyle: { color: string }
        areaStyle?: { color: string; opacity: number }
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
    expect(series.areaStyle).toMatchObject({ color: chartColors.value.violet })
    expect(series.areaStyle!.opacity).toBeGreaterThan(0)
    expect(series.areaStyle!.opacity).toBeLessThan(0.25)
    wrapper.unmount()
  })

  it('shows reader-backed period statistics, measured-day coverage and only actual readings', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-03-01', end_date: '2026-03-04', selected_provider: null,
      points: [{ date: '2026-03-04', weight_kg: 69 }, { date: '2026-03-01', weight_kg: 72 }],
      summary: {
        measurement_count: 2, latest: { date: '2026-03-04', weight_kg: 69 },
        change_kg: -3, average_kg: 70.5, minimum_kg: 69, maximum_kg: 72,
      },
    } satisfies WeightResponse)
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2026-03-01&end=2026-03-04')
    try {
      expect(wrapper.get('.weight-summary-primary > strong').text()).toBe('69 kg')
      expect(wrapper.get('.weight-summary-meta strong').text()).toBe('-3 kg')
      expect(wrapper.findAll('.weight-summary-secondary article').map((card) => card.get('strong').text()))
        .toEqual(['70,5 kg', '69 kg', '72 kg'])
      expect(wrapper.text()).toContain('2 von 4 Tagen')
      expect(wrapper.findAll('.weight-reading-list li').map((row) => ({
        date: row.get('time').attributes('datetime'), value: row.get('strong').text(),
      }))).toEqual([
        { date: '2026-03-01', value: '72 kg' },
        { date: '2026-03-04', value: '69 kg' },
      ])
    } finally {
      wrapper.unmount()
    }
  })

  it('pairs the weight heading with its violet decorative measurement icon', async () => {
    const { wrapper } = await mountWeight('/analyse/koerper/gewicht')
    try {
      const icon = wrapper.get('.analysis-detail-icon--weight')
      expect(icon.attributes('aria-hidden')).toBe('true')
      expect(icon.text()).toBe('◉')
      expect(wrapper.get('h1').text()).toContain('Gewicht')
      expect(wrapper.get('h1').element.contains(icon.element)).toBe(true)
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
        points: [...points],
        summary: {
          measurement_count: points.length, latest: { date: '2026-03-04', weight_kg: 70 },
          change_kg: change, average_kg: 70, minimum_kg: 70, maximum_kg: 70,
        },
      } satisfies WeightResponse)
      const { wrapper } = await mountWeight('/analyse/koerper/gewicht?period=custom&start=2026-03-01&end=2026-03-04')
      try {
        expect(wrapper.get('.weight-summary-meta strong').text()).toBe(expected)
        expect(wrapper.findAll('.weight-reading-list li')).toHaveLength(points.length)
        expect(wrapper.text()).toContain(points.length === 2 ? '2 von 4 Tagen' : '1 von 4 Tagen')
      } finally {
        wrapper.unmount()
      }
    }
  })

})
