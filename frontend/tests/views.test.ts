import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ensureCsrfToken: vi.fn().mockResolvedValue('csrf'),
  ApiError: class ApiError extends Error {},
}))

import CalendarView from '../src/views/CalendarView.vue'
import ChartPanel from '../src/components/ChartPanel.vue'
import DailyView from '../src/views/DailyView.vue'
import ImportsView from '../src/views/ImportsView.vue'
import MicronutrientsView from '../src/views/MicronutrientsView.vue'
import OverviewView from '../src/views/OverviewView.vue'
import QualityView from '../src/views/QualityView.vue'
import SettingsView from '../src/views/SettingsView.vue'
import TrendsView from '../src/views/TrendsView.vue'
import WeekdaysView from '../src/views/WeekdaysView.vue'
import WeeklyView from '../src/views/WeeklyView.vue'

const user = {
  id: 'user-1',
  username: 'admin',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  raw_payload_retention_days: 0,
  is_admin: true,
}

describe('main views', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setActivePinia(createPinia())
  })

  it('loads the dashboard summary and renders empty trend data safely', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: { date: '2026-07-19', calories_kcal: null, target_kcal: 2000, protein_g: null, tracking_status: 'no_data', tracking_reasons: ['Keine Daten'] },
          week: { consumed_kcal: 0, budget_kcal: 14000, deviation_kcal: -14000, remaining_kcal: 14000 },
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
    expect(wrapper.text()).toContain('Wochenrest')
    expect(wrapper.text()).toContain('Mo–So')
    expect(wrapper.text()).toContain('Wochenzusammenfassung')
    expect(wrapper.text()).toContain('Datenstatus')
    expect(wrapper.text()).toContain('Datenabdeckung')
    expect(wrapper.text()).toContain('Keine Datenquelle')
    expect(wrapper.text()).not.toContain('Wahrscheinlich unvollständig')
    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/trends?start=2026-06-20&end=2026-07-19&include_incomplete=true',
    )
    const allButton = wrapper.findAll('button').find((button) => button.text() === 'Alle')
    expect(allButton).toBeDefined()
    await allButton!.trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/trends?start=2026-06-01&end=2026-07-19&include_incomplete=true',
    )
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
        return Promise.resolve([{ id: 'target', valid_from: '2026-01-01', valid_to: null, calories_kcal: 2200, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, water_ml: null }])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') {
        return Promise.resolve({
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
      { date: '2026-07-23', calories_kcal: 2050, target_kcal: 2100 },
    ].map((point) => ({
      ...point,
      deviation_kcal: point.calories_kcal - point.target_kcal,
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
          week: { consumed_kcal: 7900, budget_kcal: 8800, deviation_kcal: -900, remaining_kcal: 900 },
          protein_7d_average_g: 130,
          last_import_at: null,
          data_start_date: '2026-07-20',
          data_end_date: '2026-07-23',
          data_day_count: 4,
        })
      }
      if (path === '/settings/targets') {
        return Promise.resolve([
          { id: 'new', valid_from: '2026-07-22', valid_to: null, calories_kcal: 2100, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null },
          { id: 'old', valid_from: '2026-01-01', valid_to: '2026-07-22', calories_kcal: 2300, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null },
        ])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ configured: false })
      return Promise.resolve({ points })
    })

    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    const caloriePanel = wrapper
      .findAllComponents(ChartPanel)
      .find((panel) => panel.props('title') === 'Kalorienaufnahme')
    const option = caloriePanel!.props('option') as {
      series: Array<{ name: string; data: Array<number | null>; step?: string }>
    }
    const budgetSeries = option.series.find((series) => series.name === 'Tagesbudget')

    expect(budgetSeries?.data).toEqual([2300, 2300, 2100, 2100])
    expect(budgetSeries?.step).toBe('middle')
  })

  it('keeps the trends page focused on nutrition without weight tracking', async () => {
    apiMock.mockResolvedValue({
      points: [{
        date: '2026-07-23',
        calories_kcal: 2050,
        target_kcal: 2100,
        deviation_kcal: -50,
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
    })

    const wrapper = mount(TrendsView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()

    expect(wrapper.text()).toContain('Protein am letzten Tag')
    expect(wrapper.text()).not.toContain('Gewicht')
    const chartTitles = wrapper.findAllComponents(ChartPanel).map((panel) => panel.props('title'))
    expect(chartTitles).toEqual(['Kalorien und gleitende Mittelwerte', 'Makronährstoffe'])
  })

  it('calculates the weekly calorie budget from daily targets with a current-budget fallback', async () => {
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
      protein_g: 80,
      carbs_g: 100,
      fat_g: 40,
      active_energy_kcal: null,
      steps: null,
      tracking_status: 'probably_incomplete',
      tracking_score: 0.5,
      tracking_reasons: [],
    }))

    apiMock.mockImplementation((path: string) => {
      if (path === '/dashboard/summary') {
        return Promise.resolve({
          today: dailyPoints.at(-1),
          week: { consumed_kcal: 3778.5036, budget_kcal: 8800, deviation_kcal: -5021.4964, remaining_kcal: 5021.4964 },
          protein_7d_average_g: 80,
          last_import_at: '2026-07-23T11:22:23Z',
          data_start_date: '2026-07-20',
          data_end_date: '2026-07-23',
          data_day_count: 4,
        })
      }
      if (path === '/settings/targets') {
        return Promise.resolve([{ id: 'target', valid_from: '2026-07-23', valid_to: null, calories_kcal: 2200, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, water_ml: null }])
      }
      if (path === '/imports') return Promise.resolve([])
      if (path === '/yazio/status') {
        return Promise.resolve({
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
    expect(budgetRow!.text()).toContain('4 von 4 Tagen')
    expect(budgetRow!.text()).not.toContain('mit aktuellem Budget bewertet')
  })

  it('renders an empty weekly budget without converting missing data to zero days', async () => {
    apiMock.mockResolvedValue({ weeks: [] })
    const wrapper = mount(WeeklyView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Wochenbudget')
    expect(wrapper.text()).toContain('Noch keine Wochenwerte vorhanden.')
    expect(wrapper.text()).not.toContain('0 kcal')
  })

  it('uses budget thresholds, calculates numeric averages, and navigates by month', async () => {
    apiMock.mockResolvedValue({
      days: [
        { date: '2026-07-18', calories_kcal: '1600.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', tracking_status: 'complete', classification: 'under_budget' },
        { date: '2026-07-19', calories_kcal: '2000.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', tracking_status: 'complete', classification: 'over_budget' },
        { date: '2026-07-20', calories_kcal: '2400.000', target_kcal: '1800.000', maintenance_kcal: '2200.000', tracking_status: 'complete', classification: 'above_maintenance' },
      ],
    })
    const wrapper = mount(CalendarView)
    await flushPromises()
    expect(wrapper.text()).toContain('Im Budget')
    expect(wrapper.text()).toContain('Über Budget')
    expect(wrapper.text()).toContain('Über Erhaltungsbedarf')
    expect(wrapper.text()).toContain('2.000 kcal')
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.findAll('.calendar-day.under_budget')).toHaveLength(1)
    expect(wrapper.findAll('.calendar-day.over_budget')).toHaveLength(1)
    expect(wrapper.findAll('.calendar-day.above_maintenance')).toHaveLength(1)
    expect(apiMock.mock.calls[0][0]).toMatch(
      /^\/analytics\/calendar\?start=\d{4}-\d{2}-01&end=\d{4}-\d{2}-\d{2}$/,
    )

    const initialPath = apiMock.mock.calls[0][0]
    await wrapper.find('button[aria-label="Vorheriger Monat"]').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(apiMock.mock.calls[1][0]).not.toBe(initialPath)
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
    expect(apiMock.mock.calls[0][0]).toMatch(
      /^\/analytics\/daily\?start=\d{4}-\d{2}-\d{2}&end=\d{4}-\d{2}-\d{2}$/,
    )
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
    expect(wrapper.text()).toContain('Letzte 180 Tage')
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
    const details = wrapper.findAll('button').find((button) => button.text() === 'Details')
    await details!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Messwert ist keine gültige Zahl')
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
    expect(wrapper.text()).toContain('21. Juli 2026')
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

  it('keeps budgets and account settings on separate pages and updates today’s budget', async () => {
    const now = new Date()
    const offset = now.getTimezoneOffset() * 60_000
    const today = new Date(now.getTime() - offset).toISOString().slice(0, 10)
    const currentTarget = { id: 'target', valid_from: today, valid_to: null, calories_kcal: '2100.000', maintenance_kcal: '2600.000', protein_g: '140.000', carbs_g: null, fat_g: null, fiber_g: null, water_ml: null }
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/targets') return Promise.resolve([currentTarget])
      if (path === `/settings/targets/${today}`) return Promise.resolve({ ...currentTarget, calories_kcal: 2300 })
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/yazio/status') return Promise.resolve({ configured: false, sync_enabled: false, sync_interval_minutes: null, sync_days: null, last_attempt_at: null, last_success_at: null, next_sync_at: null, last_error: null })
      if (path === '/users') return Promise.resolve([user])
      if (path === '/users/invitations' && options?.method === 'POST') {
        return Promise.resolve({
          token: 'invite_example',
          invitation_url: 'https://nutrition.example.test/einladung/invite_example',
        })
      }
      if (path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})
    })

    const targetsWrapper = mount(SettingsView, { props: { section: 'targets' } })
    await flushPromises()
    expect(targetsWrapper.text()).toContain('Budget- und Zielhistorie')
    expect(targetsWrapper.text()).toContain('2.100 kcal')
    expect(targetsWrapper.text()).toContain('2.600 kcal')
    expect(targetsWrapper.text()).not.toContain('2100.000')
    expect(targetsWrapper.text()).not.toContain('Persönliche YAZIO-Verbindung')
    const calories = targetsWrapper.find('input[type="number"]')
    await calories.setValue('2300')
    await targetsWrapper.find('form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith(`/settings/targets/${today}`, {
      method: 'PUT',
      body: JSON.stringify({
        valid_from: today,
        calories_kcal: 2300,
        maintenance_kcal: 2600,
        protein_g: 140,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
      }),
    })

    const accountWrapper = mount(SettingsView, { props: { section: 'account' } })
    await flushPromises()
    expect(accountWrapper.text()).toContain('Persönliche YAZIO-Verbindung')
    expect(accountWrapper.text()).toContain('Benutzerverwaltung')
    expect(accountWrapper.text()).not.toContain('Budget- und Zielhistorie')
    expect(accountWrapper.text()).not.toContain('Tracking-Vollständigkeit')
    expect(accountWrapper.text()).not.toContain('Gewichtseinheit')
    const invitationButton = accountWrapper
      .findAll('button')
      .find((button) => button.text() === 'Einladungslink erzeugen')
    expect(invitationButton).toBeDefined()
    await invitationButton!.trigger('click')
    await flushPromises()
    expect(accountWrapper.text()).toContain(
      'https://nutrition.example.test/einladung/invite_example',
    )
  })
})
