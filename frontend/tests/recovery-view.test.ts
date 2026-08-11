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
    ) {
      super(message)
    }
  },
}))

import RecoveryView from '../src/views/RecoveryView.vue'
import { ApiError } from '../src/api'

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
    expect(wrapper.text()).toContain('Passwort wurde geändert.')
    expect(wrapper.text()).toContain('Das Konto bleibt deaktiviert.')
    expect(wrapper.find('form').exists()).toBe(false)
    expect(sessionStorage.length).toBe(0)
  })

  it('supports manual token entry and shows password-policy details without consuming local state', async () => {
    apiMock.mockRejectedValue(new ApiError('Passwort ist zu häufig verwendet.', 422))
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="off"]').setValue('manually-pasted-token')
    await wrapper.get('input[autocomplete="new-password"]').setValue('too-short-but-matching')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('too-short-but-matching')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Passwort ist zu häufig verwendet.')
    expect(wrapper.get<HTMLInputElement>('input[autocomplete="off"]').element.value).toBe('manually-pasted-token')
  })

  it('shows the uniform public error for invalid recovery tokens', async () => {
    apiMock.mockRejectedValue(new ApiError('Recovery-Token ist ungültig oder abgelaufen', 400))
    window.history.replaceState({}, '', '/recovery#token=expired-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('replacement-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Recovery-Token ist ungültig oder abgelaufen')
    expect(wrapper.text()).not.toContain('expired-token')
  })

  it('honors recovery rate-limit guidance and releases the submit state', async () => {
    apiMock.mockRejectedValue(
      new ApiError('Zu viele Anfragen. Bitte später erneut versuchen.', 429, undefined, '45'),
    )
    window.history.replaceState({}, '', '/recovery#token=rate-limited-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('replacement-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('Zu viele Anfragen. Bitte später erneut versuchen.')
    expect(wrapper.text()).toContain('Erneut versuchen in 45 Sekunden.')
    expect(wrapper.get<HTMLButtonElement>('button[type="submit"]').element.disabled).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[autocomplete="off"]').element.value).toBe('rate-limited-token')
  })

  it('does not submit when password confirmation differs', async () => {
    window.history.replaceState({}, '', '/recovery#token=recovery-token')
    const { wrapper } = await mountRecovery()

    await wrapper.get('input[autocomplete="new-password"]').setValue('replacement-password-is-long')
    await wrapper.findAll('input[autocomplete="new-password"]')[1].setValue('different-password-is-long')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.text()).toContain('Die Passwörter stimmen nicht überein.')
    expect(apiMock).not.toHaveBeenCalled()
  })
})
