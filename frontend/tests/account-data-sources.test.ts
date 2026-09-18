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

const availabilityByArea = {
  nutrition: [
    { provider_key: 'apple_health', available: false, status: 'no_data' },
    { provider_key: 'google_health', available: true, status: 'available' },
    { provider_key: 'yazio', available: true, status: 'available' },
  ],
  weight: [
    { provider_key: 'apple_health', available: true, status: 'available' },
    { provider_key: 'health_auto_export', available: false, status: 'no_data' },
    { provider_key: 'yazio', available: true, status: 'available' },
  ],
  activity_energy: [
    { provider_key: 'apple_health', available: false, status: 'no_data' },
    { provider_key: 'health_auto_export', available: true, status: 'available' },
    { provider_key: 'yazio', available: true, status: 'available' },
  ],
} as const

describe('AccountDataSourcesView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setLocale(DEFAULT_LOCALE)
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/provider-preferences/') && options?.method === 'PUT') {
        return Promise.resolve({ data_area: path.split('/').at(-1), provider_key: 'yazio' })
      }
      if (path.startsWith('/settings/provider-preferences/') && options?.method === 'DELETE') {
        return Promise.resolve(undefined)
      }
      if (path === '/settings/provider-preferences') {
        return Promise.resolve({
          preferences: [
            { data_area: 'nutrition', provider_key: 'apple_health' },
            { data_area: 'nutrition', provider_key: 'yazio' },
            { data_area: 'weight', provider_key: 'yazio' },
          ],
        })
      }
      const area = path.split('/').at(-1) as keyof typeof availabilityByArea
      return Promise.resolve({ data_area: area, providers: availabilityByArea[area] })
    })
  })

  it('renders persisted provider order and keeps unavailable entries visible with status badges', async () => {
    const wrapper = mount(AccountDataSourcesView)
    await flushPromises()

    const nutritionRows = wrapper.findAll('[data-area="nutrition"] .provider-priority-row')
    expect(nutritionRows).toHaveLength(2)
    expect(nutritionRows[0].attributes('data-provider-key')).toBe('apple_health')
    expect(nutritionRows[1].attributes('data-provider-key')).toBe('yazio')
    expect(nutritionRows[0].text()).toContain('Apple Health')
    expect(nutritionRows[0].text()).toContain('noch keine Daten')
    expect(nutritionRows[0].find('button[aria-label*="entfernen"]').exists()).toBe(true)
  })

  it('supports keyboard-accessible reorder, add, remove, and complete-list save per area', async () => {
    const wrapper = mount(AccountDataSourcesView)
    await flushPromises()

    const nutrition = wrapper.get('[data-area="nutrition"]')
    await nutrition.find('.provider-priority-row:nth-child(2) button[aria-label*="nach oben"]').trigger('click')
    await nutrition.get('select[name="nutrition-add-provider"]').setValue('google_health')
    await nutrition.get('button[aria-label="Provider hinzufügen"]').trigger('click')
    await nutrition.find('.provider-priority-row[data-provider-key="apple_health"] button[aria-label*="entfernen"]').trigger('click')
    await nutrition.get('form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/provider-preferences/nutrition', {
      method: 'PUT',
      body: JSON.stringify({ provider_keys: ['yazio', 'google_health'] }),
    })
    expect(wrapper.text()).toContain('Prioritäten gespeichert.')

    await wrapper.get('[data-area="activity_energy"] form').trigger('submit')
    await flushPromises()
    expect(apiMock).toHaveBeenLastCalledWith('/settings/provider-preferences/activity_energy', {
      method: 'DELETE',
    })
  })

  it('saves an empty area independently with DELETE', async () => {
    apiMock.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/settings/provider-preferences') return Promise.resolve({ preferences: [] })
      if (path.startsWith('/settings/provider-preferences/') && options?.method === 'DELETE') return Promise.resolve(undefined)
      const area = path.split('/').at(-1) as keyof typeof availabilityByArea
      return Promise.resolve({ data_area: area, providers: availabilityByArea[area] })
    })

    const wrapper = mount(AccountDataSourcesView)
    await flushPromises()
    await wrapper.get('[data-area="nutrition"] form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenLastCalledWith('/settings/provider-preferences/nutrition', { method: 'DELETE' })
  })
})
