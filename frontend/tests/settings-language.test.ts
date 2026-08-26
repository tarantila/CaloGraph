import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  localizeApiError: () => 'The request failed.',
}))

import SettingsView from '../src/views/SettingsView.vue'
import { DEFAULT_LOCALE, i18n, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const user = {
  id: 'user-1',
  username: 'admin',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
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
        const body = JSON.parse(String(options.body)) as { language: 'de' | 'en' }
        return Promise.resolve({ ...user, language: body.language })
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

  it('persists both directions and rerenders without a reload', async () => {
    const wrapper = mount(SettingsView, {
      props: { section: 'account' },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    const language = wrapper.get<HTMLSelectElement>('select[name="language"]')
    expect(language.element.value).toBe('de')
    expect(wrapper.text()).toContain('Sprache')
    expect(wrapper.find('.language-switcher').exists()).toBe(false)

    await language.setValue('en')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/settings/profile', {
      method: 'PUT',
      body: expect.stringContaining('"language":"en"'),
    })
    expect(useAuthStore().user?.language).toBe('en')
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.text()).toContain('Language')

    await language.setValue('de')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

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
    const wrapper = mount(SettingsView, {
      props: { section: 'account' },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    wrapper.unmount()
    setLocale('en')
    resolveProfile({ ...user })
    await flushPromises()

    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('keeps account data actions compact and exposes a localized backup picker', async () => {
    const wrapper = mount(SettingsView, {
      props: { section: 'account' },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('.profile-form').findAll(':scope > .field')).toHaveLength(4)
    expect(wrapper.get('.profile-submit').classes()).toContain('compact-action')
    expect(wrapper.get('.account-data-card h2').text()).toBe('Meine Daten')
    expect(wrapper.findAll('.account-data-card')).toHaveLength(1)
    expect(wrapper.findAll('.account-action-button')).not.toHaveLength(0)
    expect(wrapper.find('.portable-import-actions').exists()).toBe(true)
    expect(wrapper.get('input[type="file"]').element.getAttribute('aria-label')).toBeNull()
    expect(wrapper.get('.account-file-picker').text()).toContain('Datei auswählen')
    expect(wrapper.get('.backup-validate-button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.token-create-row').find('button').exists()).toBe(true)
    const compactPrimaryLabels = [
      'Profil speichern',
      'Passwort ändern',
      'Authenticator einrichten',
      'Passkey einrichten',
      'Verbindung einrichten',
      'Token erzeugen',
    ]
    const actionButtons = new Map(
      wrapper.findAll('button').map((button) => [button.text(), button]),
    )
    for (const label of compactPrimaryLabels) {
      expect(actionButtons.get(label)?.classes()).toContain('compact-action')
      expect(actionButtons.get(label)?.classes()).not.toContain('secondary')
    }
    expect(wrapper.get('.account-file-picker .button').classes()).toContain('secondary')
    expect(wrapper.get('.backup-validate-button').classes()).not.toContain('secondary')
    expect(actionButtons.get('Widerrufen')?.classes()).toEqual(
      expect.arrayContaining(['text-button', 'danger']),
    )
    expect(wrapper.get('table thead').find('button').exists()).toBe(false)

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
  })
})
