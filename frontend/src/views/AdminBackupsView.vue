<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  PhArrowCounterClockwise,
  PhArrowSquareOut,
  PhDatabase,
  PhInfo,
  PhLockKey,
  PhCheckCircle,
  PhQuestion,
  PhWarningCircle,
  PhXCircle,
} from '@phosphor-icons/vue'

import { ApiError, getBackupStatus, localizeApiError } from '../api'
import { formatDateTime } from '../date-format'
import { i18n } from '../i18n'
import type { BackupComponentStatus, BackupHealthState, BackupStatus } from '../types'

const t = i18n.global.t.bind(i18n.global)
const loading = ref(true)
const error = ref('')
const status = ref<BackupStatus>({
  schema_version: 1,
  overall_state: 'unknown',
  reason_codes: ['report_missing'],
})

const state = computed<BackupHealthState>(() => error.value ? 'unknown' : status.value.overall_state)
const stateLabel = computed(() => t(`backupHealth.states.${state.value}`))
const stateMessage = computed(() => t(`backupHealth.messages.${state.value}`))
const stateIcon = computed(() => ({
  healthy: PhCheckCircle,
  attention: PhWarningCircle,
  failed: PhXCircle,
  unknown: PhQuestion,
  disabled: PhInfo,
}[state.value]))
function reasonText(code: string): string {
  const key = `backupHealth.reasons.${code}`
  const translated = t(key)
  return translated === key ? t('backupHealth.notReported') : translated
}
const reasonLabels = computed(() => status.value.reason_codes.map(reasonText))

function displayDate(value: string | undefined): string {
  return value ? formatDateTime(value) : t('backupHealth.notAvailable')
}
function displayVerification(component: BackupComponentStatus | undefined): string {
  if (!component) return t('backupHealth.notReported')
  if (component.state === 'disabled') return t('backupHealth.states.disabled')
  if (component.verification === 'full') return t('backupHealth.fullVerification')
  if (component.verification === 'checksum') return t('backupHealth.checksumOnly')
  return t('backupHealth.notVerified')
}
function displayMatch(component: BackupComponentStatus | undefined): string {
  if (!component || component.matching_backup === undefined || component.state === 'disabled') return t('backupHealth.notReported')
  return component.matching_backup ? t('backupHealth.matches') : t('backupHealth.doesNotMatch')
}
function displayEncryption(component: BackupComponentStatus | undefined): string {
  return component?.state !== 'disabled' && component?.encryption === 'age'
    ? t('backupHealth.ageMethod')
    : t('backupHealth.notReported')
}
function displayDays(value: number | undefined): string {
  return value === undefined ? t('backupHealth.notReported') : `${value} ${t(value === 1 ? 'common.day' : 'common.days')}`
}
function displayHours(value: number | undefined): string {
  return value === undefined ? t('backupHealth.notAvailable') : `${value} ${t('common.hours')}`
}
async function loadStatus(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    status.value = await getBackupStatus()
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause, 'backupHealth.loadFailed') : t('backupHealth.loadFailed')
    status.value = { schema_version: 1, overall_state: 'unknown', reason_codes: [] }
  } finally {
    loading.value = false
  }
}

onMounted(() => { void loadStatus() })
</script>

<template>
  <section class="page-section">
    <h1>{{ t('backupHealth.title') }}</h1>
    <p class="page-description">{{ t('backupHealth.description') }}</p>

    <section class="card admin-panel backup-health-overall" :aria-busy="loading" :aria-labelledby="'backup-health-heading'">
      <div v-if="error" class="backup-health-error" role="alert">{{ error }}</div>
      <div class="admin-panel-header backup-health-header">
        <div>
          <h2 id="backup-health-heading"><component :is="stateIcon" :size="20" aria-hidden="true" /> {{ t('backupHealth.overall') }}</h2>
          <p v-if="loading" class="backup-health-loading">{{ t('backupHealth.loading') }}</p>
          <p v-else aria-live="polite">{{ stateMessage }}</p>
          <ul v-if="!loading && state !== 'healthy' && state !== 'disabled' && reasonLabels.length" class="backup-health-reasons">
            <li v-for="(label, index) in reasonLabels" :key="`${status.reason_codes[index]}-${index}`">{{ label }}</li>
          </ul>
        </div>
        <span v-if="!loading" class="backup-health-badge" :class="`is-${state}`"><component :is="stateIcon" :size="15" aria-hidden="true" /> {{ stateLabel }}</span>
      </div>
      <dl v-if="!loading" class="backup-health-summary">
        <div><dt>{{ t('backupHealth.automation') }}</dt><dd>{{ status.automation?.enabled === false ? t('backupHealth.states.disabled') : status.automation?.enabled === true ? t('backupHealth.enabled') : t('backupHealth.notReported') }}</dd></div>
        <div><dt>{{ t('backupHealth.lastAttempt') }}</dt><dd>{{ displayDate(status.automation?.last_attempt_at) }}</dd></div>
        <div><dt>{{ t('backupHealth.lastComplete') }}</dt><dd>{{ displayDate(status.automation?.last_success_at) }}</dd></div>
        <div><dt>{{ t('backupHealth.nextRun') }}</dt><dd>{{ displayDate(status.automation?.next_run_at) }}</dd></div>
        <div><dt>{{ t('backupHealth.schedule') }}</dt><dd>{{ status.automation?.schedule_time && status.automation.schedule_timezone ? `${status.automation.schedule_time} · ${status.automation.schedule_timezone}` : t('backupHealth.notReported') }}</dd></div>
        <div><dt>{{ t('backupHealth.retention') }}</dt><dd>{{ displayDays(status.automation?.retention_days) }}</dd></div>
        <div><dt>{{ t('backupHealth.lastVerified') }}</dt><dd>{{ displayDate(status.components?.database?.last_verified_at) }}</dd></div>
        <div><dt>{{ t('backupHealth.freshnessThreshold') }}</dt><dd>{{ status.freshness_threshold_seconds ? displayDays(Math.round(status.freshness_threshold_seconds / 86400)) : t('backupHealth.notAvailable') }}</dd></div>
      </dl>
    </section>

    <section v-if="!loading" class="backup-health-section" aria-labelledby="backup-components-heading">
      <div class="admin-panel-header"><h2 id="backup-components-heading">{{ t('backupHealth.componentsTitle') }}</h2></div>
      <div class="backup-component-grid">
        <section class="card admin-panel backup-component" aria-labelledby="backup-database-heading">
          <h3 id="backup-database-heading"><PhDatabase :size="20" aria-hidden="true" /> {{ t('backupHealth.database') }}</h3>
          <dl>
            <div><dt>{{ t('backupHealth.lastSuccessful') }}</dt><dd>{{ displayDate(status.components?.database?.last_success_at) }}</dd></div>
            <div><dt>{{ t('backupHealth.fullVerificationLabel') }}</dt><dd>{{ displayVerification(status.components?.database) }}</dd></div>
            <div><dt>{{ t('backupHealth.age') }}</dt><dd>{{ displayHours(status.components?.database?.age_seconds !== undefined ? Math.round(status.components.database.age_seconds / 3600) : undefined) }}</dd></div>
            <div><dt>{{ t('backupHealth.encryption') }}</dt><dd>{{ displayEncryption(status.components?.database) }}</dd></div>
          </dl>
        </section>
        <section class="card admin-panel backup-component" aria-labelledby="backup-secrets-heading">
          <h3 id="backup-secrets-heading"><PhLockKey :size="20" aria-hidden="true" /> {{ t('backupHealth.secrets') }}</h3>
          <dl>
            <div><dt>{{ t('backupHealth.lastSuccessful') }}</dt><dd>{{ displayDate(status.components?.environment_secrets?.last_success_at) }}</dd></div>
            <div><dt>{{ t('backupHealth.fullVerificationLabel') }}</dt><dd>{{ displayVerification(status.components?.environment_secrets) }}</dd></div>
            <div><dt>{{ t('backupHealth.matchesDatabase') }}</dt><dd>{{ displayMatch(status.components?.environment_secrets) }}</dd></div>
            <div><dt>{{ t('backupHealth.encryption') }}</dt><dd>{{ displayEncryption(status.components?.environment_secrets) }}</dd></div>
          </dl>
        </section>
        <section v-if="status.components?.restore_test" class="card admin-panel backup-component" aria-labelledby="backup-restore-heading">
          <h3 id="backup-restore-heading"><PhArrowCounterClockwise :size="20" aria-hidden="true" /> {{ t('backupHealth.restore') }}</h3>
          <dl>
            <div><dt>{{ t('backupHealth.lastRestoreTest') }}</dt><dd>{{ displayDate(status.components.restore_test.last_restore_test_at) }}</dd></div>
            <div><dt>{{ t('backupHealth.recommendedInterval') }}</dt><dd>{{ t('backupHealth.quarterly') }}</dd></div>
            <div><dt>{{ t('backupHealth.offHost') }}</dt><dd>{{ status.components.restore_test.off_host_copy === true ? t('backupHealth.reported') : t('backupHealth.operatorManaged') }}</dd></div>
            <div><dt>{{ t('backupHealth.immutable') }}</dt><dd>{{ status.components.restore_test.immutable_copy === true ? t('backupHealth.reported') : t('backupHealth.operatorManaged') }}</dd></div>
          </dl>
        </section>
      </div>
    </section>

    <section v-if="!loading" class="card admin-panel backup-protection" aria-labelledby="backup-protection-heading">
      <h2 id="backup-protection-heading">{{ t('backupHealth.protectionTitle') }}</h2>
      <dl>
        <div><dt>{{ t('backupHealth.formatLabel') }}</dt><dd>{{ t('backupHealth.formatValue') }}</dd></div>
        <div><dt>{{ t('backupHealth.boundaryLabel') }}</dt><dd>{{ t('backupHealth.operatorManaged') }}</dd></div>
        <div><dt>{{ t('backupHealth.retentionLabel') }}</dt><dd>{{ t('backupHealth.retentionValue') }}</dd></div>
      </dl>
    </section>

    <div class="backup-boundary-note">
      <PhInfo :size="20" aria-hidden="true" />
      <div><strong>{{ t('backupHealth.boundaryTitle') }}</strong><p>{{ t('backupHealth.boundaryDescription') }}</p></div>
    </div>
    <a class="button secondary compact-action backup-doc-link" href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md" target="_blank" rel="noopener noreferrer">
      {{ t('backupHealth.docs') }} <span class="sr-only">{{ t('backupHealth.opensNewTab') }}</span><PhArrowSquareOut :size="17" aria-hidden="true" />
    </a>
  </section>
</template>
