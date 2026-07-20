import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../src/stores/auth'

describe('authentication store', () => {
  beforeEach(() => setActivePinia(createPinia()))

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
})

