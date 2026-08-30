import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock, ApiErrorMock } = vi.hoisted(() => {
  class MockApiError extends Error {}
  return { apiMock: vi.fn(), ApiErrorMock: MockApiError }
})

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: ApiErrorMock,
  localizeApiError: () => 'The request could not be processed.',
}))

import AccountPersonalDataView from '../src/views/AccountPersonalDataView.vue'
import { isoDateInTimeZone } from '../src/date-format'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const personalProfile = {
  display_name: 'Alex',
  gender: 'non_binary',
  birth_date: '1990-04-12',
  height_cm: 172.5,
  diet_type: 'pescetarian',
  health_notes: 'Prefers evening meals.',
  intolerances: 'Hazelnuts',
} as const

function mockProfileApi() {
  apiMock.mockImplementation((path: string, options?: RequestInit) => {
    if (path === '/settings/personal-profile' && options?.method === 'PUT') {
      return Promise.resolve(JSON.parse(String(options.body)))
    }
    return Promise.resolve({ ...personalProfile })
  })
}

describe('AccountPersonalDataView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setActivePinia(createPinia())
    useAuthStore().user = {
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
    setLocale(DEFAULT_LOCALE)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads all personal fields, exposes exact enums, and fully replaces empty values with null', async () => {
    mockProfileApi()
    const wrapper = mount(AccountPersonalDataView)
    await flushPromises()

    expect(wrapper.find('input[name="display_name"]').element).toBeTruthy()
    expect(wrapper.find('select[name="gender"]').element).toBeTruthy()
    expect(wrapper.find('input[name="birth_date"]').element).toBeTruthy()
    expect(wrapper.find('input[name="height_cm"]').element).toBeTruthy()
    expect(wrapper.find('select[name="diet_type"]').element).toBeTruthy()
    expect(wrapper.find('textarea[name="health_notes"]').element).toBeTruthy()
    expect(wrapper.find('textarea[name="intolerances"]').element).toBeTruthy()

    expect(wrapper.findAll<HTMLSelectElement>('select[name="gender"] option').map((option) => option.element.value)).toEqual([
      '', 'female', 'male', 'non_binary', 'other', 'prefer_not_to_say',
    ])
    expect(wrapper.findAll<HTMLSelectElement>('select[name="diet_type"] option').map((option) => option.element.value)).toEqual([
      '', 'no_special_diet', 'vegetarian', 'vegan', 'pescetarian', 'other', 'prefer_not_to_say',
    ])

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenLastCalledWith('/settings/personal-profile', {
      method: 'PUT',
      body: JSON.stringify(personalProfile),
    })
    expect(wrapper.text()).toContain('Persönliche Daten gespeichert.')

    await wrapper.get('input[name="display_name"]').setValue('   ')
    await wrapper.get('select[name="gender"]').setValue('')
    await wrapper.get('input[name="birth_date"]').setValue('')
    await wrapper.get('input[name="height_cm"]').setValue('')
    await wrapper.get('select[name="diet_type"]').setValue('')
    await wrapper.get('textarea[name="health_notes"]').setValue('   ')
    await wrapper.get('textarea[name="intolerances"]').setValue('   ')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/personal-profile', {
      method: 'PUT',
      body: JSON.stringify({
        display_name: null,
        gender: null,
        birth_date: null,
        height_cm: null,
        diet_type: null,
        health_notes: null,
        intolerances: null,
      }),
    })
    wrapper.unmount()
  })

  it('sets browser constraints for birthday and height and rejects invalid values before PUT', async () => {
    mockProfileApi()
    const wrapper = mount(AccountPersonalDataView)
    await flushPromises()

    const birthday = wrapper.get<HTMLInputElement>('input[name="birth_date"]')
    const height = wrapper.get<HTMLInputElement>('input[name="height_cm"]')
    expect(birthday.attributes('max')).toBe(isoDateInTimeZone('Europe/Berlin'))
    expect(height.attributes('min')).toBe('0.01')
    expect(height.attributes('max')).toBe('300')

    await birthday.setValue('31.12.2999')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Das Geburtsdatum darf nicht in der Zukunft liegen.')

    await birthday.setValue('12.04.1990')
    await height.setValue('0')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('Die Größe muss über 0 und darf höchstens 300 cm sein.')

    await height.setValue('301')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiMock.mock.calls.filter(([path]) => path === '/settings/personal-profile').length).toBe(1)
    wrapper.unmount()
  })

  it('uses the account timezone and refreshes the birthday boundary after midnight', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-01T10:59:30Z'))
    useAuthStore().user = { ...useAuthStore().user!, timezone: 'Pacific/Auckland' }
    mockProfileApi()

    const wrapper = mount(AccountPersonalDataView)
    await flushPromises()
    const birthday = wrapper.get<HTMLInputElement>('input[name="birth_date"]')
    expect(birthday.attributes('max')).toBe('2026-01-01')

    await vi.advanceTimersByTimeAsync(60_000)
    expect(birthday.attributes('max')).toBe('2026-01-02')

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('localizes API failures without leaking details and supports retry', async () => {
    const secret = new ApiErrorMock('database password leaked')
    apiMock
      .mockRejectedValueOnce(secret)
      .mockResolvedValueOnce({ ...personalProfile })

    const wrapper = mount(AccountPersonalDataView)
    await flushPromises()
    expect(wrapper.get('[role="alert"] p').text()).toBe('The request could not be processed.')
    expect(wrapper.text()).not.toContain('database password leaked')
    expect(wrapper.get('button').text()).toBe('Erneut versuchen')

    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.get('input[name="display_name"]').element).toBeTruthy()
    expect(apiMock).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('disables all personal controls while a replacement is pending', async () => {
    let resolveSave!: (value: typeof personalProfile) => void
    mockProfileApi()
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/personal-profile' && options?.method === 'PUT') {
        return new Promise<typeof personalProfile>((resolve) => { resolveSave = resolve })
      }
      return Promise.resolve({ ...personalProfile })
    })

    const wrapper = mount(AccountPersonalDataView)
    await flushPromises()
    const submit = wrapper.get<HTMLButtonElement>('button[type="submit"]')
    const pending = wrapper.get('form').trigger('submit')
    await Promise.resolve()
    expect(submit.element.disabled).toBe(true)
    expect(wrapper.findAll('input, select, textarea').every((control) => (control.element as HTMLInputElement).disabled)).toBe(true)

    resolveSave(personalProfile)
    await pending
    await flushPromises()
    expect(submit.element.disabled).toBe(false)
    expect(wrapper.text()).toContain('Persönliche Daten gespeichert.')
    wrapper.unmount()
  })
})
