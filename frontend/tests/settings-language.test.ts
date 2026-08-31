import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  localizeApiError: () => 'The request failed.',
}))

import AccountDataPrivacyView from '../src/views/AccountDataPrivacyView.vue'
import AccountGeneralSettingsView from '../src/views/AccountGeneralSettingsView.vue'
import AccountIntegrationsView from '../src/views/AccountIntegrationsView.vue'
import AccountSecurityView from '../src/views/AccountSecurityView.vue'
import { DEFAULT_LOCALE, i18n, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
const user = {
  id: 'user-1',
  username: 'admin',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  preferred_weight_unit: 'kg' as const,
  raw_payload_retention_days: 30,
  is_admin: true,
  is_active: true,
  deactivated_at: null,
}

describe('account language setting', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    setLocale(DEFAULT_LOCALE)
    const auth = useAuthStore()
    auth.user = { ...user }
    apiMock.mockReset()
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile' && options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as {
          language: 'de' | 'en'
          timezone: string
          week_starts_on: number
          preferred_weight_unit: 'kg' | 'lb'
          raw_payload_retention_days: number
        }
        return Promise.resolve({ ...user, ...body })
      }
      if (path === '/settings/profile') return Promise.resolve({ ...user })
      if (path === '/settings/tokens') return Promise.resolve([{ id: 'token-1', label: 'iPhone', token_prefix: 'cg_1', last_used_at: null, revoked_at: null }])
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      }
      if (path === '/yazio/status') {
        return Promise.resolve({ available: true, configured: false, sync_enabled: false, sync_interval_minutes: null, sync_days: null, last_attempt_at: null, last_success_at: null, next_sync_at: null, last_error: null })
      }
      if (path === '/users' || path === '/users/invitations') return Promise.resolve([])
      return Promise.resolve({})
    })
  })

  afterEach(() => {
    setLocale(DEFAULT_LOCALE)
  })

  it('persists language, timezone, week start, and unit system through legacy storage', async () => {
    const wrapper = mount(AccountGeneralSettingsView)
    await flushPromises()

    const language = wrapper.get<HTMLSelectElement>('select[name="language"]')
    const timezone = wrapper.get<HTMLSelectElement>('select[name="timezone"]')
    const weekStartsOn = wrapper.get<HTMLSelectElement>('select[name="week_starts_on"]')
    const unitSystem = wrapper.get<HTMLSelectElement>('select[name="unit_system"]')
    expect(language.element.value).toBe('de')
    expect(timezone.element.value).toBe('Europe/Berlin')
    expect(weekStartsOn.element.value).toBe('0')
    expect(unitSystem.element.value).toBe('metric')
    expect(wrapper.findAll('select[name="unit_system"] option').map((option) => option.text())).toEqual([
      'Metrisch',
      'Imperial',
    ])
    expect(wrapper.text()).toContain('Einheitensystem')
    expect(wrapper.find('.language-switcher').exists()).toBe(false)

    await language.setValue('en')
    await timezone.setValue('Europe/Vienna')
    await weekStartsOn.setValue('6')
    await unitSystem.setValue('imperial')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({
        language: 'en',
        timezone: 'Europe/Vienna',
        week_starts_on: 6,
        preferred_weight_unit: 'lb',
        raw_payload_retention_days: 30,
      }),
    })
    expect(useAuthStore().user?.language).toBe('en')
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.text()).toContain('Unit system')

    await language.setValue('de')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({
        language: 'de',
        timezone: 'Europe/Vienna',
        week_starts_on: 6,
        preferred_weight_unit: 'lb',
        raw_payload_retention_days: 30,
      }),
    })
    expect(useAuthStore().user?.language).toBe('de')
    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
    wrapper.unmount()
  })
  it('does not let a late settings response switch a public page locale', async () => {
    let resolveProfile!: (value: typeof user) => void
    apiMock.mockImplementationOnce(
      () => new Promise<typeof user>((resolve) => { resolveProfile = resolve }),
    )
    const wrapper = mount(AccountGeneralSettingsView)
    await vi.waitFor(() => expect(resolveProfile).toBeTypeOf('function'))
    wrapper.unmount()
    setLocale('en')
    resolveProfile({ ...user })
    await flushPromises()

    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('loads a second preference page after an in-flight full replacement save', async () => {
    let serverProfile = { ...user }
    let resolveFirstSave!: () => void
    let firstSavePending = true
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path !== '/settings/profile') return Promise.resolve({})
      if (options?.method !== 'PUT') return Promise.resolve({ ...serverProfile })
      const body = JSON.parse(String(options.body)) as typeof serverProfile
      if (firstSavePending) {
        firstSavePending = false
        return new Promise<typeof serverProfile>((resolve) => {
          resolveFirstSave = () => {
            serverProfile = { ...serverProfile, ...body }
            resolve({ ...serverProfile })
          }
        })
      }
      serverProfile = { ...serverProfile, ...body }
      return Promise.resolve({ ...serverProfile })
    })

    const generalWrapper = mount(AccountGeneralSettingsView)
    await flushPromises()
    await generalWrapper.get('select[name="language"]').setValue('en')
    void generalWrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(resolveFirstSave).toBeTypeOf('function'))
    generalWrapper.unmount()

    const privacyWrapper = mount(AccountDataPrivacyView)
    await Promise.resolve()
    expect(apiMock.mock.calls.filter(([path, options]) =>
      path === '/settings/profile' && (options as RequestInit | undefined)?.method !== 'PUT',
    )).toHaveLength(1)

    resolveFirstSave()
    await flushPromises()
    await vi.waitFor(() => {
      expect(privacyWrapper.find('input[name="raw_payload_retention_days"]').exists()).toBe(true)
    })
    const retention = privacyWrapper.get<HTMLInputElement>('input[name="raw_payload_retention_days"]')
    await retention.setValue('60')
    await privacyWrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({
        language: 'en',
        timezone: 'Europe/Berlin',
        week_starts_on: 0,
        preferred_weight_unit: 'kg',
        raw_payload_retention_days: 60,
      }),
    })
    privacyWrapper.unmount()
  })

  it('keeps account data actions compact and exposes a localized backup picker', async () => {
    const wrapper = mount(AccountDataPrivacyView)
    await flushPromises()

    const retention = wrapper.get<HTMLInputElement>('input[name="raw_payload_retention_days"]')
    expect(retention.element.value).toBe('30')
    expect(wrapper.get('.account-data-card h2').text()).toBe('Datenverwaltung')
    expect(wrapper.findAll('.account-data-card')).toHaveLength(1)
    expect(wrapper.findAll('.account-action-button')).not.toHaveLength(0)
    expect(wrapper.find('.portable-import-actions').exists()).toBe(true)
    expect(wrapper.get('input[type="file"]').element.getAttribute('aria-label')).toBeNull()
    expect(wrapper.get('.account-file-picker').text()).toContain('Datei auswählen')
    expect(wrapper.get('.backup-validate-button').attributes('disabled')).toBeDefined()

    await retention.setValue('60')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenLastCalledWith('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({
        language: 'de',
        timezone: 'Europe/Berlin',
        week_starts_on: 0,
        preferred_weight_unit: 'kg',
        raw_payload_retention_days: 60,
      }),
    })

    const fileInput = wrapper.get<HTMLInputElement>('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', {
      value: [new File(['backup'], 'backup-2026.zip', { type: 'application/zip' })],
      configurable: true,
    })
    await fileInput.trigger('change')
    const filename = wrapper.get('.selected-file-name')
    expect(filename.text()).toBe('backup-2026.zip')
    expect(filename.attributes('title')).toBe('backup-2026.zip')
    expect(wrapper.get('.backup-validate-button').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
    const securityWrapper = mount(AccountSecurityView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    expect(securityWrapper.get('.password-submit').classes()).toContain('compact-action')
    expect(securityWrapper.get('.mfa-card button[type="submit"]').classes()).toContain('compact-action')
    expect(securityWrapper.get('.passkey-card button[type="submit"]').classes()).toContain('compact-action')
    expect(securityWrapper.get('.token-create-button').classes()).toContain('compact-action')
    securityWrapper.unmount()

    const integrationsWrapper = mount(AccountIntegrationsView, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()
    expect(integrationsWrapper.get('.yazio-connection-card button[type="submit"]').classes()).toContain('compact-action')
    integrationsWrapper.unmount()
  })

  it('previews and applies the selected portable backup before refreshing preferences', async () => {
    const backup = new File(['backup'], 'portable.zip', { type: 'application/zip' })
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile') return Promise.resolve({ ...user })
      if (path === '/import/calo/preview') {
        expect(options?.method).toBe('POST')
        expect((options?.body as FormData).get('file')).toBe(backup)
        return Promise.resolve({ health_samples: 12, targets: 2, tracking_overrides: 1 })
      }
      if (path === '/import/calo/apply') {
        expect(options?.method).toBe('POST')
        expect((options?.body as FormData).get('file')).toBe(backup)
        return Promise.resolve({})
      }
      return Promise.resolve({})
    })

    const wrapper = mount(AccountDataPrivacyView)
    await flushPromises()
    expect(wrapper.find('.setup-notice').exists()).toBe(false)
    const fileInput = wrapper.get<HTMLInputElement>('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { value: [backup], configurable: true })
    await fileInput.trigger('change')
    await wrapper.get('.backup-validate-button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.setup-notice').text()).toContain('12')
    await wrapper.get('.setup-notice button').trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/import/calo/apply', {
      method: 'POST',
      body: expect.any(FormData),
    })
    expect(apiMock.mock.calls.filter(([path]) => path === '/settings/profile')).toHaveLength(2)
    expect(wrapper.text()).toContain('Datensicherung importiert.')
    wrapper.unmount()
  })

  it('keeps portable apply unavailable after a failed preview', async () => {
    const backup = new File(['invalid'], 'invalid.zip', { type: 'application/zip' })
    apiMock.mockImplementation((path: string) => {
      if (path === '/settings/profile') return Promise.resolve({ ...user })
      if (path === '/import/calo/preview') return Promise.reject(new Error('raw parser detail'))
      return Promise.resolve({})
    })

    const wrapper = mount(AccountDataPrivacyView)
    await flushPromises()
    const fileInput = wrapper.get<HTMLInputElement>('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { value: [backup], configurable: true })
    await fileInput.trigger('change')
    await wrapper.get('.backup-validate-button').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toBe('Datensicherung konnte nicht importiert werden.')
    expect(wrapper.find('.setup-notice').exists()).toBe(false)
    expect(apiMock.mock.calls.some(([path]) => path === '/import/calo/apply')).toBe(false)
    wrapper.unmount()
  })
})
