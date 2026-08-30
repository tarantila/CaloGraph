import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock, createPasskeyMock } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  createPasskeyMock: vi.fn(),
}))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  localizeApiError: () => 'Die Sicherheitsaktion ist fehlgeschlagen.',
}))
vi.mock('../src/webauthn', () => ({
  createPasskey: createPasskeyMock,
  isPasskeySupported: () => true,
}))

import AccountSecurityView from '../src/views/AccountSecurityView.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const user = {
  id: 'user-1',
  username: 'alex',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  raw_payload_retention_days: 30,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

let router: Router

function mountSecurity() {
  return mount(AccountSecurityView, { global: { plugins: [router] } })
}

function installBaseApi() {
  apiMock.mockImplementation((path: string) => {
    if (path === '/settings/tokens') return Promise.resolve([])
    if (path === '/settings/mfa') {
      return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
    }
    if (path === '/settings/passkeys') return Promise.resolve([])
    return Promise.resolve({})
  })
}

describe('AccountSecurityView workflows', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    useAuthStore().user = { ...user }
    setLocale(DEFAULT_LOCALE)
    apiMock.mockReset()
    createPasskeyMock.mockReset()
    installBaseApi()
    router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/konto/sicherheit', name: 'account-security', component: { template: '<div />' } },
        { path: '/login', name: 'login', component: { template: '<div />' } },
      ],
    })
    await router.push({ name: 'account-security' })
    await router.isReady()
  })

  it('changes the password, clears the session, and redirects to login', async () => {
    const replaceSpy = vi.spyOn(router, 'replace')
    const clearSessionSpy = vi.spyOn(useAuthStore(), 'clearSession').mockImplementation(() => undefined)
    const wrapper = mountSecurity()
    await flushPromises()
    const passwordInputs = wrapper.findAll<HTMLInputElement>('.password-change-card input')
    await passwordInputs[0].setValue('current-password')
    await passwordInputs[1].setValue('new-password-long-enough')
    await passwordInputs[2].setValue('new-password-long-enough')
    await wrapper.get('.password-change-card form').trigger('submit')
    await flushPromises()

    expect(replaceSpy).toHaveBeenCalledWith({ name: 'login', query: { passwordChanged: '1' } })
    expect(apiMock).toHaveBeenCalledWith('/auth/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'current-password', new_password: 'new-password-long-enough' }),
    })
    expect(clearSessionSpy).toHaveBeenCalledOnce()
    expect(router.currentRoute.value).toMatchObject({ name: 'login', query: { passwordChanged: '1' } })
    wrapper.unmount()
  })

  it('sets up TOTP, exposes one-time recovery codes, regenerates them, and disables TOTP', async () => {
    let mfaEnabled = false
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/tokens' || path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({ totp_enabled: mfaEnabled, totp_setup_pending: false, recovery_codes_remaining: mfaEnabled ? 8 : 0 })
      }
      if (path === '/settings/mfa/totp/setup') {
        return Promise.resolve({ secret: 'SYNTHETIC', provisioning_uri: 'otpauth://synthetic', qr_svg_data_url: 'data:image/svg+xml,<svg />' })
      }
      if (path === '/settings/mfa/totp/confirm') {
        mfaEnabled = true
        return Promise.resolve({ recovery_codes: ['first-code', 'second-code'] })
      }
      if (path === '/settings/mfa/totp/recovery-codes') {
        return Promise.resolve({ recovery_codes: ['replacement-code'] })
      }
      if (path === '/settings/mfa/totp' && options?.method === 'DELETE') {
        mfaEnabled = false
        return Promise.resolve({})
      }
      return Promise.resolve({})
    })

    const wrapper = mountSecurity()
    await flushPromises()
    await wrapper.get('.mfa-card input[type="password"]').setValue('current-password')
    await wrapper.get('.mfa-card form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/mfa/totp/setup', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'current-password' }),
    })

    await wrapper.get('.mfa-setup input').setValue('123456')
    await wrapper.get('.mfa-setup form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/mfa/totp/confirm', {
      method: 'POST',
      body: JSON.stringify({ code: '123456' }),
    })
    expect(wrapper.text()).toContain('first-code')

    const activeInputs = wrapper.findAll<HTMLInputElement>('.mfa-card form input')
    await activeInputs[0].setValue('current-password')
    await activeInputs[1].setValue('654321')
    await wrapper.get('.mfa-card .button.secondary').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/mfa/totp/recovery-codes', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'current-password', code: '654321' }),
    })
    expect(wrapper.text()).toContain('replacement-code')

    await wrapper.get('.mfa-card input[type="password"]').setValue('current-password')
    await wrapper.get('.mfa-card input[autocomplete="one-time-code"]').setValue('654321')
    await wrapper.get('.mfa-card .text-button.danger').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/mfa/totp', {
      method: 'DELETE',
      body: JSON.stringify({ current_password: 'current-password', code: '654321' }),
    })
    expect(wrapper.text()).not.toContain('replacement-code')
    wrapper.unmount()
  })

  it('registers and removes a passkey with the exact WebAuthn sequence', async () => {
    const passkeys: Array<Record<string, unknown>> = []
    const credential = { id: 'credential-1', raw_id: 'raw' }
    createPasskeyMock.mockResolvedValue(credential)
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/tokens') return Promise.resolve([])
      if (path === '/settings/mfa') {
        return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      }
      if (path === '/settings/passkeys/options') {
        return Promise.resolve({ challenge_id: 'challenge-1', public_key: { challenge: 'synthetic' } })
      }
      if (path === '/settings/passkeys' && options?.method === 'POST') {
        const created = { id: 'passkey-1', label: 'Laptop', device_type: 'single_device', backed_up: false, created_at: '2026-01-01T10:00:00Z', last_used_at: null }
        passkeys.push(created)
        return Promise.resolve(created)
      }
      if (path === '/settings/passkeys') return Promise.resolve([...passkeys])
      if (path === '/settings/passkeys/passkey-1' && options?.method === 'DELETE') {
        passkeys.splice(0)
        return Promise.resolve({})
      }
      return Promise.resolve({})
    })

    const wrapper = mountSecurity()
    await flushPromises()
    await wrapper.get('.passkey-card input[placeholder]').setValue('Laptop')
    await wrapper.get('.passkey-card input[type="password"]').setValue('current-password')
    await wrapper.get('.passkey-card form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/settings/passkeys/options', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'current-password', code: null }),
    })
    expect(createPasskeyMock).toHaveBeenCalledWith({ challenge: 'synthetic' })
    expect(apiMock).toHaveBeenCalledWith('/settings/passkeys', {
      method: 'POST',
      body: JSON.stringify({ challenge_id: 'challenge-1', label: 'Laptop', credential }),
    })
    expect(wrapper.text()).toContain('Laptop')

    await wrapper.get('.passkey-card input[type="password"]').setValue('remove-password')
    await wrapper.get('.passkey-item .text-button.danger').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/passkeys/passkey-1', {
      method: 'DELETE',
      body: JSON.stringify({ current_password: 'remove-password', code: null }),
    })
    wrapper.unmount()
  })

  it('shows a token once, refreshes the list, revokes it, and localizes failures', async () => {
    let created = false
    let failCreate = false
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/mfa') {
        return Promise.resolve({ totp_enabled: false, totp_setup_pending: false, recovery_codes_remaining: 0 })
      }
      if (path === '/settings/passkeys') return Promise.resolve([])
      if (path === '/settings/tokens' && options?.method === 'POST') {
        if (failCreate) return Promise.reject(new Error('raw token service detail'))
        created = true
        return Promise.resolve({ token: 'cg_once_plaintext' })
      }
      if (path === '/settings/tokens/token-1' && options?.method === 'DELETE') {
        created = false
        return Promise.resolve({})
      }
      if (path === '/settings/tokens') {
        return Promise.resolve(created
          ? [{ id: 'token-1', label: 'iPhone', token_prefix: 'cg_1', created_at: '2026-01-01T10:00:00Z', last_used_at: null, revoked_at: null }]
          : [])
      }
      return Promise.resolve({})
    })

    const wrapper = mountSecurity()
    await flushPromises()
    await wrapper.get('.token-create-button').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/tokens', {
      method: 'POST',
      body: JSON.stringify({ label: 'iPhone' }),
    })
    expect(wrapper.text()).toContain('cg_once_plaintext')
    await wrapper.get('.token-card .text-button.danger').trigger('click')
    await flushPromises()
    expect(apiMock).toHaveBeenCalledWith('/settings/tokens/token-1', { method: 'DELETE' })

    failCreate = true
    await wrapper.get('.token-create-button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.token-card [role="alert"]').text()).toBe('Token konnte nicht erzeugt werden.')
    expect(wrapper.text()).not.toContain('raw token service detail')
    wrapper.unmount()
  })
})
