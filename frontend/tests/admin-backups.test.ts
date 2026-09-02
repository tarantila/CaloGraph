import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import AdminBackupsView from '../src/views/AdminBackupsView.vue'
import { getBackupStatus } from '../src/api'
import { setLocale } from '../src/i18n'
import type { BackupStatus, RestoreTestState } from '../src/types'

const healthyStatus: BackupStatus = {
  schema_version: 1,
  overall_state: 'healthy',
  reason_codes: [],
  reported_at: '2026-09-01T08:00:00Z',
  freshness_threshold_seconds: 172800,
  automation: { enabled: true, last_success_at: '2026-09-01T07:00:00Z', next_run_at: '2026-09-02T07:00:00Z' },
  components: {
    database: { state: 'healthy', encryption: 'age', matching_backup: true, last_success_at: '2026-09-01T07:00:00Z' },
    environment_secrets: { state: 'healthy', encryption: 'age', matching_backup: true, last_success_at: '2026-09-01T07:00:00Z' },
  },
  recovery: {
    overall_state: 'attention',
    archive_verification: {
      overall_state: 'attention',
      components: {
        database: { state: 'not_verified', verified_at: '2026-08-01T08:00:00Z', latest_artifact_verified: false },
        environment_secrets: { state: 'not_verified', latest_artifact_verified: false },
      },
    },
    restore_test: { state: 'never_tested', result: 'NEVER_TESTED' },
  },
}

vi.mock('../src/api', async () => {
  const actual = await vi.importActual<typeof import('../src/api')>('../src/api')
  return { ...actual, getBackupStatus: vi.fn(async () => healthyStatus) }
})

function statusWithRestore(state: RestoreTestState): BackupStatus {
  return {
    ...healthyStatus,
    recovery: {
      ...healthyStatus.recovery!,
      restore_test: {
        state,
        result: state === 'failed' ? 'RESTORE_TEST_FAILED' : state === 'never_tested' ? 'NEVER_TESTED' : 'RESTORE_TESTED',
        tested_at: state === 'never_tested' ? undefined : '2026-08-01T08:00:00Z',
        last_success_at: state === 'current' || state === 'due' ? '2026-08-01T08:00:00Z' : undefined,
        next_due_at: state === 'current' ? '2026-10-30T08:00:00Z' : undefined,
        failure_code: state === 'failed' ? 'schema_check_failed' : undefined,
      },
    },
  }
}

describe('admin backups view', () => {
  it('separates healthy backup operation from unverified newest archive', async () => {
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Healthy'))

    expect(wrapper.text()).toContain('Backup operation')
    expect(wrapper.text()).toContain('Last successful backup')
    expect(wrapper.text()).toContain('Next scheduled backup')
    expect(wrapper.text()).toContain('Recovery checks')
    expect(wrapper.text()).toContain('A previous archive was externally verified')
    expect(wrapper.text()).toContain('the latest artifact is not verified')
    expect(wrapper.text()).not.toContain('Latest required components are complete, fresh, matching, and externally verified')
    expect(wrapper.findAll('.backup-metric')).toHaveLength(4)
    expect(wrapper.findAll('.backup-component')).toHaveLength(4)
    expect(wrapper.findAll('button')).toHaveLength(0)
    wrapper.unmount()
  })

  it('renders independent archive and isolated-test action links in German', async () => {
    setLocale('de')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Externe Archivprüfung'))

    expect(wrapper.text()).toContain('Nie getestet')
    expect(wrapper.text()).toContain('Vom Betreiber ausgeführt')
    expect(wrapper.text()).toContain('keine Datenbank wurde wiederhergestellt')
    expect(wrapper.find('a[href$="docs/backup-restore.md#5"]').exists()).toBe(true)
    expect(wrapper.find('a[href$="docs/backup-restore.md#6"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it.each([
    ['never_tested', 'Never tested', true],
    ['current', 'Current', false],
    ['due', 'Due', true],
    ['unknown', 'Unknown', true],
    ['failed', 'Failed', true],
  ] as const)('shows restore-test state %s with accurate guidance', async (state, label, needsAction) => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce(statusWithRestore(state))
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain(label))
    const restoreCard = wrapper.find('[aria-labelledby="backup-restore-heading"]')
    expect(restoreCard.text()).toContain('separate disposable PostgreSQL')
    expect(restoreCard.find('a[href$="docs/backup-restore.md#6"]').exists()).toBe(needsAction)
    expect(restoreCard.text()).toContain(needsAction ? 'Diagnosis and action' : 'Last successful isolated test')
    wrapper.unmount()
  })

  it('keeps operation attention diagnostic and action visible for one reason', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      overall_state: 'attention',
      reason_codes: ['stale'],
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Needs attention'))
    expect(wrapper.text()).toContain('The last successful backup is stale.')
    expect(wrapper.findAll('.backup-health-reasons li')).toHaveLength(1)
    wrapper.unmount()
  })

  it('keeps a failed latest attempt separate from recovery checks', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      overall_state: 'failed',
      reason_codes: ['latest_attempt_failed'],
      automation: { enabled: true, last_attempt_at: '2026-09-02T08:00:00Z' },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Failed'))
    expect(wrapper.text()).toContain('The latest backup attempt failed')
    expect(wrapper.text()).toContain('Recovery checks')
    wrapper.unmount()
  })

  it('uses one neutral unavailable alert on API failure', async () => {
    vi.mocked(getBackupStatus).mockRejectedValueOnce(new Error('transport'))
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.find('[role="alert"]').exists()).toBe(true))
    expect(wrapper.findAll('[role="alert"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('Backup status could not be loaded. This does not mean a backup failed.')
    expect(wrapper.find('a[href$="docs/backup-restore.md#5"]').exists()).toBe(false)
    expect(wrapper.find('a[href$="docs/backup-restore.md#6"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Select the newest artifact')
    wrapper.unmount()
  })

  it('keeps loading state compact and announced', async () => {
    vi.mocked(getBackupStatus).mockImplementationOnce(() => Promise.withResolvers<BackupStatus>().promise)
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Loading backup status'))
    expect(wrapper.find('[aria-busy="true"]').exists()).toBe(true)
    expect(wrapper.findAll('.backup-metric-skeleton')).toHaveLength(4)
    wrapper.unmount()
  })

  it('does not treat malformed recovery evidence as never tested', async () => {
    vi.mocked(getBackupStatus).mockResolvedValueOnce({
      ...healthyStatus,
      recovery: {
        ...healthyStatus.recovery!,
        restore_test: { state: 'unknown', reason: 'evidence_unavailable' },
      },
    })
    setLocale('en')
    const wrapper = mount(AdminBackupsView)
    await vi.waitFor(() => expect(wrapper.text()).toContain('Unknown'))
    const restoreCard = wrapper.find('[aria-labelledby="backup-restore-heading"]')
    expect(restoreCard.text()).not.toContain('Never tested')
    expect(restoreCard.text()).toContain('Provide a valid sanitized restore-test record')
    expect(restoreCard.find('a[href$="docs/backup-restore.md#6"]').exists()).toBe(true)
    wrapper.unmount()
  })
})
