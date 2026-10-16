import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock, localizeApiError: () => 'Eintrag nicht verfügbar' }))

import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import NutritionMealDetailView from '../src/views/NutritionMealDetailView.vue'

const persistedMeal = {
  event_id: 'meal-uuid', local_date: '2026-10-13', local_time: '19:30',
  meal_type: 'dinner', display_name: 'Lachs', provider_key: 'google_health',
  serving_amount: 2, serving_unit: 'portion', dietary_energy_kcal: 501.6,
  protein_g: 38.25, carbohydrates_g: 20, fat_g: 28, fiber_g: 3,
  sugar_g: null, saturated_fat_g: 4.25,
}

async function mountDetail(path = '/ernaehrung/eintrag/meal-uuid?from=analysis&period=custom&start=2026-10-01&end=2026-10-13') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/ernaehrung/eintrag/:eventId', component: NutritionMealDetailView },
      { path: '/analyse/ernaehrung', component: { template: '<div />' } },
      { path: '/ernaehrung', component: { template: '<div />' } },
      { path: '/analyse', component: { template: '<div />' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  setActivePinia(createPinia())
  setLocale('de')
  useAuthStore().user = {
    id: 'meal-user', username: 'owner', language: 'de', timezone: 'UTC',
    week_starts_on: 0, raw_payload_retention_days: 0, is_admin: false,
    is_active: true, deactivated_at: null,
  }
  apiMock.mockReset()
  apiMock.mockResolvedValue(persistedMeal)
})

describe('persisted Nutrition meal detail', () => {
  it('shows the stored portion and seven totals without multiplying or inventing optional data', async () => {
    const { wrapper } = await mountDetail()
    expect(apiMock).toHaveBeenCalledWith('/analytics/nutrition/analysis/meals/meal-uuid')
    expect(wrapper.get('h1').text()).toBe('Lachs')
    expect(wrapper.text()).toContain('13.10.2026')
    expect(wrapper.text()).toContain('19:30')
    expect(wrapper.text()).toContain('Abendessen')
    expect(wrapper.text()).toContain('2 Portionen')
    expect(wrapper.text()).toContain('502 kcal')
    expect(wrapper.text()).toContain('38,3 g')
    expect(wrapper.text()).not.toContain('1.003')
    expect(wrapper.text()).not.toContain('google_health')
    expect(wrapper.text()).not.toContain('Google Health')
    expect(wrapper.text()).not.toMatch(/Zutaten|Komponenten|Foto/)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.findAll('.card')).toHaveLength(1)
    expect(wrapper.findAll('h2').map((heading) => heading.text())).toEqual(['Nährwerte'])
    const nutritionLink = wrapper.findAll('nav a').find((link) => link.text() === 'Ernährung')
    expect(nutritionLink?.attributes('href')).toBe('/analyse/ernaehrung?period=custom&start=2026-10-01&end=2026-10-13')
    const dateLink = wrapper.findAll('nav a').find((link) => link.text() === '13.10.2026')
    expect(dateLink?.attributes('href')).toBe('/ernaehrung?date=2026-10-13')
    wrapper.unmount()
  })
  it('renders primary nutrition values and secondary key/value rows without a fixed table', async () => {
    const { wrapper } = await mountDetail()

    expect(wrapper.findAll('.nutrition-meal-primary-value')).toHaveLength(4)
    expect(wrapper.findAll('.nutrition-meal-secondary-value')).toHaveLength(3)
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.text()).toContain('502 kcal')
    expect(wrapper.text()).toContain('38,3 g')
    expect(wrapper.text()).toContain('Ballaststoffe')
    expect(wrapper.text()).toContain('Gesättigte Fettsäuren')
    const sugarRow = wrapper.findAll('.nutrition-meal-secondary-value').find((row) => row.text().includes('Zucker'))
    expect(sugarRow?.get('dd').text()).toBe('–')
    wrapper.unmount()
  })


  it('preserves daily navigation context and handles an unavailable event without fabricated meal content', async () => {
    apiMock.mockRejectedValue(new Error('not available'))
    const { wrapper } = await mountDetail('/ernaehrung/eintrag/missing?from=daily&date=2026-10-12')
    expect(wrapper.get('[role="alert"]').text()).toContain('Eintrag nicht verfügbar')
    expect(wrapper.find('table').exists()).toBe(false)
    expect(wrapper.findAll('nav a').find((link) => link.text() === 'Ernährung')?.attributes('href')).toBe('/ernaehrung?date=2026-10-12')
    wrapper.unmount()
  })

  it('shows a date breadcrumb without Analysis context when opened from the daily view', async () => {
    const { wrapper } = await mountDetail('/ernaehrung/eintrag/meal-uuid?from=daily&date=2026-10-13')
    const breadcrumbLinks = wrapper.findAll('nav a')

    expect(breadcrumbLinks.map((link) => link.text())).toEqual(['Ernährung', '13.10.2026'])
    expect(breadcrumbLinks[1]?.attributes('href')).toBe('/ernaehrung?date=2026-10-13')
    wrapper.unmount()
  })
  it('uses router history when available and the meal date as the deep-link fallback', async () => {
    const { wrapper, router } = await mountDetail('/analyse/ernaehrung?period=30')
    await router.push('/ernaehrung/eintrag/meal-uuid?from=analysis&period=30')
    await flushPromises()
    Object.assign(router.options.history.state, { back: '/analyse/ernaehrung?period=30' })
    await wrapper.get('[data-meal-back]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/analyse/ernaehrung?period=30')
    wrapper.unmount()

    const directLink = await mountDetail('/ernaehrung/eintrag/meal-uuid?from=daily&date=2026-10-13')
    await directLink.wrapper.get('[data-meal-back]').trigger('click')
    await flushPromises()
    expect(directLink.router.currentRoute.value.fullPath).toBe('/ernaehrung?date=2026-10-13')
    directLink.wrapper.unmount()
  })
  it('uses history from queryless daily Nutrition rather than adding a date fallback entry', async () => {
    const { wrapper, router } = await mountDetail('/ernaehrung')
    await router.push('/ernaehrung/eintrag/meal-uuid?from=daily')
    await flushPromises()
    Object.assign(router.options.history.state, { back: '/ernaehrung' })

    await wrapper.get('[data-meal-back]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/ernaehrung')
    wrapper.unmount()
  })


  it('reloads the persisted response on an event UUID change', async () => {
    const { wrapper, router } = await mountDetail()
    apiMock.mockResolvedValue({ ...persistedMeal, event_id: 'second-meal', display_name: 'Pudding', local_time: null })
    await router.push('/ernaehrung/eintrag/second-meal?from=daily&date=2026-10-13')
    await flushPromises()
    expect(apiMock).toHaveBeenLastCalledWith('/analytics/nutrition/analysis/meals/second-meal')
    expect(wrapper.get('h1').text()).toBe('Pudding')
    expect(wrapper.text()).not.toContain('19:30')
    wrapper.unmount()
  })
  it('reloads the persisted event for a new account instead of keeping the previous account detail', async () => {
    const { wrapper } = await mountDetail()
    apiMock.mockResolvedValueOnce({ ...persistedMeal, display_name: 'Benutzer B' })
    useAuthStore().user = { ...useAuthStore().user!, id: 'meal-user-b' }
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('Benutzer B')
    expect(apiMock).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
