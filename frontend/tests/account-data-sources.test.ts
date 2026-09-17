import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class extends Error {},
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import AccountDataSourcesView from '../src/views/AccountDataSourcesView.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

describe('AccountDataSourcesView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setLocale(DEFAULT_LOCALE)
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/provider-preferences/nutrition' && options?.method === 'PUT') {
        return Promise.resolve({ data_area: 'nutrition', provider_key: 'apple_health' })
      }
      if (path === '/settings/provider-preferences') {
        return Promise.resolve({ preferences: [{ data_area: 'nutrition', provider_key: 'yazio' }] })
      }
      return Promise.resolve({
        data_area: 'nutrition',
        providers: [
          { provider_key: 'apple_health', available: true, status: 'available' },
          { provider_key: 'google_health', available: false, status: 'not_configured' },
          { provider_key: 'yazio', available: true, status: 'available' },
        ],
      })
    })
  })

  it('loads the persisted selection and saves a changed provider without URL state', async () => {
    const wrapper = mount(AccountDataSourcesView)
    await flushPromises()

    expect(wrapper.get<HTMLSelectElement>('select[name="nutrition-provider"]').element.value).toBe('yazio')
    await wrapper.get('select[name="nutrition-provider"]').setValue('apple_health')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/provider-preferences/nutrition', {
      method: 'PUT',
      body: JSON.stringify({ provider_key: 'apple_health' }),
    })
    expect(wrapper.text()).toContain('Datenquelle gespeichert.')
  })
})
