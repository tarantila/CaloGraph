import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    constructor(message: string, public status: number) {
      super(message)
    }
  },
}))

import UserManagement from '../src/components/UserManagement.vue'
import { ApiError } from '../src/api'
import type { User } from '../src/types'

const admin: User = {
  id: 'admin-id',
  username: 'admin',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  raw_payload_retention_days: 0,
  is_admin: true,
  is_active: true,
  deactivated_at: null,
}
const activeUser: User = {
  ...admin,
  id: 'active-id',
  username: 'active-user',
  is_admin: false,
}
const inactiveUser: User = {
  ...activeUser,
  id: 'inactive-id',
  username: 'inactive-user',
  is_active: false,
  deactivated_at: '2026-08-10T12:00:00Z',
}
const otherInactiveUser: User = {
  ...inactiveUser,
  id: 'other-id',
  username: 'other-user',
}


function mountManagement() {
  return mount(UserManagement, {
    props: {
      users: [activeUser, inactiveUser, otherInactiveUser, admin],
      currentUserId: admin.id,
    },
  })
}

function rowFor(wrapper: VueWrapper, username: string) {
  const row = wrapper.findAll('tbody tr').find((candidate) => candidate.text().includes(username))
  expect(row).toBeDefined()
  return row!
}

function buttonNamed(wrapper: VueWrapper, name: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text() === name)
  expect(button).toBeDefined()
  return button!
}

describe('UserManagement', () => {
  beforeEach(() => {
    apiMock.mockReset()
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    })
  })

  it('shows lifecycle state and never offers actions for the current administrator', () => {
    const wrapper = mountManagement()

    expect(rowFor(wrapper, 'admin').text()).toContain('Du')
    expect(rowFor(wrapper, 'admin').text()).toContain('Eigenes Konto')
    expect(rowFor(wrapper, 'admin').findAll('button')).toHaveLength(0)
    expect(rowFor(wrapper, 'active-user').text()).toContain('Aktiv')
    expect(rowFor(wrapper, 'active-user').text()).toContain('Deaktivieren')
    expect(rowFor(wrapper, 'inactive-user').text()).toContain('Deaktiviert')
    expect(rowFor(wrapper, 'inactive-user').text()).toContain('seit 10.8.2026')
    expect(rowFor(wrapper, 'inactive-user').text()).toContain('Recovery ausstellen')
    expect(rowFor(wrapper, 'inactive-user').text()).toContain('Authentikatoren zurücksetzen')
    expect(rowFor(wrapper, 'inactive-user').text()).toContain('Endgültig löschen')
  })

  it('confirms deactivation and refreshes only after the backend succeeds', async () => {
    let resolveRequest: (() => void) | undefined
    apiMock.mockReturnValue(new Promise<void>((resolve) => { resolveRequest = resolve }))
    const wrapper = mountManagement()

    await rowFor(wrapper, 'active-user').get('button').trigger('click')
    expect(wrapper.text()).toContain('Sitzungen und API-Tokens werden ungültig')
    await buttonNamed(wrapper, 'Benutzer deaktivieren').trigger('click')
    expect(apiMock).toHaveBeenCalledWith('/users/active-id/deactivate', { method: 'POST' })
    expect(wrapper.emitted('refresh')).toBeUndefined()

    resolveRequest!()
    await flushPromises()
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    expect(wrapper.emitted('message')?.at(-1)).toEqual(['active-user wurde deaktiviert.'])
  })

  it('keeps lifecycle errors visible inside the open confirmation dialog', async () => {
    apiMock.mockRejectedValue(new ApiError('Für dieses Konto läuft bereits eine Benutzeroperation.', 409))
    const wrapper = mountManagement()

    await rowFor(wrapper, 'active-user').get('button').trigger('click')
    await buttonNamed(wrapper, 'Benutzer deaktivieren').trigger('click')
    await flushPromises()

    expect(wrapper.get('.action-dialog').text()).toContain(
      'Für dieses Konto läuft bereits eine Benutzeroperation.',
    )
    expect(wrapper.get('.action-dialog').attributes('role')).toBe('dialog')
    expect(wrapper.emitted('refresh')).toBeUndefined()
  })

  it('reactivates an inactive account without pretending to restore old credentials', async () => {
    apiMock.mockResolvedValue(undefined)
    const wrapper = mountManagement()

    await buttonNamed(wrapper, 'Reaktivieren').trigger('click')
    expect(wrapper.text()).toContain('Alte Sitzungen, API-Tokens und Einladungen bleiben ungültig')
    await buttonNamed(wrapper, 'Benutzer reaktivieren').trigger('click')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/users/inactive-id/reactivate', { method: 'POST' })
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })

  it('keeps recovery credentials and token transient and copies only after a click', async () => {
    apiMock.mockResolvedValue({
      recovery_token: 'recovery-secret',
      expires_at: '2026-08-11T12:30:00Z',
    })
    const wrapper = mountManagement()

    await buttonNamed(wrapper, 'Recovery ausstellen').trigger('click')
    await wrapper.get('input[autocomplete="current-password"]').setValue('admin-password')
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('AB12CD34EF56')
    expect(wrapper.get('.table-scroll').attributes('inert')).toBeDefined()
    await rowFor(wrapper, 'other-user')
      .get('button:nth-of-type(3)')
      .trigger('click')
    await wrapper.get('.action-dialog form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/users/inactive-id/recovery-links', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'admin-password', code: 'AB12CD34EF56' }),
    })
    expect(wrapper.text()).toContain('/recovery#token=recovery-secret')
    expect(navigator.clipboard.writeText).not.toHaveBeenCalled()

    await buttonNamed(wrapper, 'Link kopieren').trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
      `${window.location.origin}/recovery#token=recovery-secret`,
    )
    await buttonNamed(wrapper, 'Schließen').trigger('click')
    expect(wrapper.text()).not.toContain('recovery-secret')
    expect(wrapper.find('input[autocomplete="current-password"]').exists()).toBe(false)
  })

  it('requires fresh reauthentication for authenticator reset', async () => {
    apiMock.mockResolvedValue(undefined)
    const wrapper = mountManagement()

    await buttonNamed(wrapper, 'Authentikatoren zurücksetzen').trigger('click')
    await wrapper.get('input[autocomplete="current-password"]').setValue('admin-password')
    await wrapper.get('.action-dialog form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/users/inactive-id/authenticators/reset', {
      method: 'POST',
      body: JSON.stringify({ current_password: 'admin-password', code: null }),
    })
  })

  it('enables hard delete only for exact username confirmation and fresh reauthentication', async () => {
    apiMock.mockResolvedValue(undefined)
    const wrapper = mountManagement()

    await buttonNamed(wrapper, 'Endgültig löschen').trigger('click')
    const submit = buttonNamed(wrapper, 'Konto endgültig löschen')
    await wrapper.get('input[autocomplete="current-password"]').setValue('admin-password')
    await wrapper.get('input[autocomplete="off"]').setValue('INACTIVE-USER')
    expect(submit.attributes('disabled')).toBeDefined()
    await wrapper.get('input[autocomplete="off"]').setValue('inactive-user')
    expect(submit.attributes('disabled')).toBeUndefined()
    await wrapper.get('.action-dialog form').trigger('submit')
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/users/inactive-id', {
      method: 'DELETE',
      body: JSON.stringify({
        current_password: 'admin-password',
        code: null,
        confirm_username: 'inactive-user',
      }),
    })
    expect(wrapper.emitted('refresh')).toHaveLength(1)
  })
})
