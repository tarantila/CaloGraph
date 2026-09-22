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
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import NutritionView from '../src/views/NutritionView.vue'

const canonicalGroup = {
  provider_key: 'google_health' as const,
  status: 'available' as const,
  record_count: 1,
  summary: {
    calories_kcal: 640,
    protein_g: 31.5,
    carbohydrates_g: 72,
    fat_g: 19,
  },
  events: [{
    provider_key: 'google_health' as const,
    occurred_at: '2026-09-22T08:30:00Z',
    meal_type: 'breakfast',
    food_name: 'Haferbrei',
    calories_kcal: 640,
    protein_g: 31.5,
    carbohydrates_g: 72,
    fat_g: 19,
    serving_amount: 1.5,
    serving_unit: 'Portion',
  }],
}

function response(overrides: Record<string, unknown> = {}) {
  return {
    date: '2026-09-22',
    view: 'canonical',
    canonical: null,
    providers: [],
    ...overrides,
  }
}

async function mountView() {
  const wrapper = mount(NutritionView)
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

describe('NutritionView', () => {
  it('requests the authenticated local day in canonical mode by default', async () => {
    await mountView()

    expect(apiMock).toHaveBeenCalledWith(
      '/analytics/verification/nutrition?date=2026-09-22&view=canonical',
    )
  })

  it('renders canonical summary and event values without provider-internal details', async () => {
    apiMock.mockResolvedValue(response({ canonical: canonicalGroup }))

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('Verfügbar')
    expect(wrapper.text()).toContain('1 Einträge')
    expect(wrapper.text()).toContain('640 kcal')
    expect(wrapper.text()).toContain('31,5 g')
    expect(wrapper.text()).toContain('72 g')
    expect(wrapper.text()).toContain('19 g')
    expect(wrapper.text()).toContain('Haferbrei')
    expect(wrapper.text()).toContain('Portion')
    expect(wrapper.text()).not.toContain('google_health')
  })

  it('keeps all-source provider summaries and events separate without summing them', async () => {
    apiMock
      .mockResolvedValueOnce(response({ canonical: canonicalGroup }))
      .mockResolvedValueOnce(response({
        view: 'all',
        providers: [
          {
            ...canonicalGroup,
            summary: { calories_kcal: 300, protein_g: null, carbohydrates_g: null, fat_g: null },
            events: [{
              ...canonicalGroup.events[0],
              food_name: 'Google-Frühstück',
              calories_kcal: 300,
              protein_g: null,
              carbohydrates_g: null,
              fat_g: null,
            }],
          },
          {
            provider_key: 'yazio' as const,
            status: 'available' as const,
            record_count: 1,
            summary: { calories_kcal: 900, protein_g: null, carbohydrates_g: null, fat_g: null },
            events: [{
              ...canonicalGroup.events[0],
              provider_key: 'yazio' as const,
              food_name: 'YAZIO-Mittagessen',
              calories_kcal: 900,
              protein_g: null,
              carbohydrates_g: null,
              fat_g: null,
            }],
          },
          {
            provider_key: 'apple_health' as const,
            status: 'no_data' as const,
            record_count: 0,
            summary: { calories_kcal: null, protein_g: null, carbohydrates_g: null, fat_g: null },
            events: [],
          },
        ],
      }))
    const wrapper = await mountView()

    const allSourcesButton = wrapper.findAll('button').find(
      (button) => button.text() === 'Alle Quellen anzeigen',
    )
    if (!allSourcesButton) throw new Error('All-source toggle not found')
    await allSourcesButton.trigger('click')
    await flushPromises()

    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=all')
    expect(allSourcesButton.attributes('aria-pressed')).toBe('true')
    expect(wrapper.text()).toContain('Google Health')
    expect(wrapper.text()).toContain('YAZIO')
    expect(wrapper.text()).toContain('Apple Health')
    expect(wrapper.text()).toContain('300 kcal')
    expect(wrapper.text()).toContain('900 kcal')
    expect(wrapper.text()).not.toContain('1.200 kcal')
    expect(wrapper.findAll('.verification-provider-group')).toHaveLength(3)
  })

  it('shows the backend food fallback and a dash for missing meal details', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        events: [{
          ...canonicalGroup.events[0],
          occurred_at: null,
          meal_type: null,
          food_name: 'Unbenannter Eintrag',
          serving_amount: null,
          serving_unit: null,
        }],
      },
    }))

    const wrapper = await mountView()

    expect(wrapper.get('.verification-event-row').findAll('td').at(1)?.text()).toBe('—')
    expect(wrapper.get('.verification-event-row').find('.verification-event-food').text()).toBe('Unbenannter Eintrag')
  })

  it('moves by one local day and returns to today', async () => {
    const wrapper = await mountView()

    await wrapper.get('button[aria-label="Vorheriger Tag"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-21&view=canonical')

    await wrapper.get('button[aria-label="Nächster Tag"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=canonical')

    await wrapper.get('button[aria-label="Vorheriger Tag"]').trigger('click')
    await flushPromises()
    const todayButton = wrapper.findAll('button').find((button) => button.text() === 'Heute')
    if (!todayButton) throw new Error('Today button not found')
    await todayButton.trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=canonical')
  })

  it('announces loading while the nutrition request is pending', async () => {
    const request = Promise.withResolvers<unknown>()
    apiMock.mockReturnValue(request.promise)

    const wrapper = mount(NutritionView)

    expect(wrapper.get('[role="status"]').text()).toContain('Ernährungsdaten werden geladen')
    request.resolve(response())
    await flushPromises()
  })

  it('shows a localized error and retries the request', async () => {
    apiMock.mockRejectedValueOnce(new ApiError('request failed'))
    apiMock.mockResolvedValueOnce(response({ canonical: canonicalGroup }))

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

  it('shows an explicit empty state when no canonical nutrition data is available', async () => {
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Für diesen Tag liegen keine Ernährungseinträge vor.')
  })
})
