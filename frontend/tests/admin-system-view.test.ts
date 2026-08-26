import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/api', () => ({
  ApiError: class ApiError extends Error {},
  api: vi.fn(),
  localizeApiError: vi.fn(() => 'Fehler'),
}))

import { api } from '../src/api'
import { setLocale } from '../src/i18n'
import AdminSystemView from '../src/views/AdminSystemView.vue'

const mockedApi = vi.mocked(api)

function response(status: 'current' | 'update_available' | 'development' | 'unknown') {
  return {
    version: {
      running: '0.5.0',
      latest: status === 'unknown' ? null : '0.4.2',
      status,
      release_url: status === 'unknown' ? null : 'https://github.com/tarantila/CaloGraph/releases/tag/v0.4.2',
      checked_at: '2026-08-24T00:00:00Z',
    },
    database: 'healthy' as const,
    security_audit_retention_days: 90,
    security_audit_enabled: true,
    security_audit_events_24h: 37,
    failed_logins_24h: 2,
    yazio_scheduler_enabled: true,
    yazio_scheduler_available: true,
  }
}

beforeEach(() => {
  setLocale('de')
})

afterEach(() => {
  vi.clearAllMocks()
  setLocale('de')
})

describe('admin system status', () => {
  it.each([
    ['current', 'Aktuell'],
    ['update_available', 'Update verfügbar'],
    ['development', 'Entwicklungsversion'],
    ['unknown', 'Prüfung nicht möglich'],
  ] as const)('renders the %s release state', async (status, label) => {
    mockedApi.mockResolvedValue(response(status))
    const wrapper = mount(AdminSystemView)
    await flushPromises()

    expect(wrapper.get('h1').text()).toBe('Systemstatus')
    expect(wrapper.text()).toContain(label)
    wrapper.unmount()
  })

  it('shows current audit metrics without presenting retention as status', async () => {
    mockedApi.mockResolvedValue(response('development'))
    const wrapper = mount(AdminSystemView)
    await flushPromises()

    expect(wrapper.text()).toContain('0.5.0')
    expect(wrapper.text()).toContain('0.4.2')
    expect(wrapper.text()).toContain('Datenbank')
    expect(wrapper.text()).toContain('Betriebsbereit')
    expect(wrapper.text()).not.toContain('Healthy')
    expect(wrapper.text()).toContain('YAZIO-Scheduler')
    const auditCard = wrapper.get('.system-audit-card')
    expect(auditCard.text()).toContain('37')
    expect(auditCard.text()).toContain('Ereignisse / 24 h')
    expect(auditCard.text()).toContain('2')
    expect(auditCard.text()).toContain('fehlgeschlagene Anmeldungen / 24 h')
    expect(auditCard.text()).not.toContain('Aktiv')
    expect(auditCard.text()).not.toContain('Aufbewahrung')
    expect(auditCard.text()).not.toContain('90 Tage')
    expect(auditCard.find('.system-status-badge').exists()).toBe(false)
    expect(auditCard.get('.system-audit-failures').classes()).toContain('attention')
    expect(wrapper.get('a[target="_blank"]').attributes('rel')).toBe('noopener noreferrer')
    wrapper.unmount()
  })

  it('renders zero audit metrics without an unknown state', async () => {
    mockedApi.mockResolvedValue({
      ...response('current'),
      security_audit_events_24h: 0,
      failed_logins_24h: 0,
    })
    const wrapper = mount(AdminSystemView)
    await flushPromises()

    const auditCard = wrapper.get('.system-audit-card')
    expect(auditCard.get('.system-audit-primary dd').text()).toBe('0')
    expect(auditCard.get('.system-audit-failures dd').text()).toBe('0')
    expect(auditCard.get('.system-audit-failures').classes()).not.toContain('attention')
    expect(auditCard.text()).not.toContain('Prüfung nicht möglich')
    wrapper.unmount()
  })
})
