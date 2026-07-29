import { createPinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import LoginView from '../src/views/LoginView.vue'

describe('LoginView', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
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

    expect(wrapper.get('h1').text()).toBe('CaloGraph')
    expect(wrapper.findAll('input')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Gesundheitsdaten')
    expect(wrapper.text()).not.toContain('Registrieren')

    await wrapper.get('button.login-method-button').trigger('click')

    expect(wrapper.get('h1').text()).toBe('Anmelden')
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

    expect(wrapper.text()).toContain('Mit Passkey anmelden')
  })
})
