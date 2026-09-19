import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {},
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
  setCsrfToken: vi.fn(),
}))

import AccountDataSourcesView from '../src/views/AccountDataSourcesView.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import type { User } from '../src/types'
import { useAuthStore } from '../src/stores/auth'

type DataArea = 'nutrition' | 'weight' | 'activity_energy'
type ProviderKey = 'apple_health' | 'google_health' | 'health_auto_export' | 'yazio'

const availabilityByArea = {
  nutrition: [
    { provider_key: 'google_health', available: true, status: 'available' },
    { provider_key: 'yazio', available: true, status: 'available' },
    { provider_key: 'apple_health', available: false, status: 'no_data' },
  ],
  weight: [
    { provider_key: 'yazio', available: true, status: 'available' },
    { provider_key: 'apple_health', available: true, status: 'available' },
    { provider_key: 'health_auto_export', available: false, status: 'no_data' },
  ],
  activity_energy: [
    { provider_key: 'yazio', available: true, status: 'available' },
    { provider_key: 'apple_health', available: false, status: 'no_data' },
    { provider_key: 'health_auto_export', available: true, status: 'available' },
  ],
} as const

const initialPreferences: Record<DataArea, ProviderKey[]> = {
  nutrition: ['apple_health', 'yazio'],
  weight: ['health_auto_export'],
  activity_energy: [],
}

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason?: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

let serverPreferences: Record<DataArea, ProviderKey[]>
let putHandler: ((area: DataArea, providerKeys: ProviderKey[]) => Promise<unknown>) | undefined
let preferenceGetCount = 0

function configureApi(): void {
  serverPreferences = structuredClone(initialPreferences)
  putHandler = undefined
  preferenceGetCount = 0
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/settings/provider-preferences' && !options?.method) {
      preferenceGetCount += 1
      return Promise.resolve({
        preferences: (Object.entries(serverPreferences) as Array<[DataArea, ProviderKey[]]>).flatMap(([dataArea, providerKeys]) => (
          providerKeys.map((provider_key) => ({ data_area: dataArea, provider_key }))
        )),
      })
    }
    if (path.startsWith('/settings/provider-preferences/') && options?.method === 'PUT') {
      const area = path.split('/').at(-1) as DataArea
      const providerKeys = JSON.parse(String(options.body)).provider_keys as ProviderKey[]
      const result = putHandler?.(area, providerKeys) ?? Promise.resolve({})
      return result.then((response) => {
        serverPreferences[area] = [...providerKeys]
        return response
      })
    }
    if (path.startsWith('/settings/provider-preferences/') && options?.method === 'DELETE') {
      const area = path.split('/').at(-1) as DataArea
      serverPreferences[area] = []
      return Promise.resolve(undefined)
    }
    const area = path.split('/').at(-1) as keyof typeof availabilityByArea
    return Promise.resolve({ data_area: area, providers: availabilityByArea[area] })
  })
}

let testPinia = createPinia()

function mountView() {
  return mount(AccountDataSourcesView, { global: { plugins: [testPinia] } })
}

describe('AccountDataSourcesView', () => {
  beforeEach(() => {
    testPinia = createPinia()
    setActivePinia(testPinia)
    apiMock.mockReset()
    setLocale(DEFAULT_LOCALE)
    configureApi()
  })

  it('renders every supported provider in effective order, including unavailable rows, with only reorder controls', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.findAll('[data-area="nutrition"] .provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'apple_health',
      'yazio',
      'google_health',
    ])
    expect(wrapper.findAll('[data-area="weight"] .provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'health_auto_export',
      'yazio',
      'apple_health',
    ])
    expect(wrapper.findAll('[data-area="activity_energy"] .provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'yazio',
      'apple_health',
      'health_auto_export',
    ])

    const unavailableRow = wrapper.get('[data-area="nutrition"] [data-provider-key="apple_health"]')
    expect(unavailableRow.text()).toContain('Apple Health')
    expect(unavailableRow.text()).toContain('noch keine Daten')
    expect(wrapper.findAll('select')).toHaveLength(0)
    expect(wrapper.findAll('[aria-label*="entfernen"]')).toHaveLength(0)
    expect(wrapper.findAll('.provider-priority-save')).toHaveLength(0)

    const nutritionRows = wrapper.findAll('[data-area="nutrition"] .provider-priority-row')
    expect(nutritionRows[0].find('button[aria-label*="nach oben"]').attributes('disabled')).toBeDefined()
    expect(nutritionRows.at(-1)!.find('button[aria-label*="nach unten"]').attributes('disabled')).toBeDefined()
    expect(unavailableRow.find('button[aria-label*="nach unten"]').attributes('disabled')).toBeUndefined()
  })

  it('auto-saves a complete reordered area and exposes local saving and saved states', async () => {
    const pending = deferred<unknown>()
    putHandler = () => pending.promise
    const wrapper = mountView()
    await flushPromises()

    const nutrition = wrapper.get('[data-area="nutrition"]')
    await nutrition.get('[data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    expect(apiMock).toHaveBeenLastCalledWith('/settings/provider-preferences/nutrition', {
      method: 'PUT',
      body: JSON.stringify({ provider_keys: ['yazio', 'apple_health', 'google_health'] }),
    })
    expect(nutrition.get('.provider-priority-save-status').text()).toContain('Speichert …')
    expect(wrapper.find('.setup-notice').exists()).toBe(false)

    pending.resolve({})
    await flushPromises()
    expect(nutrition.get('.provider-priority-save-status').text()).toContain('✓ Gespeichert')
    expect(nutrition.get('.provider-priority-save-status').classes()).toContain('saved')
  })

  it('shows a local error when an automatic area save fails', async () => {
    putHandler = () => Promise.reject(new Error('save failed'))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-area="nutrition"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    await flushPromises()

    const status = wrapper.get('[data-area="nutrition"] .provider-priority-save-status')
    expect(status.text()).toContain('Speichern fehlgeschlagen')
    expect(status.classes()).toContain('error')
  })

  it('coalesces rapid reorder requests and sends the latest desired order after the active request', async () => {
    const first = deferred<unknown>()
    const second = deferred<unknown>()
    const putBodies: ProviderKey[][] = []
    putHandler = (_area, providerKeys) => {
      putBodies.push(providerKeys)
      return putBodies.length === 1 ? first.promise : second.promise
    }
    const wrapper = mountView()
    await flushPromises()
    const nutrition = wrapper.get('[data-area="nutrition"]')

    await nutrition.get('[data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    await nutrition.get('[data-provider-key="apple_health"] button[aria-label*="nach unten"]').trigger('click')
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])

    first.resolve({})
    await flushPromises()
    expect(putBodies).toEqual([
      ['yazio', 'apple_health', 'google_health'],
      ['yazio', 'google_health', 'apple_health'],
    ])
    second.resolve({})
    await flushPromises()
    expect(nutrition.findAll('.provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'yazio',
      'google_health',
      'apple_health',
    ])
  })

  it('saves different areas independently', async () => {
    const pending: Record<DataArea, Deferred<unknown>> = {
      nutrition: deferred<unknown>(),
      weight: deferred<unknown>(),
      activity_energy: deferred<unknown>(),
    }
    putHandler = (area) => pending[area].promise
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-area="nutrition"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    await wrapper.get('[data-area="weight"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    expect(apiMock).toHaveBeenCalledWith('/settings/provider-preferences/nutrition', expect.objectContaining({ method: 'PUT' }))
    expect(apiMock).toHaveBeenCalledWith('/settings/provider-preferences/weight', expect.objectContaining({ method: 'PUT' }))
    expect(wrapper.get('[data-area="nutrition"] .provider-priority-save-status').text()).toContain('Speichert …')
    expect(wrapper.get('[data-area="weight"] .provider-priority-save-status').text()).toContain('Speichert …')

    pending.nutrition.resolve({})
    await flushPromises()
    expect(wrapper.get('[data-area="nutrition"] .provider-priority-save-status').text()).toContain('✓ Gespeichert')
    expect(wrapper.get('[data-area="weight"] .provider-priority-save-status').text()).toContain('Speichert …')
    pending.weight.resolve({})
    await flushPromises()
  })

  it('waits for an in-flight save before a remounted view reloads preferences', async () => {
    const pending = deferred<unknown>()
    putHandler = () => pending.promise
    const first = mountView()
    await flushPromises()
    await first.get('[data-area="nutrition"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    expect(apiMock).toHaveBeenCalledWith('/settings/provider-preferences/nutrition', expect.objectContaining({ method: 'PUT' }))
    first.unmount()

    const remounted = mountView()
    await flushPromises()
    expect(preferenceGetCount).toBe(1)

    pending.resolve({})
    await flushPromises()
    expect(preferenceGetCount).toBe(2)
    expect(remounted.findAll('[data-area="nutrition"] .provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'yazio',
      'apple_health',
      'google_health',
    ])
  })

  it('drops queued reorder requests when the auth session changes before remount', async () => {
    sessionStorage.setItem('calograph_csrf', 'synthetic-old-session')
    const active = deferred<unknown>()
    const putBodies: ProviderKey[][] = []
    putHandler = (_area, providerKeys) => {
      putBodies.push(providerKeys)
      return active.promise
    }
    const first = mountView()
    await flushPromises()
    await first.get('[data-area="nutrition"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    await first.get('[data-area="nutrition"] [data-provider-key="apple_health"] button[aria-label*="nach unten"]').trigger('click')
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])

    sessionStorage.setItem('calograph_csrf', 'synthetic-new-session')
    first.unmount()
    const remounted = mountView()
    await flushPromises()
    expect(preferenceGetCount).toBe(1)
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])

    active.resolve({})
    await flushPromises()
    expect(preferenceGetCount).toBe(2)
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])
    expect(remounted.findAll('[data-area="nutrition"] .provider-priority-row').map((row) => row.attributes('data-provider-key'))).toEqual([
      'yazio',
      'apple_health',
      'google_health',
    ])
  })
  it('drops queued reorder requests when the auth session changes while mounted', async () => {
    sessionStorage.setItem('calograph_csrf', 'synthetic-old-session')
    const auth = useAuthStore()
    auth.user = { id: 'old-user' } as unknown as User
    const active = deferred<unknown>()
    const putBodies: ProviderKey[][] = []
    putHandler = (_area, providerKeys) => {
      putBodies.push(providerKeys)
      return active.promise
    }
    const wrapper = mountView()
    await flushPromises()
    await wrapper.get('[data-area="nutrition"] [data-provider-key="yazio"] button[aria-label*="nach oben"]').trigger('click')
    await wrapper.get('[data-area="nutrition"] [data-provider-key="apple_health"] button[aria-label*="nach unten"]').trigger('click')
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])

    auth.clearSession()
    await flushPromises()
    active.resolve({})
    await flushPromises()
    expect(putBodies).toEqual([['yazio', 'apple_health', 'google_health']])
  })


  it('uses the concise priority hint in both supported locales', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('1 = höchste Priorität')

    setLocale('en')
    await flushPromises()
    expect(wrapper.text()).toContain('1 = highest priority')
  })
})
