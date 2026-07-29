import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
}))

import RegisterView from '../src/views/RegisterView.vue'

describe('RegisterView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    window.history.replaceState({}, '', '/')
  })

  it('scrubs the raw token, exchanges it, and registers with the cookie state', async () => {
    apiMock.mockResolvedValue(undefined)
    window.history.replaceState({}, '', '/einladung#token=invite_secret')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/einladung', name: 'register', component: RegisterView },
        { path: '/login', name: 'login', component: { template: '<div />' } },
      ],
    })
    await router.push('/einladung')
    await router.isReady()

    const wrapper = mount(RegisterView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(window.location.hash).toBe('')
    expect(apiMock).toHaveBeenCalledWith('/auth/invitation/exchange', {
      method: 'POST',
      body: JSON.stringify({ token: 'invite_secret' }),
    })
    await wrapper.get('input[autocomplete="username"]').setValue('friend')
    await wrapper.get('input[autocomplete="new-password"]').setValue('friend-password-is-long')
    await wrapper
      .findAll('input[autocomplete="new-password"]')[1]
      .setValue('friend-password-is-long')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username: 'friend',
        password: 'friend-password-is-long',
      }),
    })
    expect(router.currentRoute.value).toMatchObject({
      name: 'login',
      query: { registered: '1' },
    })
    wrapper.unmount()
  })

  it('resumes registration from a valid short-lived cookie state', async () => {
    apiMock.mockResolvedValue({ valid: true })
    window.history.replaceState({}, '', '/einladung')
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/einladung', name: 'register', component: RegisterView }],
    })
    await router.push('/einladung')
    await router.isReady()

    const wrapper = mount(RegisterView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/auth/invitation/status')
    expect(wrapper.find('form').exists()).toBe(true)
    wrapper.unmount()
  })
})
