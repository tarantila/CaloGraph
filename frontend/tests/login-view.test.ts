import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LoginView from '../src/views/LoginView.vue'
import { PUBLIC_LOCALE, setLocale } from '../src/i18n'

describe('LoginView', () => {
  beforeEach(() => {
    setLocale('en')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ setup_required: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })
  it('offers the first administrator setup on an empty instance', async () => {
    let resolveBootstrap!: (response: Response) => void
    const pendingBootstrap = new Promise<Response>((resolve) => {
      resolveBootstrap = resolve
    })
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ setup_required: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockReturnValueOnce(pendingBootstrap)
    vi.stubGlobal('fetch', fetchMock)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/login', component: LoginView }],
    })
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('Create administrator account')
    const inputs = wrapper.findAll('input')
    expect(inputs).toHaveLength(3)
    expect(inputs.map((input) => input.attributes('autocomplete'))).toEqual([
      'username',
      'new-password',
      'new-password',
    ])

    await inputs[0]!.setValue('admin')
    await inputs[1]!.setValue('correct horse battery staple')
    await inputs[2]!.setValue('correct horse battery staple')
    await wrapper.get('form').trigger('submit')
    expect(inputs.every((input) => input.element.disabled)).toBe(true)

    resolveBootstrap(new Response('{}', {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }))
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Administrator account created')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/auth/bootstrap', expect.objectContaining({
      method: 'POST',
    }))
    wrapper.unmount()
  })

  it('shows the password form only after selecting the sign-in method', async () => {
    vi.stubGlobal('isSecureContext', false)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/login', component: LoginView }],
    })
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, {
      attachTo: document.body,
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('CaloGraph')
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.text()).toContain('Sign in with password')
    expect(wrapper.findAll('input')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Gesundheitsdaten')
    expect(wrapper.text()).not.toContain('Registrieren')
    expect(wrapper.find('.language-switcher').exists()).toBe(false)

    await wrapper.get('button.login-method-button').trigger('click')

    expect(wrapper.get('h1').text()).toBe('Sign in')
    expect(wrapper.findAll('input')).toHaveLength(2)
    expect(wrapper.get('input[autocomplete="username"]').element).toBe(document.activeElement)

    await wrapper.get('button.login-back-button').trigger('click')
    expect(wrapper.get('h1').text()).toBe('CaloGraph')
    expect(wrapper.findAll('input')).toHaveLength(0)
    wrapper.unmount()
  })

  it('offers passkey sign-in in a secure WebAuthn-capable browser', async () => {
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('PublicKeyCredential', class PublicKeyCredential {})
    vi.stubGlobal('navigator', { credentials: {} })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/login', component: LoginView }],
    })
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Sign in with passkey')
    expect(document.documentElement.lang).toBe('en')
    expect(wrapper.find('.language-switcher').exists()).toBe(false)
    wrapper.unmount()
  })

  it('uses German on the unauthenticated public login page by default', async () => {
    setLocale(PUBLIC_LOCALE)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/login', component: LoginView }],
    })
    await router.push('/login')
    await router.isReady()

    const wrapper = mount(LoginView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    expect(document.documentElement.lang).toBe('de')
    expect(wrapper.text()).toContain('Mit Passwort anmelden')
    expect(wrapper.text()).not.toContain('Sign in with password')
    wrapper.unmount()
  })
})
