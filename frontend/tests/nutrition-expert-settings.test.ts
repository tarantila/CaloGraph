import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import type { ProviderConnectionView } from '../src/generated/ai-contracts'
import AccountNutritionExpertView from '../src/views/AccountNutritionExpertView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { setAuthenticatedIdentity } from '../src/auth-session'

const initialPreferences = {
  data_domains: ['nutrition', 'targets', 'weight', 'activity', 'profile', 'health_context'],
  include_meal_names: true,
  configuration_version: 1,
}
const connection: ProviderConnectionView = {
  id: 'connection-1', label: 'Synthetic provider', provider_kind: 'local', model_id: 'synthetic-model',
  base_url: 'http://synthetic.invalid/v1', enabled: true, timeout_seconds: 30,
  configuration_version: 2, has_credential: true,
  created_at: '2026-11-25T00:00:00Z', updated_at: '2026-11-25T00:00:00Z',
}
const otherConnection: ProviderConnectionView = {
  ...connection,
  id: 'connection-other', label: 'Other disabled provider', provider_kind: 'gateway', enabled: false,
  model_id: 'other-model', configuration_version: 3,
}
const chatgptConnection: ProviderConnectionView = {
  ...connection,
  id: 'connection-chatgpt', label: 'ChatGPT plan', provider_kind: 'chatgpt_plan', model_id: 'GPT-6.1-Sol', base_url: null,
}
const consent = (id: string, revoked_at: string | null = null, owner = connection) => ({
  id, connection_id: owner.id, configuration_version: owner.configuration_version, provider_kind: owner.provider_kind, model_id: owner.model_id,
  base_url: owner.base_url, purpose: 'nutrition_chat', data_domains: ['nutrition'], include_meal_names: false,
  history: { mode: 'all_available', start: null, end: null }, policy_version: 'test', state: revoked_at ? 'revoked' : 'active',
  created_at: '2026-11-25T00:00:00Z', revoked_at,
})

let wrappers: VueWrapper[] = []
let preferences = { ...initialPreferences, data_domains: [...initialPreferences.data_domains] }
let consents = [consent('consent-current'), consent('consent-older-scope')]
let connections: ProviderConnectionView[] = [connection]
let activeConnection: ProviderConnectionView | null = connection
let splitConnectionPages = false

function setupResponse() {
  return { connection: activeConnection, preferences: { ...preferences }, consent: null, generation_available: true }
}

function responsePage(connectionId: string, after: string | null) {
  const items = consents.filter(item => item.connection_id === connectionId)
  if (!after) return { items: items.slice(0, 1), next_after: items.length > 1 ? 'page-2' : null }
  return { items: items.slice(1), next_after: null }
}

function installDefaultApi(): void {
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/expert/setup') return setupResponse()
    if (path.startsWith('/settings/ai/connections?')) {
      const after = new URLSearchParams(path.split('?')[1]).get('after')
      if (splitConnectionPages && !after) return { items: connections.slice(0, 1), next_after: connections[0]?.id ?? null }
      if (splitConnectionPages && after) return { items: connections.slice(1), next_after: null }
      return { items: connections, next_after: null }
    }
    if (path.startsWith('/settings/ai/consents?')) {
      const query = new URLSearchParams(path.split('?')[1])
      return responsePage(query.get('connection_id') ?? '', query.get('after'))
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
    consents = [consent('consent-current'), consent('consent-older-scope')]
    connections = [connection]
    activeConnection = connection
    splitConnectionPages = false
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
      if (path.startsWith('/settings/ai/connections?')) return { items: connections, next_after: null }
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
      if (path.startsWith('/settings/ai/connections?')) return { items: connections, next_after: null }
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
    expect(wrapper.text()).toContain('synthetic-model')
    const requestsBeforeSwitch = apiMock.mock.calls.length
    useAuthStore().user = { ...useAuthStore().user!, id: 'owner-b' }
    await flushPromises()
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('synthetic-model')
    expect(wrapper.get('[role="alert"]').text()).toContain('Die Sitzung wurde geändert')
    expect(apiMock.mock.calls).toHaveLength(requestsBeforeSwitch)
  })

  it('stops further revocations after an account change during a pending delete', async () => {
    const defaultApi = apiMock.getMockImplementation()!
    let finishDelete!: () => void
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/ai/consents/consent-current' && options?.method === 'DELETE') {
        return new Promise<void>(resolve => { finishDelete = () => resolve() })
      }
      return defaultApi(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(apiMock).toHaveBeenCalledWith('/settings/ai/consents/consent-current', expect.objectContaining({ method: 'DELETE' })))
    useAuthStore().user = { ...useAuthStore().user!, id: 'owner-b' }
    finishDelete()
    await flushPromises()
    const deletions = apiMock.mock.calls.filter(([, options]) => options?.method === 'DELETE')
    expect(deletions.map(([path]) => path)).toEqual(['/settings/ai/consents/consent-current'])
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('synthetic-model')
    expect(wrapper.get('[role="alert"]').text()).toContain('Die Sitzung wurde geändert')
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

  it('revokes all consent scopes across pages and confirms only after a full re-read', async () => {
    const wrapper = await mounted()
    expect(wrapper.get('.consent-row strong').text()).toBe('Lokaler KI-Server')
    expect(wrapper.get('.consent-destination').text()).toContain('synthetic-model')
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Freigaben widerrufen.'))

    const deletions = apiMock.mock.calls.filter(([path, options]) => path.startsWith('/settings/ai/consents/') && options?.method === 'DELETE')
    expect(deletions.map(([path]) => path).sort()).toEqual([
      '/settings/ai/consents/consent-current',
      '/settings/ai/consents/consent-older-scope',
    ])
    expect(consents.every(item => item.revoked_at !== null)).toBe(true)
    expect(wrapper.find('button.compact-action').exists()).toBe(false)
  })

  it('does not confirm partial revocation and permits retry while a consent remains', async () => {
    const defaultApi = apiMock.getMockImplementation()!
    let failOnce = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/ai/consents/consent-older-scope' && options?.method === 'DELETE' && failOnce) {
        failOnce = false
        throw new Error('synthetic deletion failure')
      }
      return defaultApi(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Freigaben konnten nicht widerrufen werden'))
    expect(wrapper.text()).not.toContain('Freigaben widerrufen. Neue Anfragen benötigen deine Zustimmung.')
    expect(wrapper.find('button.compact-action').exists()).toBe(true)

    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Freigaben widerrufen. Neue Anfragen benötigen deine Zustimmung.'))
    expect(consents.every(item => item.revoked_at !== null)).toBe(true)
  })

  it.each([
    ['the active provider has no grants', true],
    ['there is no active provider', false],
  ] as const)('shows and revokes stale grants on a disabled provider when %s', async (_scenario, activeConnectionEnabled) => {
    connections = [{ ...chatgptConnection, enabled: activeConnectionEnabled }, otherConnection]
    activeConnection = activeConnectionEnabled ? chatgptConnection : null
    splitConnectionPages = true
    consents = [consent('other-consent-a', null, otherConnection), consent('other-consent-b', null, otherConnection)]
    const wrapper = await mounted()

    expect(wrapper.find('.consent-row[data-connection-id="connection-chatgpt"]').exists()).toBe(false)
    const otherRow = wrapper.get('.consent-row[data-connection-id="connection-other"]')
    expect(otherRow.text()).toContain('OpenAI-kompatibler Anbieter')
    expect(otherRow.text()).toContain('other-model')
    expect(otherRow.text()).toContain('Eine frühere Freigabe ist gespeichert')
    expect(apiMock.mock.calls.filter(([path]) => String(path).startsWith('/settings/ai/connections?'))).toHaveLength(2)

    await otherRow.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.consent-row[data-connection-id="connection-other"]').exists()).toBe(false))
    const deletions = apiMock.mock.calls.filter(([path, options]) => String(path).startsWith('/settings/ai/consents/') && options?.method === 'DELETE')
    expect(deletions.map(([path]) => path).sort()).toEqual([
      '/settings/ai/consents/other-consent-a',
      '/settings/ai/consents/other-consent-b',
    ])
    expect(consents.every(item => item.revoked_at !== null)).toBe(true)
    expect(apiMock.mock.calls.some(([path, options]) => String(path).startsWith('/settings/ai/consents/') && options?.method === 'PUT')).toBe(false)
  })

  it('preserves another provider permission when revoking one provider', async () => {
    connections = [connection, otherConnection]
    consents = [consent('primary-consent', null, connection), consent('other-consent', null, otherConnection)]
    const wrapper = await mounted()
    await wrapper.get('.consent-row[data-connection-id="connection-other"] button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.consent-row[data-connection-id="connection-other"]').exists()).toBe(false))
    expect(wrapper.get('.consent-row[data-connection-id="connection-1"] button.compact-action').text()).toBe('Freigaben widerrufen')
    expect(consents.find(item => item.id === 'primary-consent')?.revoked_at).toBeNull()
    expect(apiMock.mock.calls.filter(([, options]) => options?.method === 'DELETE').map(([path]) => path)).toEqual(['/settings/ai/consents/other-consent'])
  })

  it('shows an empty permission state without disabling preferences when no providers exist', async () => {
    connections = []
    activeConnection = null
    const wrapper = await mounted()
    expect(wrapper.get('.consent-settings').text()).toContain('Keine aktiven KI-Freigaben')
    expect(wrapper.findAll('.consent-row')).toHaveLength(0)
    expect(wrapper.get<HTMLInputElement>('input[name="expert-domain"][value="weight"]').element.disabled).toBe(false)
  })

  it('keeps data settings usable and retries when the owned provider list cannot be read', async () => {
    const defaultApi = apiMock.getMockImplementation()!
    let failOnce = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/connections?') && failOnce) {
        failOnce = false
        throw new Error('synthetic connection-list failure')
      }
      return defaultApi(path, options)
    })
    const wrapper = await mounted()
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(6)
    expect(wrapper.text()).toContain('Freigaben konnten nicht geladen werden')
    await wrapper.get('.expert-permission-settings button.text-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.consent-settings').exists()).toBe(true))
    expect(wrapper.findAll('input[name="expert-domain"]')).toHaveLength(6)
  })
})
