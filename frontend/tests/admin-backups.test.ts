import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AdminBackupsView from '../src/views/AdminBackupsView.vue'
import { getBackupStatus } from '../src/api'
import { setLocale } from '../src/i18n'

const healthyStatus = {
  schema_version: 1 as const,
  overall_state: 'healthy' as const,
  reason_codes: [],
  reported_at: '2026-09-01T08:00:00Z',
  freshness_threshold_seconds: 172800,
  automation: { last_success_at: '2026-09-01T07:00:00Z' },
  components: {
    database: { state: 'healthy' as const, verification: 'full' as const, encryption: 'age' as const, last_success_at: '2026-09-01T07:00:00Z' },
    environment_secrets: { state: 'healthy' as const, verification: 'full' as const, encryption: 'age' as const, matching_backup: true, last_success_at: '2026-09-01T07:00:00Z' },
  },
}

vi.mock('../src/api', async () => {
  const actual = await vi.importActual<typeof import('../src/api')>('../src/api')
  return { ...actual, getBackupStatus: vi.fn(async () => healthyStatus) }
})

describe('admin backups view', () => {
  it('renders four compact metrics and three peer cards without operational controls', async () => {
    setLocale('de')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('In Ordnung'))

    expect(wrapper.text()).toContain('Backup-Status')
    expect(wrapper.text()).toContain('In Ordnung')
    expect(wrapper.text()).toContain('Datenbanksicherung')
    expect(wrapper.text()).toContain('Umgebung & Secrets')
    expect(wrapper.text()).toContain('Isolierter Wiederherstellungstest')
    expect(wrapper.findAll('.backup-metric')).toHaveLength(4)
    expect(wrapper.findAll('.backup-component')).toHaveLength(3)
    expect(wrapper.findAll('h2')).toHaveLength(3)
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('input')).toHaveLength(0)
    const documentation = wrapper.get('a[href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md"]')
    expect(documentation.attributes('target')).toBe('_blank')
    expect(documentation.attributes('rel')).toBe('noopener noreferrer')
    expect(wrapper.get('.backup-metric .backup-metric-value').attributes('aria-live')).toBe('polite')
    wrapper.unmount()
  })

  it('keeps the boundary and uses one neutral unavailable alert on API failure', async () => {
    vi.mocked(getBackupStatus).mockRejectedValueOnce(new Error('transport'))
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1)
    expect(wrapper.text().match(/Status unavailable/g)).toHaveLength(1)
    expect(wrapper.get('.backup-metric').findAll('.backup-metric-helper')).toHaveLength(0)
    expect(wrapper.text()).toContain('Backup status could not be loaded. This does not mean a backup failed.')
    expect(wrapper.text()).toContain('Safe operational boundary')
    wrapper.unmount()
  })

  it('localizes every diagnostic and does not imply disabled secrets are encrypted', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      overall_state: 'attention',
      reason_codes: ['backup_missing', 'future_timestamp'],
      components: {
        ...healthyStatus.components,
        environment_secrets: { state: 'disabled' as const, verification: 'not_reported' as const, matching_backup: false },
      },
    })
    setLocale('de')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Kein erfolgreicher Backup-Lauf wurde gemeldet.'))

    expect(wrapper.text()).toContain('Der gemeldete Zeitpunkt liegt in der Zukunft und ist nicht vertrauenswürdig.')
    expect(wrapper.text()).toContain('Das Backup für Umgebung & Secrets ist per Konfiguration deaktiviert.')
    const secretsCard = wrapper.findAll('.backup-component')[1]
    expect(secretsCard.text()).not.toContain('Passt')
    expect(secretsCard.text()).not.toContain('age')
    wrapper.unmount()
  })

  it('marks stale reports and highlights the last complete metric', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      overall_state: 'attention',
      reason_codes: ['stale'],
      reported_at: '2026-09-01T08:00:00Z',
      components: { ...healthyStatus.components, database: { ...healthyStatus.components.database, last_success_at: '2026-08-20T07:00:00Z' } },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Stale'))
    expect(wrapper.find('.backup-metric.is-stale').exists()).toBe(true)
    expect(wrapper.text()).toContain('The last complete backup is older')
    wrapper.unmount()
  })

  it('computes component age from the current time and rejects invalid or future timestamps', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-09-02T08:00:00Z'))
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      reported_at: '2026-09-02T07:59:00Z',
      components: {
        ...healthyStatus.components,
        database: { ...healthyStatus.components.database, last_success_at: '2026-09-01T08:00:00Z' },
      },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Healthy'))

    const lastCompleteMetric = wrapper.findAll('.backup-metric')[1]
    expect(lastCompleteMetric.text()).toContain('1 day')
    expect(lastCompleteMetric.text()).not.toContain('Not reported')
    wrapper.unmount()

    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      components: {
        ...healthyStatus.components,
        database: { ...healthyStatus.components.database, last_success_at: '2026-09-03T08:00:00Z' },
      },
    })
    const futureWrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(futureWrapper.text()).toContain('Healthy'))
    expect(futureWrapper.findAll('.backup-metric')[1].text()).toContain('Not reported')
    expect(futureWrapper.text()).not.toContain('NaN')
    futureWrapper.unmount()

    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      components: {
        ...healthyStatus.components,
        database: { ...healthyStatus.components.database, last_success_at: 'not-a-timestamp' },
      },
    })
    const invalidWrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(invalidWrapper.text()).toContain('Healthy'))
    expect(invalidWrapper.findAll('.backup-metric')[1].text()).toContain('Not reported')
    expect(invalidWrapper.text()).not.toContain('NaN')
    invalidWrapper.unmount()
  })

  it('uses component-specific full verification details', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      components: {
        ...healthyStatus.components,
        database: { ...healthyStatus.components.database, last_verified_at: '2026-09-02T07:00:00Z' },
        environment_secrets: { ...healthyStatus.components.environment_secrets, last_verified_at: '2026-09-02T07:00:00Z' },
      },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('pg_restore'))

    const components = wrapper.findAll('.backup-component')
    expect(components[0].text()).toContain('pg_restore')
    expect(components[1].text()).toContain('tar archive')
    expect(components[1].text()).not.toContain('pg_restore')
    wrapper.unmount()
  })

  it('renders disabled automation as a normal neutral state', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      overall_state: 'disabled',
      reason_codes: ['deactivated'],
      automation: { enabled: false },
      components: {
        database: { state: 'disabled' as const },
        environment_secrets: { state: 'disabled' as const },
      },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Disabled'))
    expect(wrapper.text()).toContain('Not scheduled')
    expect(wrapper.text()).toContain('Environment & secrets backup is disabled by configuration.')
    expect(wrapper.text()).toContain('Automated backups are disabled; no current database artifact is reported.')
    wrapper.unmount()
  })

  it('collapses a missing report without placeholder rows or enabled secrets claims', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      schema_version: 1,
      overall_state: 'unknown',
      reason_codes: ['report_missing'],
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Status unavailable'))
    expect(wrapper.text()).toContain('No backup status report is available.')
    expect(wrapper.get('.backup-protection').text()).toContain('Not reported')
    expect(wrapper.get('.backup-protection').text()).not.toContain('Enabled')
    expect(wrapper.findAll('.backup-component')).toHaveLength(3)
    expect(wrapper.findAll('.backup-component dl')).toHaveLength(0)
    wrapper.unmount()
  })

  it('keeps loading state compact and announced', async () => {
    vi.mocked(getBackupStatus).mockImplementationOnce(() => Promise.withResolvers<never>().promise)
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Loading backup status'))
    expect(wrapper.find('[aria-busy="true"]').exists()).toBe(true)
    expect(wrapper.findAll('.backup-metric-skeleton')).toHaveLength(4)
    wrapper.unmount()
  })

  it('distinguishes checksum evidence from an isolated restore test', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      components: {
        database: { state: 'attention' as const, verification: 'checksum' as const, encryption: 'age' as const, last_success_at: '2026-09-01T07:00:00Z' },
        environment_secrets: { state: 'healthy' as const, verification: 'checksum' as const, encryption: 'age' as const, matching_backup: true, last_success_at: '2026-09-01T07:00:00Z' },
        restore_test: { last_restore_test_at: '2026-08-01T07:00:00Z', off_host_copy: true, immutable_copy: false },
      },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Checksum only'))
    expect(wrapper.text()).toContain('Isolated restore test')
    expect(wrapper.text()).toContain('Reported')
    expect(wrapper.text()).toContain('Recoverability has not been verified.')
    expect(wrapper.text()).not.toContain('Archive decrypted and processed with pg_restore')
    wrapper.unmount()
  })
})
