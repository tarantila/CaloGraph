import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ensureCsrfToken: vi.fn().mockResolvedValue('csrf'),
  ApiError: class ApiError extends Error {},
}))

import CalendarView from '../src/views/CalendarView.vue'
import ImportsView from '../src/views/ImportsView.vue'
import OverviewView from '../src/views/OverviewView.vue'
import SettingsView from '../src/views/SettingsView.vue'
import WeeklyView from '../src/views/WeeklyView.vue'

const user = {
  id: 'user-1',
  username: 'admin',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  preferred_weight_unit: 'kg',
  raw_payload_retention_days: 0,
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
          week: { consumed_kcal: 0, budget_kcal: 2000, deviation_kcal: -2000, remaining_kcal: 2000 },
          protein_7d_average_g: null,
          current_weight_kg: null,
          weight_change_kg: null,
          last_import_at: null,
        })
      }
      return Promise.resolve({ points: [] })
    })
    const wrapper = mount(OverviewView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Kalorien heute')
    expect(wrapper.text()).toContain('Keine Daten')
  })

  it('renders an empty weekly budget without converting missing data to zero days', async () => {
    apiMock.mockResolvedValue({ weeks: [] })
    const wrapper = mount(WeeklyView, { global: { stubs: { ChartPanel: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Wochenbudget')
    expect(wrapper.findAll('tbody tr')).toHaveLength(0)
  })

  it('adds textual meaning to calendar classifications', async () => {
    apiMock.mockResolvedValue({
      days: [{ date: '2026-07-18', calories_kcal: 2000, tracking_status: 'complete', classification: 'on_target' }],
    })
    const wrapper = mount(CalendarView)
    await flushPromises()
    expect(wrapper.text()).toContain('Im Zielbereich')
  })

  it('renders import batch errors and status counts', async () => {
    apiMock.mockResolvedValue([{ id: 'batch', source_type: 'test', client_identifier: null, status: 'completed_with_errors', started_at: '2026-07-19T10:00:00Z', finished_at: '2026-07-19T10:01:00Z', received: 5, inserted: 3, updated: 0, skipped: 1, failed: 1, unknown_types: ['unknown'], error_message: null }])
    const wrapper = mount(ImportsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Mit Fehlern')
    expect(wrapper.text()).toContain('5')
  })

  it('loads target history and all account settings', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve(user)
      if (path === '/settings/targets') return Promise.resolve([{ id: 'target', valid_from: '2026-01-01', valid_to: null, calories_kcal: 2100, protein_g: 140, carbs_g: null, fat_g: null, fiber_g: null, water_ml: null }])
      if (path === '/settings/tokens') return Promise.resolve([])
      return Promise.resolve({ calories_full_ratio: 0.6, calories_partial_ratio: 0.35, median_full_ratio: 0.5, median_partial_ratio: 0.3, complete_score: 7, probably_complete_score: 5, probably_incomplete_score: 3 })
    })
    const wrapper = mount(SettingsView)
    await flushPromises()
    expect(wrapper.text()).toContain('Zielhistorie')
    expect(wrapper.text()).toContain('2100 kcal')
    expect(wrapper.text()).toContain('Tracking-Vollständigkeit')
  })
})
