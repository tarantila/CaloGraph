import { config, flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const { apiMock, authExpiredMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  authExpiredMock: vi.fn(),
}))
vi.mock('../src/api', () => ({
  api: apiMock,
  notifyAuthenticationExpired: authExpiredMock,
  ensureCsrfToken: vi.fn().mockResolvedValue('csrf'),
  ApiError: class ApiError extends Error {},
  localizeApiError: () => 'The request could not be processed.',
}))

import CalendarView from '../src/views/CalendarView.vue'
import ChartPanel from '../src/components/ChartPanel.vue'
import DateInput from '../src/components/DateInput.vue'
import DailyView from '../src/views/DailyView.vue'
import ImportsView from '../src/views/ImportsView.vue'
import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import OverviewView from '../src/views/OverviewView.vue'
import QualityView from '../src/views/QualityView.vue'
import AccountDataPrivacyView from '../src/views/AccountDataPrivacyView.vue'
import AccountIntegrationsView from '../src/views/AccountIntegrationsView.vue'
import AccountSecurityView from '../src/views/AccountSecurityView.vue'
import AccountTargetsView from '../src/views/AccountTargetsView.vue'
import TrendsView from '../src/views/TrendsView.vue'
import WeekdaysView from '../src/views/WeekdaysView.vue'
import WeeklyView from '../src/views/WeeklyView.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const user = {
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

describe('main views', () => {
  beforeEach(() => {
    apiMock.mockReset()
    authExpiredMock.mockReset()
    setActivePinia(createPinia())
    setLocale(DEFAULT_LOCALE)
    config.global.stubs = {
      RouterLink: { props: ['to'], template: '<a :href="to"><slot /></a>' },
    }
  })
  afterEach(() => {
    setLocale(DEFAULT_LOCALE)
  })

  it('loads the dashboard summary and renders empty trend data safely', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: { date: '2026-07-19', calories_kcal: null, target_kcal: null, maintenance_kcal: null, deviation_kcal: null, activity_mode: null, activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: 0, activity_data_status: 'disabled', effective_budget_kcal: null, effective_maintenance_kcal: null, effective_deviation_kcal: null, protein_g: null, tracking_status: 'no_data', tracking_reasons: ['Keine Daten'] },
          week: { consumed_kcal: 0, budget_kcal: null, deviation_kcal: null, remaining_kcal: null, activity_credit_kcal: 0, effective_budget_kcal: null, effective_deviation_kcal: null, effective_remaining_kcal: null },
          protein_7d_average_g: null,
          last_import_at: null,
          data_start_date: '2026-06-01',
          data_end_date: null,
          data_day_count: 0,
        })
      }
      if (path === '/settings/targets' || path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') {
        return Promise.resolve({
          available: true,
          configured: false,
          sync_enabled: false,
          sync_interval_minutes: null,
          sync_days: null,
          last_attempt_at: null,
          last_success_at: null,
          next_sync_at: null,
          last_error: null,
        })
      }
      return Promise.resolve({ points: [] })
    })
    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Ernährungsüberblick')
    expect(wrapper.text()).toContain('Verbleibend')
    expect(wrapper.text()).toContain('Noch kein vollständiges Wochenbudget')
    expect(wrapper.text()).toContain('Wochenzusammenfassung')
    expect(wrapper.text()).toContain('Datenstatus')
    expect(wrapper.text()).toContain('Datenabdeckung')
    expect(wrapper.text()).toContain('Keine Datenquelle')
    expect(wrapper.text()).not.toContain('Wahrscheinlich unvollständig')
    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/trends?start=2026-06-20&end=2026-07-19&include_incomplete=true',
    )
    const periodControl = wrapper.get('.period-control')
    expect(periodControl.findAll('button')).toHaveLength(4)
    expect(periodControl.findAll('button.active')).toHaveLength(1)
    expect(periodControl.text()).toContain('7 Tage')
    expect(periodControl.text()).toContain('30 Tage')
    expect(periodControl.text()).toContain('60 Tage')
    expect(periodControl.text()).toContain('Alle')
    const allButton = wrapper.findAll('button').find((button) => button.text() === 'Alle')
    expect(allButton).toBeDefined()
    await allButton!.trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/trends?start=2026-06-01&end=2026-07-19&include_incomplete=true&period=all',
    )
  })
  it('zeigt bei dauerhaftem Dashboard-Transportfehler einen lokalisierten Retry-Zustand', async () => {
    const auth = useAuthStore()
    auth.user = user
    let failed = true
    const summary = {
      today: { date: '2026-07-19', calories_kcal: null, target_kcal: null, protein_g: null, tracking_status: 'no_data', tracking_reasons: [] },
      week: { consumed_kcal: 0, budget_kcal: null, deviation_kcal: null, remaining_kcal: null },
      protein_7d_average_g: null,
      last_import_at: null,
      data_start_date: null,
      data_end_date: null,
      data_day_count: 0,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary' && failed) {
        failed = false
        return Promise.reject(new Error('temporary transport failure'))
      }
      if (path === '/dashboard/summary') return Promise.resolve(summary)
      if (path === '/settings/targets' || path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ available: false, configured: false })
      return Promise.resolve({ points: [] })
    })

    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('The request could not be processed.')
    expect(wrapper.findAll('button').some((button) => button.text() === 'Erneut versuchen')).toBe(true)
    expect(auth.user).toEqual(user)

    await wrapper.findAll('button').find((button) => button.text() === 'Erneut versuchen')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Ernährungsüberblick')
    expect(wrapper.text()).not.toContain('The request could not be processed.')
    expect(auth.user).toEqual(user)
  })

  it('renders the central overview in English after a locale switch', async () => {
    setLocale('en')
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: { date: '2026-07-19', calories_kcal: null, target_kcal: null, protein_g: null, tracking_status: 'no_data', tracking_reasons: [] },
          week: { consumed_kcal: 0, budget_kcal: null, deviation_kcal: null, remaining_kcal: null },
          protein_7d_average_g: null,
          last_import_at: null,
          data_start_date: null,
          data_end_date: null,
          data_day_count: 0,
        })
      }
      if (path === '/settings/targets' || path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ available: false, configured: false })
      return Promise.resolve({ points: [] })
    })

    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('Nutrition overview')
    expect(wrapper.text()).toContain('Remaining')
    expect(wrapper.text()).not.toContain('Ernährungsüberblick')
  })

  it('starts the personal YAZIO sync from the data status card', async () => {
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: { date: '2026-07-23', calories_kcal: 1900, target_kcal: 2200, protein_g: 130, tracking_status: 'complete', tracking_reasons: [] },
          week: { consumed_kcal: 7000, budget_kcal: 15400, deviation_kcal: -8400, remaining_kcal: 8400 },
          protein_7d_average_g: 125,
          last_import_at: '2026-07-23T11:22:23Z',
          data_start_date: '2026-05-25',
          data_end_date: '2026-07-23',
          data_day_count: 60,
        })
      }
      if (path === '/settings/targets') {
        return Promise.resolve([{ id: 'target', valid_from: '2026-01-01', valid_to: null, calories_kcal: 2200, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, water_ml: null, target_weight_min_kg: null, target_weight_max_kg: null }])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') {
        return Promise.resolve({
          available: true,
          configured: true,
          sync_enabled: true,
          sync_interval_minutes: 360,
          sync_days: 7,
          last_attempt_at: '2026-07-23T11:22:22Z',
          last_success_at: '2026-07-23T11:22:23Z',
          next_sync_at: '2026-07-23T17:22:23Z',
          last_error: null,
        })
      }
      if (path === '/yazio/sync' && options?.method === 'POST') {
        return Promise.resolve({
          batch_id: 'batch',
          status: 'completed',
          received: 7,
          inserted: 1,
          updated: 2,
          skipped: 4,
          failed: 0,
          unknown_types: [],
        })
      }
      return Promise.resolve({ points: [] })
    })

    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Automatisch alle 6 Std. · letzte 7 Tage')

    const syncButton = wrapper.findAll('button').find((button) => button.text().includes('Jetzt synchronisieren'))
    expect(syncButton).toBeDefined()
    await syncButton!.trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/yazio/sync', { method: 'POST' })
    expect(wrapper.text()).toContain('1 neu · 2 aktualisiert · 4 unverändert')
  })

  it('draws the calorie budget from each day’s historical target', async () => {
    const points = [
      { date: '2026-07-20', calories_kcal: 1900, target_kcal: 2300 },
      { date: '2026-07-21', calories_kcal: 2000, target_kcal: 2300 },
      { date: '2026-07-22', calories_kcal: 1950, target_kcal: 2100 },
      { date: '2026-07-23', calories_kcal: 2150, target_kcal: 2100 },
    ].map((point) => ({
      ...point,
      maintenance_kcal: null,
      deviation_kcal: point.calories_kcal - point.target_kcal,
      activity_mode: 'off',
      activity_source_type: null,
      active_energy_kcal: null,
      activity_credit_kcal: 0,
      activity_data_status: 'disabled',
      effective_budget_kcal: point.target_kcal,
      effective_maintenance_kcal: null,
      effective_deviation_kcal: point.calories_kcal - point.target_kcal,
      protein_g: 130,
      carbs_g: 220,
      fat_g: 70,
      tracking_status: 'complete',
      tracking_score: 8,
      tracking_reasons: [],
    }))
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: points.at(-1),
          week: {
            consumed_kcal: 7900,
            budget_kcal: 8800,
            deviation_kcal: -900,
            remaining_kcal: 900,
            activity_credit_kcal: 0,
            effective_budget_kcal: 8800,
            effective_deviation_kcal: -900,
            effective_remaining_kcal: 900,
          },
          protein_7d_average_g: 130,
          last_import_at: null,
          data_start_date: '2026-07-20',
          data_end_date: '2026-07-23',
          data_day_count: 4,
        })
      }
      if (path === '/settings/targets') {
        return Promise.resolve([
          { id: 'new', valid_from: '2026-07-22', valid_to: null, calories_kcal: 2100, maintenance_kcal: null, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, activity_mode: 'off', activity_source_type: null, target_weight_min_kg: null, target_weight_max_kg: null },
          { id: 'old', valid_from: '2026-01-01', valid_to: '2026-07-22', calories_kcal: 2300, maintenance_kcal: null, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, activity_mode: 'off', activity_source_type: null, target_weight_min_kg: null, target_weight_max_kg: null },
        ])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ available: true, configured: false })
      return Promise.resolve({ points })
    })

    const wrapper = mount(OverviewView, {
      global: {
        stubs: {
          ChartPanel: {
            props: ['title', 'option', 'empty', 'height'],
            template: '<section><slot name="header-actions" /></section>',
          },
        },
      },
    })
    await flushPromises()
    const caloriePanel = wrapper
      .findAllComponents(ChartPanel)
      .find((panel) => panel.props('title') === 'Kalorienaufnahme')
    const option = caloriePanel!.props('option') as {
      series: Array<{
        name: string
        data: Array<number | null>
        step?: string
        lineStyle?: { color?: string; width?: number; type?: string }
        itemStyle?: { color?: string }
      }>
    }
    const dailyBudgetSeries = option.series.find((series) => series.name === 'Tagesbudget')

    expect(dailyBudgetSeries?.data).toEqual([2300, 2300, 2100, 2100])
    expect(dailyBudgetSeries?.step).toBe('middle')
    expect(dailyBudgetSeries?.lineStyle).toEqual({ color: '#fb923c', width: 2, type: 'dashed' })
    expect(dailyBudgetSeries?.itemStyle).toEqual({ color: '#fb923c' })
    expect(option.series.find((series) => series.name === 'Basisbudget')).toBeUndefined()
    expect(option.series.find((series) => series.name === 'Effektives Budget')).toBeUndefined()
    expect(wrapper.get('.dashboard-period-range').text()).toBe(
      '20.07. – 23.07.2026 · 4/4 mit Daten',
    )

    const highlightSwitch = wrapper.get<HTMLInputElement>('input[role="switch"]')
    expect(highlightSwitch.element.checked).toBe(false)
    await highlightSwitch.setValue(true)

    const highlightedOption = caloriePanel!.props('option') as {
      series: Array<{
        name: string
        data: Array<number | null | { value: number; itemStyle: { color: string } }>
      }>
    }
    const intakeSeries = highlightedOption.series.find((series) => series.name === 'Aufnahme')

    expect(intakeSeries?.data.slice(0, 3)).toEqual([1900, 2000, 1950])
    expect(intakeSeries?.data[3]).toMatchObject({
      value: 2150,
      itemStyle: { color: '#fb7185' },
    })
  })

  it('shows credited activity below the remaining budget and in the calorie tooltip', async () => {
    const point = {
      date: '2026-07-23',
      calories_kcal: 1800,
      target_kcal: 2100,
      maintenance_kcal: null,
      deviation_kcal: -300,
      activity_mode: 'full',
      activity_source_type: 'apple_health_xml',
      active_energy_kcal: 317,
      activity_credit_kcal: 317,
      activity_data_status: 'credited',
      effective_budget_kcal: 2417,
      effective_maintenance_kcal: null,
      effective_deviation_kcal: -617,
      protein_g: 130,
      carbs_g: 220,
      fat_g: 70,
      tracking_status: 'complete',
      tracking_score: 8,
      tracking_reasons: [],
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: point,
          week: {
            consumed_kcal: 1800,
            budget_kcal: 2100,
            deviation_kcal: -300,
            remaining_kcal: 300,
            activity_credit_kcal: 317,
            effective_budget_kcal: 2417,
            effective_deviation_kcal: -617,
            effective_remaining_kcal: 617,
          },
          protein_7d_average_g: 130,
          last_import_at: null,
          data_start_date: point.date,
          data_end_date: point.date,
          data_day_count: 1,
        })
      }
      if (path === '/settings/targets') return Promise.resolve([])
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ available: true, configured: false })
      return Promise.resolve({ points: [point] })
    })
    const wrapper = mount(OverviewView, {
      global: {
        stubs: {
          ChartPanel: {
            props: ['title', 'option', 'empty', 'height'],
            template: '<section><slot name="header-actions" /></section>',
          },
        },
      },
    })
    await flushPromises()

    const caloriePanel = wrapper
      .findAllComponents(ChartPanel)
      .find((panel) => panel.props('title') === 'Kalorienaufnahme')
    const option = caloriePanel!.props('option') as {
      tooltip: { formatter: (params: unknown) => string }
      series: Array<{
        name: string
        data: Array<number | null>
        lineStyle?: { color?: string; width?: number; type?: string }
        itemStyle?: { color?: string }
      }>
    }

    expect(wrapper.text()).toContain('inkl. +317 kcal durch Aktivitäten')
    expect(
      option.tooltip.formatter([{
        axisValueLabel: '23.07.',
        dataIndex: 0,
        marker: '',
        seriesName: 'Aufnahme',
        value: 1800,
      }]),
    ).toContain('Aktivitätsgutschrift: +317 kcal')

    expect(option.series.map((series) => series.name)).toEqual([
      'Aufnahme',
      'Basisbudget',
      'Effektives Budget',
    ])
    expect(option.series.find((series) => series.name === 'Basisbudget')?.lineStyle).toEqual({
      color: '#64748b',
      width: 2,
      type: 'dashed',
    })
    expect(option.series.find((series) => series.name === 'Basisbudget')?.itemStyle).toEqual({
      color: '#64748b',
    })
    expect(option.series.find((series) => series.name === 'Effektives Budget')?.lineStyle).toEqual({
      color: '#fb923c',
      width: 2,
      type: 'dashed',
    })
    expect(option.series.find((series) => series.name === 'Effektives Budget')?.itemStyle).toEqual({
      color: '#fb923c',
    })
    expect(option.series.find((series) => series.name === 'Effektives Budget')?.data).toEqual([2417])

  })

  it('keeps the trends page focused on nutrition without weight tracking', async () => {
    apiMock.mockResolvedValue({
      points: [{
        date: '2026-07-23',
        calories_kcal: 2050,
        target_kcal: 2100,
        maintenance_kcal: null,
        deviation_kcal: -50,
        activity_mode: 'off',
        activity_source_type: null,
        active_energy_kcal: null,
        activity_credit_kcal: 0,
        activity_data_status: 'disabled',
        effective_budget_kcal: 2100,
        effective_maintenance_kcal: null,
        effective_deviation_kcal: -50,
        protein_g: 140,
        carbs_g: 200,
        fat_g: 70,
        tracking_status: 'complete',
        tracking_score: 1,
        tracking_reasons: ['Kalorienwert vorhanden'],
        average_7d: 2000,
        average_14d: 1980,
        average_28d: 1950,
      }],
      budget_balance: {
        tracked_days: 1,
        within_budget_days: 1,
        over_budget_days: 0,
        over_maintenance_days: 0,
        unclassified_budget_days: 0,
      },
    })

    const wrapper = mount(TrendsView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('ohne historisches Budget')

    expect(wrapper.text()).toContain('Protein am letzten Tag')
    expect(wrapper.text()).not.toContain('Gewicht')
    const chartPanels = wrapper.findAllComponents(ChartPanel)
    expect(chartPanels.map((panel) => panel.props('title'))).toEqual([
      'Kalorien und gleitende Mittelwerte',
      'Makronährstoffe',
    ])
    const calorieOption = chartPanels[0].props('option') as {
      xAxis: { data: string[] }
      legend: { data: string[] }
      series: Array<{ name: string; data: unknown[] }>
    }
    expect(calorieOption.xAxis.data).toEqual(['23.07.'])
    expect(calorieOption.legend.data).not.toContain('Aktivitätsgutschrift')
    expect(calorieOption.series.map((series) => series.name)).toEqual([
      'Kalorienaufnahme',
      '7-Tage-Schnitt',
      '14-Tage-Schnitt',
      '28-Tage-Schnitt',
      'Tagesbudget',
    ])
    expect(calorieOption.series.at(-1)?.data).toEqual([2100])
  })
  it('zeigt die historische Budgetbilanz als neutrale Trends-Statistik', async () => {
    apiMock.mockResolvedValue({
      points: [],
      budget_balance: {
        tracked_days: 81,
        within_budget_days: 64,
        over_budget_days: 12,
        over_maintenance_days: 5,
        unclassified_budget_days: 2,
      },
    })

    const wrapper = mount(TrendsView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    const balance = wrapper.get('.budget-balance-section')
    expect(balance.text()).toContain('Budgetbilanz')
    expect(balance.text()).toContain('Seit deinem ersten getrackten Tag')
    expect(balance.text()).toContain('Getrackte Tage81')
    expect(balance.text()).toContain('Im Budget64')
    expect(balance.text()).toContain('Über Budget12')
    expect(balance.text()).toContain('Über Erhaltungsbedarf5')
    expect(balance.text()).toContain('davon 2 ohne historisches Budget')
  })
  it('zeigt historische Aktivitätsgutschriften mit korrekter Null- und Fehlend-Semantik', async () => {
    apiMock.mockResolvedValue({
      points: [
        {
          date: '2026-07-20',
          calories_kcal: 1450,
          target_kcal: 1580,
          maintenance_kcal: null,
          deviation_kcal: -130,
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          active_energy_kcal: 999,
          activity_credit_kcal: 310,
          activity_data_status: 'credited',
          effective_budget_kcal: 1890,
          effective_maintenance_kcal: null,
          effective_deviation_kcal: -440,
          protein_g: 120,
          carbs_g: 150,
          fat_g: 50,
          tracking_status: 'complete',
          tracking_score: 1,
          tracking_reasons: [],
          average_7d: 1450,
          average_14d: 1450,
          average_28d: 1450,
        },
        {
          date: '2026-07-21',
          calories_kcal: 1500,
          target_kcal: 1580,
          maintenance_kcal: null,
          deviation_kcal: -80,
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          active_energy_kcal: 0,
          activity_credit_kcal: 0,
          activity_data_status: 'credited',
          effective_budget_kcal: 1580,
          effective_maintenance_kcal: null,
          effective_deviation_kcal: -80,
          protein_g: 121,
          carbs_g: 151,
          fat_g: 51,
          tracking_status: 'complete',
          tracking_score: 1,
          tracking_reasons: [],
          average_7d: 1475,
          average_14d: 1475,
          average_28d: 1475,
        },
        {
          date: '2026-07-22',
          calories_kcal: 1510,
          target_kcal: 1580,
          maintenance_kcal: null,
          deviation_kcal: -70,
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          active_energy_kcal: 410,
          activity_credit_kcal: 0,
          activity_data_status: 'missing',
          effective_budget_kcal: 1580,
          effective_maintenance_kcal: null,
          effective_deviation_kcal: -70,
          protein_g: 122,
          carbs_g: 152,
          fat_g: 52,
          tracking_status: 'complete',
          tracking_score: 1,
          tracking_reasons: [],
          average_7d: 1487,
          average_14d: 1487,
          average_28d: 1487,
        },
        {
          date: '2026-07-23',
          calories_kcal: 1520,
          target_kcal: 1580,
          maintenance_kcal: null,
          deviation_kcal: -60,
          activity_mode: 'off',
          activity_source_type: null,
          active_energy_kcal: 500,
          activity_credit_kcal: 0,
          activity_data_status: 'disabled',
          effective_budget_kcal: 1580,
          effective_maintenance_kcal: null,
          effective_deviation_kcal: -60,
          protein_g: 123,
          carbs_g: 153,
          fat_g: 53,
          tracking_status: 'complete',
          tracking_score: 1,
          tracking_reasons: [],
          average_7d: 1495,
          average_14d: 1495,
          average_28d: 1495,
        },
      ],
    })

    const wrapper = mount(TrendsView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    const option = wrapper.findAllComponents(ChartPanel)[0].props('option') as {
      legend: { data: string[] }
      series: Array<{
        name: string
        type: string
        data: unknown[]
        stack?: string
        barMaxWidth?: number
        itemStyle?: { color?: string; borderColor?: string; borderWidth?: number }
      }>
      tooltip: { formatter: (params: unknown) => string }
    }
    expect(option.legend.data).toContain('Aktivitätsgutschrift')
    const barSeries = option.series.filter((series) => series.type === 'bar')
    expect(barSeries.map((series) => series.name)).toEqual(['Kalorienaufnahme', 'Aktivitätsgutschrift'])
    expect(barSeries.map((series) => series.stack)).toEqual(['calories', 'calories'])
    expect(barSeries.map((series) => series.barMaxWidth)).toEqual([24, 24])
    expect(option.series.filter((series) => series.type === 'line').every((series) => series.stack == null)).toBe(true)
    const activitySeries = option.series.find((series) => series.name === 'Aktivitätsgutschrift')
    expect(activitySeries?.data).toEqual([310, null, null, null])
    expect(activitySeries?.itemStyle).toMatchObject({
      color: '#f6c445',
      borderColor: '#ffe08a',
      borderWidth: 1,
    })
    expect(activitySeries?.itemStyle?.color).not.toBe('#fb923c')
    expect(option.series.find((series) => series.name === 'Effektives Tagesbudget')?.data).toEqual([1890, null, null, null])

    const tooltip = option.tooltip.formatter([{
      axisValueLabel: '20.07.',
      dataIndex: 0,
      marker: '',
      seriesName: 'Kalorienaufnahme',
      value: 1450,
    }])
    expect(tooltip).not.toContain('Gesamtbalken')
    expect(tooltip).toContain('Aktivitätsgutschrift: +310 kcal')
    expect(tooltip).toContain('Basisbudget: 1.580 kcal')
    expect(tooltip).toContain('Effektives Tagesbudget: 1.890 kcal')
    expect(tooltip).toContain('7-Tage-Schnitt: 1.450 kcal')

    const missingActivityTooltip = option.tooltip.formatter([{
      axisValueLabel: '22.07.',
      dataIndex: 2,
      marker: '',
      seriesName: 'Kalorienaufnahme',
      value: 1510,
    }])
    expect(missingActivityTooltip).toContain('Tagesbudget: 1.580 kcal')
    expect(missingActivityTooltip).not.toContain('Aktivitätsgutschrift')
  })

  it('does not backfill historical days with the current calorie target', async () => {
    const dailyPoints = [
      { date: '2026-07-20', calories_kcal: '1005.850', target_kcal: null },
      { date: '2026-07-21', calories_kcal: '1129.0668', target_kcal: null },
      { date: '2026-07-22', calories_kcal: '1119.5868', target_kcal: null },
      { date: '2026-07-23', calories_kcal: '524.000', target_kcal: '2200.000' },
    ].map((point) => ({
      ...point,
      deviation_kcal:
        point.target_kcal == null
          ? null
          : Number(point.calories_kcal) - Number(point.target_kcal),
      maintenance_kcal: null,
      activity_mode: 'off',
      activity_source_type: null,
      active_energy_kcal: null,
      activity_credit_kcal: 0,
      activity_data_status: 'disabled',
      effective_budget_kcal: point.target_kcal,
      effective_maintenance_kcal: null,
      effective_deviation_kcal: point.target_kcal == null ? null : Number(point.calories_kcal) - Number(point.target_kcal),
      tracking_status: 'probably_incomplete',
    }))

    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: dailyPoints.at(-1),
          week: { consumed_kcal: 3778.5036, budget_kcal: 8800, deviation_kcal: -5021.4964, remaining_kcal: 5021.4964, activity_credit_kcal: 0, effective_budget_kcal: 8800, effective_deviation_kcal: -5021.4964, effective_remaining_kcal: 5021.4964 },
          protein_7d_average_g: 80,
          last_import_at: '2026-07-23T11:22:23Z',
          data_start_date: '2026-07-20',
          data_end_date: '2026-07-23',
          data_day_count: 4,
        })
      }
      if (path === '/settings/targets') {
        return Promise.resolve([{ id: 'target', valid_from: '2026-07-23', valid_to: null, calories_kcal: 2200, maintenance_kcal: null, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, water_ml: null, activity_mode: 'off', activity_source_type: null, target_weight_min_kg: null, target_weight_max_kg: null }])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') {
        return Promise.resolve({
          available: true,
          configured: true,
          sync_enabled: true,
          sync_interval_minutes: 360,
          sync_days: 7,
          last_attempt_at: null,
          last_success_at: null,
          next_sync_at: null,
          last_error: null,
        })
      }
      return Promise.resolve({ points: dailyPoints })
    })

    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    const budgetRow = wrapper
      .findAll('.weekly-summary-list > div')
      .find((row) => row.text().includes('Kalorienbudget eingehalten'))
    expect(budgetRow).toBeDefined()
    expect(budgetRow!.text()).toContain('1 von 1 Tag')
    expect(budgetRow!.text()).not.toContain('4 von 4 Tagen')
    expect(wrapper.text()).not.toContain('Aktivitätsgutschrift')
  })

  it('renders an empty weekly budget without converting missing data to zero days', async () => {
    apiMock.mockResolvedValue({ weeks: [] })
    const wrapper = mount(WeeklyView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Wochenbudget')
    expect(wrapper.text()).toContain('Noch keine Wochenwerte vorhanden.')
    expect(wrapper.text()).not.toContain('0 kcal')
  })
  it('uses the configured Sunday week start in weekly range labels', async () => {
    apiMock.mockResolvedValue({
      weeks: [{
        week_start: '2026-08-09',
        consumed_kcal: 1900,
        budget_kcal: 2200,
        deviation_kcal: -300,
        remaining_kcal: 300,
        activity_credit_kcal: 0,
        effective_budget_kcal: 2200,
        effective_deviation_kcal: -300,
        effective_remaining_kcal: 300,
        mean_kcal: 1900,
        median_kcal: 1900,
      }],
    })
    const auth = useAuthStore()
    auth.user = { ...user, week_starts_on: 6 }

    const wrapper = mount(WeeklyView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('Sonntag bis Samstag')
    expect(wrapper.get('tbody tr td').text()).toContain('09.08. – 15.08.2026')
    expect(wrapper.text()).not.toContain('Montag bis Sonntag')
  })

  it('shows missing weekly targets as empty instead of a zero budget', async () => {
    apiMock.mockResolvedValue({
      weeks: [{
        week_start: '2026-08-10',
        consumed_kcal: 1900,
        budget_kcal: null,
        deviation_kcal: null,
        remaining_kcal: null,
        activity_credit_kcal: 0,
        effective_budget_kcal: null,
        effective_deviation_kcal: null,
        effective_remaining_kcal: null,
        mean_kcal: 1900,
        median_kcal: 1900,
      }],
    })
    const wrapper = mount(WeeklyView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('Wochenrest–')
    expect(wrapper.text()).not.toContain('Wochenrest0 kcal')
    expect(wrapper.findAll('td').filter((cell) => cell.text() === '–')).toHaveLength(2)
    const differenceCell = wrapper.get('tbody tr').findAll('td')[3]
    expect(differenceCell.classes()).not.toContain('under')
    expect(differenceCell.classes()).not.toContain('over')
  })

  it('hebt Wochen über dem Budget standardmäßig hervor und lässt sich umschalten', async () => {
    apiMock.mockResolvedValue({
      weeks: [
        { week_start: '2026-07-20', consumed_kcal: 2500, budget_kcal: 2200, deviation_kcal: 300, remaining_kcal: -300, activity_credit_kcal: 0, effective_budget_kcal: 2200, effective_deviation_kcal: 300, effective_remaining_kcal: -300, mean_kcal: 2500, median_kcal: 2500 },
        { week_start: '2026-07-27', consumed_kcal: 2200, budget_kcal: 2200, deviation_kcal: 0, remaining_kcal: 0, activity_credit_kcal: 0, effective_budget_kcal: 2200, effective_deviation_kcal: 0, effective_remaining_kcal: 0, mean_kcal: 2200, median_kcal: 2200 },
        { week_start: '2026-08-03', consumed_kcal: 1900, budget_kcal: 2200, deviation_kcal: -300, remaining_kcal: 300, activity_credit_kcal: 0, effective_budget_kcal: 2200, effective_deviation_kcal: -300, effective_remaining_kcal: 300, mean_kcal: 1900, median_kcal: 1900 },
        { week_start: '2026-08-10', consumed_kcal: 2100, budget_kcal: null, deviation_kcal: null, remaining_kcal: null, activity_credit_kcal: 0, effective_budget_kcal: null, effective_deviation_kcal: null, effective_remaining_kcal: null, mean_kcal: 2100, median_kcal: 2100 },
      ],
    })
    const wrapper = mount(WeeklyView, {
      global: {
        stubs: {
          ChartPanel: {
            props: ['title', 'option', 'empty', 'height'],
            template: '<section><slot name="header-actions" /></section>',
          },
        },
      },
    })
    await flushPromises()

    const panel = wrapper.findComponent(ChartPanel)
    const getIntakeData = () => {
      // ChartPanel erhält hier die von WeeklyView erzeugte, bekannte Teststruktur.
      const option = panel.props('option') as { series: Array<{ name: string; data: unknown[] }> }
      return option.series.find((series) => series.name === 'Aufnahme')!.data
    }
    const highlightSwitch = wrapper.get<HTMLInputElement>('input[role="switch"]')

    expect(highlightSwitch.element.checked).toBe(true)
    expect(getIntakeData()).toEqual([
      { value: 2500, itemStyle: { color: '#fb7185' } },
      2200,
      1900,
      2100,
    ])

    await highlightSwitch.setValue(false)
    await flushPromises()
    expect(getIntakeData()).toEqual([2500, 2200, 1900, 2100])

    await highlightSwitch.setValue(true)
    await flushPromises()
    expect(getIntakeData()[0]).toMatchObject({
      value: 2500,
      itemStyle: { color: '#fb7185' },
    })
  })

  it('keeps historical activity credits visible without duplicating inactive weekly budgets', async () => {
    apiMock.mockResolvedValue({
      weeks: [
        { week_start: '2026-07-20', consumed_kcal: 2100, budget_kcal: 2200, deviation_kcal: -100, remaining_kcal: 100, activity_credit_kcal: 0, effective_budget_kcal: 2200, effective_deviation_kcal: -100, effective_remaining_kcal: 100, mean_kcal: 2100, median_kcal: 2100 },
        { week_start: '2026-07-27', consumed_kcal: 2300, budget_kcal: 2200, deviation_kcal: 100, remaining_kcal: -100, activity_credit_kcal: 300, effective_budget_kcal: 2500, effective_deviation_kcal: -200, effective_remaining_kcal: 200, mean_kcal: 2300, median_kcal: 2300 },
      ],
    })
    const wrapper = mount(WeeklyView, {
      global: {
        stubs: {
          ChartPanel: {
            props: ['title', 'option', 'empty', 'height'],
            template: '<section />',
          },
        },
      },
    })
    await flushPromises()

    const panel = wrapper.findComponent(ChartPanel)
    const option = panel.props('option') as { series: Array<{ name: string; data: Array<number | null> }> }
    const rows = wrapper.findAll('tbody tr')

    expect(wrapper.text()).toContain('Verbleibend nach Aktivität')
    const headers = wrapper.findAll('thead th').map((header) => header.text())
    expect(headers).toContain('Aktivitätsgutschrift')
    expect(headers).toContain('Abweichung')
    expect(headers).not.toContain('Abweichung zum effektiven Budget')
    expect(option.series.find((series) => series.name === 'Effektives Budget')?.data).toEqual([null, 2500])
    expect(rows[0].findAll('td').map((cell) => cell.text())).toContain('+300 kcal')
    expect(rows[0].findAll('td')[5].text()).toBe('-200 kcal')
    expect(rows[1].findAll('td').slice(3, 5).map((cell) => cell.text())).toEqual(['–', '–'])
    expect(rows[1].findAll('td')[5].text()).toBe('-100 kcal')
    expect(wrapper.text()).toContain('2 von 2')
  })

  it('uses budget thresholds, calculates numeric averages, and navigates by month', async () => {
    apiMock.mockResolvedValue({
      days: [
        { date: '2026-07-18', calories_kcal: '1600.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', deviation_kcal: '-200.000', activity_mode: 'off', activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: '0.000', activity_data_status: 'disabled', effective_budget_kcal: '1800.000', effective_maintenance_kcal: '2200.000', effective_deviation_kcal: '-200.000', tracking_status: 'complete', classification: 'under_budget' },
        { date: '2026-07-19', calories_kcal: '2000.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', deviation_kcal: '200.000', activity_mode: 'off', activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: '0.000', activity_data_status: 'disabled', effective_budget_kcal: '1800.000', effective_maintenance_kcal: '2200.000', effective_deviation_kcal: '200.000', tracking_status: 'complete', classification: 'over_budget' },
        { date: '2026-07-20', calories_kcal: '2400.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', deviation_kcal: '600.000', activity_mode: 'off', activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: '0.000', activity_data_status: 'disabled', effective_budget_kcal: '1800.000', effective_maintenance_kcal: '2200.000', effective_deviation_kcal: '600.000', tracking_status: 'complete', classification: 'above_maintenance' },
        { date: '2026-07-21', calories_kcal: '2800.000', target_kcal: '3000.000', maintenance_kcal: '2500.000', deviation_kcal: '-200.000', activity_mode: 'off', activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: '0.000', activity_data_status: 'disabled', effective_budget_kcal: '3000.000', effective_maintenance_kcal: '2500.000', effective_deviation_kcal: '-200.000', tracking_status: 'complete', classification: 'under_budget' },
        { date: '2026-07-22', calories_kcal: '3200.000', target_kcal: '3000.000', maintenance_kcal: '2500.000', deviation_kcal: '200.000', activity_mode: 'off', activity_source_type: null, active_energy_kcal: null, activity_credit_kcal: '0.000', activity_data_status: 'disabled', effective_budget_kcal: '3000.000', effective_maintenance_kcal: '2500.000', effective_deviation_kcal: '200.000', tracking_status: 'complete', classification: 'above_maintenance' },
      ],
    })
    const wrapper = mount(CalendarView)
    await flushPromises()
    expect(wrapper.text()).toContain('Im Budget')
    expect(wrapper.text()).toContain('Über Budget')
    expect(wrapper.text()).toContain('Über Budget und Erhaltungsbedarf')
    expect(wrapper.text()).toContain('2.400 kcal')
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.findAll('.calendar-day.under_budget')).toHaveLength(2)
    expect(wrapper.findAll('.calendar-day.over_budget')).toHaveLength(1)
    expect(wrapper.findAll('.calendar-day.above_maintenance')).toHaveLength(2)
    expect(wrapper.get('.calendar-day.under_budget').attributes('aria-label')).toBe(
      '18.07.2026: Im Budget',
    )
    const calorieProgress = wrapper.findAll<HTMLProgressElement>(
      'progress.calendar-calorie-progress',
    )
    expect(calorieProgress).toHaveLength(5)
    expect(Number(calorieProgress[0].attributes('value'))).toBeCloseTo(1600 / 1800)
    expect(calorieProgress[1].attributes('value')).toBe('1')
    expect(calorieProgress[2].attributes('value')).toBe('1')
    expect(Number(calorieProgress[3].attributes('value'))).toBeCloseTo(2800 / 3000)
    expect(calorieProgress[0].attributes('aria-label')).toBe(
      '1.600 von 1.800 kcal Tagesbudget',
    )
    expect(apiMock.mock.calls[0][0]).toMatch(
      /^\/analytics\/calendar\?start=\d{4}-\d{2}-01&end=\d{4}-\d{2}-\d{2}$/,
    )

    const initialPath = apiMock.mock.calls[0][0]
    await wrapper.find('button[aria-label="Vorheriger Monat"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(apiMock.mock.calls[1][0]).not.toBe(initialPath)
  })

  it('uses effective calendar budgets only for positive activity credits', async () => {
    const base = {
      calories_kcal: 1800,
      target_kcal: 2000,
      maintenance_kcal: null,
      deviation_kcal: -200,
      active_energy_kcal: null,
      effective_maintenance_kcal: null,
      protein_g: 120,
      carbs_g: 180,
      fat_g: 60,
      tracking_status: 'complete',
      tracking_score: 1,
      tracking_reasons: [],
      classification: 'under_budget',
    }
    apiMock.mockResolvedValue({
      days: [
        {
          ...base,
          date: '2026-07-18',
          calories_kcal: 2100,
          deviation_kcal: 100,
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          active_energy_kcal: 300,
          activity_credit_kcal: 300,
          activity_data_status: 'credited',
          effective_budget_kcal: 2300,
          effective_deviation_kcal: -200,
        },
        {
          ...base,
          date: '2026-07-19',
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          activity_credit_kcal: 0,
          activity_data_status: 'credited',
          effective_budget_kcal: 2000,
          effective_deviation_kcal: -200,
        },
        {
          ...base,
          date: '2026-07-20',
          activity_mode: 'full',
          activity_source_type: 'apple_health_xml',
          activity_credit_kcal: 0,
          activity_data_status: 'missing',
          effective_budget_kcal: 2000,
          effective_deviation_kcal: -200,
        },
        {
          ...base,
          date: '2026-07-21',
          activity_mode: 'off',
          activity_source_type: null,
          active_energy_kcal: 300,
          activity_credit_kcal: 0,
          activity_data_status: 'disabled_with_data',
          effective_budget_kcal: 2000,
          effective_deviation_kcal: -200,
        },
      ],
    })
    const wrapper = mount(CalendarView)
    await flushPromises()
    const [credited, creditedZero, missing, disabledWithData] = wrapper.findAll('.calendar-day')

    expect(credited.text()).toContain('Basisbudget 2.000')
    expect(credited.text()).toContain('Aktivitätsgutschrift +300')
    expect(credited.text()).toContain('Effektives Budget 2.300')
    expect(credited.text()).toContain('Aktivitätskalorien berücksichtigt')
    expect(credited.get('progress').attributes('aria-label')).toBe(
      '2.100 von 2.300 kcal Effektives Budget',
    )

    for (const day of [creditedZero, missing, disabledWithData]) {
      expect(day.text()).toMatch(/Tagesbudget\s+2\.000\s+kcal/)
      expect(day.text()).not.toContain('Aktivitätsgutschrift')
      expect(day.text()).not.toContain('Effektives Budget')
      expect(day.get('progress').attributes('aria-label')).toBe(
        '1.800 von 2.000 kcal Tagesbudget',
      )
    }
  })

  it('labels nutrition without a target without assigning a budget class', async () => {
    apiMock.mockResolvedValue({
      days: [{
        date: '2026-07-18',
        calories_kcal: '1600.000',
        target_kcal: null,
        maintenance_kcal: null,
        tracking_status: 'complete',
        classification: 'no_target',
      }],
    })
    const wrapper = mount(CalendarView)
    await flushPromises()

    expect(wrapper.get('.calendar-day.no_target').text()).toContain('Kein Ziel festgelegt')
    expect(wrapper.find('.calendar-day.under_budget').exists()).toBe(false)
    expect(wrapper.find('.calendar-day.over_budget').exists()).toBe(false)
  })

  it('calculates daily averages from decimal strings without rendering NaN', async () => {
    apiMock.mockResolvedValue([
      {
        date: '2026-07-18',
        calories_kcal: '1600.000',
        target_kcal: '2000.000',
        maintenance_kcal: null,
        deviation_kcal: '-400.000',
        protein_g: '120.000',
        carbs_g: '180.000',
        fat_g: '60.000',
        tracking_status: 'complete',
        tracking_score: 1,
        tracking_reasons: [],
      },
      {
        date: '2026-07-19',
        calories_kcal: '2000.000',
        target_kcal: '2000.000',
        maintenance_kcal: null,
        deviation_kcal: '0.000',
        protein_g: '140.000',
        carbs_g: '220.000',
        fat_g: '70.000',
        tracking_status: 'complete',
        tracking_score: 1,
        tracking_reasons: [],
      },
    ])
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: DailyView }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(DailyView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).toContain('1.800 kcal')
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).toContain('Protein')
    expect(wrapper.text()).not.toContain('Eiweiß')
    expect(apiMock.mock.calls[0][0]).toMatch(
      /^\/analytics\/daily\?start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}$/,
    )
  })
  it('sorts daily values by raw data, keeps missing values last, and preserves the choice after a data reload', async () => {
    const dailyPoints = [
      {
        date: '2026-07-18',
        calories_kcal: 900,
        deviation_kcal: -50,
        protein_g: 40,
        carbs_g: 100,
        fat_g: null,
        tracking_status: 'complete',
      },
      {
        date: '2026-07-20',
        calories_kcal: 10000,
        deviation_kcal: 250,
        protein_g: null,
        carbs_g: 300,
        fat_g: 100,
        tracking_status: 'probably_incomplete',
      },
      {
        date: '2026-07-19',
        calories_kcal: null,
        deviation_kcal: 100,
        protein_g: 120,
        carbs_g: null,
        fat_g: 60,
        tracking_status: 'no_data',
      },
      {
        date: '2026-07-21',
        calories_kcal: 1200,
        deviation_kcal: 250,
        protein_g: 120,
        carbs_g: 300,
        fat_g: 60,
        tracking_status: 'probably_complete',
      },
      {
        date: '2026-07-17',
        calories_kcal: 900,
        deviation_kcal: -100,
        protein_g: 20,
        carbs_g: 80,
        fat_g: 0,
        tracking_status: 'future_status',
      },
    ]
    apiMock.mockResolvedValue(dailyPoints)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: DailyView }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(DailyView, { global: { plugins: [router] } })
    await flushPromises()
    const rowDates = () => wrapper.findAll('tbody tr').map((row) => row.find('td').text())
    const rowValues = (column: number) =>
      wrapper.findAll('tbody tr').map((row) => row.findAll('td')[column].text())
    const sortButtons = () => wrapper.findAll('thead button')

    expect(rowDates()).toEqual(['21.07.2026', '20.07.2026', '19.07.2026', '18.07.2026', '17.07.2026'])
    expect(wrapper.findAll('thead th').map((header) => header.attributes('aria-sort'))).toEqual([
      'descending', 'none', 'none', 'none', 'none', 'none', 'none',
    ])
    expect(wrapper.text()).toContain('5')
    expect(wrapper.text()).toContain('3.250 kcal')

    await sortButtons()[0].trigger('click')
    expect(rowDates()).toEqual(['17.07.2026', '18.07.2026', '19.07.2026', '20.07.2026', '21.07.2026'])
    await sortButtons()[0].trigger('click')
    expect(rowDates()).toEqual(['21.07.2026', '20.07.2026', '19.07.2026', '18.07.2026', '17.07.2026'])

    await sortButtons()[2].trigger('click')
    expect(rowValues(2)).toEqual(['10.000 kcal', '1.200 kcal', '900 kcal', '900 kcal', '–'])
    expect(wrapper.findAll('thead th').map((header) => header.attributes('aria-sort'))[2]).toBe('descending')
    await sortButtons()[2].trigger('click')
    expect(rowValues(2)).toEqual(['900 kcal', '900 kcal', '1.200 kcal', '10.000 kcal', '–'])
    expect(wrapper.text()).toContain('3.250 kcal')

    await sortButtons()[3].trigger('click')
    expect(rowValues(3)).toEqual(['250 kcal', '250 kcal', '100 kcal', '-50 kcal', '-100 kcal'])
    await sortButtons()[3].trigger('click')
    expect(rowValues(3)).toEqual(['-100 kcal', '-50 kcal', '100 kcal', '250 kcal', '250 kcal'])

    await sortButtons()[4].trigger('click')
    expect(rowValues(4)).toEqual(['120 g', '120 g', '40 g', '20 g', '–'])
    await sortButtons()[4].trigger('click')
    expect(rowValues(4)).toEqual(['20 g', '40 g', '120 g', '120 g', '–'])

    await sortButtons()[5].trigger('click')
    expect(rowValues(5)).toEqual(['300 g', '300 g', '100 g', '80 g', '–'])
    await sortButtons()[5].trigger('click')
    expect(rowValues(5)).toEqual(['80 g', '100 g', '300 g', '300 g', '–'])

    await sortButtons()[6].trigger('click')
    expect(rowValues(6)).toEqual(['100 g', '60 g', '60 g', '0 g', '–'])
    await sortButtons()[6].trigger('click')
    expect(rowValues(6)).toEqual(['0 g', '60 g', '60 g', '100 g', '–'])

    await sortButtons()[1].trigger('click')
    expect(rowDates()).toEqual(['21.07.2026', '18.07.2026', '20.07.2026', '19.07.2026', '17.07.2026'])
    expect(rowValues(1)).toEqual(['●Erfasst', '●Erfasst', '●Kalorienwert fehlt', '●Keine Daten', '●future_status'])
    await sortButtons()[1].trigger('click')
    expect(rowDates()).toEqual(['17.07.2026', '19.07.2026', '20.07.2026', '21.07.2026', '18.07.2026'])
    expect(rowValues(1)).toEqual(['●future_status', '●Keine Daten', '●Kalorienwert fehlt', '●Erfasst', '●Erfasst'])

    apiMock.mockResolvedValueOnce(dailyPoints.slice().reverse())
    await wrapper.get('.filter-panel button').trigger('click')
    await flushPromises()
    expect(rowDates()).toEqual(['17.07.2026', '19.07.2026', '20.07.2026', '21.07.2026', '18.07.2026'])
    wrapper.unmount()
  })

  it.each([
    ['activity mode off', { activity_mode: 'off', activity_data_status: 'disabled', activity_credit_kcal: 0 }],
    ['activity mode full without source data', { activity_mode: 'full', activity_data_status: 'missing', activity_credit_kcal: 0 }],
    ['disabled activity data', { activity_mode: 'off', activity_data_status: 'disabled_with_data', activity_credit_kcal: 0 }],
  ])('hides the activity credit column for %s', async (_scenario, activity) => {
    apiMock.mockResolvedValue([{
      date: '2026-07-18',
      calories_kcal: 1800,
      deviation_kcal: -200,
      protein_g: 120,
      carbs_g: 180,
      fat_g: 60,
      tracking_status: 'complete',
      ...activity,
    }])
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: DailyView }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(DailyView, { global: { plugins: [router] } })
    await flushPromises()

    expect(wrapper.text()).not.toContain('Aktivitätsgutschrift')
    wrapper.unmount()
  })

  it('shows and sorts historical activity credits only when activity is relevant', async () => {
    apiMock.mockResolvedValue([
      {
        date: '2026-07-18',
        calories_kcal: 1800,
        deviation_kcal: -200,
        activity_mode: 'full',
        activity_credit_kcal: 317,
        activity_data_status: 'credited',
        protein_g: 120,
        carbs_g: 180,
        fat_g: 60,
        tracking_status: 'complete',
      },
      {
        date: '2026-07-19',
        calories_kcal: 1800,
        deviation_kcal: -200,
        activity_mode: 'full',
        activity_credit_kcal: 0,
        activity_data_status: 'credited',
        protein_g: 120,
        carbs_g: 180,
        fat_g: 60,
        tracking_status: 'complete',
      },
      {
        date: '2026-07-20',
        calories_kcal: 1800,
        deviation_kcal: -200,
        activity_mode: 'full',
        activity_credit_kcal: 0,
        activity_data_status: 'missing',
        protein_g: 120,
        carbs_g: 180,
        fat_g: 60,
        tracking_status: 'complete',
      },
      {
        date: '2026-07-21',
        calories_kcal: 1800,
        deviation_kcal: -200,
        activity_mode: 'off',
        activity_credit_kcal: 0,
        activity_data_status: 'disabled',
        protein_g: 120,
        carbs_g: 180,
        fat_g: 60,
        tracking_status: 'complete',
      },
    ])
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: DailyView }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(DailyView, { global: { plugins: [router] } })
    await flushPromises()

    const activityHeader = wrapper.findAll('thead button').find(
      (button) => button.text().includes('Aktivitätsgutschrift'),
    )
    const activityValues = () =>
      wrapper.findAll('tbody tr').map((row) => row.findAll('td')[4].text())

    expect(activityHeader).toBeDefined()
    expect(activityValues()).toEqual(['–', '–', '–', '+317 kcal'])
    await activityHeader!.trigger('click')
    expect(activityValues()).toEqual(['+317 kcal', '–', '–', '–'])
  })


  it('filters weekday analysis by week or a custom date range', async () => {
    apiMock.mockResolvedValue({
      weekdays: [
        {
          weekday: 0,
          label: 'Montag',
          count: 4,
          mean_kcal: 1900,
          median_kcal: 1880,
          p25_kcal: 1800,
          p75_kcal: 2000,
          mean_deviation_kcal: -100,
          mean_protein_g: 130,
        },
      ],
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: WeekdaysView }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(WeekdaysView, {
      global: { plugins: [router], stubs: { ChartPanel: true } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Aktuelle Woche')
    expect(wrapper.text()).toContain('Letzte Woche')
    expect(wrapper.text()).toContain('6 Monate')
    expect(apiMock.mock.calls[0][0]).toMatch(
      /^\/analytics\/weekdays\?start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}$/,
    )
    const initialPath = apiMock.mock.calls[0][0]
    await wrapper.find('select').setValue('last-week')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(apiMock.mock.calls[1][0]).not.toBe(initialPath)
  })

  it('renders import batch errors and status counts', async () => {
    const batch = { id: 'batch', source_type: 'test', client_identifier: null, status: 'completed_with_errors', started_at: '2026-07-19T10:00:00Z', finished_at: '2026-07-19T10:01:00Z', received: 5, inserted: 3, updated: 0, skipped: 1, failed: 1, unknown_types: ['unknown'], error_message: null }
    apiMock.mockImplementation((path: string) => {
      if (path === '/imports') return Promise.resolve([batch])
      return Promise.resolve({ ...batch, errors: [{ item_index: 4, metric_type: 'protein', error_code: 'invalid_sample', safe_detail: 'Messwert ist keine gültige Zahl' }] })
    })
    const wrapper = mount(ImportsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Mit Fehlern')
    expect(wrapper.text()).toContain('5')
    expect(wrapper.get('.import-submit').classes()).toContain('compact-action')
    const actionColumn = wrapper.get('.import-action-column')
    expect(actionColumn.get('.import-file-picker').text()).toContain('Datei auswählen')
    const importButton = actionColumn.get<HTMLButtonElement>('.import-submit')
    expect(importButton.text()).toBe('Datei importieren')
    expect(importButton.attributes('disabled')).toBeDefined()
    const details = wrapper.findAll('button').find((button) => button.text() === 'Details')
    await details!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Messwert ist keine gültige Zahl')
  })


  it('shows German date fields while queuing a YAZIO date range as ISO values', async () => {
    const status = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      sync_interval_override_minutes: null,
      sync_days_override: null,
      historical_sync: {
        state: 'idle',
        start_date: null,
        end_date: null,
        started_at: null,
        completed_at: null,
        last_error: null,
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve(status)
      if (path === '/yazio/sync/history/range' && options?.method === 'POST') {
        return Promise.resolve({
          ...status,
          historical_sync: {
            ...status.historical_sync,
            state: 'pending',
            start_date: '2024-03-01',
            end_date: '2026-08-12',
          },
        })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(ImportsView)
    await flushPromises()
    const dateInputs = wrapper.findAll<HTMLInputElement>('input[type="text"]')
    expect(dateInputs).toHaveLength(2)
    expect(dateInputs[0].attributes('placeholder')).toBe('TT.MM.JJJJ')
    expect(dateInputs[1].attributes('placeholder')).toBe('TT.MM.JJJJ')
    expect(wrapper.get('button.secondary.compact-action').classes()).toContain('compact-action')

    await dateInputs[0].setValue('01.03.2024')
    await dateInputs[1].setValue('12.08.2026')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === 'Zeitraum synchronisieren')!
      .trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/yazio/sync/history/range', {
      method: 'POST',
      body: JSON.stringify({ from_date: '2024-03-01', end_date: '2026-08-12' }),
    })
  })

  it('stops historical polling when its initial request settles after unmount', async () => {
    vi.useFakeTimers()
    let resolveImports!: (value: never[]) => void
    let resolveStatus!: (value: Record<string, unknown>) => void
    apiMock.mockImplementation((path: string) => {
      if (path === '/imports') {
        return new Promise<never[]>((resolve) => {
          resolveImports = resolve
        })
      }
      if (path === '/yazio/status') {
        return new Promise<Record<string, unknown>>((resolve) => {
          resolveStatus = resolve
        })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(ImportsView)
    expect(apiMock).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    resolveImports([])
    resolveStatus({
      available: true,
      configured: true,
      sync_enabled: true,
      historical_sync: { state: 'pending' },
    })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5_000)
    expect(apiMock).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })

  it('explains that failed history imports need credential renewal when sync is paused', async () => {
    const status = {
      available: true,
      configured: true,
      sync_enabled: false,
      sync_interval_minutes: 360,
      sync_days: 7,
      sync_interval_override_minutes: null,
      sync_days_override: null,
      historical_sync: {
        state: 'failed',
        start_date: null,
        end_date: null,
        started_at: null,
        completed_at: null,
        last_error: 'YAZIO-Anmeldung fehlgeschlagen.',
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: 'YAZIO-Anmeldung fehlgeschlagen.',
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/imports') return Promise.resolve([])
      return Promise.resolve(status)
    })

    const wrapper = mount(ImportsView)
    await flushPromises()

    expect(wrapper.text()).toContain('fehlgeschlagen; Zugangsdaten aktualisieren')
    expect(
      wrapper
        .findAll('button')
        .find((button) => button.text().includes('Zeitraum synchronisieren'))
        ?.attributes('disabled'),
    ).toBeDefined()
  })

  it('explains data availability in plain language', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-07-20',
      end_date: '2026-07-23',
      total_days: 4,
      recorded_days: 3,
      coverage_ratio: 0.75,
      missing_days: ['2026-07-21'],
      incomplete_days: [{ date: '2026-07-22', tracking_status: 'incomplete', tracking_reasons: ['Ernährungsdaten vorhanden, aber kein Kalorienwert'] }],
      unknown_types: [],
      failed_records: 0,
      imports: [],
    })
    const wrapper = mount(QualityView)
    await flushPromises()
    expect(wrapper.text()).toContain('Wann gilt ein Tag als erfasst?')
    expect(wrapper.text()).toContain('Niedrige Werte werden nicht als unvollständig interpretiert')
    expect(wrapper.text()).toContain('Ernährungsdaten vorhanden, aber kein Kalorienwert')
    expect(wrapper.text()).toContain('21.07.2026')
  })

  it('shows micronutrient orientation only with visible source coverage', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-07-01',
      end_date: '2026-07-23',
      source: 'yazio_export_v1',
      recorded_days: 20,
      last_updated_at: '2026-07-23T10:30:00Z',
      available_sources: [
        { source_type: 'yazio_export_v1', last_updated_at: '2026-07-23T10:30:00Z' },
      ],
      definition: {
        coverage_threshold: 0.7,
        orientation_threshold_percent: 80,
      },
      nutrients: [
        {
          id: 'mineral.iron',
          metric_type: 'iron_mg',
          label: 'Eisen',
          category: 'mineral',
          unit: 'mg',
          eu_nrv: 14,
          total: 196,
          average_daily: 9.8,
          days_with_value: 20,
          coverage_ratio: 1,
          percent_of_nrv: 70,
          status: 'below_orientation',
        },
        {
          id: 'vitamin.d',
          metric_type: 'vitamin_d_ug',
          label: 'Vitamin D',
          category: 'vitamin',
          unit: 'ug',
          eu_nrv: 5,
          total: 30,
          average_daily: 1.5,
          days_with_value: 5,
          coverage_ratio: 0.25,
          percent_of_nrv: 30,
          status: 'insufficient_data',
        },
      ],
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: MicronutrientsView }],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(MicronutrientsView, {
      global: { plugins: [router], stubs: { DateFilter: true } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Mikronährstoffanalyse')
    expect(wrapper.text()).toContain('Eisen')
    expect(wrapper.text()).toContain('Unter Orientierung')
    expect(wrapper.text()).toContain('Noch zu wenige Angaben')
    expect(wrapper.text()).toContain('Anteil am EU-Referenzwert')
    expect(wrapper.text()).toContain('5 von 20 Tagen mit Angaben (25 %) · mindestens 14 nötig')
    expect(wrapper.text()).toContain('60 Tage aus YAZIO nachladen')
    expect(wrapper.text()).toContain('keine Diagnose')
    expect(wrapper.text().indexOf('Mineralstoffe')).toBeLessThan(
      wrapper.text().indexOf('So ist die Auswertung zu lesen'),
    )
  })

  it('keeps budgets and account settings separate and clears an optional target', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const currentTarget = { id: 'target', valid_from: today, valid_to: null, calories_kcal: '2100.000', maintenance_kcal: '2600.000', protein_g: '140.000', carbs_g: null, fat_g: null, fiber_g: null, water_ml: null, target_weight_min_kg: null, target_weight_max_kg: null, activity_mode: 'off', activity_source_type: null }
    const historicalTarget = { ...currentTarget, id: 'historical-target', valid_from: '2026-07-27', valid_to: '2026-08-02' }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/targets') return Promise.resolve([currentTarget, historicalTarget])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === `/settings/targets/${today}`) return Promise.resolve({ ...currentTarget, calories_kcal: 2300 })
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({
          totp_enabled: false,
          totp_setup_pending: false,
          recovery_codes_remaining: 0,
        })
      }
      if (path === '/yazio/status') return Promise.resolve({ available: true, configured: true, sync_enabled: true, sync_interval_minutes: 360, sync_days: 7, last_attempt_at: null, last_success_at: null, next_sync_at: null, last_error: null })
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations' && options?.method === 'POST') {
        return Promise.resolve({
          invitation_url: 'https://nutrition.example.test/einladung#token=invite_example',
        })
      }
      if (path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})

    })

    const targetsWrapper = mount(AccountTargetsView)
    await flushPromises()
    expect(targetsWrapper.text()).toContain('Budget- und Zielhistorie')
    expect(targetsWrapper.text()).toContain('2.100 kcal')
    expect(targetsWrapper.text()).toContain('2.600 kcal')
    expect(targetsWrapper.text()).not.toContain('2100.000')
    expect(targetsWrapper.text()).toContain('27.07.2026')
    expect(targetsWrapper.text()).toContain('02.08.2026')
    expect(targetsWrapper.text()).not.toContain('2026-07-27')
    expect(targetsWrapper.text()).not.toContain('2026-08-02')
    expect(targetsWrapper.text()).not.toContain('Persönliche YAZIO-Verbindung')
    expect(targetsWrapper.get('form > .button').classes()).toContain('compact-action')
    const budgetHelp = targetsWrapper.get('.budget-help')
    expect(budgetHelp.get('h2').text()).toBe('So wird die Änderung verwendet')
    expect(budgetHelp.findAll(':scope > p').map((paragraph) => paragraph.text())).toEqual([
      'Das Kalorienbudget ist deine tägliche Obergrenze. Das Proteinziel wird als Wert behandelt, den du möglichst erreichen möchtest.',
      'Der optionale Erhaltungsbedarf ist deine geschätzte Kalorienmenge, bei der dein Gewicht ungefähr stabil bleibt. Dein Kalorienbudget kann darunter, darauf oder darüber liegen.',
      'Im Kalender bleibt das Budget maßgeblich: Werte bis zum Budget sind grün, Werte über dem Budget orange und Werte über Budget und Erhaltungsbedarf rot.',
      'Mit „Gültig ab“ bestimmst du den ersten Tag der neuen Werte. Gibt es für dieses Datum bereits eine Version, wird sie aktualisiert. Frühere Auswertungen behalten die damals gültigen Werte.',
    ])
    const calories = targetsWrapper.find('input[type="number"]')
    await calories.setValue('2300')
    const maintenance = targetsWrapper.findAll<HTMLInputElement>('input[type="number"]')[1]
    await maintenance.setValue('')
    await targetsWrapper.find('form').trigger('submit')
    await flushPromises()
    const [year, month, day] = today.split('-')
    expect(targetsWrapper.text()).toContain(
      `Budget und Ziele ab ${day}.${month}.${year} gespeichert.`,
    )
    expect(apiMock).toHaveBeenCalledWith(`/settings/targets/${today}`, {
      method: 'PUT',
      body: JSON.stringify({
        valid_from: today,
        calories_kcal: 2300,
        maintenance_kcal: null,
        protein_g: 140,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
        target_weight_min_kg: null,
        target_weight_max_kg: null,
        activity_mode: 'off',
        activity_source_type: null,
    })
    })
    targetsWrapper.unmount()

    const integrationsWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(integrationsWrapper.get('h2').text()).toBe('YAZIO')
    integrationsWrapper.unmount()

    const securityWrapper = mount(AccountSecurityView)
    await flushPromises()
    expect(securityWrapper.text()).toContain('Zwei-Faktor-Authentifizierung')
    expect(securityWrapper.text()).toContain('Passkeys')
    securityWrapper.unmount()
  })
  it('starts a native same-origin data export and waits for server acceptance', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens' || path === '/settings/passkeys' || path === '/users/invitations') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') return Promise.resolve({ available: false, configured: false, sync_enabled: false })
      if (path === '/users') return Promise.resolve([user])
      return Promise.resolve({})
    })
    let clickedHref = ''
    let hasDownloadAttribute = false
    let downloadAttributeValue: string | null = null
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clickedHref = this.href
      hasDownloadAttribute = this.hasAttribute('download')
      downloadAttributeValue = this.getAttribute('download')
      const downloadId = new URL(this.href).searchParams.get('download_id')!
      document.cookie = `calograph_export_status_${downloadId.replaceAll('-', '')}=accepted; Path=/`
    })

    const wrapper = mount(AccountDataPrivacyView)
    await flushPromises()
    const exportButton = wrapper.findAll('button').find((button) => button.text() === 'Export herunterladen')
    expect(exportButton).toBeDefined()
    const pending = exportButton!.trigger('click')
    await Promise.resolve()
    expect(exportButton!.element.disabled).toBe(true)
    await pending

    expect(downloadAttributeValue).toBe('')
    const downloadUrl = new URL(clickedHref)
    expect(downloadUrl.pathname).toBe('/api/v1/settings/export')
    expect(hasDownloadAttribute).toBe(true)
    expect(downloadUrl.searchParams.has('download_id')).toBe(true)
    expect(apiMock.mock.calls.some(([path]) => path === '/settings/export')).toBe(false)
    expect(click).toHaveBeenCalledTimes(1)
    expect(exportButton!.element.disabled).toBe(false)
  })
  it('handles native export busy and unauthenticated status signals', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens' || path === '/settings/passkeys' || path === '/users/invitations') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') return Promise.resolve({ available: false, configured: false, sync_enabled: false })
      if (path === '/users') return Promise.resolve([user])
      return Promise.resolve({})
    })
    let status: 'busy' | 'unauthenticated' = 'busy'
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      const downloadId = new URL(this.href).searchParams.get('download_id')!
      document.cookie = `calograph_export_status_${downloadId.replaceAll('-', '')}=${status}; Path=/`
    })

    const wrapper = mount(AccountDataPrivacyView)
    await flushPromises()
    const exportButton = wrapper.findAll('button').find((button) => button.text() === 'Export herunterladen')!
    await exportButton.trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('The request could not be processed.')

    status = 'unauthenticated'
    await exportButton.trigger('click')
    await flushPromises()
    expect(authExpiredMock).toHaveBeenCalledTimes(1)
  })

  it('isolates export status cookies between two account tabs', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens' || path === '/settings/passkeys' || path === '/users/invitations') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') return Promise.resolve({ available: false, configured: false, sync_enabled: false })
      if (path === '/users') return Promise.resolve([user])
      return Promise.resolve({})
    })
    const downloadA = '00000000-0000-4000-8000-00000000000a'
    const downloadB = '00000000-0000-4000-8000-00000000000b'
    vi.spyOn(crypto, 'randomUUID').mockReturnValueOnce(downloadA).mockReturnValueOnce(downloadB)
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      const downloadId = new URL(this.href).searchParams.get('download_id')!
      if (downloadId === downloadA) {
        document.cookie = `calograph_export_status_${downloadA.replaceAll('-', '')}=accepted; Path=/`
      }
    })

    const tabA = mount(AccountDataPrivacyView)
    const tabB = mount(AccountDataPrivacyView)
    await flushPromises()
    const buttonA = tabA.findAll('button').find((button) => button.text() === 'Export herunterladen')!
    const buttonB = tabB.findAll('button').find((button) => button.text() === 'Export herunterladen')!
    const first = buttonA.trigger('click')
    await Promise.resolve()
    const second = buttonB.trigger('click')
    await Promise.resolve()
    document.cookie = `calograph_export_status_${downloadB.replaceAll('-', '')}=busy; Path=/`
    await first

    expect(document.cookie).toContain(`calograph_export_status_${downloadB.replaceAll('-', '')}=busy`)
    await second
  })

  it('uses an activity switch, hides its source while disabled, and summarizes history compactly', async () => {
    const target = {
      id: 'target',
      valid_from: '2026-08-11',
      valid_to: null,
      calories_kcal: 2100,
      maintenance_kcal: null,
      protein_g: 140,
      carbs_g: null,
      fat_g: null,
      fiber_g: null,
      target_weight_min_kg: null,
      target_weight_max_kg: null,
      activity_mode: 'full',
      activity_source_type: 'apple_health_xml',
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([target])
      if (path === '/settings/activity-sources') {
        return Promise.resolve([{ source_type: 'apple_health_xml' }])
      }
      if (path === '/settings/profile') {
        return Promise.resolve({ id: 'test-user', language: 'de', preferred_weight_unit: 'kg' })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()
    const activitySwitch = wrapper.get<HTMLInputElement>('input[role="switch"]')

    expect(activitySwitch.element.checked).toBe(true)
    expect(wrapper.get('.activity-status-badge').text()).toBe('Aktiv')
    expect(wrapper.get<HTMLSelectElement>('select[name="activity-source"]').element.value).toBe(
      'apple_health_xml',
    )
    expect(wrapper.text()).toContain('An · Apple Health')
    expect(wrapper.text()).toContain('Importierte Aktivitätskalorien erhöhen dein Kalorienbudget')

    await activitySwitch.setValue(false)
    expect(wrapper.get('.activity-status-badge').text()).toBe('Deaktiviert')
    expect(wrapper.find('select[name="activity-source"]').exists()).toBe(false)

    await activitySwitch.setValue(true)
    expect(wrapper.get('.activity-status-badge').text()).toBe('Aktiv')
    expect(wrapper.find('select[name="activity-source"]').exists()).toBe(true)
  })
  it('places macro targets, activity, and target weight before saving', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.resolve({ ...user, preferred_weight_unit: 'kg' })
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()

    expect(wrapper.find('.macro-target-settings').exists()).toBe(true)
    expect(wrapper.findAll('.settings-subsection')).toHaveLength(2)
    const macroInputs = wrapper.findAll('.macro-target-grid input')
    expect(macroInputs).toHaveLength(4)
    const form = wrapper.get('form')
    const formChildren = [...form.element.children]
    const macroIndex = formChildren.indexOf(wrapper.get('.macro-target-settings').element)
    const activityIndex = formChildren.indexOf(wrapper.get('.activity-target-settings').element)
    const targetWeightIndex = formChildren.indexOf(wrapper.get('.target-weight-settings').element)
    const saveIndex = formChildren.indexOf(wrapper.get('button[type="submit"]').element)
    expect(activityIndex).toBeGreaterThan(macroIndex)
    expect(targetWeightIndex).toBeGreaterThan(activityIndex)
    expect(saveIndex).toBeGreaterThan(targetWeightIndex)
    expect(wrapper.get<HTMLInputElement>('input[role="switch"]').element.checked).toBe(false)
    wrapper.unmount()
  })
  it('does not expose editable target controls when preferences fail to load', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.reject(new Error('preference unavailable'))
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('Einstellungen konnten nicht geladen werden.')
    expect(wrapper.find('.content-grid').exists()).toBe(false)
    expect(wrapper.find('form').exists()).toBe(false)
    wrapper.unmount()
  })
  it('loads target weight modes in the preferred unit and labels each history entry', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const baseTarget = {
      valid_to: null,
      calories_kcal: 2100,
      maintenance_kcal: null,
      protein_g: 140,
      carbs_g: null,
      fat_g: null,
      fiber_g: null,
      activity_mode: 'off',
      activity_source_type: null,
    }
    const targets = [
      { ...baseTarget, id: 'none', valid_from: '2026-07-01', valid_to: '2026-07-10', target_weight_min_kg: null, target_weight_max_kg: null },
      { ...baseTarget, id: 'exact', valid_from: '2026-07-11', valid_to: '2026-07-20', target_weight_min_kg: '70.000', target_weight_max_kg: '70.000' },
      { ...baseTarget, id: 'range', valid_from: today, target_weight_min_kg: '65.000', target_weight_max_kg: '80.000' },
    ]
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve(targets)
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.resolve({ ...user, preferred_weight_unit: 'lb' })
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()

    expect(wrapper.find('.target-weight-settings').exists()).toBe(true)
    expect(wrapper.find('.target-weight-mode-options').exists()).toBe(true)
    const targetWeightSettings = wrapper.get('fieldset.target-weight-settings')
    expect(targetWeightSettings.get('legend').text()).toBe('Zielgewicht')
    const targetWeightModes = targetWeightSettings.get('[role="radiogroup"]')
    expect(targetWeightModes.attributes('aria-label')).toBe('Zielgewicht festlegen')
    expect(wrapper.text()).toContain('Kein Zielgewicht')
    expect(wrapper.text()).toContain('Festes Zielgewicht')
    expect(wrapper.text()).toContain('Zielbereich')
    expect(wrapper.findAll<HTMLInputElement>('input[name="target-weight-mode"]')).toHaveLength(3)
    expect(wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="range"]').element.checked).toBe(true)
    expect(wrapper.get<HTMLInputElement>('input[name="target-weight-min"]').element.value).toBe('143.3')
    expect(wrapper.get<HTMLInputElement>('input[name="target-weight-max"]').element.value).toBe('176.4')
    expect(wrapper.text()).toContain('Von (lb)')
    expect(wrapper.text()).toContain('Bis (lb)')
    expect(wrapper.text()).toContain('Zielgewicht: 154,3 lb')
    expect(wrapper.text()).toContain('Zielgewicht: 143,3–176,4 lb')
    expect(wrapper.find('.target-weight-range').findAll('input')).toHaveLength(2)
  })

  it('submits exact, range, and none target-weight payloads across mode transitions', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const target = {
      id: 'target',
      valid_from: today,
      valid_to: null,
      calories_kcal: 2100,
      maintenance_kcal: null,
      protein_g: 140,
      carbs_g: null,
      fat_g: null,
      fiber_g: null,
      target_weight_min_kg: 75,
      target_weight_max_kg: 75,
      activity_mode: 'off',
      activity_source_type: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([target])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.resolve({ ...user, preferred_weight_unit: 'kg' })
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Zielgewicht (kg)')
    expect(wrapper.findAll('input[name="target-weight-exact"]')).toHaveLength(1)
    expect(wrapper.findAll('.target-weight-range input')).toHaveLength(0)
    await wrapper.get('form').trigger('submit')
    await flushPromises()


    let saveCalls = apiMock.mock.calls.filter(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse(String(saveCalls[0][1].body))).toMatchObject({
      target_weight_min_kg: 75,
      target_weight_max_kg: 75,
    })

    await wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="range"]').setValue()
    expect(wrapper.findAll('input[name="target-weight-exact"]')).toHaveLength(0)
    await wrapper.get<HTMLInputElement>('input[name="target-weight-min"]').setValue('70')
    expect(wrapper.findAll('.target-weight-range input')).toHaveLength(2)
    await wrapper.get<HTMLInputElement>('input[name="target-weight-max"]').setValue('80')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    saveCalls = apiMock.mock.calls.filter(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse(String(saveCalls[1][1].body))).toMatchObject({
      target_weight_min_kg: 70,
      target_weight_max_kg: 80,
    })

    await wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="none"]').setValue()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    saveCalls = apiMock.mock.calls.filter(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse(String(saveCalls[2][1].body))).toMatchObject({
      target_weight_min_kg: null,
      target_weight_max_kg: null,
    })

    await wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="exact"]').setValue()
    await wrapper.get<HTMLInputElement>('input[name="target-weight-exact"]').setValue('77')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    saveCalls = apiMock.mock.calls.filter(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')
    expect(JSON.parse(String(saveCalls[3][1].body))).toMatchObject({
      target_weight_min_kg: 77,
      target_weight_max_kg: 77,
    })
  })

  it('converts lb input once and preserves its displayed value across repeated mode changes', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const target = {
      id: 'target',
      valid_from: today,
      valid_to: null,
      calories_kcal: 2100,
      maintenance_kcal: null,
      protein_g: 140,
      carbs_g: null,
      fat_g: null,
      fiber_g: null,
      target_weight_min_kg: null,
      target_weight_max_kg: null,
      activity_mode: 'off',
      activity_source_type: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([target])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.resolve({ ...user, preferred_weight_unit: 'lb' })
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()
    await wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="exact"]').setValue()
    const exact = wrapper.get<HTMLInputElement>('input[name="target-weight-exact"]')
    await exact.setValue('165.3')
    const displayed = Number(exact.element.value)

    for (const mode of ['range', 'exact', 'range', 'exact'] as const) {
      await wrapper.get<HTMLInputElement>(`input[name="target-weight-mode"][value="${mode}"]`).setValue()
    }
    expect(Number(wrapper.get<HTMLInputElement>('input[name="target-weight-exact"]').element.value)).toBeCloseTo(displayed, 8)

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const saveCall = apiMock.mock.calls.find(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')
    expect(saveCall).toBeDefined()
    const payload = JSON.parse(String(saveCall![1].body))
    const expectedKg = Math.round((165.3 / 2.2046226218487757) * 1000) / 1000
    expect(payload).toMatchObject({
      target_weight_min_kg: expectedKg,
      target_weight_max_kg: expectedKg,
    })
  })

  it('blocks reversed and over-limit range values before making a save request', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const target = {
      id: 'target',
      valid_from: today,
      valid_to: null,
      calories_kcal: 2100,
      maintenance_kcal: null,
      protein_g: 140,
      carbs_g: null,
      fat_g: null,
      fiber_g: null,
      target_weight_min_kg: 75,
      target_weight_max_kg: 75,
      activity_mode: 'off',
      activity_source_type: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([target])
      if (path === '/settings/activity-sources') return Promise.resolve([])
      if (path === '/settings/profile') return Promise.resolve({ ...user, preferred_weight_unit: 'kg' })
      return Promise.resolve({})
    })

    const wrapper = mount(AccountTargetsView)
    await flushPromises()
    await wrapper.get<HTMLInputElement>('input[name="target-weight-mode"][value="range"]').setValue()
    await wrapper.get<HTMLInputElement>('input[name="target-weight-min"]').setValue('80')
    await wrapper.get<HTMLInputElement>('input[name="target-weight-max"]').setValue('70')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('#target-weight-error').text()).toContain('Das untere Zielgewicht muss kleiner als das obere sein')
    expect(apiMock.mock.calls.some(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')).toBe(false)

    await wrapper.get<HTMLInputElement>('input[name="target-weight-min"]').setValue('70')
    await wrapper.get<HTMLInputElement>('input[name="target-weight-max"]').setValue('1001')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('#target-weight-error').text()).toContain('Das Zielgewicht muss größer als 0 und höchstens 1000 kg sein')
    expect(apiMock.mock.calls.some(([path, options]) =>
      path === `/settings/targets/${today}` && (options as RequestInit | undefined)?.method === 'PUT')).toBe(false)
  })


  it('starts targetless users with empty required goals and saves only their values', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/targets') return Promise.resolve([])
      if (path === '/settings/profile') {
        return Promise.resolve({ id: 'test-user', language: 'de', preferred_weight_unit: 'kg' })
      }
      return Promise.resolve({})
    })
    const auth = useAuthStore()
    auth.needsTargetSetup = true
    const wrapper = mount(AccountTargetsView)
    await flushPromises()

    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const [year, month, day] = today.split('-')
    const numbers = wrapper.findAll<HTMLInputElement>('input[type="number"]')

    expect(wrapper.text()).toContain('Lege zuerst deine persönlichen Ziele fest.')
    expect(wrapper.get<HTMLInputElement>('input[type="text"]').element.value).toBe(
      `${day}.${month}.${year}`,
    )
    expect(numbers[0].element.value).toBe('')
    expect(numbers[2].element.value).toBe('')

    await numbers[0].setValue('2100')
    await numbers[2].setValue('130')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/settings/targets', {
      method: 'POST',
      body: JSON.stringify({
        valid_from: today,
        calories_kcal: 2100,
        maintenance_kcal: null,
        protein_g: 130,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
        target_weight_min_kg: null,
        target_weight_max_kg: null,
        activity_mode: 'off',
        activity_source_type: null,
      }),
    })
    expect(auth.needsTargetSetup).toBe(false)
  })

  it('requires and submits an explicit German date range for initial YAZIO setup', async () => {
    const status = {
      available: true,
      configured: false,
      sync_enabled: false,
      sync_interval_minutes: null,
      sync_days: null,
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({
          totp_enabled: false,
          totp_setup_pending: false,
          recovery_codes_remaining: 0,
        })
      }
      if (path === '/yazio/status') return Promise.resolve(status)
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      if (path === '/yazio/connection') {
        return Promise.resolve({
          ...status,
          configured: true,
          sync_enabled: true,
          historical_sync: {
            state: 'pending',
            start_date: '2026-07-20',
            end_date: '2026-07-23',
            started_at: null,
            completed_at: null,
            last_error: null,
          },
        })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Erster Datenimport von')
    expect(wrapper.get('.yazio-icon').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('.yazio-card-title svg').exists()).toBe(false)
    const dateInputs = wrapper
      .findAllComponents(DateInput)
      .map((component) => component.get<HTMLInputElement>('input[type="text"]'))
    expect(dateInputs).toHaveLength(2)
    expect(
      wrapper.get<HTMLButtonElement>('.yazio-connection-card button[type="submit"]').element.disabled,
    ).toBe(true)
    await wrapper.get('input[name="yazio-email"]').setValue('owner@example.com')
    await wrapper.get('input[name="yazio-password"]').setValue('very-secret')
    await dateInputs[0].setValue('20.07.2026')
    await dateInputs[1].setValue('23.07.2026')
    await wrapper.get('.yazio-connection-card form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('YAZIO-Verbindung gespeichert.')
    expect(wrapper.text()).toContain('Der erste Datenimport läuft im Hintergrund.')
    expect(wrapper.text()).toContain('Du kannst diese Seite verlassen.')
    expect(wrapper.text()).toContain('Zu den Importen')

    expect(apiMock).toHaveBeenLastCalledWith('/yazio/connection', {
      method: 'PUT',
      body: JSON.stringify({
        email: 'owner@example.com',
        password: 'very-secret',
        from_date: '2026-07-20',
        end_date: '2026-07-23',
      }),
    })
    wrapper.unmount()
  })

  it('verweist bei einem fehlgeschlagenen ersten YAZIO-Import auf die Importdetails', async () => {
    const failedStatus = {
      available: true,
      configured: true,
      sync_enabled: false,
      sync_interval_minutes: 360,
      sync_days: 7,
      historical_sync: {
        state: 'failed',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: '2026-08-13T10:00:00',
        completed_at: null,
        last_error: 'YAZIO-Anmeldung fehlgeschlagen.',
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: 'YAZIO-Anmeldung fehlgeschlagen.',
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') return Promise.resolve(failedStatus)
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Erster Datenimport fehlgeschlagen')
    expect(wrapper.text()).toContain('Details unter Importe')
    wrapper.unmount()
  })
  it('uses a YAZIO header, accessible credential labels, and non-secret replacement placeholders', async () => {
    const status = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') return Promise.resolve(status)
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.findAll('.yazio-credential-form label.field')[0].text()).toContain('YAZIO-E-Mail')
    expect(wrapper.findAll('.yazio-credential-form label.field')[1].text()).toContain('YAZIO-Passwort')
    expect(wrapper.get<HTMLInputElement>('input[name="yazio-email"]').element.value).toBe('')
    expect(wrapper.get<HTMLInputElement>('input[name="yazio-password"]').element.value).toBe('')
    expect(wrapper.get<HTMLInputElement>('input[name="yazio-email"]').attributes('placeholder')).toBe(
      'Gespeichert — neu eingeben zum Ersetzen',
    )
    expect(wrapper.get<HTMLInputElement>('input[name="yazio-password"]').attributes('placeholder')).toBe(
      'Gespeichert — neu eingeben zum Ersetzen',
    )
    expect(wrapper.findAll('.yazio-credential-form small')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('very-secret')
    wrapper.unmount()
  })


  it('clears a stale YAZIO error before a successful retry', async () => {
    let saveAttempts = 0
    const status = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: null,
      sync_days: null,
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({
          totp_enabled: false,
          totp_setup_pending: false,
          recovery_codes_remaining: 0,
        })
      }
      if (path === '/yazio/status') return Promise.resolve(status)
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      if (path === '/yazio/connection') {
        saveAttempts += 1
        return saveAttempts === 1
          ? Promise.reject(new Error('temporary failure'))
          : Promise.resolve({ ...status, configured: true, sync_enabled: true })
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(wrapper.find('.yazio-connection-card .date-input').exists()).toBe(false)
    await wrapper.get('input[name="yazio-email"]').setValue('owner@example.com')
    await wrapper.get('input[name="yazio-password"]').setValue('very-secret')

    const form = wrapper.get('.yazio-connection-card form')
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('YAZIO-Verbindung konnte nicht gespeichert werden.')

    await form.trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenLastCalledWith('/yazio/connection', {
      method: 'PUT',
      body: JSON.stringify({
        email: 'owner@example.com',
        password: 'very-secret',
      }),
    })
    expect(wrapper.text()).not.toContain('YAZIO-Verbindung konnte nicht gespeichert werden.')
    expect(wrapper.text()).toContain('YAZIO-Verbindung aktualisiert.')
    expect(wrapper.text()).not.toContain('Erster Datenimport läuft im Hintergrund.')
  })

  it('pollt den ersten YAZIO-Import ohne parallele Requests und stoppt nach Abschluss', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const poll = Promise.withResolvers<unknown>()
    const baseStatus = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
    }
    const pendingStatus = {
      ...baseStatus,
      historical_sync: {
        state: 'pending',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: null,
        completed_at: null,
        last_error: null,
      },
    }
    const runningStatus = {
      ...pendingStatus,
      historical_sync: { ...pendingStatus.historical_sync, state: 'running' },
    }
    const completedStatus = {
      ...runningStatus,
      historical_sync: {
        ...runningStatus.historical_sync,
        state: 'completed',
        completed_at: '2026-08-13T10:42:00',
      },
    }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      if (path === '/yazio/status') {
        statusCalls += 1
        return statusCalls === 1 ? Promise.resolve(pendingStatus) : poll.promise
      }
      if (path === '/yazio/connection' && options?.method === 'PUT') {
        return Promise.resolve(runningStatus)
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Erster Datenimport wartet auf den Scheduler')

    await wrapper.get('input[name="yazio-email"]').setValue('owner@example.com')
    await wrapper.get('input[name="yazio-password"]').setValue('very-secret')
    await wrapper.get('.yazio-connection-card form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Erster Datenimport läuft im Hintergrund')

    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)

    poll.resolve(completedStatus)
    await flushPromises()
    expect(wrapper.text()).toContain('Erster Datenimport abgeschlossen:')
    expect(wrapper.text()).toContain('13.08.2026, 10:42')

    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('verwirft verspätete YAZIO-Statusantworten nach Unmount', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const poll = Promise.withResolvers<unknown>()
    const status = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      historical_sync: {
        state: 'pending',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: null,
        completed_at: null,
        last_error: null,
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      if (path === '/yazio/status') {
        statusCalls += 1
        return statusCalls === 1 ? Promise.resolve(status) : poll.promise
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    wrapper.unmount()
    poll.resolve({
      ...status,
      historical_sync: { ...status.historical_sync, state: 'completed', completed_at: '2026-08-13T10:42:00' },
    })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    vi.useRealTimers()
  })

  it('stoppt den YAZIO-Poll beim Wechsel aus den Kontoeinstellungen', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const poll = Promise.withResolvers<unknown>()
    const pendingStatus = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      historical_sync: {
        state: 'pending',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: null,
        completed_at: null,
        last_error: null,
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      if (path === '/settings/targets') return Promise.resolve([])
      if (path === '/yazio/status') {
        statusCalls += 1
        return statusCalls === 1 ? Promise.resolve(pendingStatus) : poll.promise
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)

    wrapper.unmount()
    const targetsWrapper = mount(AccountTargetsView)
    await flushPromises()
    poll.resolve({
      ...pendingStatus,
      historical_sync: { ...pendingStatus.historical_sync, state: 'completed', completed_at: '2026-08-13T10:42:00' },
    })
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    targetsWrapper.unmount()
    vi.useRealTimers()
  })
  it('pollt den aktiven YAZIO-Import unabhängig von Benutzerverwaltung', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const pendingStatus = {
      available: true,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      historical_sync: {
        state: 'pending',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: null,
        completed_at: null,
        last_error: null,
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') {
        statusCalls += 1
        return Promise.resolve(pendingStatus)
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(2)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('stoppt den Poll, wenn YAZIO serverseitig deaktiviert wird', async () => {
    vi.useFakeTimers()
    let statusCalls = 0
    const unavailableStatus = {
      available: false,
      configured: true,
      sync_enabled: true,
      sync_interval_minutes: 360,
      sync_days: 7,
      historical_sync: {
        state: 'running',
        start_date: '2026-07-20',
        end_date: '2026-07-23',
        started_at: '2026-08-13T10:00:00',
        completed_at: null,
        last_error: null,
      },
      last_attempt_at: null,
      last_success_at: null,
      next_sync_at: null,
      last_error: null,
    }
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      if (path === '/yazio/status') {
        statusCalls += 1
        return Promise.resolve(unavailableStatus)
      }
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})
    })

    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(statusCalls).toBe(1)
    wrapper.unmount()
    vi.useRealTimers()
  })

})
