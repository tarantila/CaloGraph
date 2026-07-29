import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, setCsrfToken } from '../src/api'
import { useAuthStore } from '../src/stores/auth'

describe('authentication store', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    setCsrfToken(null)
  })

  it('stores the user returned by login', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
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
})
