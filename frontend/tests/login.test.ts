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

  it('uses an invitation without requiring an existing CSRF session', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: '2', username: 'friend', is_admin: false }), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        invitation_token: 'invite_example',
        username: 'friend',
        password: 'a-long-personal-password',
      }),
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/auth/register')
    expect(new Headers(options?.headers).has('X-CSRF-Token')).toBe(false)
  })
})
