<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { PhArrowSquareOut, PhCheckCircle, PhDatabase, PhInfo, PhLockKey, PhQuestion, PhWarningCircle, PhXCircle } from '@phosphor-icons/vue'
import { ApiError, getBackupStatus, localizeApiError } from '../api'
import { formatDateTime } from '../date-format'
import { i18n } from '../i18n'
import type { ArchiveVerificationStatus, BackupHealthState, BackupStatus, RestoreTestState } from '../types'

const t = i18n.global.t.bind(i18n.global)
const loading = ref(true)
const error = ref('')
const status = ref<BackupStatus>({ schema_version: 1, overall_state: 'unknown', reason_codes: [] })
const state = computed<BackupHealthState>(() => error.value ? 'unknown' : status.value.overall_state)
const statusKind = computed(() => state.value === 'attention' && status.value.reason_codes.includes('stale') ? 'stale' : state.value)
const stateLabel = computed(() => t(`backupHealth.states.${statusKind.value}`))
const stateIcon = computed(() => ({ healthy: PhCheckCircle, stale: PhWarningCircle, attention: PhWarningCircle, failed: PhXCircle, unknown: PhQuestion, disabled: PhInfo }[statusKind.value]))
const automationDisabled = computed(() => status.value.automation?.enabled === false || state.value === 'disabled')
const recovery = computed(() => status.value.recovery)
const recoveryState = computed<BackupHealthState>(() => recovery.value?.overall_state || 'unknown')
const restoreTest = computed(() => recovery.value?.restore_test)
const restoreState = computed<RestoreTestState>(() => restoreTest.value?.state || 'unknown')
const restoreLabel = computed(() => t(`backupHealth.recoveryStates.${restoreState.value}`))
const archive = computed(() => recovery.value?.archive_verification.components || {})
const archiveOverallState = computed<BackupHealthState>(() => recovery.value?.archive_verification.overall_state || 'unknown')
const recoveryDisabled = computed(() => !error.value && (state.value === 'disabled' || recoveryState.value === 'disabled'))
const archiveDisabled = computed(() => !error.value && (recoveryDisabled.value || archiveOverallState.value === 'disabled'))
const archiveNeedsAction = computed(() => !error.value && !archiveDisabled.value && ['attention', 'unknown', 'failed'].includes(archiveOverallState.value))
const restoreNeedsAction = computed(() => !error.value && !recoveryDisabled.value && ['due', 'never_tested', 'unknown', 'failed'].includes(restoreState.value))
const secretsComponent = computed(() => status.value.components?.environment_secrets)
const secretsDisabled = computed(() => automationDisabled.value || secretsComponent.value?.state === 'disabled')
const databaseDisabled = computed(() => automationDisabled.value || status.value.components?.database?.state === 'disabled')
const databaseState = computed<BackupHealthState>(() => status.value.components?.database?.state || 'unknown')
const secretsState = computed<BackupHealthState>(() => secretsComponent.value?.state || 'unknown')

function stateText(value: BackupHealthState): string {
  return t(`backupHealth.states.${value}`)
}
function operationCardLabel(value: BackupHealthState): string {
  return error.value ? t('backupHealth.unavailable') : stateText(value)
}
function recoveryCardLabel(value: BackupHealthState): string {
  return error.value ? t('backupHealth.unavailable') : stateText(value)
}

function reasonText(code: string): string {
  const key = `backupHealth.reasons.${code}`
  const translated = t(key)
  return translated === key ? t('backupHealth.reportUnavailable') : translated
}
const reasonLabels = computed(() => [...new Set(status.value.reason_codes)].map(reasonText))
const statusMessage = computed(() => {
  if (error.value) return t('backupHealth.reportUnavailable')
  if (statusKind.value === 'failed' && status.value.reason_codes.includes('latest_attempt_failed')) return t('backupHealth.latestAttemptFailedAt', { date: displayDate(status.value.automation?.last_attempt_at) })
  if (statusKind.value === 'unknown' && status.value.reason_codes.length) return reasonText(status.value.reason_codes[0])
  return t(`backupHealth.messages.${statusKind.value === 'stale' ? 'attention' : statusKind.value}`)
})
function displayDate(value: string | undefined): string { return value ? formatDateTime(value) : t('backupHealth.notReported') }
function displaySchedule(): string {
  const automation = status.value.automation
  return automation?.schedule_time && automation.schedule_timezone ? t('backupHealth.dailyAt', { time: automation.schedule_time, timezone: automation.schedule_timezone }) : t('backupHealth.notReported')
}
function displayAge(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return t('backupHealth.notReported')
  const days = Math.round(seconds / 86400)
  return days > 0 ? `${days} ${t(days === 1 ? 'common.day' : 'common.days')}` : `${Math.max(1, Math.round(seconds / 3600))} ${t('common.hours')}`
}
function displayThreshold(seconds: number | undefined): string { return displayAge(seconds) }
function displayVerification(value: ArchiveVerificationStatus | undefined): string {
  if (!value) return t('backupHealth.notReported')
  if (value.state === 'verified') return t('backupHealth.verification.verified')
  if (value.state === 'disabled') return t('backupHealth.states.disabled')
  if (value.state === 'unknown') return t('backupHealth.verification.unknown')
  return t('backupHealth.verification.notVerified')
}
function archiveDetail(value: ArchiveVerificationStatus | undefined, component: 'database' | 'environment_secrets' = 'database'): string {
  if (value?.state === 'verified') {
    const key = component === 'database' ? 'backupHealth.verificationDetails.fullDatabase' : 'backupHealth.verificationDetails.fullEnvironmentSecrets'
    return t(key, { date: displayDate(value.verified_at) })
  }
  if (value?.state === 'unknown') return t('backupHealth.verificationDetails.unknown')
  if (value?.verified_at) return t('backupHealth.verificationDetails.previous', { date: displayDate(value.verified_at) })
  if (!value) return t('backupHealth.verificationDetails.notReported')
  return t('backupHealth.verificationDetails.notVerified')
}
function restoreAction(): string {
  if (restoreState.value === 'current') return t('backupHealth.restoreTestCurrent', { date: displayDate(restoreTest.value?.last_success_at), next: displayDate(restoreTest.value?.next_due_at) })
  if (restoreState.value === 'due') return t('backupHealth.restoreTestDue', { date: displayDate(restoreTest.value?.last_success_at) })
  if (restoreState.value === 'never_tested') return t('backupHealth.restoreTestNever')
  return t('backupHealth.restoreTestUnknownAction')
}
function loadStatus(): void {
  loading.value = true
  error.value = ''
  void getBackupStatus().then(value => { status.value = value }).catch(cause => {
    error.value = cause instanceof ApiError ? localizeApiError(cause, 'backupHealth.loadFailed') : t('backupHealth.loadFailed')
    status.value = { schema_version: 1, overall_state: 'unknown', reason_codes: [] }
  }).finally(() => { loading.value = false })
}
onMounted(loadStatus)
</script>

<template>
  <section class="page-section">
    <h1>{{ t('backupHealth.title') }}</h1>
    <p class="page-description">{{ t('backupHealth.description') }}</p>
    <section class="backup-health-overall" :aria-busy="loading" aria-labelledby="backup-health-heading">
      <div v-if="error" class="backup-health-error" role="alert">{{ error }}</div>
      <div class="admin-panel-header backup-health-header"><div><h2 id="backup-health-heading">{{ t('backupHealth.overview') }}</h2><p v-if="loading" class="backup-health-loading" aria-live="polite">{{ t('backupHealth.loading') }}</p><ul v-if="!loading && !error && statusKind !== 'healthy' && statusKind !== 'disabled'" class="backup-health-reasons"><li v-for="(label, index) in reasonLabels" :key="`${status.reason_codes[index]}-${index}`">{{ label }}</li></ul></div></div>
      <div v-if="loading" class="backup-overview-grid backup-overview-loading" aria-hidden="true"><div v-for="index in 4" :key="index" class="card backup-metric-skeleton"></div></div>
      <div v-else class="backup-overview-grid">
        <article class="card backup-metric" :class="`is-${statusKind}`"><span class="backup-metric-label">{{ t('backupHealth.operationTitle') }}</span><strong class="backup-metric-value" aria-live="polite"><component :is="stateIcon" :size="17" aria-hidden="true" /> {{ error ? t('backupHealth.unavailable') : stateLabel }}</strong><span class="backup-metric-helper">{{ statusMessage }}</span></article>
        <article class="card backup-metric"><span class="backup-metric-label">{{ t('backupHealth.lastComplete') }}</span><strong class="backup-metric-value">{{ error ? t('backupHealth.unavailable') : displayDate(status.automation?.last_success_at) }}</strong></article>
        <article class="card backup-metric"><span class="backup-metric-label">{{ t('backupHealth.nextScheduled') }}</span><strong class="backup-metric-value">{{ error ? t('backupHealth.unavailable') : automationDisabled ? t('backupHealth.notScheduled') : displayDate(status.automation?.next_run_at) }}</strong><span class="backup-metric-helper">{{ error ? t('backupHealth.reportUnavailable') : displaySchedule() }}</span></article>
        <article class="card backup-metric" :class="`is-${error ? 'unknown' : recoveryDisabled ? 'disabled' : recoveryState}`"><span class="backup-metric-label">{{ t('backupHealth.recoveryTitle') }}</span><strong class="backup-metric-value">{{ error ? t('backupHealth.unavailable') : recoveryDisabled ? t('backupHealth.states.disabled') : restoreLabel }}</strong><span class="backup-metric-helper">{{ error ? t('backupHealth.reportUnavailable') : recoveryDisabled ? t('backupHealth.states.disabled') : t('backupHealth.archiveVerificationTitle') + ' · ' + stateText(archiveOverallState) }}</span></article>
      </div>
    </section>

    <section v-if="!loading" class="backup-health-section" aria-labelledby="backup-operation-heading"><div class="admin-panel-header"><h2 id="backup-operation-heading">{{ t('backupHealth.operationTitle') }}</h2></div><div class="backup-component-grid">
      <section class="card admin-panel backup-component" :class="{ 'is-unavailable': error }" aria-labelledby="backup-database-heading"><div class="backup-component-header"><h3 id="backup-database-heading"><PhDatabase :size="20" aria-hidden="true" /> {{ t('backupHealth.database') }}</h3><span class="backup-health-badge" :class="`is-${error ? 'unknown' : databaseDisabled ? 'disabled' : databaseState}`">{{ operationCardLabel(databaseDisabled ? 'disabled' : databaseState) }}</span></div><p v-if="error" class="backup-component-empty">{{ t('backupHealth.reportUnavailable') }}</p><p v-else-if="databaseDisabled" class="backup-component-empty">{{ t('backupHealth.databaseDisabled') }}</p><p v-else-if="!status.components?.database" class="backup-component-empty">{{ t('backupHealth.reportUnavailable') }}</p><dl v-else><div><dt>{{ t('backupHealth.latestArtifact') }}</dt><dd>{{ displayDate(status.components.database.last_success_at) }}</dd></div><div><dt>{{ t('backupHealth.matchesDatabase') }}</dt><dd>{{ status.components.database.matching_backup ? t('backupHealth.matches') : t('backupHealth.doesNotMatch') }}</dd></div></dl></section>
      <section class="card admin-panel backup-component" :class="{ 'is-unavailable': error }" aria-labelledby="backup-secrets-heading"><div class="backup-component-header"><h3 id="backup-secrets-heading"><PhLockKey :size="20" aria-hidden="true" /> {{ t('backupHealth.secrets') }}</h3><span class="backup-health-badge" :class="`is-${error ? 'unknown' : secretsDisabled ? 'disabled' : secretsState}`">{{ operationCardLabel(secretsDisabled ? 'disabled' : secretsState) }}</span></div><p v-if="error" class="backup-component-empty">{{ t('backupHealth.reportUnavailable') }}</p><p v-else-if="secretsDisabled" class="backup-component-empty">{{ t('backupHealth.secretsDisabled') }} <small>{{ t('backupHealth.secretsDisabledHelper') }}</small></p><p v-else-if="!status.components?.environment_secrets" class="backup-component-empty">{{ t('backupHealth.reportUnavailable') }}</p><dl v-else><div><dt>{{ t('backupHealth.latestArchive') }}</dt><dd>{{ displayDate(status.components.environment_secrets.last_success_at) }}</dd></div><div><dt>{{ t('backupHealth.matchesDatabase') }}</dt><dd>{{ status.components.environment_secrets.matching_backup ? t('backupHealth.matches') : t('backupHealth.doesNotMatch') }}</dd></div></dl></section>
    </div></section>

    <section v-if="!loading" class="backup-health-section" aria-labelledby="backup-recovery-heading"><div class="admin-panel-header"><h2 id="backup-recovery-heading">{{ t('backupHealth.recoveryTitle') }}</h2></div><p class="page-description">{{ error ? t('backupHealth.reportUnavailable') : recoveryDisabled ? t('backupHealth.recoveryDisabled') : t('backupHealth.isolatedTestDescription') }}</p><div class="backup-component-grid">
      <section class="card admin-panel backup-component" :class="{ 'is-unavailable': error }" aria-labelledby="backup-archive-heading"><div class="backup-component-header"><h3 id="backup-archive-heading">{{ t('backupHealth.archiveVerificationTitle') }}</h3><span class="backup-health-badge" :class="`is-${error ? 'unknown' : archiveDisabled ? 'disabled' : archiveOverallState}`">{{ error ? t('backupHealth.unavailable') : archiveDisabled ? t('backupHealth.states.disabled') : recoveryCardLabel(archiveOverallState) }}</span></div><p v-if="error || archiveDisabled" class="backup-component-empty">{{ error ? t('backupHealth.reportUnavailable') : t('backupHealth.archiveVerificationDisabled') }}</p><template v-else><p>{{ t('backupHealth.archiveVerificationNoRestore') }}</p><p v-if="archiveNeedsAction"><strong>{{ t('backupHealth.diagnosis') }}:</strong> {{ t('backupHealth.archiveVerificationAction') }}</p><p v-else class="backup-component-evidence">{{ t('backupHealth.archiveVerificationStatus') }}</p><dl><div><dt>{{ t('backupHealth.database') }}</dt><dd>{{ displayVerification(archive.database) }} <small>{{ archiveDetail(archive.database) }}</small></dd></div><div><dt>{{ t('backupHealth.secrets') }}</dt><dd>{{ displayVerification(archive.environment_secrets) }} <small>{{ archiveDetail(archive.environment_secrets, 'environment_secrets') }}</small></dd></div></dl><a v-if="archiveNeedsAction" class="button secondary compact-action" href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md#5" target="_blank" rel="noopener noreferrer">{{ t('backupHealth.actionAnchor') }} <span class="sr-only">{{ t('backupHealth.opensNewTab') }}</span><PhArrowSquareOut :size="17" aria-hidden="true" /></a></template></section>
      <section class="card admin-panel backup-component" :class="{ 'is-unavailable': error }" aria-labelledby="backup-restore-heading"><div class="backup-component-header"><h3 id="backup-restore-heading">{{ t('backupHealth.restoreTest') }}</h3><span class="backup-health-badge" :class="`is-${error ? 'unknown' : recoveryDisabled ? 'disabled' : restoreState}`">{{ error ? t('backupHealth.unavailable') : recoveryDisabled ? t('backupHealth.states.disabled') : restoreLabel }}</span></div><p v-if="error || recoveryDisabled" class="backup-component-empty">{{ error ? t('backupHealth.reportUnavailable') : t('backupHealth.recoveryDisabled') }}</p><template v-else><p>{{ t('backupHealth.isolatedTestDescription') }}</p><p><strong v-if="restoreNeedsAction">{{ t('backupHealth.diagnosis') }}:</strong> {{ restoreAction() }}</p><a v-if="restoreNeedsAction" class="button secondary compact-action" href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md#6" target="_blank" rel="noopener noreferrer">{{ t('backupHealth.actionAnchor') }} <span class="sr-only">{{ t('backupHealth.opensNewTab') }}</span><PhArrowSquareOut :size="17" aria-hidden="true" /></a></template></section>
    </div></section>

    <section v-if="!loading" class="card admin-panel backup-protection" aria-labelledby="backup-protection-heading"><h2 id="backup-protection-heading">{{ t('backupHealth.protectionTitle') }}</h2><dl><div><dt>{{ t('backupHealth.schedule') }}</dt><dd>{{ automationDisabled ? t('backupHealth.notScheduled') : displaySchedule() }}</dd></div><div><dt>{{ t('backupHealth.freshnessThreshold') }}</dt><dd>{{ displayThreshold(status.freshness_threshold_seconds) }}</dd></div></dl><div class="backup-boundary-note"><PhInfo :size="20" aria-hidden="true" /><div><strong>{{ t('backupHealth.boundaryTitle') }}</strong><p>{{ t('backupHealth.boundaryDescription') }}</p></div></div><a class="button secondary compact-action backup-doc-link" href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md#2" target="_blank" rel="noopener noreferrer">{{ t('backupHealth.docs') }} <span class="sr-only">{{ t('backupHealth.opensNewTab') }}</span><PhArrowSquareOut :size="17" aria-hidden="true" /></a></section>
  </section>
</template>
