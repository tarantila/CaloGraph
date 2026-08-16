import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ApiTransportError,
  api,
  setAuthenticationExpiredHandler,
  setCsrfToken,
} from '../src/api'
import { i18n, PUBLIC_LOCALE, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

describe('authentication store', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    setActivePinia(createPinia())
    setCsrfToken(null)
    setAuthenticationExpiredHandler(null)
    setLocale(PUBLIC_LOCALE)
  })
  it('retryt einen GET-Transportfehler genau einmal', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await expect(api<{ ok: boolean }>('/dashboard/summary')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('gibt nach zwei GET-Transportfehlern einen ApiTransportError zurück', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('fetch failed'))

    await expect(api('/dashboard/summary')).rejects.toBeInstanceOf(ApiTransportError)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('retryt HTTP-Fehler nicht', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 500 }))

    await expect(api('/dashboard/summary')).rejects.toMatchObject({ status: 500 })
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('retryt HTTP 401 nicht', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 401 }))

    await expect(api('/dashboard/summary')).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it.each(['POST', 'PUT'])('retryt %s-Transportfehler niemals', async (method) => {
    setCsrfToken('csrf')
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('fetch failed'))

    await expect(api('/settings/profile', {
      method,
      body: JSON.stringify({ language: 'de' }),
    })).rejects.toBeInstanceOf(ApiTransportError)
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  it('retryt den CSRF-GET genau einmal, ohne die Mutation zu wiederholen', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'csrf' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true }), { status: 200 }))

    await expect(api('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ language: 'de' }),
    })).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/auth/csrf')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/v1/auth/csrf')
    expect(fetchMock.mock.calls[2][0]).toBe('/api/v1/settings/profile')
  })
  it('bündelt parallele CSRF-Refreshes derselben Session', async () => {
    let resolveCsrf!: (response: Response) => void
    const pendingCsrf = new Promise<Response>((resolve) => { resolveCsrf = resolve })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (input === '/api/v1/auth/csrf') return pendingCsrf
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    })

    const first = api('/settings/profile', { method: 'PUT', body: JSON.stringify({ language: 'de' }) })
    const second = api('/settings/profile', { method: 'PUT', body: JSON.stringify({ language: 'en' }) })
    await Promise.resolve()
    await Promise.resolve()
    expect(fetchMock.mock.calls.filter(([input]) => input === '/api/v1/auth/csrf')).toHaveLength(1)

    resolveCsrf(new Response(JSON.stringify({ csrf_token: 'csrf' }), { status: 200 }))
    await expect(Promise.all([first, second])).resolves.toEqual([{ ok: true }, { ok: true }])
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('behandelt einen CSRF-Transportfehler ohne Sessionablauf', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('fetch failed'))

    await expect(api('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ language: 'de' }),
    })).rejects.toBeInstanceOf(ApiTransportError)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(expired).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('calograph_csrf')).toBeNull()
  })

  it('behandelt einen echten CSRF-401 weiterhin als Sessionablauf', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 401 }))

    await expect(api('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ language: 'de' }),
    })).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(expired).toHaveBeenCalledOnce()
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
    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf')
  })
  it('invalidates profile updates when login enters MFA', async () => {
    const auth = useAuthStore()
    const user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: true,
      is_active: true,
      deactivated_at: null,
    }
    auth.user = user
    const generation = auth.beginProfileUpdate()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ mfa_required: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(auth.login('admin', 'password-password')).resolves.toBe(false)
    expect(auth.currentProfileUpdateGeneration()).toBe(generation + 1)
    expect(auth.user).toBeNull()
    expect(auth.mfaRequired).toBe(true)
  })
  it('ignores stale profile-update responses', () => {
    const auth = useAuthStore()
    const user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: true,
      is_active: true,
      deactivated_at: null,
    }
    auth.user = user

    const first = auth.beginProfileUpdate('en')
    const second = auth.beginProfileUpdate('de')

    expect(auth.commitProfileUpdate(first, { ...user, language: 'en' })).toBe(false)
    expect(auth.user?.language).toBe('de')
    expect(auth.commitProfileUpdate(second, user)).toBe(true)
  })

  it('keeps the public locale when a profile update resolves after public navigation', () => {
    const auth = useAuthStore()
    const user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: true,
      is_active: true,
      deactivated_at: null,
    }
    auth.user = user
    const update = auth.beginProfileUpdate()
    setLocale(PUBLIC_LOCALE)
    auth.beginProfileUpdate()

    expect(auth.commitProfileUpdate(update, user)).toBe(false)
    expect(i18n.global.locale.value).toBe(PUBLIC_LOCALE)
    expect(document.documentElement.lang).toBe(PUBLIC_LOCALE)
  })


  it('does not dispatch queued profile updates after session reset', async () => {
    const auth = useAuthStore()
    const user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: true,
      is_active: true,
      deactivated_at: null,
    }
    auth.user = user
    const firstGeneration = auth.beginProfileUpdate()
    let release!: () => void
    const blocker = new Promise<void>((resolve) => { release = resolve })
    const first = auth.enqueueProfileUpdate(firstGeneration, async () => {
      await blocker
      return user
    })
    await Promise.resolve()

    const secondGeneration = auth.beginProfileUpdate('en')
    let secondCalled = false
    const second = auth.enqueueProfileUpdate(secondGeneration, async () => {
      secondCalled = true
      return user
    })
    auth.clearSession()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        mfa_required: false,
        user,
        csrf_token: 'next-csrf',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(auth.login('admin', 'password-password')).resolves.toBe(true)
    release()

    await expect(first).resolves.toEqual(user)
    await expect(second).resolves.toBeNull()
    expect(secondCalled).toBe(false)
  })

  it('detects targetless users without inventing goal values', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: '1',
            username: 'friend',
            language: 'de',
            timezone: 'Europe/Berlin',
            week_starts_on: 0,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    const auth = useAuthStore()

    expect(await auth.ensureUser()).toBe(true)
    expect(auth.needsTargetSetup).toBe(true)
    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/auth/me',
      '/api/v1/settings/targets',
    ])

    auth.completeTargetSetup()
    expect(auth.needsTargetSetup).toBe(false)
  })
  it('behält die Session bei einem Reconcile-Rate-Limit', async () => {
    const user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      is_admin: false,
      is_active: true,
      deactivated_at: null,
      raw_payload_retention_days: 0,
    }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          type: 'urn:calograph:problem:rate-limited',
          status: 429,
          detail: 'Zu viele Anfragen. Bitte später erneut versuchen.',
        }),
        { status: 429, headers: { 'Content-Type': 'application/problem+json', 'Retry-After': '60' } },
      ),
    )
    const auth = useAuthStore()
    setCsrfToken('csrf')
    auth.user = user
    auth.needsTargetSetup = false

    await auth.reconcileAchievements(true)

    expect(auth.user).toEqual(user)
    expect(auth.newlyUnlockedAchievements).toEqual([])
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('verwirft veraltete Achievement-Reconciliation nach Sessionwechsel', async () => {
    const firstUser = {
      id: 'first',
      username: 'first',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: false,
      is_active: true,
      deactivated_at: null,
    }
    const secondUser = { ...firstUser, id: 'second', username: 'second' }
    let releaseFirst!: (response: Response) => void
    const firstResponse = new Promise<Response>((resolve) => { releaseFirst = resolve })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      if (String(input).endsWith('/achievements/reconcile')) {
        if (fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/achievements/reconcile')).length === 1) {
          return firstResponse
        }
        return new Response(JSON.stringify({ achievements: [], newly_unlocked: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      throw new Error(`Unexpected request: ${String(input)}`)
    })
    const auth = useAuthStore()
    setCsrfToken('csrf')
    auth.user = firstUser
    auth.needsTargetSetup = false

    const staleReconciliation = auth.reconcileAchievements(true)
    await Promise.resolve()
    auth.clearSession()
    setCsrfToken('csrf')
    auth.user = secondUser
    auth.needsTargetSetup = false
    releaseFirst(new Response(JSON.stringify({
      achievements: [],
      newly_unlocked: [{
        key: 'first_day',
        category: 'tracking',
        kind: 'milestone',
        hidden: false,
        unlocked: true,
        unlocked_at: '2026-08-16T10:00:00Z',
        progress: 1,
        target: 1,
        sort_order: 10,
      }],
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await staleReconciliation

    expect(auth.newlyUnlockedAchievements).toEqual([])
    await auth.reconcileAchievements(true)
    expect(auth.newlyUnlockedAchievements).toEqual([])
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/achievements/reconcile'))).toHaveLength(2)
  })

  it('reloads the target setup state when another user logs in', async () => {
    const firstUser = {
      id: '1',
      username: 'first',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
    }
    const secondUser = { ...firstUser, id: '2', username: 'second' }
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(firstUser), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: 'target' }]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'bootstrap-csrf' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        achievements: [],
        newly_unlocked: [],
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        mfa_required: false,
        user: secondUser,
        csrf_token: 'second-csrf',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(secondUser), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
    const auth = useAuthStore()

    expect(await auth.ensureUser()).toBe(true)
    expect(auth.needsTargetSetup).toBe(false)
    expect(await auth.login('second', 'second-password')).toBe(true)
    expect(auth.needsTargetSetup).toBeNull()
    expect(await auth.ensureUser()).toBe(true)
    expect(auth.needsTargetSetup).toBe(true)
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
  it('behält User und CSRF-State bei /auth/me-Transportfehlern', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        mfa_required: false,
        user: { id: '1', username: 'admin', language: 'de', timezone: 'Europe/Berlin', week_starts_on: 0 },
        csrf_token: 'csrf',
      }), { status: 200 }))
      .mockRejectedValue(new TypeError('fetch failed'))
    const auth = useAuthStore()

    await auth.login('admin', 'password-password')
    expect(await auth.ensureUser()).toBe(true)
    expect(auth.user?.username).toBe('admin')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf')
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('behält User bei einem /auth/me-HTTP-500', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        mfa_required: false,
        user: { id: '1', username: 'admin', language: 'de', timezone: 'Europe/Berlin', week_starts_on: 0 },
        csrf_token: 'csrf',
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
    const auth = useAuthStore()

    await auth.login('admin', 'password-password')
    expect(await auth.ensureUser()).toBe(true)
    expect(auth.user?.username).toBe('admin')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('markiert einen Fresh Load bei Transportfehlern als vorübergehend nicht bestimmbar', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('fetch failed'))
    const auth = useAuthStore()

    expect(await auth.ensureUser()).toBe(false)
    expect(auth.user).toBeNull()
    expect(auth.sessionRestoreUnavailable).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('behandelt einen Fresh Load bei HTTP-500 ebenfalls nicht als Logout', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 500 }))
    const auth = useAuthStore()

    expect(await auth.ensureUser()).toBe(false)
    expect(auth.user).toBeNull()
    expect(auth.sessionRestoreUnavailable).toBe(true)
    expect(fetchMock).toHaveBeenCalledOnce()
  })
  it('überschreibt einen neuen Login nicht mit einem alten /auth/me-Transportfehler', async () => {
    let rejectAuth!: (reason: unknown) => void
    const pendingAuth = new Promise<Response>((_, reject) => { rejectAuth = reject })
    let authCalls = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) {
        authCalls += 1
        if (authCalls === 1) return pendingAuth
        throw new TypeError('fetch failed')
      }
      return new Response(JSON.stringify({
        mfa_required: false,
        user: { id: '2', username: 'new-user', language: 'en', timezone: 'Europe/Berlin', week_starts_on: 0 },
        csrf_token: 'new-csrf',
      }), { status: 200 })
    })
    const auth = useAuthStore()
    const staleRestore = auth.ensureUser()
    await Promise.resolve()
    await expect(auth.login('new-user', 'password-password')).resolves.toBe(true)
    rejectAuth(new TypeError('fetch failed'))

    await expect(staleRestore).resolves.toBe(true)
    expect(auth.user?.username).toBe('new-user')
    expect(i18n.global.locale.value).toBe('en')
    expect(sessionStorage.getItem('calograph_csrf')).toBe('new-csrf')
    expect(auth.sessionRestoreUnavailable).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(3)
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
  it('does not expire a newer session because of an older protected response', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    setCsrfToken('csrf-a')
    let resolveResponse!: (response: Response) => void
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => pendingResponse)

    const request = api('/dashboard/summary')
    await Promise.resolve()
    setCsrfToken(null)
    setCsrfToken('csrf-b')
    resolveResponse(new Response(JSON.stringify({ detail: 'Nicht authentifiziert' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    }))

    await expect(request).rejects.toMatchObject({ status: 401 })
    expect(expired).not.toHaveBeenCalled()
  })
  it('does not install a stale CSRF token after a session change', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    setCsrfToken(null)
    let resolveResponse!: (response: Response) => void
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => pendingResponse)

    const request = api('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ language: 'de' }),
    })
    await Promise.resolve()
    setCsrfToken('csrf-b')
    resolveResponse(new Response(JSON.stringify({ csrf_token: 'csrf-a' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    await expect(request).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(expired).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('calograph_csrf')).toBe('csrf-b')
  })
  it('signals expiry after a CSRF refresh when the following mutation is rejected', async () => {
    const expired = vi.fn()
    setAuthenticationExpiredHandler(expired)
    setCsrfToken(null)
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ csrf_token: 'csrf-a' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Nicht authentifiziert' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }))

    await expect(api('/settings/profile', {
      method: 'PUT',
      body: JSON.stringify({ language: 'de' }),
    })).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(2)
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
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
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
    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
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
    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
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
        JSON.stringify({ detail: 'Too many requests. Please try again later.' }),
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
      message: 'Too many requests. Please try again later.',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).has('X-CSRF-Token')).toBe(false)
  })

  it('returns to the public English locale on logout', async () => {
    const auth = useAuthStore()
    auth.user = {
      id: '1',
      username: 'admin',
      language: 'de',
      timezone: 'Europe/Berlin',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: true,
      is_active: true,
      deactivated_at: null,
    }
    setLocale('de')
    setCsrfToken('csrf')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))

    await auth.logout()

    expect(auth.user).toBeNull()
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

})
