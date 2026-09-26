import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {
    constructor(
      message: string,
      public status: number,
      public requestId?: string,
      public retryAfter?: string,
      public problemType?: string,
      public problemTitle?: string,
    ) {
      super(message)
    }
  },
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import AccountIntegrationsView from '../src/views/AccountIntegrationsView.vue'
import { ApiError } from '../src/api'
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
const withingsStatus = {
  available: true,
  credentials_configured: true,
  redirect_uri: 'https://app.example.test/withings/oauth/callback',
  configured: true,
  connected: true,
  state: 'active',
  granted_scopes: ['user.metrics', 'user.activity'],
  access_token_expires_at: '2026-09-17T08:00:00Z',
  last_attempt_at: '2026-09-18T08:00:00Z',
  last_success_at: '2026-09-18T08:01:00Z',
  last_error_category: null,
}

const withingsSyncResult = {
  status: 'partial_failure',
  weight: {
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
    status: 'failed',
    fetched_count: 3,
    persisted_count: 0,
    requested_start: '2026-09-18',
    requested_end: '2026-09-18',
    covered_start: null,
    covered_end: null,
    error_code: 'provider_error',
  },
} as const

let withingsStatusCalls = 0

let yazioStatusCalls = 0
let googleStatusCalls = 0

function configureApi(overrides: Record<string, unknown> = {}): void {
  yazioStatusCalls = 0
  googleStatusCalls = 0
  withingsStatusCalls = 0
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/withings/status') {
      withingsStatusCalls += 1
      if (withingsStatusCalls > 1 && overrides.withingsStatusRefreshError) {
        return Promise.reject(new Error('status refresh failed'))
      }
      const result = withingsStatusCalls > 1
        ? overrides.withingsStatusAfterAction ?? overrides.withingsStatus ?? withingsStatus
        : overrides.withingsStatus ?? withingsStatus
      return result instanceof Error ? Promise.reject(result) : Promise.resolve(result)
    }
    if (path === '/withings/credentials' && options?.method === 'PUT') {
      return Promise.resolve(overrides.withingsCredentialsStatus ?? {
        ...withingsStatus,
        connected: false,
        state: 'not_connected',
      })
    }
    if (path === '/withings/credentials' && options?.method === 'DELETE') {
      return Promise.resolve(overrides.withingsCredentialsDeletedStatus ?? {
        ...withingsStatus,
        configured: false,
        credentials_configured: false,
        connected: false,
        state: 'not_configured',
      })
    }
    if (path === '/withings/sync' && options?.method === 'POST') {
      if (overrides.withingsSyncError) {
        return Promise.reject(
          overrides.withingsSyncError instanceof Error
            ? overrides.withingsSyncError
            : new Error('raw provider detail'),
        )
      }
      return Promise.resolve(overrides.withingsSyncResult ?? withingsSyncResult)
    }
    if (path === '/withings/oauth/start' && options?.method === 'POST') {
      if (overrides.withingsOAuthError) return Promise.reject(new Error('raw provider detail'))
      return Promise.resolve({ authorization_url: 'https://account.withings.example/authorize?state=test' })
    }
    if (path === '/withings/connection/test' && options?.method === 'POST') {
      if (overrides.withingsConnectionTestError) {
        return Promise.reject(
          overrides.withingsConnectionTestError instanceof Error
            ? overrides.withingsConnectionTestError
            : new Error('raw provider detail'),
        )
      }
      return Promise.resolve(overrides.withingsConnectionTestResponse ?? {
        ok: true,
        state: 'active',
        error_category: null,
      })
    }
    if (path === '/withings/connection' && options?.method === 'DELETE') return Promise.resolve({})
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
      if (overrides.googleConnectionTestError) {
        return Promise.reject(
          overrides.googleConnectionTestError instanceof Error
            ? overrides.googleConnectionTestError
            : new Error('provider detail'),
        )
      }
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
    expect(wrapper.get('.withings-card h2').text()).toBe('Withings')
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
    expect(icons).toHaveLength(4)
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

  it('keeps page loading until Google and Withings statuses load', async () => {
    const { promise: pendingGoogleStatus, resolve: resolveGoogleStatus } = Promise.withResolvers<unknown>()
    const { promise: pendingWithingsStatus, resolve: resolveWithingsStatus } = Promise.withResolvers<unknown>()
    apiMock.mockImplementation((path: string) => {
      if (path === '/google-health/status') return pendingGoogleStatus
      if (path === '/yazio/status') return Promise.resolve(yazioStatus)
      if (path === '/withings/status') return pendingWithingsStatus
      return Promise.resolve({})
    })
    const wrapper = mount(AccountIntegrationsView)
    expect(wrapper.get('[role="status"]').text()).toContain('Wird geladen')
    resolveGoogleStatus(googleStatus)
    await flushPromises()
    expect(wrapper.find('.dashboard-loading').exists()).toBe(true)
    resolveWithingsStatus(withingsStatus)
    await flushPromises()
    expect(wrapper.find('.google-health-card .google-health-sync-button').exists()).toBe(true)
    expect(wrapper.find('.withings-card .withings-sync-button').exists()).toBe(true)
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

  it('never renders credential or provider secret values returned by status and error APIs', async () => {
    configureApi({
      googleStatus: {
        ...googleStatus,
        state: 'refresh-token-sentinel',
        sync_state: 'access-token-sentinel',
      },
      googleConnectionTestError: new Error('client-secret-sentinel'),
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.html()).not.toContain('refresh-token-sentinel')
    expect(wrapper.html()).not.toContain('access-token-sentinel')
    await wrapper.get('.google-health-connection-test').trigger('click')
    await flushPromises()
    expect(wrapper.html()).not.toContain('client-secret-sentinel')
    expect(wrapper.get('.google-health-card').text()).toContain('Verbindungstest ist fehlgeschlagen')
  })

  it('keeps terminal failed sync state distinct from a scheduled retry', async () => {
    configureApi({ googleStatus: { ...googleStatus, sync_state: 'failed', retry_attempt: 2, retry_max_attempts: 3, next_retry_at: null } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    expect(wrapper.get('.google-health-status-badge').text()).toBe('Synchronisierung fehlgeschlagen')
    expect(wrapper.get('.google-status-panel').text()).not.toContain('Versuch 2 von 3')
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
  it.each([
    ['disabled', { available: false, configured: false, credentials_configured: false, connected: false, state: 'disabled' }, 'Serverseitig deaktiviert', false, false],
    ['not_configured', { configured: false, credentials_configured: false, connected: false, state: 'not_configured' }, 'Zugangsdaten noch nicht eingerichtet', false, false],
    ['not_connected', { connected: false, state: 'not_connected' }, 'Nicht verbunden', true, false],
    ['reauth_required', { connected: true, state: 'reauth_required' }, 'Erneute Autorisierung erforderlich', true, false],
    ['active', { connected: true, state: 'active' }, 'Verbunden', false, true],
    ['connected error', { connected: true, state: 'error' }, 'Verbindungsfehler', true, true],
    ['unconnected error', { connected: false, state: 'error' }, 'Verbindungsfehler', true, false],
  ] as const)('renders Withings %s state without exposing scopes', async (_state, overrides, status, canConnect, canSync) => {
    configureApi({ withingsStatus: { ...withingsStatus, ...overrides } })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe(status)
    expect(card.find('.withings-connect-button').exists()).toBe(canConnect)
    expect(card.find('.withings-sync-button').exists()).toBe(canSync)
    expect(card.text()).not.toContain('user.metrics')
    expect(card.text()).not.toContain('user.activity')
  })
  it('allows a connected Withings error state to retry without clearing the stored error before status does', async () => {
    const syncResponse = Promise.withResolvers<typeof withingsSyncResult>()
    const statusRefresh = Promise.withResolvers<typeof withingsStatus>()
    configureApi({
      withingsStatus: { ...withingsStatus, state: 'error', last_error_category: 'provider_error' },
      withingsSyncResult: syncResponse.promise,
      withingsStatusAfterAction: statusRefresh.promise,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')

    await card.get('.withings-sync-button').trigger('click')
    expect(apiMock).toHaveBeenCalledWith('/withings/sync', { method: 'POST' })
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')

    syncResponse.resolve(withingsSyncResult)
    await flushPromises()
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')

    statusRefresh.resolve({ ...withingsStatus, last_error_category: null })
    await flushPromises()
    expect(card.text()).not.toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')
  })

  it('reports status-load errors safely and leaves a retry action', async () => {
    configureApi({ withingsStatus: new Error('raw provider detail') })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-error').text()).toContain('Withings-Status konnte nicht geladen werden')
    expect(card.text()).not.toContain('raw provider detail')
    expect(card.get('.withings-error button').text()).toContain('Erneut versuchen')
  })

  it('saves and removes only local Withings credential inputs', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    configureApi({
      withingsStatus: {
        ...withingsStatus,
        configured: false,
        credentials_configured: false,
        connected: false,
        state: 'not_configured',
      },
      withingsCredentialsStatus: {
        ...withingsStatus,
        connected: false,
        state: 'not_connected',
        client_secret: 'response-secret-sentinel',
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    const card = wrapper.get('.withings-card')
    const clientId = card.get<HTMLInputElement>('input[name="withings-client-id"]')
    const clientSecret = card.get<HTMLInputElement>('input[name="withings-client-secret"]')
    expect(clientId.element.value).toBe('')
    expect(clientSecret.element.value).toBe('')

    await clientId.setValue('one-sided-client')
    await card.get('.withings-credentials-panel').trigger('submit')
    await flushPromises()
    expect(card.text()).toContain('Client-ID und Client-Secret müssen gemeinsam eingegeben werden')
    expect(apiMock.mock.calls.some(([path]) => path === '/withings/credentials')).toBe(false)

    await clientSecret.setValue('submitted-secret')
    await card.get('.withings-credentials-panel').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/credentials', {
      method: 'PUT',
      body: JSON.stringify({ client_id: 'one-sided-client', client_secret: 'submitted-secret' }),
    })
    expect(clientId.element.value).toBe('')
    expect(clientSecret.element.value).toBe('')
    expect(card.html()).not.toContain('submitted-secret')
    expect(card.html()).not.toContain('response-secret-sentinel')

    await card.get('.withings-delete-credentials').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/credentials', { method: 'DELETE' })
    expect(card.get('.withings-status-badge').text()).toBe('Zugangsdaten noch nicht eingerichtet')
    expect(card.find('.withings-connect-button').exists()).toBe(false)
  })

  it('starts OAuth from the callback-aware connection card with safe failures', async () => {
    window.history.replaceState({}, '', '/konto/integrationen?withings=error')
    configureApi({
      withingsStatus: { ...withingsStatus, connected: false, state: 'not_connected' },
      withingsOAuthError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    const card = wrapper.get('.withings-card')

    expect(card.text()).toContain('Withings konnte nicht verbunden werden')
    await card.get('.withings-connect-button').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/oauth/start', { method: 'POST' })
    expect(card.get('.withings-error').text()).toContain('Withings konnte nicht verbunden werden')
    expect(card.text()).not.toContain('raw provider detail')
  })

  it('tests connection success and reauthorization while hiding provider errors', async () => {
    configureApi({
      withingsStatusAfterAction: {
        ...withingsStatus,
        last_attempt_at: '2026-09-19T08:01:00Z',
        last_success_at: '2026-09-19T08:02:00Z',
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/connection/test', { method: 'POST' })
    expect(wrapper.get('.withings-card').text()).toContain('Verbindung ist aktiv.')
    const metadataRows = wrapper.findAll('.withings-connection-panel .integration-details > div')
    expect(metadataRows.map((row) => row.get('dt').text())).toEqual([
      'Access-Token gültig bis',
      'Letzter Vorgang',
      'Letzter erfolgreicher Vorgang',
    ])
    expect(metadataRows[1].get('dd').text()).toBe('19.09.2026, 10:01')
    expect(metadataRows[2].get('dd').text()).toBe('19.09.2026, 10:02')

    wrapper.unmount()
    configureApi({
      withingsConnectionTestResponse: { ok: false, state: 'reauth_required', error_category: 'invalid_grant' },
      withingsStatusAfterAction: { ...withingsStatus, state: 'reauth_required' },
    })
    const reauthWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await reauthWrapper.get('.withings-test-button').trigger('click')
    await flushPromises()
    const reauthCard = reauthWrapper.get('.withings-card')
    expect(reauthCard.get('.withings-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(reauthCard.get('.withings-connect-button').attributes('aria-label')).toContain('erneut autorisieren')
    expect(reauthCard.text()).not.toContain('invalid_grant')

    reauthWrapper.unmount()
    configureApi({ withingsConnectionTestError: true })
    const failedWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await failedWrapper.get('.withings-test-button').trigger('click')
    await flushPromises()
    expect(failedWrapper.get('.withings-error').text()).toContain('Withings-Verbindungstest ist fehlgeschlagen')
    expect(failedWrapper.text()).not.toContain('raw provider detail')
  })

  it('shows reauthorization after a connection test even when status refresh fails', async () => {
    configureApi({
      withingsConnectionTestResponse: { ok: false, state: 'reauth_required', error_category: 'invalid_grant' },
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(card.get('.withings-connect-button').attributes('aria-label')).toContain('erneut autorisieren')
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.text()).not.toContain('Verbindung ist aktiv.')
    wrapper.unmount()
  })
  it('fails closed to an unknown error state after an untyped 409 when status refresh fails', async () => {
    configureApi({
      withingsConnectionTestError: new ApiError('not connected', 409, undefined, undefined, 'about:blank'),
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(withingsStatusCalls).toBe(2)
    expect(card.get('.withings-status-badge').text()).toBe('Verbindungsfehler')
    expect(card.find('.withings-connect-button').exists()).toBe(true)
    expect(card.find('.withings-test-button').exists()).toBe(true)
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.find('.withings-disconnect-button').exists()).toBe(true)
    expect(card.get('.withings-connection-panel').text()).toContain('18.09.2026, 10:01')
    expect(card.get('.withings-connection-panel').text()).toContain('Nicht verfügbar')
    expect(card.text()).not.toContain('Fehler beim letzten Vorgang:')
    expect(card.text()).toContain('Withings-Status konnte nicht geladen werden')
    wrapper.unmount()
  })

  it('uses refreshed active status after an untyped 409 from the Withings connection test', async () => {
    configureApi({
      withingsConnectionTestError: new ApiError('operation busy', 409),
      withingsStatusAfterAction: withingsStatus,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(withingsStatusCalls).toBe(2)
    expect(card.get('.withings-status-badge').text()).toBe('Verbunden')
    expect(card.find('.withings-test-button').exists()).toBe(true)
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    wrapper.unmount()
  })

  it('uses refreshed status after a typed 409 from the Withings connection test', async () => {
    configureApi({
      withingsConnectionTestError: new ApiError(
        'operation busy',
        409,
        undefined,
        undefined,
        'urn:calograph:problem:user-operation-busy',
      ),
      withingsStatusAfterAction: withingsStatus,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(withingsStatusCalls).toBe(2)
    expect(card.get('.withings-status-badge').text()).toBe('Verbunden')
    expect(card.find('.withings-connect-button').exists()).toBe(false)
    expect(card.find('.withings-test-button').exists()).toBe(true)
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    expect(card.find('.withings-disconnect-button').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps a failed connection-test state and safe error category when status refresh fails', async () => {
    configureApi({
      withingsConnectionTestResponse: { ok: false, state: 'error', error_category: 'provider_error' },
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-test-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Verbindungsfehler')
    expect(card.find('.withings-connect-button').exists()).toBe(true)
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    expect(card.find('.withings-disconnect-button').exists()).toBe(true)
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')
    expect(card.text()).not.toContain('provider_error')
    expect(card.text()).not.toContain('Verbindung ist aktiv.')
    wrapper.unmount()
  })

  it('shows reauthorization after a sync even when status refresh fails', async () => {
    configureApi({
      withingsSyncResult: { ...withingsSyncResult, status: 'reauth_required' },
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(card.get('.withings-connect-button').attributes('aria-label')).toContain('erneut autorisieren')
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    wrapper.unmount()
  })
  it('applies a provider error from a partial Withings sync before a failed status refresh', async () => {
    configureApi({ withingsStatusRefreshError: true })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Verbindungsfehler')
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')
    expect(card.text()).not.toContain('provider_error')
    expect(card.text()).toContain('Der Withings-Status konnte nach der Synchronisierung nicht aktualisiert werden.')
    wrapper.unmount()
  })

  it('fails closed to an unknown error state after an untyped 409 from Withings sync when status refresh fails', async () => {
    configureApi({
      withingsSyncError: new ApiError('not configured', 409, undefined, undefined, 'about:blank'),
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(withingsStatusCalls).toBe(2)
    expect(card.get('.withings-status-badge').text()).toBe('Verbindungsfehler')
    expect(card.find('.withings-connect-button').exists()).toBe(true)
    expect(card.find('.withings-test-button').exists()).toBe(true)
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.find('.withings-disconnect-button').exists()).toBe(true)
    expect(card.get('.withings-connection-panel').text()).toContain('18.09.2026, 10:01')
    expect(card.get('.withings-connection-panel').text()).toContain('Nicht verfügbar')
    expect(card.text()).not.toContain('Fehler beim letzten Vorgang:')
    expect(card.text()).toContain('Der Withings-Status konnte nach der Synchronisierung nicht aktualisiert werden.')
    expect(card.text()).not.toContain('user.metrics')
    expect(card.text()).not.toContain('user.activity')
    wrapper.unmount()
  })

  it('uses refreshed active status after an untyped 409 from Withings sync', async () => {
    configureApi({
      withingsSyncError: new ApiError('operation busy', 409),
      withingsStatusAfterAction: withingsStatus,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(withingsStatusCalls).toBe(2)
    expect(card.get('.withings-status-badge').text()).toBe('Verbunden')
    expect(card.find('.withings-sync-button').exists()).toBe(true)
    wrapper.unmount()
  })

  it.each([
    ['failed', 'reauth_required'],
    ['failed', 'scope_missing'],
    ['failed', 'invalid_grant'],
    ['partial_failure', 'reauth_required'],
    ['partial_failure', 'scope_missing'],
    ['partial_failure', 'invalid_grant'],
  ] as const)('shows reauthorization for a %s aggregate and %s domain error when status refresh fails', async (aggregateStatus, errorCode) => {
    const reauthSyncResult = {
      status: aggregateStatus,
      weight: aggregateStatus === 'failed'
        ? { ...withingsSyncResult.weight, status: 'failed', error_code: errorCode }
        : { ...withingsSyncResult.weight, status: 'success', error_code: null },
      activity_energy: { ...withingsSyncResult.activity_energy, status: 'failed', error_code: errorCode },
    } as const
    configureApi({
      withingsSyncResult: reauthSyncResult,
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(card.get('.withings-connect-button').attributes('aria-label')).toContain('erneut autorisieren')
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.get('.withings-sync-result').text()).toContain('Erneute Autorisierung erforderlich')
    expect(card.get('.import-message.warning').text()).toBe('Der Withings-Status konnte nach der Synchronisierung nicht aktualisiert werden.')
    expect(card.get('.import-message.warning').text()).not.toContain('erfolgreich')
    expect(card.text()).not.toContain(errorCode)
    wrapper.unmount()
  })

  it('shows credential reauthorization after a failed sync when status refresh fails', async () => {
    const credentialUnavailableSyncResult = {
      status: 'failed',
      weight: { ...withingsSyncResult.weight, status: 'failed', error_code: 'credential_unavailable' },
      activity_energy: { ...withingsSyncResult.activity_energy, status: 'failed', error_code: 'credential_unavailable' },
    } as const
    configureApi({
      withingsSyncResult: credentialUnavailableSyncResult,
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Erneute Autorisierung erforderlich')
    expect(card.get('.withings-connect-button').attributes('aria-label')).toContain('erneut autorisieren')
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.get('.withings-sync-result').text()).toContain('Erneute Autorisierung erforderlich')
    expect(card.text()).not.toContain('credential_unavailable')
    expect(card.get('.import-message.warning').text()).toBe('Der Withings-Status konnte nach der Synchronisierung nicht aktualisiert werden.')
    wrapper.unmount()
  })

  it('fails closed as disconnected after a not-connected sync when status refresh fails', async () => {
    const notConnectedSyncResult = {
      status: 'failed',
      weight: { ...withingsSyncResult.weight, status: 'failed', error_code: 'not_connected' },
      activity_energy: { ...withingsSyncResult.activity_energy, status: 'failed', error_code: 'not_connected' },
    } as const
    configureApi({
      withingsSyncResult: notConnectedSyncResult,
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()

    const card = wrapper.get('.withings-card')
    expect(card.get('.withings-status-badge').text()).toBe('Nicht verbunden')
    expect(card.find('.withings-connect-button').exists()).toBe(true)
    expect(card.get('.withings-connect-button').text()).toBe('Mit Withings verbinden')
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.find('.withings-test-button').exists()).toBe(false)
    expect(card.find('.withings-disconnect-button').exists()).toBe(false)
    expect(card.get('.withings-connection-panel').text()).toContain('18.09.2026, 10:01')
    expect(card.get('.withings-connection-panel').text()).not.toContain('17.09.2026, 10:00')
    expect(card.text()).not.toContain('user.metrics')
    expect(card.text()).not.toContain('user.activity')
    expect(card.text()).not.toContain('not_connected')
    expect(card.get('.import-message.warning').text()).toBe('Der Withings-Status konnte nach der Synchronisierung nicht aktualisiert werden.')
    wrapper.unmount()
  })

  it('shows stored Withings provider errors as a safe localized status', async () => {
    configureApi({
      withingsStatus: { ...withingsStatus, last_error_category: 'provider_error' },
    })
    const wrapper = mount(AccountIntegrationsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()


    const card = wrapper.get('.withings-card')
    expect(card.text()).toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')
    expect(card.text()).not.toContain('Synchronisierungsfehler')
    expect(card.text()).not.toContain('provider_error')
  })

  it('keeps Withings disconnected when status refresh fails after a successful disconnect', async () => {
    configureApi({
      withingsStatus: { ...withingsStatus, last_error_category: 'provider_error' },
      withingsStatusRefreshError: true,
    })
    const wrapper = mount(AccountIntegrationsView, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()


    const card = wrapper.get('.withings-card')
    await card.get('.withings-disconnect-button').trigger('click')
    await flushPromises()

    expect(card.get('.withings-status-badge').text()).toBe('Nicht verbunden')
    expect(card.text()).toContain('Withings-Verbindung wurde getrennt.')
    expect(card.text()).toContain('Withings-Verbindung wurde getrennt, aber der Status konnte nicht aktualisiert werden')
    expect(card.find('.withings-connect-button').exists()).toBe(true)
    expect(card.find('.withings-disconnect-button').exists()).toBe(false)
    expect(card.find('.withings-sync-button').exists()).toBe(false)
    expect(card.get('.withings-connection-panel').text()).toContain('18.09.2026, 10:01')
    expect(card.get('.withings-connection-panel').text()).not.toContain('17.09.2026, 10:00')
    expect(card.text()).not.toContain('Fehler beim letzten Vorgang: Fehlgeschlagen')
    expect(card.text()).not.toContain('Withings-Verbindung konnte nicht getrennt werden')
  })

  it('disconnects Withings without losing the last successful operation indication', async () => {
    configureApi({
      withingsStatusAfterAction: {
        ...withingsStatus,
        connected: false,
        state: 'not_connected',
      },
    })
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-disconnect-button').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/connection', { method: 'DELETE' })
    expect(wrapper.get('.withings-status-badge').text()).toBe('Nicht verbunden')
    expect(wrapper.get('.withings-connection-panel').text()).toContain('18.09.2026, 10:01')
  })

  it('renders partial Withings sync results safely and hides provider failure details', async () => {
    const wrapper = mount(AccountIntegrationsView)
    await flushPromises()

    await wrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/withings/sync', { method: 'POST' })
    expect(wrapper.get('.withings-sync-result').text()).toContain('Teilweise fehlgeschlagen')
    expect(wrapper.get('.withings-sync-result').text()).toContain('4')
    expect(wrapper.get('.withings-sync-result').text()).toContain('2')
    expect(wrapper.text()).not.toContain('provider_error')

    wrapper.unmount()
    configureApi({ withingsSyncError: true })
    const failedWrapper = mount(AccountIntegrationsView)
    await flushPromises()
    await failedWrapper.get('.withings-sync-button').trigger('click')
    await flushPromises()
    expect(failedWrapper.get('.withings-sync-error').text()).toContain('Withings-Synchronisierung ist fehlgeschlagen')
    expect(failedWrapper.text()).not.toContain('raw provider detail')
  })
})
