import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  api,
  setAuthenticationExpiredHandler,
  setCsrfToken,
} from '../src/api'
import { useAuthStore } from '../src/stores/auth'

describe('authentication store', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    setActivePinia(createPinia())
    setCsrfToken(null)
    setAuthenticationExpiredHandler(null)
  })

  it('stores the user returned by login', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          mfa_required: false,
          user: { id: '1', username: 'admin', language: 'de', timezone: 'Europe/Berlin', week_starts_on: 0 },
          csrf_token: 'csrf',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    const auth = useAuthStore()
    await auth.login('admin', 'password-password')
    expect(auth.user?.username).toBe('admin')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf')
  })

  it('revalidates and clears a cached user after the server session expires', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            mfa_required: false,
            user: {
              id: '1',
              username: 'admin',
              language: 'de',
              timezone: 'Europe/Berlin',
              week_starts_on: 0,
            },
            csrf_token: 'csrf',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Nicht authentifiziert' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const auth = useAuthStore()
    await auth.login('admin', 'password-password')

    expect(await auth.ensureUser()).toBe(false)

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/auth/me',
      expect.objectContaining({ credentials: 'include' }),
    )
    expect(auth.user).toBeNull()
    expect(auth.mfaRequired).toBe(false)
    expect(sessionStorage.getItem('calograph_csrf')).toBeNull()
  })

  it('signals an expired session on protected 401 responses only', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Nicht authentifiziert' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(api('/dashboard/summary')).rejects.toMatchObject({ status: 401 })
    expect(expired).toHaveBeenCalledOnce()

    await expect(
      api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username: 'admin', password: 'wrong' }),
      }),
    ).rejects.toMatchObject({ status: 401 })
    expect(expired).toHaveBeenCalledOnce()
  })

  it('requires the second factor before storing a user', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ mfa_required: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            mfa_required: false,
            user: {
              id: '1',
              username: 'admin',
              language: 'de',
              timezone: 'Europe/Berlin',
              week_starts_on: 0,
            },
            csrf_token: 'csrf-after-mfa',
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      )
    const auth = useAuthStore()

    expect(await auth.login('admin', 'password-password')).toBe(false)
    expect(auth.mfaRequired).toBe(true)
    expect(auth.user).toBeNull()
    expect(sessionStorage.getItem('calograph_csrf')).toBeNull()

    await auth.verifyMfa('123456')

    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/auth/mfa/totp/verify',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ code: '123456' }),
      }),
    )
    expect(auth.user?.username).toBe('admin')
    expect(auth.mfaRequired).toBe(false)
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf-after-mfa')
  })

  it('stores the user returned by passwordless passkey sign-in', async () => {
    const credentialJson = {
      id: 'credential',
      rawId: 'credential',
      response: {
        clientDataJSON: 'client-data',
        authenticatorData: 'authenticator-data',
        signature: 'signature',
        userHandle: 'user-handle',
      },
      authenticatorAttachment: 'platform',
      clientExtensionResults: {},
      type: 'public-key' as const,
    }
    class FakePublicKeyCredential {
      toJSON() {
        return credentialJson
      }
    }
    const getCredential = vi.fn().mockResolvedValue(new FakePublicKeyCredential())
    vi.stubGlobal('isSecureContext', true)
    vi.stubGlobal('PublicKeyCredential', FakePublicKeyCredential)
    vi.stubGlobal('navigator', { credentials: { get: getCredential } })
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            challenge_id: 'challenge-id',
            public_key: {
              challenge: 'Y2hhbGxlbmdl',
              rpId: 'localhost',
              userVerification: 'required',
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            mfa_required: false,
            user: {
              id: '1',
              username: 'admin',
              language: 'de',
              timezone: 'Europe/Berlin',
              week_starts_on: 0,
            },
            csrf_token: 'csrf-passkey',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    const auth = useAuthStore()

    await auth.loginWithPasskey()

    expect(getCredential).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/auth/passkey/verify',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          challenge_id: 'challenge-id',
          credential: credentialJson,
        }),
      }),
    )
    expect(auth.user?.username).toBe('admin')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf-passkey')
  })

  it('exchanges and uses an invitation without requiring an existing CSRF session', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (input === '/api/v1/auth/invitation/exchange') {
        return new Response(null, { status: 204 })
      }
      return new Response(
        JSON.stringify({ id: '2', username: 'friend', is_admin: false }),
        {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        },
      )
    })

    await api('/auth/invitation/exchange', {
      method: 'POST',
      body: JSON.stringify({ token: 'invite_example' }),
    })
    await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username: 'friend',
        password: 'a-long-personal-password',
      }),
    })

    expect(fetchMock).toHaveBeenCalledTimes(2)
    for (const [url, options] of fetchMock.mock.calls) {
      expect(url).toMatch(/^\/api\/v1\/auth\/(invitation\/exchange|register)$/)
      expect(new Headers(options?.headers).has('X-CSRF-Token')).toBe(false)
    }
  })
  it('preserves Retry-After for the public recovery endpoint without requesting CSRF', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ detail: 'Zu viele Anfragen. Bitte später erneut versuchen.' }),
        {
          status: 429,
          headers: { 'Content-Type': 'application/json', 'Retry-After': '45' },
        },
      ),
    )

    await expect(
      api('/auth/recovery/complete', {
        method: 'POST',
        body: JSON.stringify({
          recovery_token: 'synthetic-token',
          new_password: 'a-long-personal-password',
        }),
      }),
    ).rejects.toMatchObject({
      status: 429,
      retryAfter: '45',
      message: 'Zu viele Anfragen. Bitte später erneut versuchen.',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('X-CSRF-Token')).toBe(false)
  })

})
