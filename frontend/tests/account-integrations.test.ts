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
  client_id_configured: true,
  client_secret_configured: true,
  redirect_uri: 'https://app.example.test/google-health/oauth/callback',
  state: 'active',
  sync_state: 'idle',
  retry_attempt: 0,
  retry_max_attempts: 3,
  next_retry_at: null,
  granted_scopes: ['https://www.googleapis.com/auth/fitness.activity.read'],
  refresh_token_expires_at: null,
  last_attempt_at: '2026-09-18T08:00:00Z',
  last_success_at: '2026-09-18T08:01:00Z',
  last_error: null,
  last_error_category: null,
}

const googleSyncResult = {
  status: 'partial_failure',
  nutrition: {
    status: 'success',
    fetched_count: 4,
    persisted_count: 2,
    requested_start: '2026-09-18',
    requested_end: '2026-09-18',
    covered_start: '2026-09-18',
    covered_end: '2026-09-18',
    error_code: null,
  },
  activity_energy: {
    status: 'truncated',
    fetched_count: 3,
    persisted_count: 3,
    requested_start: '2026-09-18',
    requested_end: '2026-09-18',
    covered_start: '2026-09-18',
    covered_end: '2026-09-18',
    error_code: null,
  },
  weight: {
    status: 'no_data',
    fetched_count: 0,
    persisted_count: 0,
    requested_start: '2026-09-18',
    requested_end: '2026-09-18',
    covered_start: null,
    covered_end: null,
    error_code: null,
  },
}

let yazioStatusCalls = 0
let googleStatusCalls = 0

function configureApi(overrides: Record<string, unknown> = {}): void {
  yazioStatusCalls = 0
  googleStatusCalls = 0
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/yazio/status') {
      yazioStatusCalls += 1
      if (overrides.yazioRefreshError && yazioStatusCalls > 1) return Promise.reject(new Error('status refresh failed'))
      const result = overrides.yazioStatus ?? yazioStatus
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
    }
    if (path === '/google-health/status') {
      googleStatusCalls += 1
      const result = googleStatusCalls > 1
        ? overrides.googleStatusAfterSync ?? overrides.googleStatus ?? googleStatus
        : overrides.googleStatus ?? googleStatus
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
    }
    if (path === '/yazio/sync' && options?.method === 'POST') {
      return Promise.resolve({ inserted: 1, updated: 2, skipped: 3, failed: 0, unknown_types: [] })
    }
    if (path === '/google-health/credentials' && options?.method === 'PUT') {
      return Promise.resolve(overrides.googleCredentialsStatus ?? googleStatus)
    }
    if (path === '/google-health/credentials' && options?.method === 'DELETE') {
      return Promise.resolve(overrides.googleCredentialsDeletedStatus ?? {
        ...googleStatus,
        configured: false,
        client_id_configured: false,
        client_secret_configured: false,
        state: 'not_configured',
      })
    }
    if (path === '/google-health/connection' && options?.method === 'DELETE') {
      return Promise.resolve(overrides.googleDisconnectedStatus ?? { ...googleStatus, state: 'not_connected' })
    }
    if (path === '/google-health/connection/test' && options?.method === 'POST') {
      if (overrides.googleConnectionTestError) return Promise.reject(new Error('provider detail'))
      return Promise.resolve(overrides.googleConnectionTestResult ?? { status: 'connected', error_category: null })
    }
    if (path === '/google-health/sync' && options?.method === 'POST') {
      if (overrides.googleSyncError) return Promise.reject(new Error('raw provider detail'))
      return Promise.resolve(overrides.googleSyncResult ?? googleSyncResult)
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
    vi.stubEnv('TZ', 'Europe/Berlin')
    configureApi()
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllEnvs()
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

    const connect = wrapper.get('.google-health-connect')
    expect(connect.text()).toContain('Google Health verbinden')
    await connect.trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/oauth/start', { method: 'POST' })
    wrapper.unmount()

    configureApi({ googleStatus: { ...googleStatus, configured: false, client_id_configured: false, client_secret_configured: false, state: 'not_configured' } })
    const unconfiguredWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(unconfiguredWrapper.get('.google-health-card').find('.google-health-connect').exists()).toBe(false)
    unconfiguredWrapper.unmount()

    configureApi({ googleStatus: { ...googleStatus, available: false, configured: false, client_id_configured: false, client_secret_configured: false, state: 'disabled' } })
    const unavailableWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(unavailableWrapper.get('.google-health-card').text()).toContain('Serverseitig deaktiviert')
    expect(unavailableWrapper.get('.google-health-card').find('.google-health-connect').exists()).toBe(false)
    expect(unavailableWrapper.get('input[name="google-client-id"]').attributes('disabled')).toBeDefined()
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

  it('uses Google Health terminology and describes all synchronized domains', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const googleCardText = wrapper.get('.google-health-card').text()
    expect(googleCardText).toContain('Ernährungsdaten')
    expect(googleCardText).toContain('Aktivitätsenergie')
    expect(googleCardText).toContain('Gewicht')
    expect(googleCardText).not.toContain('Health Connect')
    expect(googleCardText).not.toContain('Android Bridge')
    expect(googleCardText).not.toContain('Google Fit')

    const icons = wrapper.findAll('.integration-card-icon')
    expect(icons).toHaveLength(3)
    expect(icons.every((icon) => icon.attributes('aria-hidden') === 'true')).toBe(true)
  })

  it('renders safe per-domain Google synchronization counts and partial failures', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const button = wrapper.get('.google-health-sync-button')
    expect(button.attributes('disabled')).toBeUndefined()
    await button.trigger('click')
    await flushPromises()

    const result = wrapper.get('.google-health-sync-result')
    expect(result.text()).toContain('Teilweise fehlgeschlagen')
    expect(result.text()).toContain('Erfolgreich')
    expect(result.text()).toContain('Begrenzt')
    expect(result.text()).toContain('Keine Daten')
    expect(result.text()).toContain('4')
    expect(result.text()).toContain('3')
    expect(result.text()).not.toContain('100')
    expect(result.text()).not.toContain('raw provider detail')
  })
  it('keeps a reauthorization sync result visible after the status refresh changes state', async () => {
    const reauthSyncResult = {
      status: 'reauth_required',
      nutrition: { ...googleSyncResult.nutrition, status: 'reauth_required', fetched_count: 5, persisted_count: 5, error_code: 'scope_missing' },
      activity_energy: { ...googleSyncResult.activity_energy, status: 'reauth_required', fetched_count: 1, persisted_count: 0, error_code: 'scope_missing' },
      weight: { ...googleSyncResult.weight, status: 'reauth_required', error_code: 'scope_missing' },
    } as const
    configureApi({
      googleSyncResult: reauthSyncResult,
      googleStatusAfterSync: { ...googleStatus, state: 'reauth_required' },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.google-health-sync-button').trigger('click')
    await flushPromises()

    const result = wrapper.get('.google-health-sync-result')
    expect(result.text()).toContain('Erneute Autorisierung erforderlich')
    expect(result.text()).toContain('Ernährung')
    expect(result.text()).toContain('5')
    expect(result.text()).toContain('Erforderliche Berechtigung fehlt')
  })
  it('renders a no-data aggregate status using backend vocabulary', async () => {
    configureApi({
      googleSyncResult: {
        ...googleSyncResult,
        status: 'no_data',
        nutrition: { ...googleSyncResult.nutrition, status: 'no_data' },
        activity_energy: { ...googleSyncResult.activity_energy, status: 'no_data' },
        weight: { ...googleSyncResult.weight, status: 'no_data' },
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await wrapper.get('.google-health-sync-button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.google-health-sync-result p strong').text()).toBe('Keine Daten')
  })
  it('shows a German reauthorization message and accessible action for missing scopes', async () => {
    configureApi({ googleStatus: { ...googleStatus, state: 'scope_missing' } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const card = wrapper.get('.google-health-card')
    expect(card.text()).toContain('Erforderliche Berechtigung fehlt')
    expect(card.text()).toContain('erneut autorisieren')
    expect(card.get('.google-health-connect').text()).toContain('Google Health erneut autorisieren')
    expect(card.get('.google-health-connect').attributes('aria-label')).toContain('erneut autorisieren')
  })

  it('keeps loading state accessible while Google status is unavailable', async () => {
    const { promise: pendingStatus, resolve: resolveStatus } = Promise.withResolvers<unknown>()
    apiMock.mockImplementation((path: string) => {
      if (path === '/google-health/status') return pendingStatus
      if (path === '/yazio/status') return Promise.resolve(yazioStatus)
      return Promise.resolve({})
    })
    const wrapper = mount(AccountIntegrationsView)
    expect(wrapper.get('[role="status"]').text()).toContain('Wird geladen')
    resolveStatus(googleStatus)
    await flushPromises()
    expect(wrapper.find('.google-health-card .google-health-sync-button').exists()).toBe(true)
  })

  it('shows a safe Google synchronization error and no provider detail', async () => {
    configureApi({ googleSyncError: true })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.google-health-sync-button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.google-health-sync-error').text()).toContain('Google-Health-Synchronisierung ist fehlgeschlagen')
    expect(wrapper.text()).not.toContain('raw provider detail')
  })

  it('saves only the current credential pair and never renders a stored secret', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('input[name="google-client-id"]').setValue('client-new')
    await wrapper.get('input[name="google-client-secret"]').setValue('secret-new')
    await wrapper.get('.google-credentials-panel').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/google-health/credentials', {
      method: 'PUT',
      body: JSON.stringify({ client_id: 'client-new', client_secret: 'secret-new' }),
    })
    expect(wrapper.get('input[name="google-client-secret"]').attributes('type')).toBe('password')
    expect(wrapper.text()).not.toContain('secret-new')

    await wrapper.get('.google-credentials-panel').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/credentials', {
      method: 'PUT',
      body: JSON.stringify({ client_id: '', client_secret: '' }),
    })
  })

  it('rejects one-sided credential changes before calling the API', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('input[name="google-client-id"]').setValue('only-client')
    await wrapper.get('.google-credentials-panel').trigger('submit')
    await flushPromises()

    expect(wrapper.get('.google-credentials-panel').text()).toContain('müssen gemeinsam eingegeben werden')
    expect(apiMock.mock.calls.some(([path]) => path === '/google-health/credentials')).toBe(false)
  })

  it('tests, disconnects and explicitly deletes Google credentials', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.google-health-connection-test').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/connection/test', { method: 'POST' })
    expect(wrapper.text()).toContain('Verbindungstest erfolgreich')

    await wrapper.get('.google-health-disconnect').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/connection', { method: 'DELETE' })

    await wrapper.get('.google-health-delete-credentials').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/google-health/credentials', { method: 'DELETE' })
  })

  it('promotes a failed connection test to reauthorization state', async () => {
    configureApi({ googleConnectionTestResult: { status: 'reauth_required', error_category: 'reauth_required' } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.google-health-connection-test').trigger('click')
    await flushPromises()

    expect(wrapper.get('.google-health-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(wrapper.get('.google-health-connect').text()).toContain('Google Health erneut autorisieren')
    expect(wrapper.find('.google-health-sync-button').exists()).toBe(false)
  })

  it('shows backend retry attempts without exposing provider errors', async () => {
    configureApi({
      googleStatus: {
        ...googleStatus,
        sync_state: 'running',
        retry_attempt: 2,
        retry_max_attempts: 3,
        next_retry_at: '2026-09-18T08:02:00Z',
        last_error: 'raw provider error',
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.get('.integration-status-badge').text()).toBe('Wird erneut versucht')
    expect(wrapper.get('.google-status-panel').text()).toContain('Versuch 2 von 3')
    expect(wrapper.text()).not.toContain('raw provider error')
  })

  it('never renders credential or provider secret values returned by status APIs', async () => {
    configureApi({
      googleStatus: {
        ...googleStatus,
        last_error: 'client-secret-sentinel',
        last_error_category: 'provider_error',
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.text()).not.toContain('client-secret-sentinel')
    expect(wrapper.text()).not.toContain('refresh-token-sentinel')
    expect(wrapper.text()).not.toContain('access-token-sentinel')
  })

  it('keeps terminal failed sync state distinct from a scheduled retry', async () => {
    configureApi({ googleStatus: { ...googleStatus, sync_state: 'failed', retry_attempt: 2, retry_max_attempts: 3, next_retry_at: null } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.get('.google-health-status-badge').text()).toBe('Synchronisierung fehlgeschlagen')
  })

  it('only renders the YAZIO next-run row while scheduler and synchronization are enabled', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(wrapper.find('.yazio-next-sync-row').exists()).toBe(true)
    wrapper.unmount()

    configureApi({ yazioStatus: { ...yazioStatus, scheduler_enabled: false } })
    const pausedWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(pausedWrapper.find('.yazio-next-sync-row').exists()).toBe(false)
    pausedWrapper.unmount()

    configureApi({ yazioStatus: { ...yazioStatus, sync_enabled: false } })
    const disabledWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    expect(disabledWrapper.find('.yazio-next-sync-row').exists()).toBe(false)
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
