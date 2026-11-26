import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock, transportError } = vi.hoisted(() => ({
  apiMock: vi.fn(),
  transportError: class ApiTransportError extends Error {},
}))
vi.mock('../src/api', () => ({ api: apiMock }))

import AIRoutingSettingsCard from '../src/components/AIRoutingSettingsCard.vue'
import type { ProviderConnectionView } from '../src/generated/ai-contracts'
import { setLocale } from '../src/i18n'

const connections = [
  { id: 'connection-a', label: 'Local', provider_kind: 'local', model_id: 'model-a', base_url: 'http://synthetic/v1', enabled: true, timeout_seconds: 30, configuration_version: 2, has_credential: true, created_at: '', updated_at: '' },
  { id: 'connection-b', label: 'Gateway', provider_kind: 'gateway', model_id: 'model-b', base_url: 'http://synthetic/v1', enabled: true, timeout_seconds: 30, configuration_version: 2, has_credential: true, created_at: '', updated_at: '' },
] as const satisfies readonly ProviderConnectionView[]

function makeView(version: number, entries: Array<{ connection_id: string; enabled: boolean }>) {
  return {
    configuration_version: version,
    primary_connection_id: entries.find(item => item.enabled)?.connection_id ?? null,
    entries: entries.map((entry, position) => ({
      ...entry, position, provider_kind: entry.connection_id === 'connection-a' ? 'local' : 'gateway',
      label: entry.connection_id === 'connection-a' ? 'Local' : 'Gateway', model_id: entry.connection_id, ready: true,
    })),
  }
}

const mountedWrappers: Array<{ unmount: () => void }> = []
beforeEach(() => { apiMock.mockReset(); setLocale('en') })
afterEach(() => { for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount(); vi.useRealTimers() })

describe('AIRoutingSettingsCard', () => {
  it('autosaves order and reconciles a persisted write whose response was lost', async () => {
    let persisted = makeView(3, [
      { connection_id: 'connection-a', enabled: true },
      { connection_id: 'connection-b', enabled: false },
    ])
    let loseResponse = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path !== '/settings/ai/routing') throw new Error(`Unexpected API request: ${path}`)
      if (options?.method === 'PUT') {
        const body = JSON.parse(String(options.body)) as { entries: Array<{ connection_id: string; enabled: boolean }> }
        persisted = makeView(persisted.configuration_version + 1, body.entries)
        if (loseResponse) { loseResponse = false; throw new transportError() }
      }
      return persisted
    })
    const wrapper = mount(AIRoutingSettingsCard, { props: { connections } })
    mountedWrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('button[aria-label="Move Local AI server down"]').trigger('click')
    await flushPromises()
    expect(persisted.entries.map(item => item.connection_id)).toEqual(['connection-b', 'connection-a'])
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Order saved')
    expect(apiMock.mock.calls.filter(([, options]) => options?.method === 'PUT')).toHaveLength(1)
    expect(apiMock.mock.calls.filter(([, options]) => options?.method !== 'PUT')).toHaveLength(2)
  })
})
