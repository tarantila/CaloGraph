import { afterEach, describe, expect, it, vi } from 'vitest'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let disposeAuthStore: (() => void) | null = null

// Test isolation: load the API and auth store from the same fresh module graph.
async function loadApi() {
  vi.resetModules()
  sessionStorage.clear()
  localStorage.removeItem('calograph_auth_session_revision')
  return import('../src/api')
}

async function setAuthenticatedUser(userId = 'account-a') {
  const { createPinia, setActivePinia } = await import('pinia')
  const { useAuthStore } = await import('../src/stores/auth')
  setActivePinia(createPinia())
  const auth = useAuthStore()
  auth.user = {
    id: userId,
    username: 'test-user',
    language: 'de',
    timezone: 'UTC',
    week_starts_on: 1,
    preferred_weight_unit: 'kg',
    raw_payload_retention_days: 0,
    is_admin: false,
    is_active: true,
    deactivated_at: null,
  }
  disposeAuthStore = () => auth.$dispose()
}

function csrfFailure() {
  return jsonResponse(403, {
    type: 'urn:calograph:problem:csrf-validation-failed',
    status: 403,
  })
}

function clearAuthMe() {
  return jsonResponse(200, { id: 'account-a' })
}

const authSessionRevisionKey = 'calograph_auth_session_revision'

afterEach(() => {
  disposeAuthStore?.()
  disposeAuthStore = null
  vi.unstubAllGlobals()
})

describe('CSRF self-healing', () => {
  it('sendet eine gültige Mutation genau einmal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-valid')

    await expect(apiModule.api('/users/invitations', {
      method: 'POST',
      body: JSON.stringify({ expires_in_days: 7 }),
    })).resolves.toEqual({ ok: true })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(new Headers(requestInit.headers).get('X-CSRF-Token')).toBe('csrf-valid')
    expect(new Headers(requestInit.headers).get('Content-Type')).toBe('application/json')
  })

  it.each(['application/pdf', 'image/png'])('preserves the declared %s attachment type and raw body', async mediaType => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: 'attachment-a' }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-valid')
    const file = new File(['synthetic test bytes'], 'synthetic-document', { type: mediaType })

    await apiModule.api('/expert/conversations/conversation-a/attachments/attachment-a', {
      method: 'PUT', body: file,
      headers: { 'Content-Type': mediaType, 'X-Attachment-Filename': 'synthetic-document' },
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    expect(requestInit.body).toBe(file)
    expect(new Headers(requestInit.headers).get('Content-Type')).toBe(mediaType)
    expect(new Headers(requestInit.headers).get('X-Attachment-Filename')).toBe('synthetic-document')
    expect(new Headers(requestInit.headers).get('X-CSRF-Token')).toBe('csrf-valid')
  })

  it('does not automatically replay a raw attachment on CSRF rejection', async () => {
    const fetchMock = vi.fn().mockResolvedValue(csrfFailure())
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-stale')
    const file = new File(['%PDF-1.7 synthetic'], 'synthetic.pdf', { type: 'application/pdf' })

    await expect(apiModule.api('/expert/conversations/conversation-a/attachments/attachment-a', {
      method: 'PUT', body: file, headers: { 'Content-Type': 'application/pdf' },
    })).rejects.toMatchObject({ status: 403 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).body).toBe(file)
  })

  it('refreshes once, verifies the initiating identity and replays a replayable mutation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(403, {
        type: 'urn:calograph:problem:csrf-validation-failed',
        status: 403,
      }))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: 'csrf-refreshed' }))
      .mockResolvedValueOnce(clearAuthMe())
      .mockResolvedValueOnce(jsonResponse(201, { id: 'invitation-1' }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-stale')

    await expect(apiModule.api('/users/invitations', {
      method: 'POST',
      body: JSON.stringify({ expires_in_days: 7 }),
      headers: { 'X-Request-Test': 'preserved' },
    })).resolves.toEqual({ id: 'invitation-1' })

    expect(fetchMock).toHaveBeenCalledTimes(4)
    const firstInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const replayInit = fetchMock.mock.calls[3]?.[1] as RequestInit
    expect(firstInit.method).toBe('POST')
    expect(replayInit.method).toBe('POST')
    expect(replayInit.body).toBe(JSON.stringify({ expires_in_days: 7 }))
    expect(new Headers(replayInit.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(replayInit.headers).get('X-Request-Test')).toBe('preserved')
    expect(new Headers(replayInit.headers).get('X-CSRF-Token')).toBe('csrf-refreshed')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf-refreshed')
  })

  it('aborts recovery when the shared session revision changes before its storage event runs', async () => {
    let mutationCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === '/api/v1/users/invitations') {
        mutationCalls += 1
        return mutationCalls === 1 ? csrfFailure() : jsonResponse(201, { id: 'wrong-account' })
      }
      if (input === '/api/v1/auth/csrf') {
        localStorage.setItem(authSessionRevisionKey, 'remote-revision-b')
        return jsonResponse(200, { csrf_token: 'csrf-b' })
      }
      return clearAuthMe()
    })
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-a')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 409, problemType: 'urn:calograph:problem:auth-session-changed' })
    expect(sessionStorage.getItem('calograph_csrf')).toBeNull()
    expect(mutationCalls).toBe(1)
    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      '/api/v1/users/invitations',
      '/api/v1/auth/csrf',
    ])
  })

  it('aborts recovery when /auth/me identifies a different account before revision publication', async () => {
    let mutationCalls = 0
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (input === '/api/v1/users/invitations') {
        mutationCalls += 1
        return mutationCalls === 1 ? csrfFailure() : jsonResponse(201, { id: 'wrong-account' })
      }
      if (input === '/api/v1/auth/csrf') return jsonResponse(200, { csrf_token: 'csrf-b' })
      if (input === '/api/v1/auth/me') return jsonResponse(200, { id: 'account-b' })
      throw new Error(`Unexpected request ${String(input)}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-a')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 409, problemType: 'urn:calograph:problem:auth-session-changed' })
    expect(sessionStorage.getItem('calograph_csrf')).toBeNull()
    expect(mutationCalls).toBe(1)
    expect(fetchMock.mock.calls.map(([input]) => input)).toEqual([
      '/api/v1/users/invitations',
      '/api/v1/auth/csrf',
      '/api/v1/auth/me',
    ])
  })

  it('führt nach einem zweiten CSRF-Fehler keinen dritten Versuch aus', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(csrfFailure())
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: 'csrf-refreshed' }))
      .mockResolvedValueOnce(clearAuthMe())
      .mockResolvedValueOnce(csrfFailure())
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    await setAuthenticatedUser()
    apiModule.setCsrfToken('csrf-stale')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 403, problemType: 'urn:calograph:problem:csrf-validation-failed' })
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it.each([
    ['invalid-request-origin', 'urn:calograph:problem:invalid-request-origin'],
    ['other-403', 'urn:calograph:problem:admin-required'],
  ])('replayed weder bei %s', async (_label, problemType) => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(403, {
      type: problemType,
      status: 403,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-valid')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 403, problemType })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('replayed weder bei 5xx noch bei Mutation-Transportfehlern', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(500, { status: 500 }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-valid')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 500 })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    fetchMock.mockReset().mockRejectedValue(new Error('network down'))
    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toBeInstanceOf(apiModule.ApiTransportError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('begrenzt Replay auf replaybare Request-Bodies', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(403, {
      type: 'urn:calograph:problem:csrf-validation-failed',
      status: 403,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-stale')

    const stream = new ReadableStream<Uint8Array>()
    await expect(apiModule.api('/users/invitations', {
      method: 'POST',
      body: stream,
    })).rejects.toMatchObject({ status: 403 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('behält den einmaligen Transport-Retry für GET bei', async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()

    await expect(apiModule.api('/auth/csrf')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
