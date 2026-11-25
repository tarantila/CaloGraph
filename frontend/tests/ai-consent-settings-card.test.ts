import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import AIConsentSettingsCard from '../src/components/AIConsentSettingsCard.vue'
import type { AIConsentView, ProviderConnectionView } from '../src/generated/ai-contracts'
import { setLocale } from '../src/i18n'

const connection = {
  id: 'connection-1', label: 'Local', provider_kind: 'local', model_id: 'synthetic-model', base_url: 'http://synthetic-provider:8081/v1',
  enabled: true, timeout_seconds: 30, configuration_version: 2, has_credential: true,
  created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z',
} satisfies ProviderConnectionView

function grant(id: string): AIConsentView {
  return {
    id, connection_id: connection.id, configuration_version: 2, provider_kind: 'local', model_id: 'synthetic-model',
    base_url: connection.base_url, purpose: id === 'consent-1' ? 'nutrition_chat' : 'nutrition_check_in',
    data_domains: ['nutrition'] as const, include_meal_names: false, include_attachments: false,
    history: { mode: 'all_available', start: null, end: null }, policy_version: 'expert-data-2026-11-14',
    state: 'active', created_at: '2026-11-13T00:00:00Z', revoked_at: null,
  }
}

function page(items: ReturnType<typeof grant>[], after: string | null) {
  return { items, next_after: after }
}
const mountedWrappers: Array<{ unmount: () => void }> = []

beforeEach(() => {
  setLocale('en')
  apiMock.mockReset()
})
afterEach(() => {
  for (const wrapper of mountedWrappers.splice(0)) wrapper.unmount()
  vi.useRealTimers()
})

describe('AIConsentSettingsCard', () => {
  it('paginates all grants, revokes each immutable consent ID, then verifies no active grants remain', async () => {
    const grants = [grant('consent-1'), grant('consent-2')]
    const deleted: string[] = []
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/consents?')) {
        const after = new URLSearchParams(path.split('?')[1]).get('after')
        return after ? page(grants.slice(1), null) : page(grants.slice(0, 1), 'consent-1')
      }
      const revoke = path.match(/^\/settings\/ai\/consents\/([^/]+)$/)
      if (revoke && options?.method === 'DELETE') {
        deleted.push(revoke[1])
        const item = grants.find(item => item.id === revoke[1])
        if (item) item.revoked_at = '2026-11-13T00:00:00Z'
        return undefined
      }
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = mount(AIConsentSettingsCard, { props: { connections: [connection], enabledConnectionIds: [connection.id] } })
    mountedWrappers.push(wrapper)
    await flushPromises()
    const revokeButton = wrapper.findAll('button').find(button => button.text().trim() === 'Revoke permissions')
    expect(revokeButton).toBeTruthy()
    await revokeButton!.trigger('click')
    await flushPromises()
    expect(deleted).toEqual(['consent-1', 'consent-2'])
    expect(wrapper.text()).toContain('Permissions revoked. New requests require your consent.')
    expect(wrapper.findAll('button').some(button => button.text().trim() === 'Revoke permissions')).toBe(false)
  })

  it('keeps remaining grants visible after a partial revoke failure', async () => {
    const grants = [grant('consent-1'), grant('consent-2')]
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/consents?')) return page(grants, null)
      const revoke = path.match(/^\/settings\/ai\/consents\/([^/]+)$/)
      if (revoke && options?.method === 'DELETE') {
        if (revoke[1] === 'consent-1') throw new Error('synthetic revoke failure')
        const item = grants.find(item => item.id === revoke[1])
        if (item) item.revoked_at = '2026-11-13T00:00:00Z'
        return undefined
      }
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = mount(AIConsentSettingsCard, { props: { connections: [connection], enabledConnectionIds: [connection.id] } })
    mountedWrappers.push(wrapper)
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text().trim() === 'Revoke permissions')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Permissions could not be revoked. Try again.')
    expect(wrapper.findAll('button').some(button => button.text().trim() === 'Revoke permissions')).toBe(true)
    expect(wrapper.text()).not.toContain('No active AI permissions.')
  })
})
