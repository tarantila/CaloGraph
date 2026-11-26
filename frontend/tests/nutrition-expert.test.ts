import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock, ApiError: class ApiError extends Error {
  constructor(message: string, public status: number, public requestId?: string, public retryAfter?: string, public problemType?: string) { super(message) }
} }))
import { ApiError } from '../src/api'
import { setLocale } from '../src/i18n'
import type { AIConsentPut, AttachmentView, ConversationView, ExpertSetup, RunPut, RunView } from '../src/generated/ai-contracts'
import NutritionExpertView from '../src/views/NutritionExpertView.vue'
import { useAuthStore } from '../src/stores/auth'
import { setAuthenticatedIdentity } from '../src/auth-session'

const connection = { id: 'connection-1', provider_kind: 'local' as const, label: 'Synthetic provider', model_id: 'synthetic-model', base_url: 'http://synthetic.invalid/v1', enabled: true, has_credential: false, configuration_version: 3, timeout_seconds: 30, created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }
let state: ExpertSetup
let grants: Record<string, object>
let conversations: ConversationView[]
let turns: RunView[]
let delivered: RunPut[]
let lostResponse: boolean
let attachments: AttachmentView[]
let pagedHistory = false
let releaseConversationList: (() => void) | null = null
let conversationListRequestCount = 0
let nextUploadFailure: unknown
let wrappers: ReturnType<typeof mount>[]
function button(wrapper: ReturnType<typeof mount>, name: string) { return wrapper.findAll('button').find(item => item.text() === name)! }
async function mounted(query = '') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/nutrition-expert', name: 'nutrition-expert', component: { template: '<div />' } },
    { path: '/settings/providers', name: 'account-ai-providers', component: { template: '<div />' } },
    { path: '/settings/expert', name: 'account-nutrition-expert', component: { template: '<div />' } },
  ] })
  await router.push('/nutrition-expert' + query)
  setActivePinia(createPinia())
  useAuthStore().user = { id: 'synthetic-owner', username: 'synthetic-owner', language: 'en', timezone: 'Europe/Berlin', week_starts_on: 0, raw_payload_retention_days: 30, is_admin: false, is_active: true, deactivated_at: null }
  setAuthenticatedIdentity('synthetic-owner')
  const wrapper = mount(NutritionExpertView, { global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
function writes() { return apiMock.mock.calls.filter(([, options]) => options?.method === 'PUT') }
function savedRun(id: string, payload: RunPut): RunView {
  return { ...payload, id, conversation_id: conversations[0]!.id, state: 'succeeded', error_code: null, provider_kind: 'local', model_id: 'synthetic-model', created_at: '2026-11-14T01:00:00Z', dispatched_at: '2026-11-14T01:00:00Z', finished_at: '2026-11-14T01:00:01Z', coverage_start: '2020-01-01', coverage_end: '2026-11-14', answer: { text: 'Synthetic response <script>unsafe</script>', findings: [{ kind: 'observation', text: 'Grounded finding', evidence_ids: ['nutrition:day:2026-11-14'] }] } }
}
beforeEach(() => {
  setLocale('en'); wrappers = []; conversations = []; turns = []; delivered = []; grants = {}; attachments = []; pagedHistory = false; releaseConversationList = null; conversationListRequestCount = 0; nextUploadFailure = undefined; lostResponse = false
  state = { connection, generation_available: true, consent: null, preferences: { data_domains: ['nutrition', 'targets', 'weight', 'activity', 'profile', 'health_context'], include_meal_names: true, configuration_version: 1 } }
  nextUploadFailure = undefined
  apiMock.mockReset()
  apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
    const payload = typeof options?.body === 'string' ? JSON.parse(options.body) : undefined
    if (path.startsWith('/expert/setup?') || path === '/expert/setup') {
      const purpose = new URL(path, 'http://synthetic.invalid').searchParams.get('mode') === 'analysis' ? 'nutrition_check_in' : 'nutrition_chat'
      const requestedAttachments = new URL(path, 'http://synthetic.invalid').searchParams.get('include_attachments') === 'true'
      const saved = grants[purpose] as { include_attachments?: boolean } | undefined
      return { ...state, consent: saved && (!requestedAttachments || saved.include_attachments) ? saved : null }
    }
    if (path.startsWith('/expert/conversations?')) {
      conversationListRequestCount++
      if (releaseConversationList) await new Promise<void>(resolve => { const release = releaseConversationList!; releaseConversationList = () => { release(); resolve() } })
      return { items: conversations, next_before: null }
    }
    if (path.startsWith('/settings/ai/consents/') && options?.method === 'PUT') {
      const consent = { ...payload, id: path.split('/').at(-1), provider_kind: 'local', model_id: 'synthetic-model', state: 'active', revoked_at: null }
      grants[payload.purpose] = consent; return consent
    }
    if (path.startsWith('/settings/ai/consents?')) {
      return { items: Object.values(grants).filter(grant => (grant as { connection_id?: string }).connection_id === connection.id), next_after: null }
    }
    if (path === '/settings/expert' && options?.method === 'PUT') return { ...payload, configuration_version: 2 }
    if (path.endsWith('/consents') && options?.method === 'DELETE') { grants = {}; return undefined }
    if (/\/turns\//.test(path) && options?.method === 'PUT') {
      const id = path.split('/').at(-1)!
      let run = turns.find(item => item.id === id)
      if (!run) { delivered.push(payload); run = savedRun(id, payload); turns.push(run) }
      if (lostResponse) { lostResponse = false; throw new Error('Transport failure') }
      return run
    }
    if (path.startsWith('/expert/runs/')) return turns.find(item => item.id === path.split('/').at(-1)) ?? Promise.reject(new Error('Missing'))
    if (/\/attachments\//.test(path)) {
      const id = path.split('/').at(-1)!
      if (options?.method === 'PUT') {
        if (nextUploadFailure) { const cause = nextUploadFailure; nextUploadFailure = undefined; throw cause }
        const file = options.body as File
        const result: AttachmentView = { id, file_name: file.name, byte_size: file.size, media_type: 'application/pdf', created_at: '2026-11-14T00:00:00Z' }
        attachments.push(result); return result
      }
      attachments = attachments.filter(item => item.id !== id); return undefined
    }
    if (path.endsWith('/attachments')) return attachments
    if (path.includes('/turns?')) {
      if (pagedHistory && !new URL(path, 'http://synthetic.invalid').searchParams.has('before_id')) {
        const recent = turns.find(item => item.id === 'recent')
        return { items: recent ? [recent] : [], next_before: { created_at: '2026-11-13T00:00:00Z', id: 'older-cursor' } }
      }
      if (pagedHistory) return { items: turns.filter(item => item.id === 'older'), next_before: null }
      return { items: [...turns].reverse(), next_before: null }
    }
    if (path.startsWith('/expert/conversations/')) {
      const id = path.split('/').at(-1)!
      if (options?.method === 'DELETE') { conversations = conversations.filter(item => item.id !== id); return undefined }
      if (options?.method === 'PUT') { const item = { id, mode: payload.mode, created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }; conversations = [item, ...conversations.filter(existing => existing.id !== id)]; return item }
      return conversations.find(item => item.id === id)
    }
    throw new Error('Unexpected test path ' + path)
  })
})
afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); setAuthenticatedIdentity(null); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('Nutrition Expert chat', () => {
  it('opens with two choices and never offers date or provider selection', async () => {
    const { wrapper } = await mounted()
    expect(wrapper.text()).toContain('Start analysis'); expect(wrapper.text()).toContain('Start chat')
    expect(wrapper.find('input[type="date"]').exists()).toBe(false)
    expect(wrapper.find('select').exists()).toBe(false)
    expect(writes()).toHaveLength(0)
    expect(apiMock.mock.calls.some(([path]) => path.includes('nutrition-days'))).toBe(false)
  })
  it('formats assistant attachment references with known filenames or a generic label only', async () => {
    const earlierId = '11111111-1111-4111-8111-111111111111'
    const currentId = '22222222-2222-4222-8222-222222222222'
    const unknownId = '33333333-3333-4333-8333-333333333333'
    const unsafeName = '<img src=x onerror=alert(1)>.pdf'
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    turns = [
      {
        ...savedRun('earlier-run', { kind: 'chat', message: `User mentioned attachment:${earlierId}`, connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }),
        id: 'earlier-run',
        attachments: [{ id: earlierId, file_name: unsafeName, media_type: 'application/pdf', byte_size: 20, created_at: '2026-11-13T00:00:00Z' }],
        answer: { text: `Earlier attachment:${earlierId}; unknown attachment:${unknownId}`, findings: [{ kind: 'observation', text: `Current attachment:${currentId}`, evidence_ids: [`attachment:${currentId}`] }] },
      },
      {
        ...savedRun('current-run', { kind: 'chat', message: 'Current user message', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }),
        id: 'current-run',
        attachments: [{ id: currentId, file_name: 'current-report.pdf', media_type: 'application/pdf', byte_size: 20, created_at: '2026-11-14T00:00:00Z' }],
        answer: { text: `Current attachment:${currentId}`, follow_up: [`Continue with attachment:${currentId}`] },
      },
    ]
    const { wrapper } = await mounted('?conversation=conversation-1')
    expect(wrapper.findAll('.answer-text')[0]!.text()).toBe(`Earlier ${unsafeName}; unknown Uploaded attachment`)
    expect(wrapper.findAll('.expert-finding p')[0]!.text()).toBe('Current current-report.pdf')
    expect(wrapper.findAll('.answer-text')[1]!.text()).toBe('Current current-report.pdf')
    expect(wrapper.find('.user-message p').text()).toBe(`User mentioned attachment:${earlierId}`)
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.get('.expert-finding').text()).toContain('Uploaded attachment')
    await wrapper.get('.follow-ups button').trigger('click')
    expect(wrapper.get('textarea').element).toHaveProperty('value', 'Continue with current-report.pdf')
  })
  it('shows only allowlisted friendly failure reasons and never provider error codes', async () => {
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    turns = [
      { ...savedRun('known-failure', { kind: 'chat', message: 'Synthetic known failure', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }), state: 'failed', error_code: 'invalid_request', answer: null },
      { ...savedRun('unknown-failure', { kind: 'chat', message: 'Synthetic unknown failure', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }), state: 'failed', error_code: 'provider-secret-synthetic', answer: null },
    ]
    const { wrapper } = await mounted('?conversation=conversation-1')
    expect(wrapper.text()).toContain('The provider rejected the request.')
    expect(wrapper.text()).toContain('The request could not be completed. There is no automatic retry.')
    expect(wrapper.text()).not.toContain('provider-secret-synthetic')
    expect(wrapper.findAll('.run-failure-reason')).toHaveLength(1)
  })
  it('does not auto-send a started analysis after navigation moves to another conversation', async () => {
    conversations = [{ id: 'other-analysis', mode: 'analysis', created_at: '2026-11-13T00:00:00Z', updated_at: '2026-11-13T00:00:00Z' }]
    const { wrapper, router } = await mounted()
    releaseConversationList = () => undefined
    await wrapper.get('.entry-card:first-child').trigger('click')
    await vi.waitFor(() => expect(conversationListRequestCount).toBe(2))
    await router.push({ query: { conversation: 'other-analysis' } })
    await flushPromises()
    const release = releaseConversationList
    releaseConversationList = null
    release?.()
    await flushPromises()
    expect(router.currentRoute.value.query.conversation).toBe('other-analysis')
    expect(apiMock.mock.calls.some(([path]) => /\/turns\/[^?]+$/.test(path))).toBe(false)
    expect(delivered).toHaveLength(0)
  })
  it('opens chat without a generation, asks first-use permission on send and escapes model text', async () => {
    state = { ...state, routing_configuration_version: 7 }
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click')
    await flushPromises()
    expect(delivered).toHaveLength(0)
    await wrapper.get('textarea').setValue('How is my protein intake?')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('entire available history')
    expect(delivered).toHaveLength(0)
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(1)
    expect(delivered[0]).toMatchObject({ kind: 'chat', connection_version: 3, preferences_version: 1, routing_configuration_version: 7 })
    const permissions = writes().filter(([path]) => path.includes('/consents/')).map(([, options]) => JSON.parse(String(options.body)) as AIConsentPut)
    expect(permissions).toHaveLength(2)
    expect(permissions.every(item => item.history.mode === 'all_available' && item.history.start === null && item.data_domains.length === 6)).toBe(true)
    expect(wrapper.text()).toContain('Synthetic response <script>unsafe</script>')
    expect(wrapper.find('script').exists()).toBe(false)
  })
  it('shows the actual provider for a successful fallback without changing the configured provider', async () => {
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-18T00:00:00Z', updated_at: '2026-11-18T00:00:00Z' }]
    turns = [{
      ...savedRun('fallback-run', { kind: 'chat', message: 'Synthetic question', connection_id: 'provider-b', connection_version: 1, preferences_version: 1, consent_id: 'grant-b' }),
      provider_kind: 'anthropic', model_id: 'synthetic-backup-model',
      provider_attempts: [
        { position: 0, connection_id: connection.id, provider_kind: 'local', model_id: 'synthetic-model', state: 'failed', error_code: 'rate_limited', dispatched_at: '2026-11-18T00:00:00Z', finished_at: '2026-11-18T00:00:01Z', winner: false },
        { position: 1, connection_id: 'provider-b', provider_kind: 'anthropic', model_id: 'synthetic-backup-model', state: 'succeeded', error_code: null, dispatched_at: '2026-11-18T00:00:02Z', finished_at: '2026-11-18T00:00:03Z', winner: true },
      ],
    }]
    const { wrapper } = await mounted('?conversation=conversation-1')
    expect(wrapper.get('.fallback-provenance').text()).toBe('Answer from Anthropic (Claude) (fallback)')
    expect(state.connection?.id).toBe(connection.id)
    expect(writes()).toHaveLength(0)
  })
  it('sends once with Enter while preserving shifted and composed newlines', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    const textarea = wrapper.get('textarea').element
    const keydown = (init: KeyboardEventInit & { keyCode?: number }) => {
      const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true, ...init })
      if (init.keyCode !== undefined) Object.defineProperty(event, 'keyCode', { value: init.keyCode })
      textarea.dispatchEvent(event)
      return event
    }

    await wrapper.get('textarea').setValue('A question')
    expect(keydown({ shiftKey: true }).defaultPrevented).toBe(false)
    expect(keydown({ isComposing: true }).defaultPrevented).toBe(false)
    expect(keydown({ keyCode: 229 }).defaultPrevented).toBe(false)
    expect(delivered).toHaveLength(0)
    await wrapper.get('textarea').setValue('First line\nSecond line')

    expect(keydown({ repeat: true }).defaultPrevented).toBe(true)
    expect(keydown({}).defaultPrevented).toBe(true)
    expect(keydown({}).defaultPrevented).toBe(true)
    await flushPromises()
    expect(delivered).toHaveLength(0)
    expect(wrapper.get('[role="dialog"]').text()).toContain('entire available history')

    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(1)
    expect(delivered[0]!.message).toBe('First line\nSecond line')
    expect(writes().filter(([path]) => /\/turns\//.test(String(path)))).toHaveLength(1)
  })
  it('keeps the transcript keyboard reachable and resizes drafts from text, followups and viewport changes', async () => {
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    turns = [{ ...savedRun('run-1', { kind: 'chat', message: 'Synthetic question', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }), answer: { text: 'Synthetic answer', findings: [], follow_up: ['Ask a follow-up'] } }]
    const { wrapper } = await mounted('?conversation=conversation-1')
    const log = wrapper.get('[role="log"]')
    expect(log.attributes('tabindex')).toBe('0')
    expect(log.attributes('aria-label')).toBeTruthy()

    const textarea = wrapper.get('textarea')
    let measuredHeight = 56
    Object.defineProperty(textarea.element, 'scrollHeight', { configurable: true, get: () => measuredHeight })
    await textarea.setValue('A question that wraps')
    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('56px')

    measuredHeight = 88
    await button(wrapper, 'Ask a follow-up').trigger('click')
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).value).toBe('Ask a follow-up')
    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('88px')

    measuredHeight = 180
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('128px')
    expect((textarea.element as HTMLTextAreaElement).style.overflowY).toBe('auto')

    measuredHeight = 40
    await textarea.setValue('')
    expect((textarea.element as HTMLTextAreaElement).style.height).toBe('40px')
    expect((textarea.element as HTMLTextAreaElement).style.overflowY).toBe('hidden')
  })
  it('updates the open conversation title when a queued first answer completes', async () => {
    vi.useFakeTimers()
    const implementation = apiMock.getMockImplementation()!
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (/\/turns\//.test(path) && options?.method === 'PUT') {
        const result = await implementation(path, options) as RunView
        const queued: RunView = { ...result, state: 'queued', answer: null }
        turns = [queued]
        conversations = [{ ...conversations[0]!, title: 'Opening question' }]
        return queued
      }
      if (path.startsWith('/expert/runs/')) {
        const completed: RunView = {
          ...turns[0]!, state: 'succeeded',
          answer: { text: 'Synthetic completed answer', conversation_title: 'Protein intake over time' },
        }
        turns = [completed]
        conversations = [{ ...conversations[0]!, title: 'Protein intake over time' }]
        return completed
      }
      return implementation(path, options)
    })
    const { wrapper } = await mounted()
    try {
      await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
      await wrapper.get('textarea').setValue('I have a question about my protein intake')
      await wrapper.get('form').trigger('submit'); await flushPromises()
      await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
      expect(wrapper.get('#expert-conversation-title').text()).toBe('Opening question')
      await vi.advanceTimersByTimeAsync(700); await flushPromises()
      expect(wrapper.get('#expert-conversation-title').text()).toBe('Protein intake over time')
      expect(wrapper.get('.answer-text').text()).toBe('Synthetic completed answer')
      expect(delivered).toHaveLength(1)
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })
  it('does not grant or generate when first-use permission is dismissed', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:first-child').trigger('click'); await flushPromises()
    await button(wrapper, 'Cancel').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(0)
    expect(writes().filter(([path]) => path.includes('/consents/'))).toHaveLength(0)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
  it('reuses the immutable run request when both submission and status responses are lost', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:first-child').trigger('click'); await flushPromises()
    const implementation = apiMock.getMockImplementation()!
    let failRead = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/expert/runs/') && failRead) { failRead = false; throw new Error('Unavailable') }
      return implementation(path, options)
    })
    lostResponse = true
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('Delivery status is unknown')
    expect(wrapper.get('textarea').element).toHaveProperty('disabled', true)
    await button(wrapper, 'Check request again').trigger('click'); await flushPromises()
    const requests = writes().filter(([path]) => path.includes('/turns/'))
    expect(requests).toHaveLength(2)
    expect(requests[0]![0]).toBe(requests[1]![0])
    expect(requests[0]![1].body).toBe(requests[1]![1].body)
    expect(delivered).toHaveLength(1)
    expect(wrapper.text()).toContain('Synthetic response')
  })
  it('requires another explicit confirmation with fresh IDs after replaying a revoked grant', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:first-child').trigger('click'); await flushPromises()
    const implementation = apiMock.getMockImplementation()!
    let revokedReplay = true
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      if (path.startsWith('/settings/ai/consents/') && options?.method === 'PUT' && revokedReplay) {
        revokedReplay = false
        return { ...JSON.parse(String(options.body)), id: path.split('/').at(-1), state: 'revoked' }
      }
      return implementation(path, options)
    })
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(0)
    expect(wrapper.get('[role="dialog"]').text()).toContain('Permission could not be completed')
    const first = writes().find(([path]) => path.includes('/consents/'))![0]
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    const requests = writes().filter(([path]) => path.includes('/consents/'))
    expect(requests[1]![0]).not.toBe(first)
    expect(delivered).toHaveLength(1)
  })
  it('starts formal analysis with permission and sends followups as chat', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:first-child').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(0)
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered[0]!.kind).toBe('analysis')
    await wrapper.get('textarea').setValue('What about last week?'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(delivered[1]!.kind).toBe('chat')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })
  it('recovers a lost response by reading the same run without a second generation', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:first-child').trigger('click'); await flushPromises()
    lostResponse = true
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(1)
    expect(writes().filter(([path]) => path.includes('/turns/'))).toHaveLength(1)
    expect(apiMock.mock.calls.some(([path]) => path.startsWith('/expert/runs/'))).toBe(true)
    expect(wrapper.text()).toContain('Synthetic response')
  })
  it('reopens a conversation via the URL without scheduling a provider request', async () => {
    conversations = [{ id: 'conversation-1', mode: 'analysis', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    turns = [savedRun('run-1', { kind: 'analysis', message: 'Assess my history', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' })]
    const { wrapper, router } = await mounted('?conversation=conversation-1')
    expect(wrapper.text()).toContain('Assess my history'); expect(wrapper.text()).toContain('Grounded finding')
    expect(writes()).toHaveLength(0)
    await router.push({ query: {} }); await flushPromises()
    expect(wrapper.findAll('.entry-card')).toHaveLength(2)
  })
  it('deletes a conversation directly from the history without opening it', async () => {
    conversations = [{ id: 'conversation-1', mode: 'chat', title: 'Fiber intake', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    const { wrapper } = await mounted()
    expect(wrapper.get('.history-item').text()).toContain('Fiber intake')
    await wrapper.get('button[aria-label="Delete conversation “Fiber intake”"]').trigger('click')
    await button(wrapper, 'Delete conversation').trigger('click'); await flushPromises()
    expect(conversations).toHaveLength(0)
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(apiMock.mock.calls.some(([path]) => path.includes('/turns?'))).toBe(false)
  })
  it('stores an attachment locally, then requests explicit document permission before sending', async () => {
    const { wrapper, router } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    const file = new File(['%PDF-1.4 synthetic'], 'synthetic-report.pdf', { type: 'application/pdf' })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change'); await flushPromises()
    expect(wrapper.get('.draft-attachments').text()).toContain('synthetic-report.pdf')
    expect(delivered).toHaveLength(0)
    await wrapper.get('form.chat-composer').trigger('submit'); await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('in full')
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered[0]!.attachment_ids).toEqual([attachments[0]!.id])
    const grants = writes().filter(([path]) => path.includes('/consents/'))
    expect(grants.every(([, options]) => JSON.parse(String(options.body)).include_attachments === true)).toBe(true)

    await router.push({ query: {} }); await flushPromises()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    await wrapper.get('textarea').setValue('A later text-only question')
    await wrapper.get('form.chat-composer').trigger('submit'); await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(delivered).toHaveLength(2)
    expect(writes().filter(([path]) => path.includes('/consents/'))).toHaveLength(2)
  })
  it('scans every bounded turn page before deciding whether document permission is required', async () => {
    pagedHistory = true
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    turns = [
      { ...savedRun('recent', { kind: 'chat', message: 'Recent question', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }), id: 'recent', state: 'succeeded', answer: null },
      { ...savedRun('older', { kind: 'chat', message: 'Earlier question', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'grant-1' }), id: 'older', state: 'succeeded', answer: null, attachments: [{ id: 'old-file', file_name: 'earlier.pdf', media_type: 'application/pdf', byte_size: 20, created_at: '2026-11-13T00:00:00Z' }] },
    ]
    const { wrapper } = await mounted('?conversation=conversation-1')
    await wrapper.get('textarea').setValue('Discuss this history')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(apiMock.mock.calls.some(([path]) => path.includes('before_id=older-cursor'))).toBe(true)
    expect(wrapper.get('[role="dialog"]').text()).toContain('attachments you send')
    await button(wrapper, 'Allow and start').trigger('click')
    await flushPromises()
    expect(writes().filter(([path]) => path.includes('/consents/')).map(([, options]) => JSON.parse(String(options?.body)).include_attachments)).toEqual([true, true])
  })
  it.each([413, 422, 409])('keeps the chat usable after a definitive upload rejection (%s)', async status => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    await wrapper.get('textarea').setValue('A question without the rejected file')
    nextUploadFailure = new ApiError('synthetic rejection', status, undefined, undefined, 'urn:calograph:problem:expert-attachment-limit')
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['%PDF-1.4 synthetic'], 'synthetic.pdf', { type: 'application/pdf' })] })
    await input.trigger('change'); await flushPromises()
    expect(wrapper.text()).toContain('attachment could not be transferred')
    expect(button(wrapper, 'Retry attachment upload')).toBeUndefined()
    expect(button(wrapper, 'Send').element).toHaveProperty('disabled', false)
  })
  it('retries an unknown upload with the same file and immutable UUID', async () => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    nextUploadFailure = new Error('synthetic transport failure')
    const file = new File(['%PDF-1.4 synthetic'], 'synthetic.pdf', { type: 'application/pdf' })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change'); await flushPromises()
    await button(wrapper, 'Retry attachment upload').trigger('click'); await flushPromises()
    const requests = writes().filter(([path]) => /\/attachments\//.test(path))
    expect(requests).toHaveLength(2)
    expect(requests[0]![0]).toBe(requests[1]![0])
    expect(requests[0]![1].body).toBe(file); expect(requests[1]![1].body).toBe(file)
  })
  it.each(['failed', 'cancelled'] as const)('explicitly reuses a %s turn with its original file IDs without uploading again', async state => {
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    const file: AttachmentView = { id: 'existing-document', file_name: 'synthetic-report.pdf', media_type: 'application/pdf', byte_size: 30, created_at: '2026-11-14T00:00:00Z' }
    turns = [{ ...savedRun('failed-run', { kind: 'chat', message: 'Explain the synthetic report', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'old-grant' }), state, answer: null, attachments: [file] }]
    const { wrapper } = await mounted('?conversation=conversation-1')
    await button(wrapper, 'Reuse message').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(0)
    expect(wrapper.get('.draft-attachments').text()).toContain('synthetic-report.pdf')
    await wrapper.get('form.chat-composer').trigger('submit'); await flushPromises()
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered[0]!.attachment_ids).toEqual([file.id])
    expect(writes().some(([path]) => /\/attachments\//.test(path))).toBe(false)
  })
  it.each(['setup', 'consent'] as const)('discards a deferred %s response after an account switch', async stage => {
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    const original = apiMock.getMockImplementation()!
    let releaseResponse: (() => void) | undefined
    apiMock.mockClear()
    apiMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const result = await original(path, options)
      if ((stage === 'setup' && path.startsWith('/expert/setup')) || (stage === 'consent' && path.startsWith('/settings/ai/consents/') && options?.method === 'PUT')) {
        await new Promise<void>(resolve => { releaseResponse = resolve })
      }
      return result
    })
    await wrapper.get('textarea').setValue('A synthetic private question')
    await wrapper.get('form.chat-composer').trigger('submit')
    await flushPromises()
    if (stage === 'consent') {
      await button(wrapper, 'Allow and start').trigger('click')
    }
    await vi.waitFor(() => expect(releaseResponse).toBeTypeOf('function'))
    const auth = useAuthStore()
    setAuthenticatedIdentity('other-synthetic-owner')
    auth.user = { ...auth.user!, id: 'other-synthetic-owner' }
    await flushPromises()
    releaseResponse!()
    await flushPromises()
    expect(delivered).toHaveLength(0)
    expect(writes()).toHaveLength(stage === 'consent' ? 1 : 0)
    expect(wrapper.text()).toContain('The session changed.')
    expect(wrapper.text()).not.toContain('A synthetic private question')
    expect(wrapper.find('.conversation-header').exists()).toBe(false)
  })
  it.each(['failed', 'cancelled'] as const)('can deselect a reopened %s attachment without deleting retained history', async runState => {
    conversations = [{ id: 'conversation-1', mode: 'chat', created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z' }]
    const file: AttachmentView = { id: 'retained-file', file_name: 'synthetic-report.pdf', media_type: 'application/pdf', byte_size: 30, created_at: '2026-11-14T00:00:00Z' }
    attachments = [file]
    turns = [{ ...savedRun('old-run', { kind: 'chat', message: 'Original synthetic question', connection_id: connection.id, connection_version: 3, preferences_version: 1, consent_id: 'old-grant' }), state: runState, answer: null, attachments: [file] }]
    const { wrapper } = await mounted('?conversation=conversation-1')
    await wrapper.get('.draft-attachments button').trigger('click'); await flushPromises()
    expect(wrapper.find('.draft-attachments').exists()).toBe(false)
    expect(apiMock.mock.calls.some(([, options]) => options?.method === 'DELETE')).toBe(false)
    await wrapper.get('textarea').setValue('Continue without the previous document')
    await wrapper.get('form.chat-composer').trigger('submit'); await flushPromises()
    await button(wrapper, 'Allow and start').trigger('click'); await flushPromises()
    expect(delivered).toHaveLength(1)
    expect(delivered[0]!.attachment_ids).toEqual([])
    expect(writes().filter(([path]) => path.includes('/consents/')).map(([, options]) => JSON.parse(String(options?.body)).include_attachments)).toEqual([false, false])
  })
  it('asks before dictation, fills the draft and never sends recognized speech automatically', async () => {
    const instances: Array<{ onresult: ((event: { results: { length: number; 0: { isFinal: boolean; 0: { transcript: string } } } }) => void) | null; onerror: ((event: { error: string }) => void) | null; onend: (() => void) | null; start: ReturnType<typeof vi.fn>; abort: ReturnType<typeof vi.fn> }> = []
    const start = vi.fn(); const abort = vi.fn()
    class FakeRecognition {
      lang = ''; continuous = false; interimResults = false
      onresult = null; onerror = null; onend = null
      start = start; abort = abort
      constructor() { instances.push(this) }
    }
    vi.stubGlobal('isSecureContext', true); vi.stubGlobal('SpeechRecognition', FakeRecognition)
    const { wrapper } = await mounted()
    await wrapper.get('.entry-card:nth-child(2)').trigger('click'); await flushPromises()
    await wrapper.get('button[aria-label="Dictate message"]').trigger('click')
    expect(start).not.toHaveBeenCalled()
    expect(wrapper.get('[role="dialog"]').text()).toContain('external speech service')
    await button(wrapper, 'Allow microphone and dictate').trigger('click')
    expect(start).toHaveBeenCalledOnce()
    instances[0]!.onresult!({ results: { length: 1, 0: { isFinal: true, 0: { transcript: 'How is my fiber intake?' } } } })
    await flushPromises()
    expect(wrapper.get('textarea').element).toHaveProperty('value', 'How is my fiber intake?')
    expect(delivered).toHaveLength(0)
    await wrapper.get('button[aria-label="Stop dictation"]').trigger('click')
    expect(abort).toHaveBeenCalledOnce()
  })
})
