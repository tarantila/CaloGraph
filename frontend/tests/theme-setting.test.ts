import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  localizeApiError: () => 'The request failed.',
}))

import AccountGeneralSettingsView from '../src/views/AccountGeneralSettingsView.vue'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'
import { effectiveTheme, initializeTheme, setThemePreference, themePreference } from '../src/theme'
import type { User } from '../src/types'

const profile: User = {
  id: 'theme-user',
  username: 'theme-user',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  preferred_weight_unit: 'kg',
  raw_payload_retention_days: 30,
  highlight_over_budget: false,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

type ChangeListener = (event: MediaQueryListEvent) => void
let media: ReturnType<typeof installMedia>

function installMedia(matches: boolean) {
  const listeners = new Set<ChangeListener>()
  const media = {
    matches,
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: vi.fn((_type: string, listener: ChangeListener) => listeners.add(listener)),
    removeEventListener: vi.fn((_type: string, listener: ChangeListener) => listeners.delete(listener)),
    dispatch(next: boolean) {
      this.matches = next
      for (const listener of listeners) listener({ matches: next } as MediaQueryListEvent)
    },
  }
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => media) })
  return media
}

describe('account appearance preference', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    useAuthStore().user = { ...profile }
    apiMock.mockReset().mockResolvedValue({ ...profile })
    window.localStorage.clear()
    media = installMedia(false)
    initializeTheme()
    setLocale('de')
  })

  afterEach(() => {
    setThemePreference('system')
    window.localStorage.clear()
    setLocale('de')
    Reflect.deleteProperty(window, 'matchMedia')
  })

  it('keeps one labelled selector available while profile data is pending and follows OS changes only in system mode', async () => {
    apiMock.mockReturnValue(new Promise(() => {}))
    const wrapper = mount(AccountGeneralSettingsView)
    const select = wrapper.get<HTMLSelectElement>('select[name="theme_preference"]')

    expect(wrapper.findAll('select[name="theme_preference"]')).toHaveLength(1)
    expect(select.element.labels).toHaveLength(1)
    expect(select.element.closest('form')).toBeNull()
    expect(select.element.closest('.card.form-card')).not.toBeNull()
    expect(select.element.value).toBe('system')
    await vi.waitFor(() => expect(apiMock).toHaveBeenCalledWith('/settings/profile'))
    media.dispatch(true)

    expect(themePreference.value).toBe('system')
    expect(select.element.value).toBe('system')
    expect(effectiveTheme.value).toBe('dark')

    await select.setValue('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(window.localStorage.getItem('calograph.theme')).toBe('light')
    expect(apiMock).not.toHaveBeenCalledWith('/settings/profile', expect.objectContaining({ method: 'PUT' }))
    wrapper.unmount()
  })

  it('keeps the local appearance selector usable after a rejected profile request', async () => {
    apiMock.mockRejectedValueOnce(new Error('profile unavailable'))
    const wrapper = mount(AccountGeneralSettingsView)
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('Allgemeine Einstellungen konnten nicht geladen werden.')
    expect(wrapper.get<HTMLSelectElement>('select[name="theme_preference"]').element.value).toBe('system')
    await wrapper.get<HTMLSelectElement>('select[name="theme_preference"]').setValue('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('calograph.theme')).toBe('dark')
    wrapper.unmount()
  })

  it('keeps appearance available during profile save without adding it to the profile payload', async () => {
    let resolveSave!: (value: User) => void
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/profile' && options?.method === 'PUT') {
        return new Promise<User>((resolve) => { resolveSave = resolve })
      }
      return Promise.resolve({ ...profile })
    })
    const wrapper = mount(AccountGeneralSettingsView)
    await flushPromises()
    await wrapper.get<HTMLSelectElement>('select[name="language"]').setValue('en')
    void wrapper.get('form').trigger('submit')
    await vi.waitFor(() => expect(resolveSave).toBeTypeOf('function'))

    const themeSelect = wrapper.get<HTMLSelectElement>('select[name="theme_preference"]')
    expect(themeSelect.element.disabled).toBe(false)
    await themeSelect.setValue('dark')
    expect(window.localStorage.getItem('calograph.theme')).toBe('dark')
    expect(apiMock).toHaveBeenCalledWith('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({
        language: 'en',
        timezone: 'Europe/Berlin',
        week_starts_on: 0,
        preferred_weight_unit: 'kg',
        raw_payload_retention_days: 30,
        highlight_over_budget: false,
      }),
    })
    expect(JSON.stringify(JSON.parse(String(apiMock.mock.calls.find(([, options]) => options?.method === 'PUT')?.[1].body))))
      .not.toContain('theme_preference')
    resolveSave({ ...profile, language: 'en' })
    await flushPromises()
    wrapper.unmount()
  })

  it('places general settings before appearance in the shared account stack', async () => {
    const wrapper = mount(AccountGeneralSettingsView)
    await flushPromises()

    expect(wrapper.findAll('.account-page-stack')).toHaveLength(1)
    const stack = wrapper.get('.account-page-stack')
    const settingsCard = wrapper.get('.account-form-card').element
    const appearanceCard = wrapper.get('.account-appearance-card').element

    expect([...stack.element.children]).toEqual([settingsCard, appearanceCard])
    wrapper.unmount()
  })
})
