<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  PhArrowCounterClockwise,
  PhArrowSquareOut,
  PhCheckCircle,
  PhDatabase,
  PhInfo,
  PhLockKey,
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
const statusKind = computed(() => state.value === 'attention' && status.value.reason_codes.includes('stale') ? 'stale' : state.value)
const stateLabel = computed(() => t(`backupHealth.states.${statusKind.value}`))
const stateIcon = computed(() => ({
  healthy: PhCheckCircle,
  stale: PhWarningCircle,
  attention: PhWarningCircle,
  failed: PhXCircle,
  unknown: PhQuestion,
  disabled: PhInfo,
}[statusKind.value]))
const automationDisabled = computed(() => status.value.automation?.enabled === false || state.value === 'disabled')
const secretsComponent = computed(() => status.value.components?.environment_secrets)
const secretsDisabled = computed(() => automationDisabled.value || secretsComponent.value?.state === 'disabled')
const secretsArchiveStatus = computed(() => {
  const component = secretsComponent.value
  if (secretsDisabled.value) return t('backupHealth.disabledSecureDefault')
  if (state.value === 'unknown' || !component?.state || component.state === 'unknown') return t('backupHealth.notReported')
  return t('backupHealth.enabled')
})

function reasonText(code: string): string {
  const key = `backupHealth.reasons.${code}`
  const translated = t(key)
  return translated === key ? t('backupHealth.notReported') : translated
}

const reasonLabels = computed(() => [...new Set(status.value.reason_codes)].map(reasonText))
const statusMessage = computed(() => {
  if (error.value) return ''
  if (statusKind.value === 'stale') return reasonText('stale')
  if (statusKind.value === 'failed') {
    const cause = status.value.reason_codes.find(code => code === 'latest_attempt_failed' || code === 'verification_failed')
    if (cause === 'latest_attempt_failed') return t('backupHealth.latestAttemptFailedAt', { date: displayDate(status.value.automation?.last_attempt_at) })
    if (cause === 'verification_failed') return reasonText(cause)
  }
  if (statusKind.value === 'unknown' && status.value.reason_codes.length) return reasonText(status.value.reason_codes[0])
  return t(`backupHealth.messages.${statusKind.value}`)
})

function displayDate(value: string | undefined): string {
  return value ? formatDateTime(value) : t('backupHealth.notReported')
}
function displayDays(value: number | undefined): string {
  return value === undefined
    ? t('backupHealth.notReported')
    : `${value} ${t(value === 1 ? 'common.day' : 'common.days')}`
}
function displayAge(seconds: number | undefined): string {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return t('backupHealth.notReported')
  if (seconds >= 86400) {
    const days = Math.max(1, Math.round(seconds / 86400))
    return `${days} ${t(days === 1 ? 'common.day' : 'common.days')}`
  }
  if (seconds < 3600) {
    const minutes = Math.max(1, Math.round(seconds / 60))
    return `${minutes} ${t('common.minutes')}`
  }
  const hours = Math.max(1, Math.round(seconds / 3600))
  return `${hours} ${t('common.hours')}`
}
function displayComponentAge(component: BackupComponentStatus | undefined): string {
  if (component?.age_seconds !== undefined) return displayAge(component.age_seconds)
  if (!component?.last_success_at) return t('backupHealth.notReported')
  const timestamp = new Date(component.last_success_at).getTime()
  const now = Date.now()
  if (!Number.isFinite(timestamp) || timestamp > now) return t('backupHealth.notReported')
  return displayAge((now - timestamp) / 1000)
}
function displayThreshold(seconds: number | undefined): string {
  return seconds === undefined ? t('backupHealth.notReported') : displayAge(seconds)
}
function displaySchedule(): string {
  const automation = status.value.automation
  return automation?.schedule_time && automation.schedule_timezone
    ? t('backupHealth.dailyAt', { time: automation.schedule_time, timezone: automation.schedule_timezone })
    : t('backupHealth.notReported')
}
function displayVerification(component: BackupComponentStatus | undefined): string {
  if (!component || component.verification === undefined || component.verification === 'not_reported') return t('backupHealth.notReported')
  if (component.verification === 'full') return t('backupHealth.verification.verified')
  if (component.verification === 'checksum') return t('backupHealth.verification.checksum')
  return t('backupHealth.verification.notVerified')
}
function verificationDetail(component: BackupComponentStatus | undefined, componentType: 'database' | 'environment_secrets'): string {
  if (!component || !component.verification || component.verification === 'not_reported') return t('backupHealth.verificationDetails.notReported')
  if (component.verification === 'full') {
    const date = displayDate(component.last_verified_at || component.last_success_at)
    const key = componentType === 'database'
      ? 'backupHealth.verificationDetails.fullDatabase'
      : 'backupHealth.verificationDetails.fullEnvironmentSecrets'
    return t(key, { date })
  }
  if (component.verification === 'checksum') return t('backupHealth.verificationDetails.checksum')
  return t('backupHealth.verificationDetails.notVerified')
}
function displayEncryption(component: BackupComponentStatus | undefined): string {
  return component?.state !== 'disabled' && component?.encryption === 'age'
    ? t('backupHealth.ageMethod')
    : t('backupHealth.notReported')
}
function componentBadge(component: BackupComponentStatus | undefined, restore = false): string {
  if (restore) return component?.last_restore_test_at ? t('backupHealth.reported') : t('backupHealth.notReported')
  if (!component || component.state === undefined) return t('backupHealth.notReported')
  return t(`backupHealth.states.${component.state}`)
}
function componentBadgeKind(component: BackupComponentStatus | undefined, restore = false): string {
  if (restore) return component?.last_restore_test_at ? 'reported' : 'unknown'
  if (!component || component.state === undefined) return 'unknown'
  return component.state
}
function matchingText(component: BackupComponentStatus | undefined): string {
  if (component?.matching_backup === undefined || component.state === 'disabled') return t('backupHealth.notReported')
  return component.matching_backup ? t('backupHealth.matches') : t('backupHealth.doesNotMatch')
}
function boundaryDescription(): string {
  const secrets = status.value.components?.environment_secrets
  const method = secretsDisabled.value
    ? t('backupHealth.boundarySecretsDisabled')
    : secrets?.encryption === 'age'
      ? t('backupHealth.boundarySecretsEnabled')
      : t('backupHealth.boundarySecretsUnknown')
  return `${t('backupHealth.boundaryDescription')} ${method}`
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

    <section class="backup-health-overall" :aria-busy="loading" :aria-labelledby="'backup-health-heading'">
      <div v-if="error" class="backup-health-error" role="alert">{{ error }}</div>
      <div class="admin-panel-header backup-health-header">
        <div>
          <h2 id="backup-health-heading">{{ t('backupHealth.overview') }}</h2>
          <p v-if="loading" class="backup-health-loading" aria-live="polite">{{ t('backupHealth.loading') }}</p>
          <ul v-if="!loading && !error && statusKind !== 'healthy' && statusKind !== 'disabled' && reasonLabels.length > 1" class="backup-health-reasons">
            <li v-for="(label, index) in reasonLabels" :key="`${status.reason_codes[index]}-${index}`">{{ label }}</li>
          </ul>
        </div>
      </div>
      <div v-if="loading" class="backup-overview-grid backup-overview-loading" aria-hidden="true">
        <div v-for="index in 4" :key="index" class="card backup-metric-skeleton"></div>
      </div>
      <div v-else class="backup-overview-grid">
        <article class="card backup-metric" :class="`is-${statusKind}`">
          <span class="backup-metric-label">{{ t('backupHealth.statusMetric') }}</span>
          <strong class="backup-metric-value" aria-live="polite"><component :is="stateIcon" :size="17" aria-hidden="true" /> {{ stateLabel }}</strong>
          <span v-if="statusMessage" class="backup-metric-helper">{{ statusMessage }}</span>
        </article>
        <article class="card backup-metric" :class="{ 'is-stale': statusKind === 'stale' }">
          <span class="backup-metric-label">{{ t('backupHealth.lastComplete') }}</span>
          <strong class="backup-metric-value">{{ displayDate(status.automation?.last_success_at) }}</strong>
          <span class="backup-metric-helper">{{ displayComponentAge(status.components?.database) }}</span>
        </article>
        <article class="card backup-metric">
          <span class="backup-metric-label">{{ t('backupHealth.nextScheduled') }}</span>
          <strong class="backup-metric-value">{{ automationDisabled ? t('backupHealth.notScheduled') : displayDate(status.automation?.next_run_at) }}</strong>
          <span class="backup-metric-helper">{{ displaySchedule() }}</span>
        </article>
        <article class="card backup-metric">
          <span class="backup-metric-label">{{ t('backupHealth.retention') }}</span>
          <strong class="backup-metric-value">{{ displayDays(status.automation?.retention_days) }}</strong>
          <span class="backup-metric-helper">{{ t('backupHealth.operatorManaged') }}</span>
        </article>
      </div>
    </section>

    <section class="backup-health-section" aria-labelledby="backup-components-heading">
      <div class="admin-panel-header"><h2 id="backup-components-heading">{{ t('backupHealth.componentsTitle') }}</h2></div>
      <div v-if="!loading" class="backup-component-grid">
        <section class="card admin-panel backup-component" aria-labelledby="backup-database-heading">
          <div class="backup-component-header">
            <h3 id="backup-database-heading"><PhDatabase :size="20" aria-hidden="true" /> {{ t('backupHealth.database') }}</h3>
            <span class="backup-health-badge" :class="`is-${componentBadgeKind(status.components?.database)}`">{{ componentBadge(status.components?.database) }}</span>
          </div>
          <p v-if="error || statusKind === 'unknown' || automationDisabled || !status.components?.database" class="backup-component-empty">{{ automationDisabled ? t('backupHealth.backupsDisabled') : t('backupHealth.reportUnavailable') }}</p>
          <dl v-else>
            <div><dt>{{ t('backupHealth.latestArtifact') }}</dt><dd>{{ displayDate(status.components?.database?.last_success_at) }} <small>{{ displayComponentAge(status.components?.database) }}</small></dd></div>
            <div><dt>{{ t('backupHealth.restoreVerification') }}</dt><dd>{{ displayVerification(status.components?.database) }} <small>{{ verificationDetail(status.components?.database, 'database') }}</small></dd></div>
            <div><dt>{{ t('backupHealth.encryption') }}</dt><dd>{{ displayEncryption(status.components?.database) }}</dd></div>
          </dl>
        </section>
        <section class="card admin-panel backup-component" aria-labelledby="backup-secrets-heading">
          <div class="backup-component-header">
            <h3 id="backup-secrets-heading"><PhLockKey :size="20" aria-hidden="true" /> {{ t('backupHealth.secrets') }}</h3>
            <span class="backup-health-badge" :class="`is-${secretsDisabled ? 'disabled' : componentBadgeKind(status.components?.environment_secrets)}`">{{ secretsDisabled ? t('backupHealth.states.disabled') : componentBadge(status.components?.environment_secrets) }}</span>
          </div>
          <p v-if="error || statusKind === 'unknown'" class="backup-component-empty">{{ t('backupHealth.reportUnavailable') }}</p>
          <p v-else-if="secretsDisabled" class="backup-component-empty">{{ t('backupHealth.secretsDisabled') }} <small>{{ t('backupHealth.secretsDisabledHelper') }}</small></p>
          <dl v-else>
            <div><dt>{{ t('backupHealth.latestArchive') }}</dt><dd>{{ displayDate(status.components?.environment_secrets?.last_success_at) }} <small>{{ matchingText(status.components?.environment_secrets) }}</small></dd></div>
            <div><dt>{{ t('backupHealth.decryptionVerification') }}</dt><dd>{{ displayVerification(status.components?.environment_secrets) }} <small>{{ verificationDetail(status.components?.environment_secrets, 'environment_secrets') }}</small></dd></div>
            <div><dt>{{ t('backupHealth.encryption') }}</dt><dd>{{ displayEncryption(status.components?.environment_secrets) }}</dd></div>
          </dl>
        </section>
        <section class="card admin-panel backup-component" aria-labelledby="backup-restore-heading">
          <div class="backup-component-header">
            <h3 id="backup-restore-heading"><PhArrowCounterClockwise :size="20" aria-hidden="true" /> {{ t('backupHealth.restoreTest') }}</h3>
            <span class="backup-health-badge" :class="`is-${componentBadgeKind(status.components?.restore_test, true)}`">{{ componentBadge(status.components?.restore_test, true) }}</span>
          </div>
          <p v-if="error || automationDisabled || !status.components?.restore_test?.last_restore_test_at" class="backup-component-empty">{{ automationDisabled ? t('backupHealth.backupsDisabled') : t('backupHealth.restoreTestUnknown') }}</p>
          <dl v-else>
            <div><dt>{{ t('backupHealth.lastRestoreTest') }}</dt><dd>{{ displayDate(status.components?.restore_test?.last_restore_test_at) }}</dd></div>
            <div><dt>{{ t('backupHealth.offHost') }}</dt><dd>{{ status.components?.restore_test?.off_host_copy === true ? t('backupHealth.reported') : t('backupHealth.notReported') }}</dd></div>
            <div><dt>{{ t('backupHealth.immutable') }}</dt><dd>{{ status.components?.restore_test?.immutable_copy === true ? t('backupHealth.reported') : t('backupHealth.notReported') }}</dd></div>
          </dl>
        </section>
      </div>
    </section>

    <section v-if="!loading" class="card admin-panel backup-protection" aria-labelledby="backup-protection-heading">
      <h2 id="backup-protection-heading">{{ t('backupHealth.protectionTitle') }}</h2>
      <dl>
        <div><dt>{{ t('backupHealth.schedule') }}</dt><dd>{{ automationDisabled ? t('backupHealth.notScheduled') : displaySchedule() }}</dd></div>
        <div><dt>{{ t('backupHealth.freshnessThreshold') }}</dt><dd>{{ displayThreshold(status.freshness_threshold_seconds) }}</dd></div>
        <div><dt>{{ t('backupHealth.secretsArchive') }}</dt><dd>{{ secretsArchiveStatus }}</dd></div>
      </dl>
      <div class="backup-boundary-note">
        <PhInfo :size="20" aria-hidden="true" />
        <div><strong>{{ t('backupHealth.boundaryTitle') }}</strong><p>{{ boundaryDescription() }}</p></div>
      </div>
      <a class="button secondary compact-action backup-doc-link" href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md" target="_blank" rel="noopener noreferrer">
        {{ t('backupHealth.docs') }} <span class="sr-only">{{ t('backupHealth.opensNewTab') }}</span><PhArrowSquareOut :size="17" aria-hidden="true" />
      </a>
    </section>
  </section>
</template>
