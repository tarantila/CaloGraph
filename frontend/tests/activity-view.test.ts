import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock, localizeApiErrorMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  localizeApiErrorMock: vi.fn(() => 'Lokalisierter Fehler beim Laden'),
}))

vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: localizeApiErrorMock,
}))

import { ApiError } from '../src/api'
import ActivityView from '../src/views/ActivityView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const canonicalRecord = {
  provider_key: 'google_health' as const,
  status: 'available' as const,
  active_energy_kcal: 321.5,
  record_count: 1,
  source_types: ['google_health_activity_v4'] as const,
}

function response(overrides: Record<string, unknown> = {}) {
  return {
    start_date: '2026-09-16',
    end_date: '2026-09-22',
    view: 'canonical',
    days: [],
    ...overrides,
  }
}

async function mountView() {
  const wrapper = mount(ActivityView)
  await flushPromises()
  return wrapper
}

function lastRequest(): string {
  return apiMock.mock.calls.at(-1)?.[0] as string
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-09-22T12:00:00Z'))
  setLocale('de')
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
  apiMock.mockReset()
  localizeApiErrorMock.mockClear()
  apiMock.mockResolvedValue(response())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('ActivityView', () => {
  it('requests the authenticated local seven-day range in canonical mode by default', async () => {
    await mountView()

    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/verification/activity?start=2026-09-16&end=2026-09-22&view=canonical',
    )
  })

  it('renders the canonical record without changing its supplied value', async () => {
    apiMock.mockResolvedValue(response({
      days: [{ date: '2026-09-22', canonical: canonicalRecord, providers: [] }],
    }))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('Verfügbar')
    expect(wrapper.text()).toContain('1 Einträge')
    expect(wrapper.text()).toContain('321.5 kcal')
    expect(wrapper.text()).toContain('google_health_activity_v4')
  })

  it('requests and renders each backend all-source provider row when toggled', async () => {
    apiMock
      .mockResolvedValueOnce(response({
        days: [{ date: '2026-09-22', canonical: canonicalRecord, providers: [] }],
      }))
      .mockResolvedValueOnce(response({
        view: 'all',
        days: [{
          date: '2026-09-22',
          canonical: null,
          providers: [
            canonicalRecord,
            {
              provider_key: 'yazio',
              status: 'no_data',
              active_energy_kcal: null,
              record_count: 0,
              source_types: ['yazio_export_v1'],
            },
            {
              provider_key: 'apple_health',
              status: 'available',
              active_energy_kcal: 411,
              record_count: 2,
              source_types: ['apple_health_xml'],
            },
          ],
        }],
      }))
    const wrapper = await mountView()

    const allSourcesButton = wrapper.findAll('button').find(
      (button) => button.text() === 'Alle Quellen anzeigen',
    )
    if (!allSourcesButton) throw new Error('All-source toggle not found')
    await allSourcesButton.trigger('click')
    await flushPromises()

    expect(lastRequest()).toBe(
      '/analytics/verification/activity?start=2026-09-16&end=2026-09-22&view=all',
    )
    expect(allSourcesButton.attributes('aria-pressed')).toBe('true')
    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('YAZIO')
    expect(wrapper.text()).toContain('Apple Health')
    expect(wrapper.text()).toContain('Keine Daten')
    expect(wrapper.text()).toContain('411 kcal')
    expect(wrapper.text()).toContain('apple_health_xml')
  })

  it('moves the inclusive range by exactly seven days in either direction', async () => {
    const wrapper = await mountView()

    await wrapper.get('button[aria-label="Vorherige 7 Tage"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe(
      '/analytics/verification/activity?start=2026-09-09&end=2026-09-15&view=canonical',
    )

    await wrapper.get('button[aria-label="Nächste 7 Tage"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe(
      '/analytics/verification/activity?start=2026-09-16&end=2026-09-22&view=canonical',
    )
  })

  it('announces loading while the activity request is pending', async () => {
    const request = Promise.withResolvers<unknown>()
    apiMock.mockReturnValue(request.promise)

    const wrapper = mount(ActivityView)

    expect(wrapper.get('[role="status"]').text()).toContain('Aktivitätsdaten werden geladen')
    request.resolve(response())
    await flushPromises()
  })

  it('shows a localized error and retries the request', async () => {
    apiMock.mockRejectedValueOnce(new ApiError('request failed', 500))
    apiMock.mockResolvedValueOnce(response({
      days: [{ date: '2026-09-22', canonical: canonicalRecord, providers: [] }],
    }))

    const wrapper = await mountView()

    expect(wrapper.get('[role="alert"]').text()).toContain('Lokalisierter Fehler beim Laden')
    expect(localizeApiErrorMock).toHaveBeenCalled()

    const retryButton = wrapper.findAll('button').find((button) => button.text() === 'Erneut versuchen')
    if (!retryButton) throw new Error('Retry button not found')
    await retryButton.trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('Google Health')
  })

  it('shows an explicit empty state when the response has no activity days', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Für diesen Zeitraum liegen keine Aktivitätsdaten vor.')
  })
})
