import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
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
    event_id: 'porridge-uuid',
    detail_available: false,
    provider_key: 'google_health' as const,
    local_time: '08:30',
    meal_type: 'breakfast',
    display_name: 'Haferbrei',
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
function calendarDay(date: string, classification: string) {
  return {
    date,
    calories_kcal: null,
    target_kcal: null,
    maintenance_kcal: null,
    deviation_kcal: null,
    activity_mode: null,
    activity_source_type: null,
    active_energy_kcal: null,
    activity_credit_kcal: 0,
    activity_data_status: 'disabled',
    effective_budget_kcal: null,
    effective_maintenance_kcal: null,
    effective_deviation_kcal: null,
    protein_g: null,
    carbs_g: null,
    fat_g: null,
    tracking_status: 'no_data',
    tracking_score: 0,
    tracking_reasons: [],
    classification,
  }
}


async function mountViewAt(path = '/ernaehrung') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/ernaehrung', component: NutritionView },
      { path: '/ernaehrung/eintrag/:eventId', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

async function mountView() {
  return (await mountViewAt()).wrapper
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
  it('uses a valid date query for the meal day and follows later date-query navigation', async () => {
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-20&view=canonical')
    expect(wrapper.get('.verification-date-navigation strong').text()).toBe('20.09.2026')

    await router.push({ query: { date: '2026-09-21' } })
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-21&view=canonical')
    expect(wrapper.get('.verification-date-navigation strong').text()).toBe('21.09.2026')
    wrapper.unmount()
  })
  it('opens the Calendar month, preserves real status classes, and changes the selected Nutrition day', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/calendar?')) {
        return Promise.resolve({
          days: [
            calendarDay('2026-09-20', 'under_budget'),
            calendarDay('2026-09-21', 'probably_incomplete'),
            calendarDay('2026-09-22', 'no_data'),
          ],
        })
      }
      const date = new URLSearchParams(path.split('?')[1] ?? '').get('date') ?? '2026-09-20'
      return Promise.resolve(response({ date, canonical: canonicalGroup }))
    })
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')

    await wrapper.get('button[aria-haspopup="dialog"]').trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/analytics/calendar?start=2026-09-01&end=2026-09-22')
    expect(wrapper.findAll('.nutrition-calendar-weekday').map((day) => day.text())).toEqual([
      'Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So',
    ])
    expect(wrapper.get('.nutrition-calendar-day[data-date="2026-09-20"]').classes()).toContain('calendar-status--under_budget')
    expect(wrapper.get('.nutrition-calendar-day[data-date="2026-09-21"]').classes()).toContain('calendar-status--probably_incomplete')
    expect(wrapper.get('.nutrition-calendar-day[data-date="2026-09-20"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('.nutrition-calendar-day[data-date="2026-09-22"]').attributes('aria-current')).toBe('date')

    await wrapper.get('.nutrition-calendar-day[data-date="2026-09-21"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.query.date).toBe('2026-09-21')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-21&view=canonical')
    wrapper.unmount()
  })
  it('drops an earlier account calendar response after switching users', async () => {
    const staleCalendar = Promise.withResolvers<unknown>()
    const currentCalendar = Promise.withResolvers<unknown>()
    let calendarRequests = 0
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/analytics/calendar?')) {
        calendarRequests += 1
        return calendarRequests === 1 ? staleCalendar.promise : currentCalendar.promise
      }
      return Promise.resolve(response({ canonical: canonicalGroup }))
    })
    const { wrapper } = await mountViewAt('/ernaehrung')

    await wrapper.get('button[aria-haspopup="dialog"]').trigger('click')
    await flushPromises()
    useAuthStore().user = { ...useAuthStore().user!, id: 'user-2' }
    await flushPromises()
    currentCalendar.resolve({ days: [calendarDay('2026-09-22', 'over_budget')] })
    await flushPromises()
    staleCalendar.resolve({ days: [calendarDay('2026-09-22', 'under_budget')] })
    await flushPromises()

    const today = wrapper.get('.nutrition-calendar-day[data-date="2026-09-22"]')
    expect(today.classes()).toContain('calendar-status--over_budget')
    expect(today.classes()).not.toContain('calendar-status--under_budget')
    wrapper.unmount()
  })



  it('keeps the selected day response when an earlier day request finishes last', async () => {
    const firstRequest = Promise.withResolvers<unknown>()
    const secondRequest = Promise.withResolvers<unknown>()
    apiMock
      .mockReturnValueOnce(firstRequest.promise)
      .mockReturnValueOnce(secondRequest.promise)
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')

    await router.push({ query: { date: '2026-09-21' } })
    await flushPromises()
    secondRequest.resolve(response({
      date: '2026-09-21',
      canonical: { ...canonicalGroup, events: [{ ...canonicalGroup.events[0], display_name: 'Neuer Tag' }] },
    }))
    await flushPromises()
    firstRequest.resolve(response({
      date: '2026-09-20',
      canonical: { ...canonicalGroup, events: [{ ...canonicalGroup.events[0], display_name: 'Alter Tag' }] },
    }))
    await flushPromises()

    expect(wrapper.get('.verification-date-navigation strong').text()).toBe('21.09.2026')
    expect(wrapper.text()).toContain('Neuer Tag')
    expect(wrapper.text()).not.toContain('Alter Tag')
    wrapper.unmount()
  })

  it('reloads daily meal data for a new account and ignores a late prior-account response', async () => {
    apiMock.mockResolvedValue(response({ canonical: canonicalGroup }))
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')
    const previousAccountRequest = Promise.withResolvers<unknown>()
    const newAccountRequest = Promise.withResolvers<unknown>()
    apiMock.mockReturnValueOnce(previousAccountRequest.promise)

    await router.push({ query: { date: '2026-09-21' } })
    await flushPromises()
    apiMock.mockReturnValueOnce(newAccountRequest.promise)
    useAuthStore().user = { ...useAuthStore().user!, id: 'user-2' }
    await flushPromises()
    newAccountRequest.resolve(response({
      date: '2026-09-21',
      canonical: { ...canonicalGroup, events: [{ ...canonicalGroup.events[0], display_name: 'Benutzer B' }] },
    }))
    await flushPromises()
    expect(wrapper.text()).toContain('Benutzer B')

    previousAccountRequest.resolve(response({
      date: '2026-09-21',
      canonical: { ...canonicalGroup, events: [{ ...canonicalGroup.events[0], display_name: 'Veraltete Daten A' }] },
    }))
    await flushPromises()
    expect(wrapper.text()).toContain('Benutzer B')
    expect(wrapper.text()).not.toContain('Veraltete Daten A')
    wrapper.unmount()
  })
  it('recalculates the local Today date when clicked after midnight', async () => {
    const { wrapper } = await mountViewAt('/ernaehrung')
    vi.setSystemTime(new Date('2026-09-23T00:30:00Z'))

    const todayButton = wrapper.findAll('button').find((button) => button.text() === 'Heute')
    if (!todayButton) throw new Error('Today button not found')
    await todayButton.trigger('click')
    await flushPromises()

    expect(wrapper.get('.verification-date-navigation strong').text()).toBe('23.09.2026')
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-23&view=canonical')
    wrapper.unmount()
  })
  it('ignores invalid date queries and keeps queryless entry on the authenticated local day', async () => {
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-02-30')
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=canonical')
    expect(router.currentRoute.value.query.date).toBeUndefined()
    wrapper.unmount()
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
    expect(wrapper.findAll('.nutrition-day-summary-item').map((item) => [
      item.get('.nutrition-day-summary-label').text(),
      item.get('.nutrition-day-summary-value').text(),
    ])).toEqual([
      ['Energie', '640 kcal'],
      ['Protein', '31,5 g'],
      ['Kohlenhydrate', '72 g'],
      ['Fett', '19 g'],
    ])
    expect(wrapper.text()).toContain('Lebensmittel')
    expect(wrapper.text()).not.toContain('08:30')
    expect(wrapper.text()).toContain('Haferbrei')
    expect(wrapper.text()).toContain('Portion')
    expect(wrapper.text()).not.toContain('google_health')
    expect(wrapper.findAll('.nutrition-food-table > thead > tr')).toHaveLength(1)
    expect(wrapper.findAll('.nutrition-food-table > thead th').map((header) => header.text())).toEqual([
      'Lebensmittel', 'Portion', 'Kalorien', 'Protein', 'Kohlenhydrate', 'Fett',
    ])
    expect(wrapper.find('.nutrition-food-table > thead th:first-child .nutrition-food-section-heading').text()).toBe('Lebensmittel')
    expect(wrapper.find('.nutrition-food-section-header').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the header and food rows on one explicit shared column group', async () => {
    apiMock.mockResolvedValue(response({ canonical: canonicalGroup }))
    const wrapper = await mountView()
    const table = wrapper.get('.nutrition-food-table')
    const columns = table.findAll('colgroup > col')

    expect(columns.map((column) => column.classes())).toEqual([
      ['nutrition-food-column--food'],
      ['nutrition-food-column--serving'],
      ['nutrition-food-column--calories'],
      ['nutrition-food-column--protein'],
      ['nutrition-food-column--carbohydrates'],
      ['nutrition-food-column--fat'],
    ])
    expect(table.findAll('thead th')).toHaveLength(columns.length)
    expect(table.get('.verification-event-row').findAll('td')).toHaveLength(columns.length)
    wrapper.unmount()
  })

  it('provides localized labels for stacked food value cells', async () => {
    apiMock.mockResolvedValue(response({ canonical: canonicalGroup }))
    const wrapper = await mountView()
    const cells = wrapper.get('.verification-event-row').findAll('td')

    expect(cells.map((cell) => cell.attributes('data-label') ?? '')).toEqual([
      '', 'Portion', 'Kalorien', 'Protein', 'Kohlenhydrate', 'Fett',
    ])
    wrapper.unmount()
  })

  it('makes each persisted food row clickable without making meal headers links', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        events: [
          { ...canonicalGroup.events[0], event_id: 'available-id', detail_available: true },
          { ...canonicalGroup.events[0], event_id: 'diagnostic-id', detail_available: false, display_name: 'Diagnose' },
          { ...canonicalGroup.events[0], event_id: 'unknown-id', detail_available: undefined, display_name: 'Unbekannt' },
        ],
      },
    }))
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')
    const allRows = wrapper.findAll('.verification-event-row')
    const rows = allRows.filter((row) => row.attributes('tabindex') === '0')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.element.tagName).toBe('TR')
    expect(rows[0]!.attributes('role')).toBeUndefined()
    expect(rows[0]!.attributes('aria-keyshortcuts')).toBe('Enter')
    expect(allRows.slice(1).map((row) => row.attributes('aria-keyshortcuts'))).toEqual([
      undefined,
      undefined,
    ])
    expect(rows[0]!.find('a').exists()).toBe(false)
    expect(wrapper.find('.verification-meal-occasion-heading[role="link"]').exists()).toBe(false)
    await rows[0]!.get('td:nth-child(2)').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe(
      '/ernaehrung/eintrag/available-id?from=daily&date=2026-09-20',
    )
    wrapper.unmount()
  })

  it('opens the existing UUID detail route when a food row receives Enter', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        events: [{ ...canonicalGroup.events[0], event_id: 'available-id', detail_available: true }],
      },
    }))
    const { wrapper, router } = await mountViewAt('/ernaehrung?date=2026-09-20')
    await wrapper.get('.verification-event-row[tabindex="0"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe(
      '/ernaehrung/eintrag/available-id?from=daily&date=2026-09-20',
    )
    wrapper.unmount()
  })

  it('localizes meal types and singular/plural portion units', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        record_count: 4,
        events: [
          { ...canonicalGroup.events[0], event_id: 'breakfast', meal_type: 'breakfast', local_time: '08:30', serving_amount: 1, serving_unit: 'portion' },
          { ...canonicalGroup.events[0], event_id: 'lunch', meal_type: 'lunch', local_time: '12:00', serving_amount: 2, serving_unit: 'portion' },
          { ...canonicalGroup.events[0], event_id: 'dinner', meal_type: 'dinner', local_time: '18:00', serving_amount: 2, serving_unit: 'portion' },
          { ...canonicalGroup.events[0], event_id: 'snack', meal_type: 'snack', local_time: '19:30', serving_amount: 1, serving_unit: 'portion' },
        ],
      },
    }))

    const wrapper = await mountView()
    const rows = wrapper.findAll('.verification-event-row')
    const cells = rows.map((row) => row.findAll('td').map((cell) => cell.text()))

    expect(wrapper.findAll('.verification-meal-occasion-label').map((title) => title.text())).toEqual([
      'Frühstück',
      'Mittagessen',
      'Abendessen',
      'Snack',
    ])
    expect(wrapper.text()).not.toContain('08:30')
    expect(wrapper.text()).not.toContain('12:00')
    expect(wrapper.text()).not.toContain('18:00')
    expect(wrapper.text()).not.toContain('19:30')
    expect(cells.map((row) => row[1])).toEqual([
      '1 Portion',
      '2 Portionen',
      '2 Portionen',
      '1 Portion',
    ])
    wrapper.unmount()
  })
  it('renders occasion title, entry count, and safe kcal in the rowgroup header', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        record_count: 3,
        events: [
          { ...canonicalGroup.events[0], event_id: 'salad', meal_type: 'lunch', local_time: '11:30', display_name: 'Linsensalat', calories_kcal: 510 },
          { ...canonicalGroup.events[0], event_id: 'schnitzel', meal_type: 'lunch', local_time: '11:40', display_name: 'Schnitzel', calories_kcal: 650 },
          { ...canonicalGroup.events[0], event_id: 'pudding', meal_type: 'snack', local_time: '19:30', display_name: 'Vanillepudding', calories_kcal: 220 },
        ],
      },
    }))
    const wrapper = await mountView()
    const lunch = wrapper.get('tbody.verification-meal-occasion[data-meal-type="lunch"]')
    const snack = wrapper.get('tbody.verification-meal-occasion[data-meal-type="snack"]')

    expect(lunch.get('.verification-meal-occasion-title').attributes('scope')).toBe('rowgroup')
    expect(lunch.get('.verification-meal-occasion-label').text()).toBe('Mittagessen')
    expect(lunch.get('.verification-meal-occasion-entry-count').text()).toBe('2 Einträge')
    expect(lunch.get('.verification-meal-occasion-total').text()).toBe('1.160 kcal')
    expect(lunch.findAll('.verification-event-food').map((entry) => entry.text()))
      .toEqual(['Linsensalat', 'Schnitzel'])
    expect(lunch.find('.verification-meal-subtotal').exists()).toBe(false)

    expect(snack.get('.verification-meal-occasion-label').text()).toBe('Snack')
    expect(snack.get('.verification-meal-occasion-entry-count').text()).toBe('1 Eintrag')
    expect(snack.get('.verification-meal-occasion-total').text()).toBe('220 kcal')
    expect(snack.findAll('.verification-event-row')).toHaveLength(1)
    expect(snack.find('.verification-meal-subtotal').exists()).toBe(false)

    expect(wrapper.get('.verification-provider-group-header h2').text()).toBe('Google Health')
    expect(wrapper.get('.nutrition-day-summary-item--energy .nutrition-day-summary-value').text())
      .toBe('640 kcal')
    wrapper.unmount()
  })

  it('orders daily meal groups semantically when provider times are missing', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        record_count: 4,
        events: [
          { ...canonicalGroup.events[0], event_id: 'dinner', meal_type: 'dinner', local_time: null },
          { ...canonicalGroup.events[0], event_id: 'snack', meal_type: 'snack', local_time: null },
          { ...canonicalGroup.events[0], event_id: 'breakfast', meal_type: 'breakfast', local_time: null },
          { ...canonicalGroup.events[0], event_id: 'lunch', meal_type: 'lunch', local_time: null },
        ],
      },
    }))

    const wrapper = await mountView()
    const groups = wrapper.findAll('tbody.verification-meal-occasion')

    expect(groups.map((group) => group.attributes('data-meal-type')))
      .toEqual(['breakfast', 'lunch', 'dinner', 'snack'])
    expect(groups.map((group) => group.get('.verification-meal-occasion-label').text()))
      .toEqual(['Frühstück', 'Mittagessen', 'Abendessen', 'Snack'])
    expect(wrapper.findAll('.verification-event-row')).toHaveLength(4)
    wrapper.unmount()
  })

  it('localizes unknown and missing meal types without dropping food rows', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        record_count: 2,
        events: [
          { ...canonicalGroup.events[0], event_id: 'unknown-type', meal_type: 'provider-dessert', display_name: 'Unbekanntes Essen', calories_kcal: 42 },
          { ...canonicalGroup.events[0], event_id: 'missing-type', meal_type: null, display_name: 'Eintrag ohne Typ', calories_kcal: 0 },
        ],
      },
    }))
    const wrapper = await mountView()
    const groups = wrapper.findAll('tbody.verification-meal-occasion')

    expect(groups).toHaveLength(2)
    expect(groups.map((group) => group.get('.verification-meal-occasion-label').text()))
      .toEqual(['Ohne Kategorie', 'Ohne Kategorie'])
    expect(groups.map((group) => group.get('.verification-meal-occasion-entry-count').text()))
      .toEqual(['1 Eintrag', '1 Eintrag'])
    const totalsByFood = Object.fromEntries(groups.map((group) => [
      group.get('.verification-event-food').text(),
      group.get('.verification-meal-occasion-total').text(),
    ]))
    expect(totalsByFood).toEqual({
      'Unbekanntes Essen': '42 kcal',
      'Eintrag ohne Typ': '0 kcal',
    })
    expect(wrapper.findAll('.verification-event-food').map((entry) => entry.text()).sort())
      .toEqual(['Eintrag ohne Typ', 'Unbekanntes Essen'])
    expect(wrapper.text()).not.toContain('provider-dessert')
    wrapper.unmount()
  })

  it('omits the occasion kcal badge when a multi-entry total is incomplete', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        record_count: 2,
        events: [
          { ...canonicalGroup.events[0], event_id: 'meal-with-kcal', meal_type: 'lunch', display_name: 'Eintrag mit Kalorien', calories_kcal: 510 },
          { ...canonicalGroup.events[0], event_id: 'meal-without-kcal', meal_type: 'lunch', display_name: 'Eintrag ohne Kalorien', calories_kcal: null },
        ],
      },
    }))
    const wrapper = await mountView()
    const lunch = wrapper.get('tbody.verification-meal-occasion[data-meal-type="lunch"]')

    expect(lunch.get('.verification-meal-occasion-entry-count').text()).toBe('2 Einträge')
    expect(lunch.find('.verification-meal-occasion-total').exists()).toBe(false)
    expect(lunch.findAll('.verification-event-row')).toHaveLength(2)
    expect(lunch.find('.verification-meal-subtotal').exists()).toBe(false)
    wrapper.unmount()
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
              event_id: 'google-breakfast',
              detail_available: false,
              provider_key: 'google_health',
              local_time: null,
              meal_type: null,
              serving_amount: null,
              serving_unit: null,
              display_name: 'Google-Frühstück',
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
              display_name: 'YAZIO-Mittagessen',
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

  it('shows the backend food fallback and an explicit label for missing serving data', async () => {
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        events: [{
          ...canonicalGroup.events[0],
          local_time: null,
          meal_type: null,
          display_name: 'Unbenannter Eintrag',
          serving_amount: null,
          serving_unit: null,
        }],
      },
    }))

    const wrapper = await mountView()
    const row = wrapper.get('.verification-event-row')

    expect(wrapper.get('.verification-meal-occasion-label').text()).toBe('Ohne Kategorie')
    expect(wrapper.get('.verification-meal-occasion-entry-count').text()).toBe('1 Eintrag')
    expect(wrapper.get('.verification-meal-occasion-total').text()).toBe('640 kcal')
    expect(row.findAll('td').at(0)?.text()).toBe('Unbenannter Eintrag')
    expect(row.findAll('td').at(1)?.text()).toBe('Nicht angegeben')
    expect(wrapper.get('.verification-event-row').find('.verification-event-food').text()).toBe('Unbenannter Eintrag')
  })

  it('localizes missing serving data in English', async () => {
    setLocale('en')
    apiMock.mockResolvedValue(response({
      canonical: {
        ...canonicalGroup,
        events: [{
          ...canonicalGroup.events[0],
          serving_amount: null,
          serving_unit: null,
        }],
      },
    }))

    const wrapper = await mountView()

    expect(wrapper.get('.verification-event-row').findAll('td').at(1)?.text()).toBe('Not specified')
    wrapper.unmount()
  })

  it('moves by one local day and keeps the URL date synchronized', async () => {
    const { wrapper, router } = await mountViewAt()

    await wrapper.get('button[aria-label="Vorheriger Tag"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-21&view=canonical')
    expect(router.currentRoute.value.query.date).toBe('2026-09-21')

    await wrapper.get('button[aria-label="Nächster Tag"]').trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=canonical')
    expect(router.currentRoute.value.query.date).toBe('2026-09-22')

    await wrapper.get('button[aria-label="Vorheriger Tag"]').trigger('click')
    await flushPromises()
    const todayButton = wrapper.findAll('button').find((button) => button.text() === 'Heute')
    if (!todayButton) throw new Error('Today button not found')
    await todayButton.trigger('click')
    await flushPromises()
    expect(lastRequest()).toBe('/analytics/verification/nutrition?date=2026-09-22&view=canonical')
    expect(router.currentRoute.value.query.date).toBe('2026-09-22')
  })


  it('announces loading while the nutrition request is pending', async () => {
    const request = Promise.withResolvers<unknown>()
    apiMock.mockReturnValue(request.promise)

    const { wrapper } = await mountViewAt()

    expect(wrapper.get('[role="status"]').text()).toContain('Ernährungsdaten werden geladen')
    request.resolve(response())
    await flushPromises()
  })

  it('shows a localized error and retries the request', async () => {
    apiMock.mockRejectedValueOnce(new ApiError('request failed', 500))
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
