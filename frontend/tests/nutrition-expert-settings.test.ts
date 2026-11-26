import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import AccountNutritionExpertView from '../src/views/AccountNutritionExpertView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { setAuthenticatedIdentity } from '../src/auth-session'

const initialPreferences = {
  data_domains: ['nutrition', 'targets', 'weight', 'activity', 'profile', 'health_context'],
  include_meal_names: true,
  configuration_version: 1,
}
let wrappers: VueWrapper[] = []
let preferences = { ...initialPreferences, data_domains: [...initialPreferences.data_domains] }
let consents: Array<{ id: string; connection_id: string; revoked_at: string | null }> = []

function setupResponse() {
  return { connection: null, preferences: { ...preferences }, consent: null, generation_available: true }
}

function responsePage(after: string | null) {
  const items = consents
  if (!after) return { items: items.slice(0, 1), next_after: items.length > 1 ? 'page-2' : null }
  return { items: items.slice(1), next_after: null }
}

function installDefaultApi(): void {
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/expert/setup') return setupResponse()
    if (path.startsWith('/settings/ai/consents?')) {
      const query = new URLSearchParams(path.split('?')[1])
      return responsePage(query.get('after'))
    }
    if (path.startsWith('/settings/ai/consents/') && options?.method === 'DELETE') {
      const id = path.split('/').at(-1)
      consents = consents.map(item => item.id === id ? { ...item, revoked_at: '2026-11-26T00:00:00Z', state: 'revoked' } : item)
      return undefined
    }
    if (path === '/settings/expert' && options?.method === 'PUT') {
      const payload = JSON.parse(String(options.body)) as typeof preferences
      preferences = { ...preferences, ...payload, configuration_version: preferences.configuration_version + 1 }
      return { ...preferences }
    }
    throw new Error(`Unexpected API request: ${path}`)
  })
}

async function mounted(): Promise<VueWrapper> {
  const wrapper = mount(AccountNutritionExpertView)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('Nutrition Expert account settings', () => {
  beforeEach(() => {
    setLocale('de')
    setActivePinia(createPinia())
    useAuthStore().user = {
      id: 'owner-a', username: 'synthetic-owner', language: 'de', timezone: 'UTC', week_starts_on: 0,
      raw_payload_retention_days: 30, is_admin: false, is_active: true, deactivated_at: null,
    }
    preferences = { ...initialPreferences, data_domains: [...initialPreferences.data_domains] }
    consents = []
    apiMock.mockReset()
    installDefaultApi()
  })

  afterEach(() => {
    wrappers.forEach(wrapper => wrapper.unmount())
    wrappers = []
    setAuthenticatedIdentity(null)
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('serializes rapid toggle changes and saves the latest complete preference set automatically', async () => {
    let releaseFirst!: (value: typeof preferences) => void
    const defaultApi = apiMock.getMockImplementation()!
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/expert' && options?.method === 'PUT' && apiMock.mock.calls.filter(([callPath, callOptions]) => callPath === path && callOptions?.method === 'PUT').length === 1) {
        return new Promise(resolve => { releaseFirst = resolve })
      }
      return defaultApi(path, options)
    })
    const wrapper = await mounted()
    const weight = wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]')
    const targets = wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="targets"]')

    await weight.setValue(false)
    await vi.waitFor(() => expect(apiMock.mock.calls.filter(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')).toHaveLength(1))
    await targets.setValue(false)
    releaseFirst({ ...initialPreferences, data_domains: ['nutrition', 'targets', 'activity', 'profile', 'health_context'] })
    await vi.waitFor(() => expect(apiMock.mock.calls.filter(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')).toHaveLength(2))
    await flushPromises()

    const writes = apiMock.mock.calls.filter(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')
    expect(JSON.parse(String(writes[0]![1]?.body))).toMatchObject({ include_meal_names: true })
    expect(JSON.parse(String(writes[1]![1]?.body)).data_domains).not.toContain('weight')
    expect(JSON.parse(String(writes[1]![1]?.body)).data_domains).not.toContain('targets')
    expect(wrapper.text()).toContain('Einstellungen gespeichert')
  })

  it('restores persisted settings after a failed save and exposes a retry', async () => {
    const defaultApi = apiMock.getMockImplementation()!
    let failNext = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/expert' && options?.method === 'PUT' && failNext) {
        failNext = false
        throw new Error('synthetic save failure')
      }
      return defaultApi(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').setValue(false)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Erneut versuchen'))
    expect(wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').element.checked).toBe(true)
    await wrapper.get('button').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Einstellungen gespeichert'))
    expect(preferences.data_domains).not.toContain('weight')
  })

  it('requires one domain and disables meal names when nutrition is switched off', async () => {
    preferences = { ...preferences, data_domains: ['nutrition'], include_meal_names: true }
    const minimumWrapper = await mounted()
    const onlyDomain = minimumWrapper.get<HTMLInputElement>('input[name="expert-domain"][value="nutrition"]')
    expect(onlyDomain.element.disabled).toBe(true)
    expect(minimumWrapper.get<HTMLInputElement>('input[name="expert-meal-names"]').element.checked).toBe(true)
    minimumWrapper.unmount()
    wrappers = []

    preferences = { ...initialPreferences, data_domains: [...initialPreferences.data_domains] }
    const wrapper = await mounted()
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="nutrition"]').setValue(false)
    await vi.waitFor(() => expect(wrapper.get<HTMLInputElement>('input[name="expert-meal-names"]').element.disabled).toBe(true))
    expect(wrapper.get<HTMLInputElement>('input[name="expert-meal-names"]').element.checked).toBe(false)
    const save = apiMock.mock.calls.find(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')
    expect(JSON.parse(String(save?.[1]?.body)).include_meal_names).toBe(false)
  })

  it('shows a transient saved toast and stops a pending save after unmount', async () => {
    vi.useFakeTimers()
    let releaseSave!: (value: typeof preferences) => void
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/expert/setup') return setupResponse()
      if (path.startsWith('/settings/ai/consents?')) return { items: [], next_after: null }
      if (path === '/settings/expert' && options?.method === 'PUT') {
        return new Promise(resolve => { releaseSave = resolve })
      }
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = await mounted()
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').setValue(false)
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/expert', expect.objectContaining({ method: 'PUT' }))
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(wrapper.text()).toContain('Einstellungen werden gespeichert')
    wrapper.unmount()
    wrappers = []
    releaseSave({ ...preferences, data_domains: ['nutrition', 'targets', 'activity', 'profile', 'health_context'] })
    await vi.runOnlyPendingTimersAsync()
    await flushPromises()
    expect(apiMock.mock.calls.filter(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')).toHaveLength(1)
    vi.useRealTimers()
  })

  it('does not save a pending preference request after the authenticated identity changes', async () => {
    setAuthenticatedIdentity('owner-a')
    let releaseSave!: (value: typeof preferences) => void
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/expert/setup') return setupResponse()
      if (path.startsWith('/settings/ai/consents?')) return { items: [], next_after: null }
      if (path === '/settings/expert' && options?.method === 'PUT') return new Promise(resolve => { releaseSave = resolve })
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = await mounted()
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').setValue(false)
    await vi.waitFor(() => expect(apiMock).toHaveBeenCalledWith('/settings/expert', expect.objectContaining({ method: 'PUT' })))
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="activity"]').setValue(false)
    setAuthenticatedIdentity('owner-b')
    releaseSave({ ...preferences, data_domains: ['nutrition', 'targets', 'activity', 'profile', 'health_context'] })
    await flushPromises()
    expect(apiMock.mock.calls.filter(([path, options]) => path === '/settings/expert' && options?.method === 'PUT')).toHaveLength(1)
    expect(wrapper.find('[role="alert"]').text()).toContain('Die Sitzung wurde geändert')
  })

  it('immediately clears displayed settings when the authenticated account changes', async () => {
    const wrapper = await mounted()
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(6)
    expect(wrapper.find('.expert-consent-card').exists()).toBe(true)
    const requestsBeforeSwitch = apiMock.mock.calls.length
    useAuthStore().user = { ...useAuthStore().user!, id: 'owner-b' }
    await flushPromises()
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(0)
    expect(wrapper.find('.expert-consent-card').exists()).toBe(false)
    expect(wrapper.get('[role="alert"]').text()).toContain('Die Sitzung wurde geändert')
    expect(apiMock.mock.calls).toHaveLength(requestsBeforeSwitch)
  })

  it('hides the saved confirmation after its short display interval', async () => {
    vi.useFakeTimers()
    const wrapper = await mounted()
    await wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').setValue(false)
    await flushPromises()
    expect(wrapper.text()).toContain('Einstellungen gespeichert')
    await vi.advanceTimersByTimeAsync(4_000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('Einstellungen gespeichert')
  })

})
