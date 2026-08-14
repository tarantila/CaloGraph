import { afterEach, describe, expect, it, vi } from 'vitest'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

async function loadApi() {
  vi.resetModules()
  sessionStorage.clear()
  return import('../src/api')
}

afterEach(() => {
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
  })

  it('refreshes einmalig und replayed eine replaybare Mutation', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(403, {
        type: 'urn:calograph:problem:csrf-validation-failed',
        status: 403,
      }))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: 'csrf-refreshed' }))
      .mockResolvedValueOnce(jsonResponse(201, { id: 'invitation-1' }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-stale')

    await expect(apiModule.api('/users/invitations', {
      method: 'POST',
      body: JSON.stringify({ expires_in_days: 7 }),
      headers: { 'X-Request-Test': 'preserved' },
    })).resolves.toEqual({ id: 'invitation-1' })

    expect(fetchMock).toHaveBeenCalledTimes(3)
    const firstInit = fetchMock.mock.calls[0]?.[1] as RequestInit
    const replayInit = fetchMock.mock.calls[2]?.[1] as RequestInit
    expect(firstInit.method).toBe('POST')
    expect(replayInit.method).toBe('POST')
    expect(replayInit.body).toBe(JSON.stringify({ expires_in_days: 7 }))
    expect(new Headers(replayInit.headers).get('Content-Type')).toBe('application/json')
    expect(new Headers(replayInit.headers).get('X-Request-Test')).toBe('preserved')
    expect(new Headers(replayInit.headers).get('X-CSRF-Token')).toBe('csrf-refreshed')
  })

  it('führt nach einem zweiten CSRF-Fehler keinen dritten Versuch aus', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(403, {
        type: 'urn:calograph:problem:csrf-validation-failed',
        status: 403,
      }))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: 'csrf-refreshed' }))
      .mockResolvedValueOnce(jsonResponse(403, {
        type: 'urn:calograph:problem:csrf-validation-failed',
        status: 403,
      }))
    vi.stubGlobal('fetch', fetchMock)
    const apiModule = await loadApi()
    apiModule.setCsrfToken('csrf-stale')

    await expect(apiModule.api('/users/invitations', { method: 'POST' }))
      .rejects.toMatchObject({ status: 403, problemType: 'urn:calograph:problem:csrf-validation-failed' })
    expect(fetchMock).toHaveBeenCalledTimes(3)
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
