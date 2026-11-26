import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import AIConsentGrantDialog from '../src/components/AIConsentGrantDialog.vue'
import type { ProviderConnectionView } from '../src/generated/ai-contracts'
import { setLocale } from '../src/i18n'

const connection = {
  id: 'connection-1', label: 'Local', provider_kind: 'local', model_id: 'synthetic-model', base_url: 'http://synthetic-provider:8081/v1',
  enabled: true, timeout_seconds: 30, configuration_version: 2, has_credential: true,
  created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
} satisfies ProviderConnectionView
const mountedWrappers: Array<{ unmount: () => void }> = []

afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  vi.useRealTimers()
})

describe('AIConsentGrantDialog', () => {
  beforeEach(() => {
    setLocale('en')
    apiMock.mockReset()
  })

  it('reconciles a partially saved attachment grant and includes its exact scopes', async () => {
    const grants: Array<Record<string, unknown>> = []
    const writes: Array<Record<string, unknown>> = []
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/expert') return { data_domains: ['nutrition', 'weight'], include_meal_names: true, configuration_version: 4 }
      if (path.startsWith('/settings/ai/consents?')) return { items: grants, next_after: null }
      if (path.startsWith('/settings/ai/consents/') && options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as Record<string, unknown>
        writes.push(body)
        if (body.purpose === 'nutrition_check_in' && body.include_attachments === true) throw new Error('synthetic uncertain response')
        const grant = {
          ...body, id: path.split('/').at(-1), configuration_version: 2, provider_kind: 'local', model_id: 'synthetic-model',
          state: 'active', created_at: '2026-11-13T00:00:00Z', revoked_at: null,
        }
        grants.push(grant)
        return grant
      }
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = mount(AIConsentGrantDialog, { props: { connection, paused: false } })
    mountedWrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('input[type="checkbox"]').setValue(true)
    const grantButton = wrapper.findAll('button').find(button => button.text().trim() === 'Grant permission')
    expect(grantButton).toBeTruthy()
    await grantButton!.trigger('click')
    await flushPromises()

    expect(writes).toHaveLength(4)
    expect(writes[0]).toMatchObject({ data_domains: ['nutrition', 'weight'], include_meal_names: true, include_attachments: false, purpose: 'nutrition_chat' })
    expect(writes[2]).toMatchObject({ data_domains: ['nutrition', 'weight'], include_meal_names: true, include_attachments: true, purpose: 'nutrition_chat' })
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(wrapper.text()).toContain('Permissions could not be fully confirmed.')
    expect(grants).toHaveLength(3)
  })
})
