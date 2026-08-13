import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createRouter, createWebHistory, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      public status: number,
      public requestId?: string,
      public retryAfter?: string,
      public problemType?: string,
    ) {
      super(message)
    }
  },
  localizeApiError: (error: { message: string; status: number; problemType?: string }) =>
    error.problemType === 'urn:calograph:problem:rate-limited'
      ? 'Too many requests. Please try again later.'
      : error.problemType === 'urn:calograph:problem:invalid-invitation'
        ? 'The recovery token is invalid or expired.'
        : error.message,
}))

import RecoveryView from '../src/views/RecoveryView.vue'
import { ApiError } from '../src/api'
import { PUBLIC_LOCALE, setLocale } from '../src/i18n'

interface MountedRecovery {
  wrapper: VueWrapper
  router: Router
}

async function mountRecovery(): Promise<MountedRecovery> {
  const router = createRouter({
    history: createWebHistory(),
    routes: [
      { path: '/recovery', name: 'recovery', component: { template: '<div />' } },
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  await router.push(`${window.location.pathname}${window.location.search}${window.location.hash}`)
  await router.isReady()
  const wrapper = mount(RecoveryView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, router }
}

describe('RecoveryView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setLocale(PUBLIC_LOCALE)
    window.history.replaceState({}, '', '/recovery')
  })

  it('takes the token from the fragment, scrubs the URL, and never creates a session', async () => {
    apiMock.mockResolvedValue(undefined)
    window.history.replaceState(
      { current: '/recovery#token=recovery_secret', back: null, forward: null },
      '',
      '/recovery#token=recovery_secret',
    )

    const { wrapper, router } = await mountRecovery()
    expect(window.location.hash).toBe('')
    expect(JSON.stringify(window.history.state)).not.toContain('recovery_secret')
    expect(router.currentRoute.value.fullPath).toBe('/recovery')
    expect(wrapper.get<HTMLInputElement>('input[autocomplete="off"]').element.value).toBe('recovery_secret')

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('replacement-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/auth/recovery/complete', {
      method: 'POST',
      body: JSON.stringify({
        recovery_token: 'recovery_secret',
        new_password: 'replacement-password-is-long',
      }),
    })
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.find('.language-switcher').exists()).toBe(false)
    expect(wrapper.text()).toContain('Password changed.')
    expect(wrapper.text()).toContain('The account remains disabled.')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(sessionStorage.length).toBe(0)
  })

  it('supports manual token entry and shows password-policy details without consuming local state', async () => {
    apiMock.mockRejectedValue(new ApiError('The password is used too often.', 422))
    const { wrapper } = await mountRecovery()

    expect(wrapper.text()).toContain('Use a long, hard-to-guess passphrase')
    await wrapper.get('input[autocomplete="off"]').setValue('manually-pasted-token')
    await wrapper.get('input[autocomplete="new-password"]').setValue('too-short-but-matching')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('too-short-but-matching')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('The password is used too often.')
    expect(wrapper.get<HTMLInputElement>('input[autocomplete="off"]').element.value).toBe('manually-pasted-token')
  })

  it('shows the uniform public error for invalid recovery tokens', async () => {
    apiMock.mockRejectedValue(new ApiError('The recovery token is invalid or expired.', 400))
    window.history.replaceState({}, '', '/recovery#token=expired-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('replacement-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('The recovery token is invalid or expired.')
    expect(wrapper.text()).not.toContain('expired-token')
  })

  it('honors recovery rate-limit guidance and releases the submit state', async () => {
    apiMock.mockRejectedValue(
      new ApiError('Too many requests. Please try again later.', 429, undefined, '45'),
    )
    window.history.replaceState({}, '', '/recovery#token=rate-limited-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('replacement-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Too many requests. Please try again later.')
    expect(wrapper.text()).toContain('Try again in 45 seconds.')
    expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[autocomplete="off"]').element.value).toBe('rate-limited-token')
  })

  it('does not submit when password confirmation differs', async () => {
    window.history.replaceState({}, '', '/recovery#token=recovery-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('different-password-is-long')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.text()).toContain('The passwords do not match.')
    expect(apiMock).not.toHaveBeenCalled()
  })
})
