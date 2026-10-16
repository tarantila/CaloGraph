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

import AnalysisMicronutrientDetailView from '../src/views/AnalysisMicronutrientDetailView.vue'
import AnalysisLayout from '../src/views/AnalysisLayout.vue'
import PeriodPicker from '../src/components/PeriodPicker.vue'
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

async function expandReadings(wrapper: VueWrapper, count: number) {
  const readings = wrapper.get('.micronutrient-daily-card')
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

function expectScreenReaderTableHeaders(wrapper: VueWrapper) {
  const headers = wrapper.findAll('.micronutrient-daily-card thead th')
  expect(headers.map((header) => header.text())).toEqual(['Datum', 'Wert'])
  for (const header of headers) {
    expect(header.attributes('scope')).toBe('col')
    expect(header.element.closest('.sr-only')?.closest('thead')).toBe(header.element.closest('thead'))
    expect(header.element.closest('[aria-hidden="true"], [hidden]')).toBeNull()
  }
  for (const row of wrapper.findAll('.micronutrient-daily-card tbody tr')) {
    expect(row.element.closest('.sr-only, [aria-hidden="true"], [hidden]')).toBeNull()
    expect(row.isVisible()).toBe(true)
  }
}

const micronutrientResponse: AnalysisMicronutrientResponse = {
  start_date: '2026-03-01',
  end_date: '2026-03-31',
  selected_provider: { provider_key: 'google_health' },
  period_capabilities: { supports_all_data: false, all_data_start_date: null },
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

function responseForRange(path: string): AnalysisMicronutrientResponse {
  const params = new URL(path, 'https://calograph.test').searchParams
  const start = params.get('start')
  const end = params.get('end')
  if (!start || !end) return micronutrientResponse
  const periodDays = (Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000 + 1
  return {
    ...micronutrientResponse,
    start_date: start,
    end_date: end,
    period_days: periodDays,
    series: [
      { date: start, provider_key: 'google_health', value: null },
      { date: end, provider_key: 'yazio', value: 0 },
    ],
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-03-31T10:00:00Z'))
  setLocale('de')
  setUser()
  apiMock.mockReset()
  apiMock.mockImplementation((path: string) => Promise.resolve(responseForRange(path)))
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Analysis micronutrient detail', () => {
  it.each([
    { label: 'default', query: '', period: '30', start: '2026-03-02', end: '2026-03-31' },
    { label: 'unsupported all data', query: '?period=all', period: '30', start: '2026-03-02', end: '2026-03-31' },
    { label: '90 days', query: '?period=90', period: '90', start: '2026-01-01', end: '2026-03-31' },
    { label: '180 days', query: '?period=180', period: '180', start: '2025-10-03', end: '2026-03-31' },
    { label: '365 days', query: '?period=365', period: '365', start: '2025-04-01', end: '2026-03-31' },
    {
      label: 'leap-year custom 365 days',
      query: '?period=custom&start=2023-04-01&end=2024-03-30',
      period: 'custom', start: '2023-04-01', end: '2024-03-30',
    },
  ])('keeps canonical micronutrient range for $label, including rolling limits', async ({ query, period, start, end }) => {
    const { wrapper, router } = await mountAnalysis(`/analyse/mikronaehrstoffe/vitamin_a_ug${query}`)
    try {
      expect(router.currentRoute.value.query).toMatchObject({ period })
      if (period === 'custom') {
        expect(router.currentRoute.value.query).toMatchObject({ start, end })
      } else {
        expect(router.currentRoute.value.query.start).toBeUndefined()
        expect(router.currentRoute.value.query.end).toBeUndefined()
      }
      expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
        `/analytics/nutrition/micronutrients?start=${start}&end=${end}&metric_key=vitamin_a_ug`,
      ])
      const picker = wrapper.getComponent(PeriodPicker)
      expect(picker.find('.analysis-period-quick-button[data-period="30"]').exists()).toBe(true)
      expect((picker.get('.analysis-period-quick-button[data-period="90"]').element as HTMLButtonElement).disabled).toBe(false)
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.find('.analysis-period-option[data-period="all"]').exists()).toBe(false)
      expect(picker.find('.analysis-period-option[data-period="180"]').exists()).toBe(true)
      expect(picker.find('.analysis-period-option[data-period="365"]').exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('applies a rolling 90-day preset through URL replacement without dropping unrelated state', async () => {
    const { wrapper, router } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=30&source=share#vitamins')
    try {
      const picker = wrapper.getComponent(PeriodPicker)
      await picker.get('.analysis-period-quick-button[data-period="90"]').trigger('click')
      await flushPromises()
      expect(router.currentRoute.value.query).toMatchObject({ period: '90', source: 'share' })
      expect(router.currentRoute.value.hash).toBe('#vitamins')
      expect(apiMock.mock.calls.at(-1)?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2026-01-01&end=2026-03-31&metric_key=vitamin_a_ug',
      )
      await picker.get('.analysis-period-custom-button').trigger('click')
      expect(picker.find('.analysis-period-option[data-period="90"]').exists()).toBe(true)
      expect(picker.find('.analysis-period-option[data-period="365"]').exists()).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })


  it('preserves a leap-year 31-day custom selection and its breadcrumb boundaries', async () => {
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      start_date: '2024-01-30', end_date: '2024-02-29',
      metrics: [{
        ...micronutrientResponse.metrics[0]!, total: 0, days_with_data: 1,
        contributors: [{ provider_key: 'yazio', days_with_data: 1 }],
      }],
      series: [{ date: '2024-02-29', provider_key: 'yazio', value: 0 }],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper, router } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2024-01-30&end=2024-02-29&source=share#vitamins',
    )
    try {
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2024-01-30', end: '2024-02-29',
      })
      expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
        '/analytics/nutrition/micronutrients?start=2024-01-30&end=2024-02-29&metric_key=vitamin_a_ug',
      ])
      const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as { xAxis: { data: string[] } }
      expect(option.xAxis.data).toHaveLength(31)
      expect([option.xAxis.data[0], option.xAxis.data.at(-1)]).toEqual(['2024-01-30', '2024-02-29'])
      for (const link of wrapper.findAll('.analysis-breadcrumbs a')) {
        const href = new URL(link.attributes('href')!, 'https://calograph.test')
        const query = href.searchParams
        expect([query.get('period'), query.get('start'), query.get('end')]).toEqual(['custom', '2024-01-30', '2024-02-29'])
        expect(query.get('source')).toBe('share')
        expect(href.hash).toBe('#vitamins')
      }
      await expandReadings(wrapper, 1)
      expect(wrapper.get('.micronutrient-daily-card time').attributes('datetime')).toBe('2024-02-29')
    } finally {
      wrapper.unmount()
    }
  })

  it('shows the average and readings without coverage or provenance', async () => {
    const series = Array.from({ length: 30 }, (_, index) => ({
      date: new Date(Date.UTC(2026, 2, 2 + index)).toISOString().slice(0, 10),
      provider_key: 'analysis_demo',
      value: index % 5 === 1 ? null : 12,
    }))
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      start_date: '2026-03-02',
      end_date: '2026-03-31',
      period_days: 30,
      selected_provider: { provider_key: 'analysis_demo' },
      metrics: [{
        ...micronutrientResponse.metrics[0]!,
        metric_key: 'vitamin_c_mg',
        label: 'Vitamin C',
        unit: 'mg',
        total: 288,
        days_with_data: 24,
        contributors: [{ provider_key: 'analysis_demo', days_with_data: 24 }],
      }],
      series,
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper, router } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_c_mg?period=30',
    )
    try {
      expect(router.currentRoute.value.query.period).toBe('30')
      expect(wrapper.get('h1').text()).toBe('Vitamin C')
      expect(wrapper.text()).toContain('Tägliche Einträge für Vitamin C im gewählten Zeitraum.')
      expect(wrapper.get('.micronutrient-summary h2').text()).toBe('Durchschnitt')
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe('12 mg')
      await expandReadings(wrapper, 24)
      expectScreenReaderTableHeaders(wrapper)
      expect(wrapper.findAll('.micronutrient-daily-card tbody tr')).toHaveLength(24)
      expect(wrapper.getComponent(PeriodPicker).props('period')).toBe('30')
      expect(wrapper.text()).not.toMatch(/analysis_demo|Google Health|YAZIO|Datenherkunft|Gesamterfasste Menge|Tage mit Werten|von 30 Tagen/i)
      expect(wrapper.text()).not.toMatch(/Mangel|Unterversorgung|Überversorgung|Überdosierung/i)
    } finally {
      wrapper.unmount()
    }
  })

  it.each([
    { key: 'vitamin_a_ug', label: 'Vitamin A', unit: 'ug', value: '12,5 µg', zero: '0 µg' },
    { key: 'vitamin_c_mg', label: 'Vitamin C', unit: 'mg', value: '12,5 mg', zero: '0 mg' },
    { key: 'calcium_mg', label: 'Calcium', unit: 'mg', value: '12,5 mg', zero: '0 mg' },
  ] as const)('visually hides semantic $label list headers while preserving dated and zero readings', async ({ key, label, unit, value, zero }) => {
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      start_date: '2026-03-02',
      metrics: [{
        ...micronutrientResponse.metrics[0]!, metric_key: key, label, unit,
        total: 12.5, days_with_data: 2,
      }],
      series: [
        { date: '2026-03-29', provider_key: 'yazio', value: 12.5 },
        { date: '2026-03-30', provider_key: 'yazio', value: null },
        { date: '2026-03-31', provider_key: 'yazio', value: 0 },
      ],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper } = await mountAnalysis(`/analyse/mikronaehrstoffe/${key}?period=30`)
    try {
      await expandReadings(wrapper, 2)
      expectScreenReaderTableHeaders(wrapper)
      const rows = wrapper.findAll('.micronutrient-daily-card tbody tr')
      expect(rows.map((row) => row.findAll('td').map((cell) => cell.text()))).toEqual([
        ['29.03.2026', value], ['31.03.2026', zero],
      ])
      expect(rows.map((row) => row.get('time').attributes('datetime'))).toEqual([
        '2026-03-29', '2026-03-31',
      ])
      expect(wrapper.find('.micronutrient-daily-card time[datetime="2026-03-30"]').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
  it.each([
    ['vitamin_a_ug', 'Vitamin A'],
    ['folate_ug', 'Folat'],
    ['vitamin_d_ug', 'Vitamin D'],
  ])('formats %s values with the German microgram symbol', async (metricKey, label) => {
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      metrics: [{
        ...micronutrientResponse.metrics[0]!,
        metric_key: metricKey,
        label,
        unit: 'ug',
        total: 25.5,
        days_with_data: 2,
      }],
      series: [
        { date: '2026-03-30', provider_key: 'yazio', value: 12.25 },
        { date: '2026-03-31', provider_key: 'yazio', value: 13.25 },
      ],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper } = await mountAnalysis(`/analyse/mikronaehrstoffe/${metricKey}?period=30`)
    try {
      expect(wrapper.get('h1').text()).toBe(label)
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe('12,75 µg')
      await expandReadings(wrapper, 2)
      expect(wrapper.findAll('.micronutrient-daily-card tbody td.number').map((cell) => cell.text()))
        .toEqual(['12,25 µg', '13,25 µg'])
      expect(wrapper.text()).not.toContain(' ug')
    } finally {
      wrapper.unmount()
    }
  })
  it.each([
    { key: 'vitamin_c_mg', label: 'Vitamin C', token: '--analysis-micronutrient-orange', color: '#fb923c' },
    { key: 'vitamin_a_ug', label: 'Vitamin A', token: '--chart-series-emerald', color: '#047857' },
    { key: 'calcium_mg', label: 'Calcium', token: '--chart-series-blue', color: '#2563eb' },
  ])('uses the identity color for $label icon accent and chart', async ({ key, label, token, color }) => {
    const previousColor = document.documentElement.style.getPropertyValue(token)
    document.documentElement.style.setProperty(token, color)
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      metrics: [{
        ...micronutrientResponse.metrics[0]!,
        metric_key: key,
        label,
        unit: 'mg',
        total: 12,
        days_with_data: 1,
        contributors: [],
      }],
      series: [{ date: '2026-03-31', provider_key: 'analysis_demo', value: 12 }],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper } = await mountAnalysis(`/analyse/mikronaehrstoffe/${key}?period=30`)
    try {
      const option = wrapper.getComponent({ name: 'ChartPanel' }).props('option') as {
        series: Array<{
          connectNulls: boolean
          lineStyle: { color: string; width: number }
          itemStyle: { color: string }
          symbolSize: number
          areaStyle: {
            origin: string
            color: { colorStops: Array<{ offset: number; color: string }> }
          }
        }>
      }
      const series = option.series[0]!
      expect(wrapper.find('.analysis-detail-icon').exists()).toBe(true)
      expect(wrapper.get('.analysis-detail-page').attributes('style')).toContain(color)
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe('12 mg')
      expect(series.lineStyle).toEqual({ color, width: 2 })
      expect(series.itemStyle.color).toBe(color)
      expect(series.symbolSize).toBe(6)
      expect(series.connectNulls).toBe(false)
      expect(series.areaStyle.origin).toBe('start')
      expect(series.areaStyle.color.colorStops.map(({ offset }) => offset)).toEqual([0, 0.45, 0.75, 1])
      expect(series.areaStyle.color.colorStops.map(({ color: stopColor }) => stopColor))
        .toEqual([0.24, 0.1, 0.03, 0].map((alpha) => expect.stringContaining(`, ${alpha})`)))
    } finally {
      wrapper.unmount()
      if (previousColor) document.documentElement.style.setProperty(token, previousColor)
      else document.documentElement.style.removeProperty(token)
    }
  })


  it('plots calendar gaps and keeps an actual zero in the readings table', async () => {
    apiMock.mockResolvedValue({
      ...micronutrientResponse,
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
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
    await expandReadings(wrapper, 3)
    const zeroReading = wrapper.get('.micronutrient-daily-card time[datetime="2026-03-30"]').element.closest('tr')
    expect(zeroReading?.textContent).toContain('0 µg')
    expect(wrapper.find('.micronutrient-daily-card time[datetime="2026-03-29"]').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/google_health|yazio|analysis_demo/i)
    wrapper.unmount()
  })


  it('normalizes a 366-day custom micronutrient range to a supported 30-day request', async () => {
    const { wrapper, router } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2025-03-31&end=2026-03-31',
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

  it('keeps the selected 365-day range visible on an empty response', async () => {
    apiMock.mockResolvedValue({
      ...responseForRange('/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31'),
      metrics: [{
        ...micronutrientResponse.metrics[0]!, total: 0, days_with_data: 0, contributors: [],
      }],
      series: [],
    } satisfies AnalysisMicronutrientResponse)
    const { wrapper, router } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=365')
    try {
      expect(router.currentRoute.value.query.period).toBe('365')
      expect(wrapper.get('.micronutrient-empty').text()).toBe('Für diesen Zeitraum liegen keine Werte vor.')
      expect(wrapper.find('.micronutrient-summary').exists()).toBe(false)
      expect(wrapper.text()).not.toMatch(/\d+ von \d+ Tagen mit Werten/)
      expect(apiMock.mock.calls[0]?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31&metric_key=vitamin_a_ug',
      )
    } finally {
      wrapper.unmount()
    }
  })

  it('retries a failed 365-day read with the same committed URL bounds', async () => {
    apiMock.mockRejectedValueOnce(new Error('synthetic provider failure'))
    const { wrapper, router } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=365')
    try {
      const request = '/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31&metric_key=vitamin_a_ug'
      expect(router.currentRoute.value.query.period).toBe('365')
      expect(wrapper.get('[role="alert"]').text()).toContain('Provider nicht verfügbar')
      expect(apiMock.mock.calls[0]?.[0]).toBe(request)
      await wrapper.get('[role="alert"] button').trigger('click')
      await flushPromises()
      expect(apiMock.mock.calls.map(([path]) => path)).toEqual([request, request])
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe('450 µg')
    } finally {
      wrapper.unmount()
    }
  })

  it('discards an older response after a rolling URL selection changes', async () => {
    let resolveOld!: (value: AnalysisMicronutrientResponse) => void
    apiMock.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const { wrapper, router } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=365')
    try {
      await router.replace({ path: router.currentRoute.value.path, query: { period: '90' } })
      await flushPromises()
      expect(apiMock.mock.calls.map(([path]) => path)).toEqual([
        '/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31&metric_key=vitamin_a_ug',
        '/analytics/nutrition/micronutrients?start=2026-01-01&end=2026-03-31&metric_key=vitamin_a_ug',
      ])
      const averageBeforeOldResponse = wrapper.get('.micronutrient-summary strong').text()
      resolveOld({
        ...responseForRange('/analytics/nutrition/micronutrients?start=2025-04-01&end=2026-03-31'),
        metrics: [{ ...micronutrientResponse.metrics[0]!, total: 9999 }],
      })
      await flushPromises()
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe(averageBeforeOldResponse)
      expect(wrapper.text()).not.toContain('4.999,5 µg')
    } finally {
      wrapper.unmount()
    }
  })

  it('rederives rolling ranges for the account-local timezone and stops reading on logout', async () => {
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=90')
    try {
      const auth = useAuthStore()
      expect(apiMock.mock.calls[0]?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2026-07-04&end=2026-10-01&metric_key=vitamin_a_ug',
      )
      auth.user = { ...auth.user!, id: 'analysis-utc-user', timezone: 'UTC' }
      await flushPromises()
      expect(apiMock.mock.calls.at(-1)?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2026-07-03&end=2026-09-30&metric_key=vitamin_a_ug',
      )
      expect(wrapper.get('.micronutrient-summary strong').text()).toBe('450 µg')
      auth.user = null
      await flushPromises()
      expect(apiMock.mock.calls).toHaveLength(2)
      expect(wrapper.find('.micronutrient-daily-card').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('keeps a rolling preset relative to the account-local date after remount', async () => {
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const first = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=90')
    try {
      expect(apiMock.mock.calls[0]?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2026-07-04&end=2026-10-01&metric_key=vitamin_a_ug',
      )
    } finally {
      first.wrapper.unmount()
    }
    apiMock.mockClear()
    vi.setSystemTime(new Date('2026-10-01T22:30:00Z'))
    const second = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=90')
    try {
      expect(apiMock.mock.calls[0]?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2026-07-05&end=2026-10-02&metric_key=vitamin_a_ug',
      )
      expect(second.router.currentRoute.value.query.period).toBe('90')
    } finally {
      second.wrapper.unmount()
    }
  })

  it('keeps historical custom bounds absolute across an account-local timezone switch', async () => {
    vi.setSystemTime(new Date('2026-09-30T22:30:00Z'))
    const { wrapper, router } = await mountAnalysis(
      '/analyse/mikronaehrstoffe/vitamin_a_ug?period=custom&start=2023-04-01&end=2024-03-30',
    )
    try {
      expect(apiMock.mock.calls[0]?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2023-04-01&end=2024-03-30&metric_key=vitamin_a_ug',
      )
      const auth = useAuthStore()
      auth.user = { ...auth.user!, id: 'analysis-utc-user', timezone: 'UTC' }
      await flushPromises()
      expect(apiMock.mock.calls.at(-1)?.[0]).toBe(
        '/analytics/nutrition/micronutrients?start=2023-04-01&end=2024-03-30&metric_key=vitamin_a_ug',
      )
      expect(router.currentRoute.value.query).toMatchObject({
        period: 'custom', start: '2023-04-01', end: '2024-03-30',
      })
    } finally {
      wrapper.unmount()
    }
  })

  it('invalidates a previous account-local response before reading the next account', async () => {
    let resolveOld!: (value: AnalysisMicronutrientResponse) => void
    apiMock.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
    const { wrapper } = await mountAnalysis('/analyse/mikronaehrstoffe/vitamin_a_ug?period=90')
    try {
      const auth = useAuthStore()
      auth.user = { ...auth.user!, id: 'next-analysis-user' }
      await flushPromises()
      expect(apiMock.mock.calls).toHaveLength(2)
      expect(wrapper.text()).toContain('450 µg')
      resolveOld({
        ...responseForRange(String(apiMock.mock.calls[0]?.[0])),
        metrics: [{ ...micronutrientResponse.metrics[0]!, total: 9999 }],
      })
      await flushPromises()
      expect(wrapper.text()).toContain('450 µg')
      expect(wrapper.text()).not.toContain('4.999,5 µg')
    } finally {
      wrapper.unmount()
    }
  })
})
