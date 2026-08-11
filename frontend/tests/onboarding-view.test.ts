import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  setCsrfToken: vi.fn(),
}))

import { useAuthStore } from '../src/stores/auth'
import SetupView from '../src/views/SetupView.vue'

const user = {
  id: 'user-1',
  username: 'friend',
  language: 'de',
  timezone: 'America/Los_Angeles',
  week_starts_on: 0,
  raw_payload_retention_days: 0,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

async function mountSetup() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.user = user
  auth.needsTargetSetup = true
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/einrichtung', name: 'setup', component: SetupView },
      { path: '/', name: 'overview', component: { template: '<h1>Übersicht</h1>' } },
      { path: '/login', name: 'login', component: { template: '<h1>Anmelden</h1>' } },
    ],
  })
  await router.push('/einrichtung')
  await router.isReady()
  const wrapper = mount(SetupView, { global: { plugins: [pinia, router] } })
  return { auth, router, wrapper }
}

describe('SetupView', () => {
  beforeEach(() => {
    apiMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('explains setup with empty required and optional goals', async () => {
    const { wrapper } = await mountSetup()
    const calories = wrapper.get<HTMLInputElement>('input[name="calories-kcal"]')
    const protein = wrapper.get<HTMLInputElement>('input[name="protein-g"]')

    expect(wrapper.get('h1').text()).toBe('Willkommen bei CaloGraph')
    expect(wrapper.text()).toContain('Bevor es losgeht, benötigen wir zwei Werte von dir.')
    expect(wrapper.find('nav').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('2200')
    expect(wrapper.text()).not.toContain('140')
    expect(wrapper.find('input[name="valid-from"]').exists()).toBe(false)
    expect(calories.element.value).toBe('')
    expect(protein.element.value).toBe('')
    expect(calories.attributes('required')).toBeDefined()
    expect(protein.attributes('required')).toBeDefined()
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    for (const input of wrapper.findAll<HTMLInputElement>('details input')) {
      expect(input.element.value).toBe('')
    }

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Kalorienbudget')
    expect(apiMock).not.toHaveBeenCalled()

    await calories.setValue('2100')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Proteinziel')
    expect(apiMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('saves through the target API with the account-zone date and opens the overview', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-11T00:30:00Z'))
    apiMock.mockResolvedValue({})
    const { auth, router, wrapper } = await mountSetup()

    await wrapper.get('input[name="calories-kcal"]').setValue('3000')
    await wrapper.get('input[name="maintenance-kcal"]').setValue('2500')
    await wrapper.get('input[name="protein-g"]').setValue('135')
    const carbs = wrapper.get('input[name="carbs-g"]')
    await carbs.setValue('200')
    await carbs.setValue('')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/settings/targets', {
      method: 'POST',
      body: JSON.stringify({
        valid_from: '2026-08-10',
        calories_kcal: 3000,
        maintenance_kcal: 2500,
        protein_g: 135,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
      }),
    })
    expect(auth.needsTargetSetup).toBe(false)
    expect(router.currentRoute.value.name).toBe('overview')
    wrapper.unmount()
  })

  it('keeps logout available without exposing normal navigation', async () => {
    apiMock.mockResolvedValue(undefined)
    const { auth, router, wrapper } = await mountSetup()

    await wrapper.get('button.setup-signout').trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
    expect(auth.user).toBeNull()
    expect(auth.needsTargetSetup).toBeNull()
    expect(router.currentRoute.value.name).toBe('login')
    wrapper.unmount()
  })
})
