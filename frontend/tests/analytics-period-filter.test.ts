import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Fehler'),
}))

import AnalyticsPeriodFilter from '../src/components/AnalyticsPeriodFilter.vue'
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

describe('responsive analytics period filter', () => {
  beforeEach(() => {
    setLocale('de')
    apiMock.mockReset()
    apiMock.mockResolvedValue({ data_start_date: '2024-01-01' })
    setUser()
  })

  it('offers compact presets and applies a direct preset without custom fields', async () => {
    const wrapper = mount(AnalyticsPeriodFilter, {
      props: { start: '2026-07-13', end: '2026-08-11' },
    })

    expect(wrapper.findAll('.analytics-period-button').map((button) => button.text())).toEqual([
      '7 Tage', '30 Tage', '60 Tage', 'Alle', 'Individuell',
    ])
    await wrapper.get('.analytics-period-button').trigger('click')

    const today = isoDateInTimeZone('Europe/Berlin')
    expect(wrapper.emitted('update:start')?.at(-1)).toEqual([shiftIsoDate(today, -6)])
    expect(wrapper.emitted('update:end')?.at(-1)).toEqual([today])
    expect(wrapper.emitted('apply')).toHaveLength(1)
    expect(wrapper.get('.analytics-period-button[aria-pressed="true"]').text()).toBe('7 Tage')
  })

  it('shows custom date controls only after selecting custom', async () => {
    const wrapper = mount(AnalyticsPeriodFilter, {
      props: { start: '2026-07-13', end: '2026-08-11' },
    })

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === 'Individuell')!.trigger('click')
    expect(wrapper.findAll('.analytics-period-button[aria-pressed="true"]')).toHaveLength(1)
    expect(wrapper.findAll('.analytics-period-button[aria-pressed="true"]')[0].text()).toBe('Individuell')
    expect(wrapper.find('.analytics-period-custom-fields').exists()).toBe(true)
    expect(wrapper.findAll('.analytics-period-custom-fields input[type="text"]')).toHaveLength(2)

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === '30 Tage')!.trigger('click')
    expect(wrapper.find('.analytics-period-custom-fields').exists()).toBe(false)
  })

  it('resolves Alle through existing summary data boundaries', async () => {
    const wrapper = mount(AnalyticsPeriodFilter, {
      props: { start: '2026-07-13', end: '2026-08-11' },
    })

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === 'Alle')!.trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/dashboard/summary')
    expect(wrapper.emitted('update:start')?.at(-1)).toEqual(['2024-01-01'])
    expect(wrapper.emitted('update:end')?.at(-1)).toHaveLength(1)
    expect(wrapper.emitted('apply')).toHaveLength(1)
  })

  it('caps Alle at the existing analytics range ceiling', async () => {
    apiMock.mockResolvedValue({ data_start_date: '2010-01-01' })
    const wrapper = mount(AnalyticsPeriodFilter, {
      props: { start: '2026-07-13', end: '2026-08-11' },
    })

    await wrapper.findAll('.analytics-period-button').find((button) => button.text() === 'Alle')!.trigger('click')
    await flushPromises()

    const today = isoDateInTimeZone('Europe/Berlin')
    expect(wrapper.emitted('update:start')?.at(-1)).toEqual([shiftIsoDate(today, -3659)])
  })
})
