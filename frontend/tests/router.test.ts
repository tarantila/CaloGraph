import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import router from '../src/router'
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
      const body = url.endsWith('/settings/targets') ? [{ id: 'target' }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/tage')

    expect(router.currentRoute.value.name).toBe('daily')
    expect(useAuthStore().needsTargetSetup).toBe(false)
  })

  it('keeps users with targets out of the setup route', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      const body = url.endsWith('/settings/targets') ? [{ id: 'target' }] : user
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    await router.push('/einrichtung')

    expect(router.currentRoute.value.name).toBe('overview')
    expect(useAuthStore().needsTargetSetup).toBe(false)
  })

  it('leaves public login and recovery routes available during setup', async () => {
    const auth = useAuthStore()
    auth.user = user
    auth.needsTargetSetup = true
    const fetchMock = vi.spyOn(globalThis, 'fetch')

    await router.push('/recovery')

    expect(router.currentRoute.value.name).toBe('recovery')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
