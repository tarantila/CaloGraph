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
      if (path === '/settings/tokens' || path === '/settings/passkeys') return Promise.resolve([])
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
})
