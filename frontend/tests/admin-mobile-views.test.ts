import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: apiMock,
  localizeApiError: vi.fn(() => 'Fehler'),
}))
import AdminAuditView from '../src/views/AdminAuditView.vue'
import AdminInvitationsView from '../src/views/AdminInvitationsView.vue'
import AdminLogsView from '../src/views/AdminLogsView.vue'
import AdminOverviewView from '../src/views/AdminOverviewView.vue'
import { setLocale } from '../src/i18n'

beforeEach(() => {
  setLocale('de')
})

afterEach(() => {
  apiMock.mockReset()
  setLocale('de')
})

describe('mobile admin presentations', () => {
  it('renders invitation cards from the same invitation data as the desktop table', async () => {
    apiMock.mockResolvedValue([
      {
        id: 'invitation-1',
        created_at: '2026-08-20T12:00:00Z',
        expires_at: '2026-08-27T12:00:00Z',
        used_at: null,
        revoked_at: null,
      },
    ])
    const wrapper = mount(AdminInvitationsView)
    await flushPromises()

    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.findAll('.mobile-invitation-card')).toHaveLength(1)
    expect(wrapper.get('.mobile-invitation-card').text()).toContain('Offen')
    expect(wrapper.get('.mobile-invitation-card button').text()).toBe('Widerrufen')
    const createButton = wrapper.get('.admin-panel-header > .button')
    expect(createButton.text()).toBe('Einladung erstellen')
    expect(createButton.classes()).toContain('compact-action')
    wrapper.unmount()
  })

  it('renders audit cards and all responsive filter controls from the audit response', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/admin/audit')) {
        return Promise.resolve({
          items: [
            {
              id: 'event-1',
              occurred_at: '2026-08-20T12:00:00Z',
              event: 'auth.login.succeeded',
              outcome: 'success',
              auth_method: 'password',
              username: 'admin',
              client_ip: '192.0.2.1',
              client_ref: '1234567890abcdef',
              location: 'Berlin',
              provider: null,
              reason: null,
            },
            {
              id: 'event-2',
              occurred_at: '2026-08-20T12:05:00Z',
              event: 'auth.login.failed',
              outcome: 'failure',
              auth_method: 'api_token',
              username: 'Unbekannt',
              client_ip: '192.0.2.2',
              client_ref: 'fedcba0987654321',
              location: null,
              provider: null,
              reason: null,
            },
            {
              id: 'event-3',
              occurred_at: '2026-08-20T12:10:00Z',
              event: 'auth.login.mfa_required',
              outcome: 'pending',
              auth_method: 'password+mfa',
              username: 'admin',
              client_ip: null,
              client_ref: null,
              location: null,
              provider: null,
              reason: null,
            },
          ],
        })
      }
      return Promise.resolve([{ id: 'user-1', username: 'admin' }])
    })
    const wrapper = mount(AdminAuditView)
    await flushPromises()

    expect(wrapper.findAll('.filters .field')).toHaveLength(5)
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
    expect(wrapper.findAll('.mobile-audit-card')).toHaveLength(3)

    const desktopOutcomes = wrapper.findAll('.admin-desktop-table .audit-outcome')
    expect(desktopOutcomes).toHaveLength(3)
    expect(desktopOutcomes[0].classes()).toContain('success')
    expect(desktopOutcomes[0].attributes('aria-label')).toBe('Erfolgreich')
    expect(desktopOutcomes[0].find('svg').exists()).toBe(true)
    expect(desktopOutcomes[1].classes()).toContain('failure')
    expect(desktopOutcomes[1].attributes('aria-label')).toBe('Fehlgeschlagen')
    expect(desktopOutcomes[1].find('svg').exists()).toBe(true)
    expect(desktopOutcomes[2].classes()).toContain('neutral')
    expect(desktopOutcomes[2].attributes('aria-label')).toBe('Ausstehend')
    expect(desktopOutcomes[2].find('svg').exists()).toBe(true)

    const rows = wrapper.findAll('tbody tr')
    expect(rows[0].text()).toContain('Anmeldung erfolgreich')
    expect(rows[0].text()).toContain('auth.login.succeeded')
    expect(rows[0].text()).toContain('Passwort')
    expect(rows[1].text()).toContain('Anmeldung fehlgeschlagen')
    expect(rows[1].text()).toContain('API-Token')
    expect(rows[2].text()).toContain('MFA angefordert')
    expect(rows[2].text()).toContain('Passwort + MFA')
    expect(wrapper.text()).not.toContain('✓')
    expect(wrapper.text()).not.toContain('✕')

    const mobileCards = wrapper.findAll('.mobile-audit-card')
    expect(mobileCards[0].text()).toContain('Erfolgreich')
    expect(mobileCards[0].text()).toContain('1234567890abcdef')
    expect(mobileCards[1].text()).toContain('Fehlgeschlagen')
    expect(mobileCards[1].text()).toContain('API-Token')
    expect(mobileCards[2].text()).toContain('Ausstehend')
    expect(mobileCards[2].text()).toContain('Passwort + MFA')
    wrapper.unmount()
  })

  it('uses the shared semantic outcome icons in recent admin activity', async () => {
    apiMock.mockResolvedValue({
      active_users: 1,
      active_sessions: 1,
      open_invitations: 0,
      successful_logins_24h: 1,
      failed_logins_24h: 1,
      recent_events: [
        { id: 'event-success', event: 'auth.login.succeeded', outcome: 'success', username: 'admin' },
        { id: 'event-failure', event: 'auth.login.failed', outcome: 'failure', username: 'Unbekannt' },
        { id: 'event-pending', event: 'auth.login.mfa_required', outcome: 'pending', username: 'admin' },
      ],
    })
    const wrapper = mount(AdminOverviewView)
    await flushPromises()

    const outcomes = wrapper.findAll('.audit-outcome')
    expect(outcomes).toHaveLength(3)
    expect(outcomes[0].classes()).toContain('success')
    expect(outcomes[1].classes()).toContain('failure')
    expect(outcomes[2].classes()).toContain('neutral')
    expect(outcomes.map((item) => item.attributes('aria-label'))).toEqual([
      'Erfolgreich',
      'Fehlgeschlagen',
      'Ausstehend',
    ])
    expect(wrapper.text()).toContain('Anmeldung erfolgreich')
    expect(wrapper.text()).toContain('Anmeldung fehlgeschlagen')
    expect(wrapper.text()).toContain('MFA angefordert')
    expect(wrapper.text()).not.toContain('auth.login.')
    expect(wrapper.text()).not.toContain('✓')
    expect(wrapper.text()).not.toContain('✕')
    wrapper.unmount()
  })

  it('uses German date controls and formatted values in logs and audit pages', async () => {
    apiMock.mockImplementation((path: string) => {
      if (path.startsWith('/admin/logs')) {
        return Promise.resolve({
          items: [{
            occurred_at: '2026-08-25T17:20:00Z',
            level: 'INFO',
            action: 'GET /api/v1/admin/logs',
            duration_ms: 4,
            request_id: 'request-1',
            status: 200,
          }],
          buffer_limit: 500,
          persistence: 'process',
        })
      }
      if (path.startsWith('/admin/audit')) {
        return Promise.resolve({
          items: [{
            id: 'event-date',
            occurred_at: '2026-08-25T17:20:00Z',
            event: 'auth.login.succeeded',
            outcome: 'success',
            auth_method: 'password',
            username: 'admin',
            client_ip: null,
            client_ref: null,
            location: null,
            provider: null,
            reason: null,
          }],
        })
      }
      if (path === '/admin/users') return Promise.resolve([])
      return Promise.resolve({})
    })

    const logs = mount(AdminLogsView)
    const audit = mount(AdminAuditView)
    await flushPromises()

    expect(logs.findAll('.filters input[type="text"]').map((input) => input.attributes('placeholder'))).toEqual([
      'TT.MM.JJJJ',
      'TT.MM.JJJJ',
    ])
    expect(logs.text()).toContain('25.08.2026')
    expect(logs.text()).toContain('Nicht dauerhaft gespeichert')
    expect(logs.text()).not.toContain('(process)')
    expect(audit.findAll('.filters input[type="text"]').map((input) => input.attributes('placeholder'))).toEqual([
      'TT.MM.JJJJ',
      'TT.MM.JJJJ',
    ])
    expect(audit.text()).toContain('25.08.2026')
    logs.unmount()
    audit.unmount()
  })
})
