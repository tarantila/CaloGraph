import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock, localizeApiError: () => 'Ladefehler' }))
vi.mock('../src/components/ChartPanel.vue', () => ({
  default: {
    props: ['title', 'option', 'empty'],
    template: '<section class="card chart-card"><div class="chart-card-header"><h2><slot name="title">{{ title }}</slot></h2><slot name="header-actions" /></div></section>',
  },
}))

import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import AnalysisNutritionOverviewView from '../src/views/AnalysisNutritionOverviewView.vue'

import type { AnalysisNutritionResponse } from '../src/types'
import {
  DEFAULT_NUTRITION_SECTION_ORDER,
  groupNutritionMealOccasions,
  projectNutritionMeals,
  moveNutritionSection,
  normalizeNutritionSectionOrder,
  nutritionSectionOrderStorageKey,
} from '../src/analysis-nutrition-overview'

const meal = (event_id: string, local_time: string | null, display_name: string) => ({
  event_id,
  local_date: '2026-10-13',
  serving_amount: null,
  serving_unit: null,
  provider_key: 'google_health',
  protein_g: null,
  carbohydrates_g: null,
  fat_g: null,
  fiber_g: null,
  sugar_g: null,
  saturated_fat_g: null,
  local_time,
  meal_type: 'dinner',
  display_name,
  dietary_energy_kcal: 120,
})

const periodMealsResponse = {
  start_date: '2026-10-01', end_date: '2026-10-13', period_days: 13,
  selected_provider: { provider_key: 'google_health' },
  period_capabilities: { supports_all_data: false, all_data_start_date: null },
  days: [
    {
      date: '2026-10-10', provider_key: 'google_health', dietary_energy_kcal: 240,
      protein_g: null, carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
      meals: [
        { ...meal('old-dinner', '19:00', 'Gemüsesuppe'), local_date: '2026-10-10', meal_type: 'dinner' },
        { ...meal('old-breakfast', '08:00', 'Vollkornbrot'), local_date: '2026-10-10', meal_type: 'breakfast' },
      ],
    },
    {
      date: '2026-10-12', provider_key: 'google_health', dietary_energy_kcal: 360,
      protein_g: null, carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
      meals: [
        { ...meal('latest-dinner', '19:30', 'Ofengemüse'), local_date: '2026-10-12', meal_type: 'dinner' },
        { ...meal('latest-breakfast', '07:30', 'Haferflocken'), local_date: '2026-10-12', meal_type: 'breakfast' },
        { ...meal('latest-lunch', '12:30', 'Linsensalat'), local_date: '2026-10-12', meal_type: 'lunch' },
      ],
    },
    {
      date: '2026-10-11', provider_key: 'google_health', dietary_energy_kcal: 600,
      protein_g: null, carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
      meals: [
        { ...meal('middle-dinner', '18:30', 'Kartoffelauflauf'), local_date: '2026-10-11', meal_type: 'dinner' },
        { ...meal('middle-breakfast-yogurt', '08:00', 'Naturjoghurt'), local_date: '2026-10-11', meal_type: 'breakfast' },
        { ...meal('middle-lunch', '12:00', 'Gemüsereis'), local_date: '2026-10-11', meal_type: 'lunch' },
        { ...meal('middle-breakfast-fruit', '08:15', 'Beeren'), local_date: '2026-10-11', meal_type: 'breakfast' },
        { ...meal('middle-breakfast-unnamed', '08:10', ''), local_date: '2026-10-11', meal_type: 'breakfast', display_name: null },
      ],
    },
  ],
} satisfies AnalysisNutritionResponse

describe('Nutrition overview helpers', () => {
  it('normalizes per-account section order and appends missing known sections', () => {
    expect(normalizeNutritionSectionOrder(['meals', 'meals', 'unknown', 'energy'])).toEqual([
      'meals', 'energy', 'macros',
    ])
    expect(normalizeNutritionSectionOrder(null)).toEqual(DEFAULT_NUTRITION_SECTION_ORDER)
    expect(nutritionSectionOrderStorageKey('user / 1')).toBe(
      'calograph.analysis.nutrition.section-order.v1.user%20%2F%201',
    )
  })

  it('moves a section by one place and keeps boundary moves stable', () => {
    expect(moveNutritionSection(DEFAULT_NUTRITION_SECTION_ORDER, 'meals', -1)).toEqual([
      'energy', 'meals', 'macros',
    ])
    expect(moveNutritionSection(DEFAULT_NUTRITION_SECTION_ORDER, 'energy', -1)).toEqual([
      'energy', 'macros', 'meals',
    ])
  })


  it('orders known meal types semantically and keeps unknown groups deterministic', () => {
    const entries = [
      { id: 'dinner-no-time', type: 'dinner', time: null, name: 'Dinner untimed', energy: null },
      { id: 'unknown-z', type: 'provider-z', time: '00:00', name: 'Unknown Z', energy: null },
      { id: 'snack-early', type: 'snack', time: '06:00', name: 'Snack', energy: null },
      { id: 'dinner-tie-z', type: 'dinner', time: '19:00', name: 'Dinner Z', energy: null },
      { id: 'lunch-no-time', type: 'lunch', time: null, name: 'Lunch untimed', energy: null },
      { id: 'breakfast-no-time', type: 'breakfast', time: null, name: 'Breakfast untimed', energy: null },
      { id: 'null-b', type: null, time: null, name: 'Null B', energy: null },
      { id: 'dinner-tie-a', type: 'dinner', time: '19:00', name: 'Dinner A', energy: null },
      { id: 'unknown-a', type: 'provider-a', time: '00:30', name: 'Unknown A', energy: null },
      { id: 'unknown-constructor', type: 'constructor', time: '00:15', name: 'Constructor', energy: null },
      { id: 'unknown-prototype', type: '__proto__', time: '00:10', name: 'Prototype', energy: null },
      { id: 'dinner-early', type: 'dinner', time: '06:00', name: 'Dinner early', energy: null },
      { id: 'null-a', type: null, time: null, name: 'Null A', energy: null },
    ] as const
    const groups = groupNutritionMealOccasions(entries)
    const groupOrder = groups.map(({ key, type }) => [key, type])

    expect(groupOrder).toEqual([
      ['type:breakfast', 'breakfast'],
      ['type:lunch', 'lunch'],
      ['type:dinner', 'dinner'],
      ['type:snack', 'snack'],
      ['entry:null-a', null],
      ['entry:null-b', null],
      ['type:__proto__', '__proto__'],
      ['type:constructor', 'constructor'],
      ['type:provider-a', 'provider-a'],
      ['type:provider-z', 'provider-z'],
    ])
    expect(groups.find((group) => group.type === 'dinner')?.entries.map((entry) => entry.id))
      .toEqual(['dinner-early', 'dinner-tie-a', 'dinner-tie-z', 'dinner-no-time'])
    expect(groupNutritionMealOccasions([...entries].reverse()).map(({ key }) => key))
      .toEqual(groups.map(({ key }) => key))
  })

  it('keeps semantic meal order in recent and full projections despite conflicting local times', () => {
    const result = projectNutritionMeals([{
      date: '2026-10-13',
      meals: [
        { ...meal('dinner', '06:00', 'Dinner'), meal_type: 'dinner' },
        { ...meal('snack', '05:00', 'Snack'), meal_type: 'snack' },
        { ...meal('lunch', '23:00', 'Lunch'), meal_type: 'lunch' },
        { ...meal('breakfast', '22:00', 'Breakfast'), meal_type: 'breakfast' },
        { ...meal('unsupported', '00:30', 'Unsupported'), meal_type: 'provider-dessert' },
        { ...meal('missing', null, 'Missing type'), meal_type: null },
      ],
    }])

    expect(result.preview[0]?.occasions.map((occasion) => occasion.type)).toEqual([
      'breakfast', 'lunch', 'dinner', 'snack', null, 'provider-dessert',
    ])
    expect(result.all[0]?.occasions.map((occasion) => occasion.type)).toEqual([
      'breakfast', 'lunch', 'dinner', 'snack', null, 'provider-dessert',
    ])
  })

  it('groups recent named meal occasions by date and orders days newest first with chronological meals', () => {
    const days = [
      { date: '2026-10-12', meals: [{ ...meal('e', '19:00', 'Abendessen'), meal_type: 'dinner' }] },
      { date: '2026-10-13', meals: [
        { ...meal('a', '07:30', 'Frühstück'), meal_type: 'breakfast' },
        { ...meal('b', '19:30', 'Pudding'), meal_type: 'snack' },
        { ...meal('c', '15:30', 'Lachs'), meal_type: 'dinner' },
        { ...meal('d', null, 'Ohne Uhrzeit'), meal_type: null },
      ] },
    ]

    const groups = projectNutritionMeals(days).preview

    expect(groups.map((group) => ({
      date: group.date,
      occasions: group.occasions.map((occasion) => ({
        type: occasion.type,
        time: occasion.time,
        entries: occasion.entries.map((entry) => ({ eventId: entry.eventId, name: entry.name })),
      })),
    }))).toEqual([
      { date: '2026-10-13', occasions: [
        { type: 'breakfast', time: '07:30', entries: [{ eventId: 'a', name: 'Frühstück' }] },
        { type: 'dinner', time: '15:30', entries: [{ eventId: 'c', name: 'Lachs' }] },
        { type: 'snack', time: '19:30', entries: [{ eventId: 'b', name: 'Pudding' }] },
        { type: null, time: null, entries: [{ eventId: 'd', name: 'Ohne Uhrzeit' }] },
      ] },
      { date: '2026-10-12', occasions: [
        { type: 'dinner', time: '19:00', entries: [{ eventId: 'e', name: 'Abendessen' }] },
      ] },
    ])
  })

  it('limits the displayed list to six complete occasions and preserves date order', () => {
    const days = [
      { date: '2026-10-13', meals: Array.from({ length: 4 }, (_, index) => ({ ...meal(`today-${index}`, `1${index}:00`, `Today ${index}`), meal_type: null })) },
      { date: '2026-10-12', meals: Array.from({ length: 4 }, (_, index) => ({ ...meal(`older-${index}`, `1${index}:00`, `Older ${index}`), meal_type: null })) },
    ]

    expect(projectNutritionMeals(days).preview.flatMap((group) => group.occasions.flatMap((occasion) => occasion.entries))).toHaveLength(6)
    expect(projectNutritionMeals(days).preview.map((group) => group.date)).toEqual(['2026-10-13', '2026-10-12'])
  })

  it('groups same-day entries by meal type and sums only complete multi-entry occasions', () => {
    const days = [{
      date: '2026-10-13',
      meals: [
        { ...meal('lunch-salad', '11:30', 'Linsensalat'), meal_type: 'lunch', dietary_energy_kcal: 510 },
        { ...meal('lunch-schnitzel', '11:30', 'Schnitzel'), meal_type: 'lunch', dietary_energy_kcal: 650 },
        { ...meal('breakfast', '07:30', 'Haferflocken'), meal_type: 'breakfast', dietary_energy_kcal: 420 },
      ],
    }]

    const date = projectNutritionMeals(days).preview[0]
    expect(date?.occasions.map((occasion) => ({
      type: occasion.type,
      time: occasion.time,
      entries: occasion.entries.map((entry) => entry.name),
      totalEnergy: occasion.totalEnergy,
    }))).toEqual([
      { type: 'breakfast', time: '07:30', entries: ['Haferflocken'], totalEnergy: null },
      { type: 'lunch', time: '11:30', entries: ['Linsensalat', 'Schnitzel'], totalEnergy: 1160 },
    ])
  })
  it('keeps real zero totals and omits subtotals when any entry lacks energy', () => {
    const occasions = projectNutritionMeals([{
      date: '2026-10-13',
      meals: [
        { ...meal('incomplete-one', '12:00', 'Eintrag A'), meal_type: 'lunch', dietary_energy_kcal: 510 },
        { ...meal('incomplete-two', '12:00', 'Eintrag B'), meal_type: 'lunch', dietary_energy_kcal: null },
        { ...meal('zero-one', '19:00', 'Null A'), meal_type: 'snack', dietary_energy_kcal: 0 },
        { ...meal('zero-two', '19:00', 'Null B'), meal_type: 'snack', dietary_energy_kcal: 0 },
      ],
    }]).preview[0]?.occasions ?? []

    expect(occasions.find((occasion) => occasion.type === 'lunch')?.totalEnergy).toBeNull()
    expect(occasions.find((occasion) => occasion.type === 'snack')?.totalEnergy).toBe(0)
  })


  it('includes unnamed same-type entries in complete occasion totals without rendering them', () => {
    const occasion = projectNutritionMeals([{
      date: '2026-10-13',
      meals: [
        { ...meal('named-lunch', '11:30', 'Linsensalat'), meal_type: 'lunch', dietary_energy_kcal: 510 },
        { ...meal('unnamed-lunch', '11:30', ''), meal_type: 'lunch', display_name: null, dietary_energy_kcal: 100 },
      ],
    }]).preview[0]?.occasions[0]

    expect(occasion?.entries.map((entry) => entry.name)).toEqual(['Linsensalat'])
    expect(occasion?.entryCount).toBe(2)
    expect(occasion?.totalEnergy).toBe(610)
  })

  it('omits an occasion subtotal when any same-type entry lacks energy', () => {
    const occasion = projectNutritionMeals([{
      date: '2026-10-13',
      meals: [
        { ...meal('named-lunch', '11:30', 'Linsensalat'), meal_type: 'lunch', dietary_energy_kcal: 510 },
        { ...meal('unnamed-lunch', '11:30', ''), meal_type: 'lunch', display_name: null, dietary_energy_kcal: null },
      ],
    }]).preview[0]?.occasions[0]

    expect(occasion?.entryCount).toBe(2)
    expect(occasion?.totalEnergy).toBeNull()
  })


})

describe('Nutrition overview interactions', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-13T12:00:00Z'))
    setActivePinia(createPinia())
    setLocale('de')
    window.localStorage.clear()
    useAuthStore().user = {
      id: 'nutrition-user', username: 'owner', language: 'de', timezone: 'UTC',
      week_starts_on: 0, raw_payload_retention_days: 0, is_admin: false,
      is_active: true, deactivated_at: null,
    }
    apiMock.mockReset()
    apiMock.mockResolvedValue({
      start_date: '2026-10-01', end_date: '2026-10-13', period_days: 13,
      selected_provider: { provider_key: 'google_health' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      days: [{
        date: '2026-10-13', provider_key: 'google_health', dietary_energy_kcal: 240, protein_g: 10,
        carbohydrates_g: 20, fat_g: 5, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [
          { ...meal('pudding-uuid', '19:30', 'Pudding'), meal_type: 'snack' },
          { ...meal('lachs-uuid', '15:30', 'Lachs'), meal_type: 'dinner' },
        ],
      }],
    })
  })

  afterEach(() => { vi.useRealTimers() })

  async function mountOverview() {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/analyse', component: { template: '<div />' } },
        { path: '/analyse/ernaehrung', component: AnalysisNutritionOverviewView },
        { path: '/analyse/ernaehrung/:metric', component: { template: '<div />' } },
        { path: '/ernaehrung/eintrag/:eventId', component: { template: '<div />' } },
      ],
    })
    await router.push('/analyse/ernaehrung?period=custom&start=2026-10-01&end=2026-10-13')
    await router.isReady()
    const wrapper = mount(RouterView, { global: { plugins: [router], stubs: { PeriodPicker: true } } })
    await flushPromises()
    return { wrapper, router }
  }

  it('reorders sections with accessible up/down controls and preserves per-account order', async () => {
    const { wrapper } = await mountOverview()
    const order = () => wrapper.findAll('[data-nutrition-section]').map((section) => section.attributes('data-nutrition-section'))
    expect(order()).toEqual(['energy', 'macros', 'meals'])
    expect(wrapper.findAll('[draggable="true"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-move-up]')).toHaveLength(3)
    expect(wrapper.findAll('[data-move-down]')).toHaveLength(3)
    expect(wrapper.get('[data-nutrition-section="energy"] [data-move-up]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-nutrition-section="meals"] [data-move-down]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-nutrition-section="meals"] [data-move-up]').trigger('click')
    expect(order()).toEqual(['energy', 'meals', 'macros'])
    expect(JSON.parse(window.localStorage.getItem('calograph.analysis.nutrition.section-order.v1.nutrition-user')!)).toEqual(['energy', 'meals', 'macros'])
    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: 'other-user' }
    await flushPromises()
    expect(order()).toEqual(['energy', 'macros', 'meals'])
    auth.user = { ...auth.user!, id: 'nutrition-user' }
    await flushPromises()
    expect(order()).toEqual(['energy', 'meals', 'macros'])
    wrapper.unmount()
    const reloaded = await mountOverview()
    expect(reloaded.wrapper.findAll('[data-nutrition-section]').map((section) => section.attributes('data-nutrition-section')))
      .toEqual(['energy', 'meals', 'macros'])
    reloaded.wrapper.unmount()
  })


  it('clears and reloads Nutrition data on account change, ignoring a late prior-account response', async () => {
    const { wrapper, router } = await mountOverview()
    const responseWithMeal = (eventId: string, name: string) => ({
      start_date: '2026-10-01', end_date: '2026-10-13', period_days: 13,
      selected_provider: { provider_key: 'google_health' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      days: [{
        date: '2026-10-13', provider_key: 'google_health', dietary_energy_kcal: 240, protein_g: 10,
        carbohydrates_g: 20, fat_g: 5, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [meal(eventId, '19:30', name)],
      }],
    })
    const oldAccountRequest = Promise.withResolvers<unknown>()
    apiMock.mockReturnValueOnce(oldAccountRequest.promise)
    await router.push({ query: { ...router.currentRoute.value.query, start: '2026-10-02' } })
    await flushPromises()
    apiMock.mockResolvedValueOnce(responseWithMeal('account-b-meal', 'Benutzer B'))

    const auth = useAuthStore()
    auth.user = { ...auth.user!, id: 'other-user' }
    await flushPromises()
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text())).toEqual(['Benutzer B'])

    oldAccountRequest.resolve(responseWithMeal('account-a-stale-meal', 'Veraltete Daten A'))
    await flushPromises()
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text())).toEqual(['Benutzer B'])
    wrapper.unmount()
  })

  it('recalculates rolling bounds on timezone change and ignores a late old-timezone response', async () => {
    vi.setSystemTime(new Date('2026-10-13T02:00:00Z'))
    const { wrapper, router } = await mountOverview()
    const staleRequest = Promise.withResolvers<unknown>()
    apiMock.mockReturnValueOnce(staleRequest.promise)

    await router.push({ query: { period: '30' } })
    await flushPromises()
    const oldBoundsCall = apiMock.mock.calls.at(-1)![0] as string
    const oldBounds = new URL(oldBoundsCall, 'http://localhost').searchParams
    expect([oldBounds.get('start'), oldBounds.get('end')]).toEqual(['2026-09-14', '2026-10-13'])

    apiMock.mockImplementation(async (url: string) => {
      const end = new URL(url, 'http://localhost').searchParams.get('end')!
      return {
        start_date: '2026-09-13', end_date: end, period_days: 30,
        selected_provider: { provider_key: 'google_health' },
        period_capabilities: { supports_all_data: false, all_data_start_date: null },
        days: [{
          date: end, provider_key: 'google_health', dietary_energy_kcal: 240,
          protein_g: 10, carbohydrates_g: 20, fat_g: 5, fiber_g: null, sugar_g: null, saturated_fat_g: null,
          meals: [meal('timezone-b-meal', '19:30', 'Zeitzone B')],
        }],
      }
    })
    const auth = useAuthStore()
    auth.user = { ...auth.user!, timezone: 'America/Los_Angeles' }
    await flushPromises()

    expect(router.currentRoute.value.query.period).toBe('30')
    expect(router.currentRoute.value.query.start).toBeUndefined()
    expect(router.currentRoute.value.query.end).toBeUndefined()
    const newBoundsCall = apiMock.mock.calls.at(-1)![0] as string
    const newBounds = new URL(newBoundsCall, 'http://localhost').searchParams
    expect([newBounds.get('start'), newBounds.get('end')]).toEqual(['2026-09-13', '2026-10-12'])
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text())).toEqual(['Zeitzone B'])

    staleRequest.resolve({
      start_date: '2026-09-14', end_date: '2026-10-13', period_days: 30,
      selected_provider: { provider_key: 'google_health' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      days: [{
        date: '2026-10-13', provider_key: 'google_health', dietary_energy_kcal: 240,
        protein_g: 10, carbohydrates_g: 20, fat_g: 5, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [meal('timezone-a-meal', '19:30', 'Veraltete Zeitzone A')],
      }],
    })
    await flushPromises()
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text())).toEqual(['Zeitzone B'])
    wrapper.unmount()
  })

  it('links the date-grouped meals by persisted UUID with the Analysis period context', async () => {
    const { wrapper } = await mountOverview()
    expect(wrapper.findAll('.nutrition-meal-name').map((name) => name.text())).toEqual(['Lachs', 'Pudding'])
    expect(wrapper.findAll('.nutrition-meal-date')).toHaveLength(1)
    expect(wrapper.get('[data-meal-entry]').attributes('href')).toBe('/ernaehrung/eintrag/lachs-uuid?period=custom&start=2026-10-01&end=2026-10-13&from=analysis')
    wrapper.unmount()
  })

  it('expands the six complete recent meal occasions in place to all period meals and collapses them again', async () => {
    apiMock.mockResolvedValue(periodMealsResponse)
    const { wrapper, router } = await mountOverview()
    const meals = wrapper.get('section[aria-labelledby="nutrition-meals-title"]')
    const renderedDays = () => meals.findAll('h3').map((heading) => {
      const group = new DOMWrapper(heading.element.parentElement!)
      return {
        date: heading.text(),
        occasions: group.findAll('[data-meal-type]').map((occasion) => ({
          title: occasion.get('h4').text(),
          type: occasion.attributes('data-meal-type'),
          entries: occasion.findAll('[data-meal-entry]').map((link) => ({
            name: link.get('span').text(),
            href: link.attributes('href'),
          })),
        })),
      }
    })
    const preview = [
      { date: '12.10.2026', occasions: [
        { title: 'Frühstück · 07:30', type: 'breakfast', entries: [
          { name: 'Haferflocken', href: '/ernaehrung/eintrag/latest-breakfast?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
        { title: 'Mittagessen · 12:30', type: 'lunch', entries: [
          { name: 'Linsensalat', href: '/ernaehrung/eintrag/latest-lunch?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
        { title: 'Abendessen · 19:30', type: 'dinner', entries: [
          { name: 'Ofengemüse', href: '/ernaehrung/eintrag/latest-dinner?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
      ] },
      { date: '11.10.2026', occasions: [
        { title: 'Frühstück · 08:00', type: 'breakfast', entries: [
          { name: 'Naturjoghurt', href: '/ernaehrung/eintrag/middle-breakfast-yogurt?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
          { name: 'Beeren', href: '/ernaehrung/eintrag/middle-breakfast-fruit?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
        { title: 'Mittagessen · 12:00', type: 'lunch', entries: [
          { name: 'Gemüsereis', href: '/ernaehrung/eintrag/middle-lunch?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
        { title: 'Abendessen · 18:30', type: 'dinner', entries: [
          { name: 'Kartoffelauflauf', href: '/ernaehrung/eintrag/middle-dinner?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
      ] },
    ]
    expect(renderedDays()).toEqual(preview)
    expect(meals.findAll('h4')).toHaveLength(6)
    expect(meals.findAll('[data-meal-entry]')).toHaveLength(7)
    expect(meals.text()).toContain('360 kcal')
    expect(meals.find('[href*="middle-breakfast-unnamed"]').exists()).toBe(false)

    const expand = meals.findAll('button').find((button) => button.text() === 'Alle Mahlzeiten im Zeitraum anzeigen')
    expect(expand, 'The meals section exposes the in-place expansion action').toBeDefined()
    expect(expand!.element.tagName).toBe('BUTTON')
    expect(expand!.attributes('type')).toBe('button')
    expect(expand!.attributes('aria-expanded')).toBe('false')
    const contentId = expand!.attributes('aria-controls')
    expect(contentId).toBeTruthy()
    const content = meals.findAll('[id]').find((element) => element.attributes('id') === contentId)
    expect(content, 'aria-controls resolves to existing meal content').toBeDefined()
    expect(content!.findAll('[data-meal-entry]')).toHaveLength(7)
    const lastPreviewLink = meals.findAll('[data-meal-entry]').at(-1)!
    expect(expand!.element.compareDocumentPosition(lastPreviewLink.element) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    const originalRoute = router.currentRoute.value.fullPath

    await expand!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(originalRoute)
    expect(renderedDays()).toEqual([
      ...preview,
      { date: '10.10.2026', occasions: [
        { title: 'Frühstück · 08:00', type: 'breakfast', entries: [
          { name: 'Vollkornbrot', href: '/ernaehrung/eintrag/old-breakfast?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
        { title: 'Abendessen · 19:00', type: 'dinner', entries: [
          { name: 'Gemüsesuppe', href: '/ernaehrung/eintrag/old-dinner?period=custom&start=2026-10-01&end=2026-10-13&from=analysis' },
        ] },
      ] },
    ])
    expect(meals.findAll('h4')).toHaveLength(8)
    expect(meals.findAll('[data-meal-entry]')).toHaveLength(9)
    expect(meals.find('[href*="middle-breakfast-unnamed"]').exists()).toBe(false)
    const collapse = meals.findAll('button').find((button) => button.text() === 'Weniger anzeigen')
    expect(collapse).toBeDefined()
    expect(collapse!.attributes('aria-expanded')).toBe('true')
    expect(collapse!.attributes('aria-controls')).toBe(contentId)

    await collapse!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe(originalRoute)
    expect(renderedDays()).toEqual(preview)
    expect(meals.findAll('h4')).toHaveLength(6)
    expect(meals.findAll('button').find((button) => button.text() === 'Alle Mahlzeiten im Zeitraum anzeigen')?.attributes('aria-expanded')).toBe('false')
    wrapper.unmount()
  })
  it('omits the expansion action when the six-occasion preview covers the full period', async () => {
    apiMock.mockResolvedValue({
      ...periodMealsResponse,
      days: periodMealsResponse.days.filter((day) => day.date !== '2026-10-10'),
    })
    const { wrapper } = await mountOverview()
    try {
      const meals = wrapper.get('section[aria-labelledby="nutrition-meals-title"]')
      expect(meals.findAll('[data-meal-type]')).toHaveLength(6)
      expect(meals.findAll('[data-meal-entry]')).toHaveLength(7)
      expect(meals.findAll('button').some((button) => button.text() === 'Alle Mahlzeiten im Zeitraum anzeigen')).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })


  it.each([
    { changed: 'start', query: { period: 'custom', start: '2026-10-02', end: '2026-10-13' }, periodDays: 12 },
    { changed: 'end', query: { period: 'custom', start: '2026-10-01', end: '2026-10-12' }, periodDays: 12 },
    { changed: 'period', query: { period: '7', start: '2026-10-07', end: '2026-10-13' }, periodDays: 7 },
  ])('resets expanded meals to the six-occasion preview when the effective $changed query changes', async ({ query, periodDays }) => {
    apiMock.mockResolvedValue(periodMealsResponse)
    const { wrapper, router } = await mountOverview()
    const meals = () => wrapper.get('section[aria-labelledby="nutrition-meals-title"]')
    const expand = meals().findAll('button').find((button) => button.text() === 'Alle Mahlzeiten im Zeitraum anzeigen')
    expect(expand).toBeDefined()
    await expand!.trigger('click')
    await flushPromises()
    expect(meals().findAll('h4')).toHaveLength(8)
    expect(meals().findAll('h3').map((heading) => heading.text())).toEqual(['12.10.2026', '11.10.2026', '10.10.2026'])

    apiMock.mockResolvedValue({
      ...periodMealsResponse,
      start_date: query.start,
      end_date: query.end,
      period_days: periodDays,
    } satisfies AnalysisNutritionResponse)
    await router.push({ query })
    await flushPromises()

    expect(router.currentRoute.value.query).toEqual(
      query.period === 'custom' ? query : { period: query.period },
    )
    expect(meals().findAll('h4')).toHaveLength(6)
    expect(meals().findAll('h3').map((heading) => heading.text())).toEqual(['12.10.2026', '11.10.2026'])
    expect(meals().findAll('[data-meal-entry]').map((link) => link.get('span').text())).toEqual([
      'Haferflocken', 'Linsensalat', 'Ofengemüse', 'Naturjoghurt', 'Beeren', 'Gemüsereis', 'Kartoffelauflauf',
    ])
    const resetAction = meals().findAll('button').find((button) => button.text() === 'Alle Mahlzeiten im Zeitraum anzeigen')
    expect(resetAction).toBeDefined()
    expect(resetAction!.attributes('aria-expanded')).toBe('false')
    expect(meals().findAll('button').some((button) => button.text() === 'Weniger anzeigen')).toBe(false)
    const mealLink = new URL(meals().get('[data-meal-entry]').attributes('href')!, 'https://calograph.test')
    expect(mealLink.pathname).toBe('/ernaehrung/eintrag/latest-breakfast')
    expect([...mealLink.searchParams.entries()]).toEqual(query.period === 'custom'
      ? [['period', 'custom'], ['start', query.start], ['end', query.end], ['from', 'analysis']]
      : [['period', query.period], ['from', 'analysis']])
    wrapper.unmount()
  })
  it('renders multiple entries in one meal occasion with an exact subtotal', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-10-01', end_date: '2026-10-13', period_days: 13,
      selected_provider: { provider_key: 'google_health' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      days: [{
        date: '2026-10-13', provider_key: 'google_health', dietary_energy_kcal: 1160, protein_g: null,
        carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [
          { ...meal('salad', '11:30', 'Linsensalat'), meal_type: 'lunch', dietary_energy_kcal: 510 },
          { ...meal('schnitzel', '11:30', 'Schnitzel'), meal_type: 'lunch', dietary_energy_kcal: 650 },
        ],
      }],
    })

    const { wrapper } = await mountOverview()
    const lunch = wrapper.get('.nutrition-meal-occasion[data-meal-type="lunch"]')
    expect(lunch.get('.nutrition-meal-occasion-title').text()).toBe('Mittagessen · 11:30')
    expect(lunch.findAll('.nutrition-meal-name').map((entry) => entry.text())).toEqual(['Linsensalat', 'Schnitzel'])
    expect(lunch.get('.nutrition-meal-subtotal span').text()).toBe('Mittagessen gesamt')
    expect(lunch.get('.nutrition-meal-subtotal strong').text()).toBe('1.160 kcal')
    expect(lunch.findAll('[data-meal-entry]')).toHaveLength(2)
    wrapper.unmount()
  })
  it('renders the total for every same-type entry even when unnamed entries are hidden', async () => {
    apiMock.mockResolvedValue({
      start_date: '2026-10-01', end_date: '2026-10-13', period_days: 13,
      selected_provider: { provider_key: 'google_health' },
      period_capabilities: { supports_all_data: false, all_data_start_date: null },
      days: [{
        date: '2026-10-13', provider_key: 'google_health', dietary_energy_kcal: 610, protein_g: null,
        carbohydrates_g: null, fat_g: null, fiber_g: null, sugar_g: null, saturated_fat_g: null,
        meals: [
          { ...meal('named-lunch', '11:30', 'Linsensalat'), meal_type: 'lunch', dietary_energy_kcal: 510 },
          { ...meal('unnamed-lunch', '11:30', ''), meal_type: 'lunch', display_name: null, dietary_energy_kcal: 100 },
        ],
      }],
    })

    const { wrapper } = await mountOverview()
    const lunch = wrapper.get('.nutrition-meal-occasion[data-meal-type="lunch"]')
    expect(lunch.findAll('.nutrition-meal-name').map((entry) => entry.text())).toEqual(['Linsensalat'])
    expect(lunch.get('.nutrition-meal-subtotal strong').text()).toBe('610 kcal')
    wrapper.unmount()
  })

})
