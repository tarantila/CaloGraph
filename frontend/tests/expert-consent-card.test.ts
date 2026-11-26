import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import ExpertConsentCard from '../src/components/ExpertConsentCard.vue'
import { setLocale } from '../src/i18n'
import { setAuthenticatedIdentity } from '../src/auth-session'
import { useAuthStore } from '../src/stores/auth'

type ConsentFixture = {
  id: string
  connection_id: string
  configuration_version: number
  provider_kind: 'chatgpt_plan' | 'local' | 'gateway'
  model_id: string
  purpose: 'nutrition_chat' | 'nutrition_check_in'
  data_domains: string[]
  include_meal_names: boolean
  history: { mode: 'all_available'; start: null; end: null }
  policy_version: string
  state: 'active' | 'connection_disabled' | 'configuration_changed' | 'revoked'
  created_at: string
  revoked_at: string | null
}

function grant(id: string, connectionId: string, provider: ConsentFixture['provider_kind'], purpose: ConsentFixture['purpose'] = 'nutrition_chat', revoked = false): ConsentFixture {
  return {
    id, connection_id: connectionId, configuration_version: 1, provider_kind: provider,
    model_id: `synthetic-${provider}-model`, purpose, data_domains: ['nutrition'], include_meal_names: false,
    history: { mode: 'all_available', start: null, end: null }, policy_version: 'synthetic-policy',
    state: revoked ? 'revoked' : provider === 'chatgpt_plan' ? 'active' : 'connection_disabled',
    created_at: '2026-11-25T00:00:00Z', revoked_at: revoked ? '2026-11-26T00:00:00Z' : null,
  }
}

let wrappers: VueWrapper[] = []
let grants: ConsentFixture[] = []
let emptyList = false
let splitIntoThreePages = true

function page(after: string | null) {
  if (emptyList) return { items: [], next_after: null }
  if (!after) return { items: grants.slice(0, 2), next_after: splitIntoThreePages ? 'cursor-1' : null }
  if (after === 'cursor-1') return { items: grants.slice(2, 4), next_after: splitIntoThreePages ? 'cursor-2' : null }
  return { items: grants.slice(4), next_after: null }
}

function defaultApi(): void {
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    if (path.startsWith('/settings/ai/consents?')) {
      return page(new URLSearchParams(path.split('?')[1]).get('after'))
    }
    if (path.startsWith('/settings/ai/consents/') && options?.method === 'DELETE') {
      const id = path.split('/').at(-1)
      grants = grants.map(item => item.id === id ? { ...item, revoked_at: '2026-11-26T00:00:00Z', state: 'revoked' } : item)
      return undefined
    }
    throw new Error(`Unexpected API request: ${path}`)
  })
}

async function mounted(paused = false): Promise<VueWrapper> {
  const wrapper = mount(ExpertConsentCard, { props: { paused }, attachTo: document.body })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('Nutrition Expert account-wide consent revocation', () => {
  beforeEach(() => {
    setLocale('de')
    setActivePinia(createPinia())
    useAuthStore().user = {
      id: 'owner-a', username: 'synthetic-owner', language: 'de', timezone: 'UTC', week_starts_on: 0,
      raw_payload_retention_days: 30, is_admin: false, is_active: true, deactivated_at: null,
    }
    setAuthenticatedIdentity('owner-a')
    grants = [
      grant('chatgpt-chat', 'chatgpt-connection', 'chatgpt_plan'),
      grant('chatgpt-check-in', 'chatgpt-connection', 'chatgpt_plan', 'nutrition_check_in'),
      grant('disabled-local-chat', 'local-connection', 'local'),
      grant('disabled-gateway-check-in', 'gateway-connection', 'gateway', 'nutrition_check_in'),
      grant('already-revoked', 'old-connection', 'local', 'nutrition_chat', true),
    ]
    emptyList = false
    splitIntoThreePages = true
    apiMock.mockReset()
    defaultApi()
  })

  afterEach(() => {
    wrappers.forEach(wrapper => wrapper.unmount())
    wrappers = []
    setAuthenticatedIdentity(null)
    vi.restoreAllMocks()
  })

  it('revokes every unrevoked purpose across paginated providers without granting consent', async () => {
    const wrapper = await mounted()
    expect(wrapper.text()).not.toContain('ChatGPT')
    expect(wrapper.text()).not.toContain('synthetic-')
    const revokeButton = wrapper.get('button.compact-action')
    expect(revokeButton.text()).toBe('KI-Einwilligung widerrufen')

    await revokeButton.trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Alle KI-Einwilligungen wurden widerrufen'))

    const consentReads = apiMock.mock.calls.filter(([path]) => String(path).startsWith('/settings/ai/consents?'))
    expect(consentReads.some(([path]) => !String(path).includes('connection_id='))).toBe(true)
    const deletions = apiMock.mock.calls.filter(([path, options]) => String(path).startsWith('/settings/ai/consents/') && options?.method === 'DELETE')
    expect(deletions.map(([path]) => path).sort()).toEqual([
      '/settings/ai/consents/chatgpt-chat',
      '/settings/ai/consents/chatgpt-check-in',
      '/settings/ai/consents/disabled-gateway-check-in',
      '/settings/ai/consents/disabled-local-chat',
    ])
    expect(new Set(deletions.map(([path]) => path)).size).toBe(4)
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(0)
    expect(grants.find(item => item.id === 'already-revoked')?.revoked_at).not.toBeNull()
    expect(apiMock.mock.calls.some(([path, options]) => String(path).startsWith('/settings/ai/consents/') && options?.method === 'PUT')).toBe(false)
    expect(wrapper.find('button.compact-action').exists()).toBe(false)
  })

  it('shows an empty state without a repeated revoke action when no grants remain', async () => {
    emptyList = true
    grants = []
    const wrapper = await mounted()
    expect(wrapper.text()).toContain('Es sind keine aktiven Datenfreigaben vorhanden.')
    expect(wrapper.find('button.compact-action').exists()).toBe(false)
    expect(apiMock.mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(0)
  })

  it('keeps one retry action after partial failure and confirms only after rereading zero grants', async () => {
    let failOnce = true
    const defaultImplementation = apiMock.getMockImplementation()!
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/ai/consents/disabled-local-chat' && options?.method === 'DELETE' && failOnce) {
        failOnce = false
        throw new Error('synthetic partial revoke failure')
      }
      return defaultImplementation(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Nicht alle Datenfreigaben konnten widerrufen werden'))
    expect(wrapper.text()).not.toContain('Alle KI-Einwilligungen wurden widerrufen')
    expect(wrapper.findAll('button.compact-action')).toHaveLength(1)
    expect(wrapper.get('button.compact-action').text()).toBe('KI-Einwilligung widerrufen')
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(1)

    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Alle KI-Einwilligungen wurden widerrufen'))
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(0)
  })

  it('reconciles a lost DELETE response as success when rereading confirms revocation', async () => {
    const defaultImplementation = apiMock.getMockImplementation()!
    let loseResponse = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path === '/settings/ai/consents/chatgpt-chat' && options?.method === 'DELETE' && loseResponse) {
        loseResponse = false
        await defaultImplementation(path, options)
        throw new Error('synthetic lost response')
      }
      return defaultImplementation(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Alle KI-Einwilligungen wurden widerrufen'))
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(0)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('does not report success when verification cannot reread the permissions, and allows retry', async () => {
    const defaultImplementation = apiMock.getMockImplementation()!
    let firstPagesRead = 0
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/consents?') && !new URLSearchParams(path.split('?')[1]).has('after')) {
        firstPagesRead += 1
        if (firstPagesRead === 3) throw new Error('synthetic verification read failure')
      }
      return defaultImplementation(path, options)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Nicht alle Datenfreigaben konnten widerrufen werden'))
    expect(wrapper.text()).not.toContain('Alle KI-Einwilligungen wurden widerrufen')
    expect(wrapper.findAll('button.compact-action')).toHaveLength(1)
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(0)

    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('Alle KI-Einwilligungen wurden widerrufen'))
    expect(grants.filter(item => item.revoked_at === null)).toHaveLength(0)
  })

  it.each(['account switch', 'unmount'] as const)('stops later deletions after a pending request and %s', async action => {
    let finishDelete!: () => void
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/consents?')) return page(new URLSearchParams(path.split('?')[1]).get('after'))
      if (path === '/settings/ai/consents/chatgpt-chat' && options?.method === 'DELETE') {
        return new Promise<void>(resolve => { finishDelete = resolve })
      }
      throw new Error(`Unexpected API request: ${path}`)
    })
    const wrapper = await mounted()
    await wrapper.get('button.compact-action').trigger('click')
    await vi.waitFor(() => expect(apiMock).toHaveBeenCalledWith('/settings/ai/consents/chatgpt-chat', expect.objectContaining({ method: 'DELETE' })))

    if (action === 'account switch') {
      setAuthenticatedIdentity('owner-b')
      useAuthStore().user = { ...useAuthStore().user!, id: 'owner-b' }
      await flushPromises()
      expect(wrapper.find('button.compact-action').exists()).toBe(false)
      expect(wrapper.find('[role="alert"]').text()).toContain('Die Sitzung wurde geändert')
    } else {
      wrapper.unmount()
      wrappers = []
    }

    finishDelete()
    await flushPromises()
    expect(apiMock.mock.calls.filter(([, options]) => options?.method === 'DELETE').map(([path]) => path)).toEqual(['/settings/ai/consents/chatgpt-chat'])
  })

  it('loads permissions after an initially paused parent resumes', async () => {
    const wrapper = await mounted(true)
    expect(apiMock).not.toHaveBeenCalled()
    await wrapper.setProps({ paused: false })
    await flushPromises()
    expect(wrapper.get('button.compact-action').text()).toBe('KI-Einwilligung widerrufen')
    expect(wrapper.get<HTMLButtonElement>('button.compact-action').element.disabled).toBe(false)
    expect(apiMock.mock.calls.filter(([path]) => path.startsWith('/settings/ai/consents?'))).toHaveLength(3)
  })

  it('presents one retry action if the initial all-consents read fails', async () => {
    apiMock.mockRejectedValue(new Error('synthetic consent-list failure'))
    const wrapper = await mounted()
    expect(wrapper.text()).toContain('Datenfreigaben konnten nicht geprüft werden')
    expect(wrapper.findAll('button.compact-action')).toHaveLength(1)
    expect(wrapper.get('button.compact-action').text()).toBe('Erneut versuchen')
  })
})
