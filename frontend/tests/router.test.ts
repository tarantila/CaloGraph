import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import router from '../src/router'
import { i18n, setLocale } from '../src/i18n'
import { useAuthStore } from '../src/stores/auth'

const user = {
  id: 'user-1',
  username: 'friend',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  raw_payload_retention_days: 0,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

describe('first-run target routing', () => {
  beforeEach(async () => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    setActivePinia(createPinia())
    await router.replace('/login')
  })

  it('forces targetless users into the dedicated setup route after login', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/tage')

    expect(router.currentRoute.value.name).toBe('setup')
    expect(router.currentRoute.value.path).toBe('/einrichtung')
    expect(useAuthStore().needsTargetSetup).toBe(true)
  })

  it('keeps the requested route for users with an existing target', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/tage')

    expect(router.currentRoute.value.name).toBe('daily')
    expect(useAuthStore().needsTargetSetup).toBe(false)
  })

  it('applies the authenticated profile locale before rendering protected routes', async () => {
    setLocale('en')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : user
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    await router.push('/konto')

    expect(i18n.global.locale.value).toBe('de')
    expect(document.documentElement.lang).toBe('de')
  })

  it('restores English for an English profile before the protected route renders', async () => {
    const englishUser = { ...user, language: 'en' }
    setLocale('de')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : englishUser
      return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

    await router.push('/trends')

    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('keeps users with targets out of the setup route', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/einrichtung')

    expect(router.currentRoute.value.name).toBe('overview')
    expect(useAuthStore().needsTargetSetup).toBe(false)
  })

  it('leaves public login and recovery routes in the public English locale during setup', async () => {
    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = true
    const pendingProfileGeneration = auth.beginProfileUpdate()
    setLocale('de')
    const fetchMock = vi.spyOn(globalThis, 'fetch')

    await router.push('/recovery')

    expect(auth.commitProfileUpdate(pendingProfileGeneration, user)).toBe(false)
    expect(router.currentRoute.value.name).toBe('recovery')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('does not let a cancelled protected restore overwrite the public locale', async () => {
    let resolveAuth!: (response: Response) => void
    const pendingAuth = new Promise<Response>((resolve) => { resolveAuth = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/auth/me')) return pendingAuth
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    const protectedNavigation = router.push('/tage')
    await Promise.resolve()
    await router.push('/recovery')
    expect(i18n.global.locale.value).toBe('en')

    resolveAuth(new Response(JSON.stringify(user), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await protectedNavigation

    expect(router.currentRoute.value.name).toBe('recovery')
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })

  it('preserves a pending profile update during protected navigation restore', async () => {
    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = false
    const pendingGeneration = auth.beginProfileUpdate('en')
    let release!: () => void
    const blocker = new Promise<void>((resolve) => { release = resolve })
    const pendingSave = auth.enqueueProfileUpdate(pendingGeneration, async () => {
      await blocker
      return { ...user, language: 'en' }
    })
    await Promise.resolve()
    setLocale('de')
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/tage')

    release()
    const savedUser = await pendingSave
    expect(auth.commitProfileUpdate(pendingGeneration, savedUser!)).toBe(true)
    expect(auth.user?.language).toBe('en')
    expect(i18n.global.locale.value).toBe('en')
    expect(document.documentElement.lang).toBe('en')
  })
  it('blocks non-administrators from the backups route', async () => {
    const auth = useAuthStore()
    auth.user = { ...user, is_admin: false }
    auth.needsTargetSetup = false
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target', target_weight_min_kg: null, target_weight_max_kg: null }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/admin/backups')

    expect(router.currentRoute.value.name).toBe('overview')
  })
  it('leitet bei Fresh-Load-Transportfehler nicht zum Login um', async () => {
    const auth = useAuthStore()
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'))

    await router.push('/tage')

    expect(router.currentRoute.value.name).toBe('login')
    expect(auth.user).toBeNull()
    expect(auth.sessionRestoreUnavailable).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('hält eine bestehende Session bei Transportfehlern während Navigation', async () => {
    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = false
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('fetch failed'))

    await router.push('/tage')

    expect(router.currentRoute.value.name).toBe('daily')
    expect(auth.user).toEqual(user)
    expect(auth.sessionRestoreUnavailable).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('maps every Account Center child and redirects /konto to personal data', async () => {
    const accountRoutes = [
      ['/konto/persoenliche-daten', 'account-personal'],
      ['/konto/budgets-und-ziele', 'account-targets'],
      ['/konto/importe', 'account-imports'],
      ['/konto/datenstatus', 'account-data-status'],
      ['/konto/integrationen', 'account-integrations'],
      ['/konto/daten-und-datenschutz', 'account-data-privacy'],
      ['/konto/allgemeine-einstellungen', 'account-general'],
      ['/konto/sicherheit', 'account-security'],
    ] as const

    for (const [path, name] of accountRoutes) {
      const resolved = router.resolve(path)
      expect(resolved.name).toBe(name)
      expect(resolved.path).toBe(path)
    }

    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = false
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(user), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    await router.push('/konto')
    expect(router.currentRoute.value.name).toBe('account-personal')
    expect(router.currentRoute.value.path).toBe('/konto/persoenliche-daten')
  })

  it('does not resolve removed settings paths to former Account Center pages', async () => {
    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = false
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(user), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    for (const path of ['/importe', '/datenqualitaet', '/budgets-und-ziele', '/einstellungen']) {
      await router.push(path)
      expect(router.currentRoute.value.name).toBe('overview')
      expect(router.currentRoute.value.path).toBe('/')
    }
  })
})
