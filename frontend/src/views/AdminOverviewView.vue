<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AuditOutcomeIcon from '../components/AuditOutcomeIcon.vue'
import { api, ApiError, localizeApiError } from '../api'
import { i18n } from '../i18n'
interface RecentEvent { id: string; occurred_at: string; event: string; outcome: string; username: string }
interface OverviewMetrics {
  active_users: number
  active_sessions: number
  open_invitations: number
  successful_logins_24h: number
  failed_logins_24h: number
  recent_events: RecentEvent[]
}

const t = i18n.global.t.bind(i18n.global)
const loading = ref(true)
const error = ref('')
const metrics = ref<OverviewMetrics | null>(null)

onMounted(async () => {
  try {
    metrics.value = await api<OverviewMetrics>('/admin/overview')
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  } finally {
    loading.value = false
  }
})

function eventLabel(eventName: string) {
  const translations = i18n.global.tm('adminEvents') as Record<string, unknown>
  const label = translations[eventName]
  return typeof label === 'string' ? label : eventName
}
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminNav.overviewTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.overviewDescription') }}</p>
    <p v-if="loading">{{ t('common.loading') }}</p>
    <div v-else-if="error" class="error" role="alert">{{ error }}</div>
    <div v-else-if="metrics" class="admin-metric-grid">
      <article class="card admin-metric-card"><strong>{{ metrics.active_users }}</strong><span>{{ t('adminUi.activeUsers') }}</span></article>
      <article class="card admin-metric-card"><strong>{{ metrics.active_sessions }}</strong><span>{{ t('adminUi.activeSessions') }}</span></article>
      <article class="card admin-metric-card"><strong>{{ metrics.open_invitations }}</strong><span>{{ t('adminUi.openInvitations') }}</span></article>
      <article class="card admin-metric-card"><strong>{{ metrics.successful_logins_24h }}</strong><span>{{ t('adminUi.successfulLogins24h') }}</span></article>
      <article class="card admin-metric-card"><strong>{{ metrics.failed_logins_24h }}</strong><span>{{ t('adminUi.failedLogins24h') }}</span></article>
    </div>
    <section v-if="metrics" class="card admin-panel" aria-labelledby="admin-recent-events">
      <div class="admin-panel-header">
        <div>
          <h2 id="admin-recent-events">{{ t('adminNav.recentActivity') }}</h2>
          <p>{{ t('adminNav.recentActivityDescription') }}</p>
        </div>
      </div>
      <div class="table-scroll">
        <table>
          <tbody>
            <tr v-for="item in metrics.recent_events" :key="item.id">
              <td><AuditOutcomeIcon :outcome="item.outcome" /></td>
              <td>{{ item.username }}</td>
              <td>{{ eventLabel(item.event) }}</td>
            </tr>
            <tr v-if="!metrics.recent_events.length"><td colspan="3" class="empty">{{ t('adminUi.noEvents') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
