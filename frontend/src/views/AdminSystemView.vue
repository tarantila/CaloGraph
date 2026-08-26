<script setup lang="ts">
import { PhDatabase, PhShieldCheck, PhTag, PhTimer } from '@phosphor-icons/vue'
import { onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { formatGermanDateTime } from '../date-format'
import { i18n } from '../i18n'

type ReleaseState = 'current' | 'update_available' | 'development' | 'unknown'

interface VersionStatus {
  running: string
  latest: string | null
  status: ReleaseState
  release_url: string | null
  checked_at: string
}

interface SystemStatus {
  version: VersionStatus
  database: 'healthy'
  security_audit_retention_days: number
  security_audit_enabled: boolean
  security_audit_events_24h: number
  failed_logins_24h: number
  yazio_scheduler_enabled: boolean
  yazio_scheduler_available: boolean
}

const t = i18n.global.t.bind(i18n.global)
const data = ref<SystemStatus | null>(null)
const error = ref('')

const releaseLabels: Record<ReleaseState, string> = {
  current: 'systemStatus.current',
  update_available: 'systemStatus.updateAvailable',
  development: 'systemStatus.development',
  unknown: 'systemStatus.unknown',
}

function releaseClass(status: ReleaseState) {
  return `system-status-badge ${status}`
}

onMounted(async () => {
  try {
    data.value = await api<SystemStatus>('/admin/system')
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  }
})
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminNav.systemTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.systemDescription') }}</p>
    <div v-if="error" class="error" role="alert">{{ error }}</div>
    <template v-else-if="data">
      <section class="card admin-panel system-version-card" aria-labelledby="system-version-title">
        <div class="admin-panel-header">
          <div class="system-card-title">
            <PhTag :size="20" aria-hidden="true" />
            <h2 id="system-version-title">{{ t('systemStatus.version') }}</h2>
          </div>
          <span :class="releaseClass(data.version.status)">{{ t(releaseLabels[data.version.status]) }}</span>
        </div>
        <dl class="system-version-details">
          <div><dt>{{ t('systemStatus.runningVersion') }}</dt><dd>{{ data.version.running }}</dd></div>
          <div><dt>{{ t('systemStatus.latestRelease') }}</dt><dd>{{ data.version.latest ?? '–' }}</dd></div>
          <div><dt>{{ t('systemStatus.releaseCheck') }}</dt><dd>{{ formatGermanDateTime(data.version.checked_at) }}</dd></div>
        </dl>
        <a
          v-if="data.version.release_url"
          class="text-button system-release-link"
          :href="data.version.release_url"
          target="_blank"
          rel="noopener noreferrer"
        >{{ t('systemStatus.viewRelease') }}</a>
      </section>

      <div class="system-status-grid">
        <section class="card admin-panel system-status-card">
          <div class="system-card-title"><PhDatabase :size="19" aria-hidden="true" /><h2>{{ t('systemStatus.databaseTitle') }}</h2></div>
          <span class="system-status-badge current">{{ t('systemStatus.healthy') }}</span>
        </section>
        <section class="card admin-panel system-status-card system-audit-card">
          <div class="system-card-title"><PhShieldCheck :size="19" aria-hidden="true" /><h2>{{ t('systemStatus.auditTitle') }}</h2></div>
          <dl class="system-audit-metrics">
            <div class="system-audit-primary">
              <dt>{{ t('systemStatus.auditEvents24h') }}</dt>
              <dd>{{ data.security_audit_events_24h }}</dd>
            </div>
            <div :class="['system-audit-failures', { attention: data.failed_logins_24h > 0 }]">
              <dt>{{ t('systemStatus.failedLogins24h') }}</dt>
              <dd>{{ data.failed_logins_24h }}</dd>
            </div>
          </dl>
        </section>
        <section class="card admin-panel system-status-card">
          <div class="system-card-title"><PhTimer :size="19" aria-hidden="true" /><h2>{{ t('systemStatus.schedulerTitle') }}</h2></div>
          <span :class="['system-status-badge', data.yazio_scheduler_enabled ? 'current' : 'unknown']">
            {{ data.yazio_scheduler_enabled ? t('systemStatus.enabled') : t('systemStatus.disabled') }}
          </span>
        </section>
      </div>
    </template>
  </section>
</template>
