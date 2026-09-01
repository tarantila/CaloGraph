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
  it('renders reported healthy status without operational controls', async () => {
    setLocale('de')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('In Ordnung'))

    expect(wrapper.text()).toContain('Backup-Status')
    expect(wrapper.text()).toContain('In Ordnung')
    expect(wrapper.text()).toContain('Datenbanksicherung')
    expect(wrapper.text()).toContain('Umgebung & Secrets')
    expect(wrapper.findAll('h2')).toHaveLength(3)
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('input')).toHaveLength(0)
    const documentation = wrapper.get('a[href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md"]')
    expect(documentation.attributes('target')).toBe('_blank')
    expect(documentation.attributes('rel')).toBe('noopener noreferrer')
    wrapper.unmount()
  })

  it('keeps the boundary and uses neutral unavailable state on API failure', async () => {
    vi.mocked(getBackupStatus).mockRejectedValueOnce(new Error('transport'))
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Status unavailable')
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
    const secretsCard = wrapper.findAll('.backup-component')[1]
    expect(secretsCard.text()).not.toContain('Passt')
    expect(secretsCard.text()).not.toContain('age')
    wrapper.unmount()
  })
})
