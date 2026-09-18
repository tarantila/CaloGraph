import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {},
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import AccountIntegrationsView from '../src/views/AccountIntegrationsView.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

const yazioStatus = {
  available: true,
  configured: true,
  sync_enabled: true,
  sync_interval_minutes: 360,
  sync_days: 7,
  sync_interval_override_minutes: null,
  sync_days_override: null,
  historical_sync: null,
  last_attempt_at: '2026-09-18T08:00:00Z',
  last_success_at: '2026-09-18T08:01:00Z',
  next_sync_at: '2026-09-18T14:01:00Z',
  last_error: 'Vorheriger Abruf fehlgeschlagen.',
}

const googleStatus = {
  available: true,
  configured: true,
  state: 'active',
  granted_scopes: ['https://www.googleapis.com/auth/fitness.activity.read'],
  refresh_token_expires_at: null,
  last_attempt_at: '2026-09-18T08:00:00Z',
  last_success_at: '2026-09-18T08:01:00Z',
  last_error: null,
}

let yazioStatusCalls = 0

function configureApi(overrides: Record<string, unknown> = {}): void {
  yazioStatusCalls = 0
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/yazio/status') {
      yazioStatusCalls += 1
      if (overrides.yazioRefreshError && yazioStatusCalls > 1) return Promise.reject(new Error('status refresh failed'))
      const result = overrides.yazioStatus ?? yazioStatus
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
    }
    if (path === '/google-health/status') {
      const result = overrides.googleStatus ?? googleStatus
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
    }
    if (path === '/yazio/sync' && options?.method === 'POST') {
      return Promise.resolve({ inserted: 1, updated: 2, skipped: 3, failed: 0, unknown_types: [] })
    }
    if (path === '/google-health/oauth/start' && options?.method === 'POST') {
      return Promise.resolve({ authorization_url: 'https://accounts.google.example/authorize?state=test' })
    }
    return Promise.resolve({})
  })
}

describe('AccountIntegrationsView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setActivePinia(createPinia())
    setLocale(DEFAULT_LOCALE)
    configureApi()
  })
  afterEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('separates YAZIO credentials, scheduler status, manual sync, and sync history', async () => {
    const wrapper = mount(AccountIntegrationsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()

    expect(wrapper.get('.yazio-credentials-panel').text()).toContain('YAZIO-Zugangsdaten')
    expect(wrapper.get('.yazio-status-panel').text()).toContain('Letzte erfolgreiche Synchronisierung')
    expect(wrapper.get('.yazio-status-panel').text()).toContain('18.09.2026, 10:01')
    expect(wrapper.text()).not.toContain('Withings')
    expect(wrapper.text()).not.toContain('Health Auto Export')
  })

  it('starts a manual YAZIO sync even when the scheduler is paused', async () => {
    configureApi({ yazioStatus: { ...yazioStatus, sync_enabled: false } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const button = wrapper.get('.yazio-manual-sync-panel button')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/yazio/sync', { method: 'POST' })
    expect(wrapper.text()).toContain('1 neu · 2 aktualisiert · 3 unverändert')
  })

  it('keeps the successful sync result when the status refresh fails', async () => {
    configureApi({ yazioRefreshError: true })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.yazio-manual-sync-panel button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('1 neu · 2 aktualisiert · 3 unverändert')
    expect(wrapper.text()).toContain('Status konnte nicht aktualisiert werden')
    expect(wrapper.text()).not.toContain('manuelle YAZIO-Synchronisierung ist fehlgeschlagen')
  })

  it('offers only the implemented Google OAuth action and no action when unavailable or unconfigured', async () => {
    configureApi({ googleStatus: { ...googleStatus, state: 'not_connected' } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const connect = wrapper.get('.google-health-card button')
    expect(connect.text()).toContain('Google Health verbinden')
    await connect.trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/oauth/start', { method: 'POST' })
    wrapper.unmount()

    configureApi({ googleStatus: { ...googleStatus, configured: false, state: 'not_connected' } })
    const unconfiguredWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(unconfiguredWrapper.get('.google-health-card').find('button').exists()).toBe(false)
    unconfiguredWrapper.unmount()

    configureApi({ googleStatus: { ...googleStatus, available: false, configured: false, state: 'disabled' } })
    const unavailableWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(unavailableWrapper.get('.google-health-card').text()).toContain('Serverseitig deaktiviert')
    expect(unavailableWrapper.get('.google-health-card').find('button').exists()).toBe(false)
    unavailableWrapper.unmount()
  })

  it('keeps the YAZIO card visible when Google status loading fails', async () => {
    configureApi({ googleStatus: new Error('temporary Google failure') })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.get('.yazio-credentials-panel')).toBeTruthy()
    expect(wrapper.get('.google-health-card').text()).toContain('Google-Health-Status konnte nicht geladen werden')
  })

  it('shows the Google callback result after returning to the integrations page', async () => {
    window.history.replaceState({}, '', '/konto/integrationen?google_health=connected')
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.get('.google-health-card').text()).toContain('Google Health wurde verbunden')
  })

  it('describes Apple Health as an import integration without pretending to offer OAuth', async () => {
    const wrapper = mount(AccountIntegrationsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()

    const appleCard = wrapper.get('.apple-health-card')
    expect(appleCard.text()).toContain('Apple-Health-Export')
    expect(appleCard.find('a').exists()).toBe(true)
    expect(appleCard.find('button').exists()).toBe(false)
  })
})
