import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))

vi.mock('../src/api', () => ({
  api: apiMock,
  ApiTransportError: class ApiTransportError extends Error {},
}))

import AccountAIProvidersView from '../src/views/AccountAIProvidersView.vue'
import type { ProviderConnectionView } from '../src/generated/ai-contracts'
import { setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const privacyVersion = 'connection-metadata-2026-11-13'
const connection = {
  id: 'connection-1', label: 'Local AI server', provider_kind: 'local', model_id: 'model-one', base_url: 'http://synthetic-provider:8081/v1',
  enabled: true, timeout_seconds: 30, configuration_version: 2, has_credential: true,
  created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
} satisfies ProviderConnectionView
const catalog = {
  providers: [
    { kind: 'openai_api', availability: 'available', authentication: 'api_key', custom_endpoint: false, model_discovery: true, manual_model: true },
    { kind: 'local', availability: 'available', authentication: 'optional_api_key', custom_endpoint: true, model_discovery: true, manual_model: true },
    { kind: 'chatgpt_plan', availability: 'planned', authentication: 'chatgpt_oauth', custom_endpoint: false, model_discovery: false, manual_model: false },
  ], privacy_version: privacyVersion,
}
const mountedWrappers: Array<{ unmount: () => void }> = []

beforeEach(() => {
  apiMock.mockReset()
  setActivePinia(createPinia())
  useAuthStore().user = {
    id: 'owner-1', username: 'owner', language: 'en', timezone: 'UTC', week_starts_on: 0,
    raw_payload_retention_days: 30, is_admin: false, is_active: true, deactivated_at: null,
  }
  setLocale('en')
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path === '/settings/ai/providers') return catalog
    if (path === '/settings/ai/connections' && !options) return { items: [connection], next_after: null }
    if (path === '/settings/ai/preferences' && !options) return { active_connection_id: null }
    if (path.endsWith('/checks/latest')) return { id: 'check-1', configuration_version: 2, models: ['model-one', 'model-two'], status: 'available', models_truncated: false }
    if (path === '/settings/ai/routing' && !options) return { configuration_version: 1, entries: [{ connection_id: connection.id, position: 0, enabled: true, provider_kind: 'local', label: 'Local AI server', model_id: 'model-one', ready: true }], primary_connection_id: connection.id }
    if (path.startsWith('/settings/ai/consents?')) return { items: [], next_after: null }
    if (path === '/settings/ai/routing' && options?.method === 'PUT') {
      const body = JSON.parse(String(options.body)) as { entries: Array<{ connection_id: string; enabled: boolean }> }
      return { configuration_version: 2, entries: body.entries.map((entry, position) => ({ ...entry, position, provider_kind: 'local', label: 'Local AI server', model_id: 'model-one', ready: true })), primary_connection_id: body.entries.find(entry => entry.enabled)?.connection_id ?? null }
    }
    if (path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT') {
      const body = JSON.parse(String(options.body)) as Record<string, unknown>
      return { ...connection, ...body, has_credential: Object.hasOwn(body, 'api_key') ? body.api_key !== null : connection.has_credential, configuration_version: connection.configuration_version + 1 }
    }
    throw new Error(`Unexpected request: ${path}`)
  })
})

async function mountLoaded() {
  const wrapper = mount(AccountAIProvidersView)
  mountedWrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('AI provider account settings', () => {
  it('autosaves a selected model without resubmitting credentials', async () => {
    const wrapper = await mountLoaded()
    expect(wrapper.find('select[name="ai-provider"] option[value="chatgpt_plan"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Connecting a ChatGPT plan is planned for v1')
    expect(wrapper.find('input[name="ai-key"]').exists()).toBe(false)

    await wrapper.get('select[name="ai-model"]').setValue('model-two')
    await flushPromises()
    const write = apiMock.mock.calls.find(([path, options]) => path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT')
    expect(write).toBeTruthy()
    const payload = JSON.parse(String(write?.[1]?.body)) as Record<string, unknown>
    expect(payload.model_id).toBe('model-two')
    expect(payload).not.toHaveProperty('api_key')
    expect(wrapper.find('button[type="submit"]').exists()).toBe(false)
  })

  it('does not submit a pending replacement key when the model changes', async () => {
    const wrapper = await mountLoaded()
    const changeKey = wrapper.findAll('button').find(button => button.text().trim() === 'Change')
    expect(changeKey).toBeTruthy()
    await changeKey!.trigger('click')
    await wrapper.get('input[name="ai-key"]').setValue('synthetic-replacement-key')
    await wrapper.get('select[name="ai-model"]').setValue('__manual__')
    await wrapper.get('input[name="ai-manual-model"]').setValue('model-two')
    await flushPromises()
    expect(apiMock.mock.calls.filter(([path, options]) => path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT')).toHaveLength(0)
    expect(wrapper.get<HTMLInputElement>('input[name="ai-key"]').element.value).toBe('synthetic-replacement-key')
  })

  it('clears a credential draft when the authenticated account changes', async () => {
    const wrapper = await mountLoaded()
    await wrapper.findAll('button').find(button => button.text().trim() === 'Change')!.trigger('click')
    await wrapper.get('input[name="ai-key"]').setValue('synthetic-key')
    useAuthStore().user = { ...useAuthStore().user!, id: 'owner-2' }
    await flushPromises()
    expect(wrapper.find('input[name="ai-key"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('The session changed. Please reload this page.')
  })

  it('does not start model discovery when the account changes during a connection save', async () => {
    let resolveWrite: ((value: ProviderConnectionView) => void) | undefined
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/ai/providers') return catalog
      if (path === '/settings/ai/connections' && !options) return { items: [connection], next_after: null }
      if (path === '/settings/ai/preferences' && !options) return { active_connection_id: null }
      if (path.endsWith('/checks/latest')) return { id: 'check-1', configuration_version: 2, models: ['model-one'], status: 'available', models_truncated: false }
      if (path === '/settings/ai/routing' && !options) return { configuration_version: 1, entries: [], primary_connection_id: null }
      if (path.startsWith('/settings/ai/consents?')) return { items: [], next_after: null }
      if (path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT') return new Promise<ProviderConnectionView>(resolve => { resolveWrite = resolve })
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = await mountLoaded()
    await wrapper.get('input[name="ai-test-consent"]').setValue(true)
    const connect = wrapper.findAll('button').find(button => button.text().trim() === 'Save access and load models')
    await connect?.trigger('click')
    await flushPromises()
    expect(resolveWrite).toBeTruthy()
    useAuthStore().user = { ...useAuthStore().user!, id: 'owner-2' }
    resolveWrite?.(connection)
    await flushPromises()
    expect(apiMock.mock.calls.some(([path, options]) => /\/checks\/[^/]+$/.test(path) && options?.method === 'PUT')).toBe(false)
    expect(wrapper.text()).toContain('The session changed. Please reload this page.')
  })

  it('requires a replacement key before changing a credential-bound endpoint', async () => {
    const wrapper = await mountLoaded()
    await wrapper.get('input[name="ai-endpoint"]').setValue('http://another-synthetic-provider:8081/v1')
    expect(wrapper.text()).toContain('Replace or remove the saved key before using another server.')
    await wrapper.get('input[name="ai-test-consent"]').setValue(true)
    const connectButton = wrapper.findAll('button').find(button => button.text().trim() === 'Save access and load models')
    await connectButton?.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Replace or remove the saved key before using another server.')
    expect(apiMock.mock.calls.filter(([path, options]) => path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT')).toHaveLength(0)
  })

  it('removes a saved key only after the explicit action and disables the connection', async () => {
    const wrapper = await mountLoaded()
    await wrapper.findAll('button').find(button => button.text().trim() === 'Remove')!.trigger('click')
    const removeButton = wrapper.findAll('button').find(button => button.text().trim() === 'Remove key and disable connection')
    expect(removeButton).toBeTruthy()
    await removeButton!.trigger('click')
    await flushPromises()
    const write = apiMock.mock.calls.find(([path, options]) => path === `/settings/ai/connections/${connection.id}` && options?.method === 'PUT')
    expect(write).toBeTruthy()
    const payload = JSON.parse(String(write?.[1]?.body)) as Record<string, unknown>
    expect(payload.api_key).toBeNull()
    expect(payload.enabled).toBe(false)
    expect(wrapper.get<HTMLInputElement>('input[name="ai-key"]').element.value).toBe('')
  })
})
afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  vi.useRealTimers()
})
