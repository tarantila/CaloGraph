<script setup lang="ts">
import { onMounted, ref } from 'vue'

import AuditOutcomeIcon from '../components/AuditOutcomeIcon.vue'
import DateInput from '../components/DateInput.vue'
import { ApiError, api, localizeApiError } from '../api'
import { formatGermanDateTime, shiftIsoDate } from '../date-format'
import { i18n } from '../i18n'

interface AuditItem { id: string; occurred_at: string; event: string; outcome: string; auth_method: string | null; username: string; client_ip: string | null; client_ref: string | null; location: string | null; provider: string | null; reason: string | null }
interface AuditUser { id: string; username: string }
const t = i18n.global.t.bind(i18n.global)
const items = ref<AuditItem[]>([])
const loading = ref(true)
const error = ref('')
const outcome = ref('')
const event = ref('')
const since = ref('')
const until = ref('')
const userId = ref('')
const users = ref<AuditUser[]>([])

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ limit: '50' })
    if (outcome.value) params.set('outcome', outcome.value)
    if (event.value) params.set('event', event.value)
    if (since.value) params.set('since', `${since.value}T00:00:00Z`)
    if (until.value) params.set('until', `${shiftIsoDate(until.value, 1)}T00:00:00Z`)
    if (userId.value) params.set('user_id', userId.value)
    const result = await api<{ items: AuditItem[] }>(`/admin/audit?${params}`)
    items.value = result.items
    error.value = ''
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  } finally {
    loading.value = false
  }
}

function translatedRecordValue(group: string, value: string) {
  const translations = i18n.global.tm(group) as Record<string, unknown>
  const label = translations[value]
  return typeof label === 'string' ? label : value
}

function eventLabel(eventName: string) {
  return translatedRecordValue('adminEvents', eventName)
}

function methodLabel(method: string | null) {
  return method ? translatedRecordValue('adminMethods', method) : '–'
}


async function loadUsers() {
  try {
    users.value = await api<AuditUser[]>('/admin/users')
  } catch {
    users.value = []
  }
}

onMounted(() => {
  void load()
  void loadUsers()
})
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminUi.auditTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.auditDescription') }}</p>
    <p v-if="loading">{{ t('common.loading') }}</p>
    <div v-else-if="error" class="error" role="alert">{{ error }}</div>
    <section v-else class="card admin-panel" aria-labelledby="admin-audit-panel">
      <div class="admin-panel-header">
        <div>
          <h2 id="admin-audit-panel">{{ t('adminUi.audit') }}</h2>
          <p>{{ t('adminUi.auditDescription') }}</p>
        </div>
        <div class="filters">
          <label class="field">{{ t('common.from') }}<DateInput v-model="since" @update:model-value="load" /></label>
          <label class="field">{{ t('common.to') }}<DateInput v-model="until" @update:model-value="load" /></label>
          <label class="field">{{ t('adminUi.outcome') }}<select v-model="outcome" @change="load"><option value="">{{ t('adminUi.all') }}</option><option value="success">{{ t('adminUi.success') }}</option><option value="failure">{{ t('adminUi.failure') }}</option></select></label>
          <label class="field">{{ t('adminUi.event') }}<input v-model="event" maxlength="64" @change="load" /></label>
          <label class="field">{{ t('adminUi.user') }}<select v-model="userId" @change="load"><option value="">{{ t('adminUi.all') }}</option><option v-for="user in users" :key="user.id" :value="user.id">{{ user.username }}</option></select></label>
        </div>
      </div>
      <div class="table-scroll admin-desktop-table">
        <table>
          <thead><tr><th>{{ t('adminUi.status') }}</th><th>{{ t('adminUi.user') }}</th><th>{{ t('adminUi.event') }}</th><th>{{ t('adminUi.method') }}</th><th>{{ t('adminUi.client') }}</th><th>{{ t('adminUi.clientRef') }}</th><th>{{ t('adminUi.location') }}</th><th>{{ t('adminUi.provider') }}</th><th>{{ t('adminUi.time') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in items" :key="item.id">
              <td><AuditOutcomeIcon :outcome="item.outcome" /></td>
              <td>{{ item.username }}</td>
              <td><span>{{ eventLabel(item.event) }}</span><small class="technical-value">{{ item.event }}</small></td>
              <td>{{ methodLabel(item.auth_method) }}</td>
              <td>{{ item.client_ip ?? t('adminUi.notAvailable') }}</td>
              <td class="audit-client-ref">{{ item.client_ref ?? t('adminUi.notAvailable') }}</td>
              <td>{{ item.location ?? t('adminUi.notAvailable') }}</td>
              <td>{{ item.provider ?? t('adminUi.notAvailable') }}</td>
              <td class="audit-time">{{ formatGermanDateTime(item.occurred_at) }}</td>
            </tr>
            <tr v-if="!items.length"><td colspan="9" class="empty">{{ t('adminUi.noEvents') }}</td></tr>
          </tbody>
        </table>
      </div>

      <div class="mobile-audit-list">
        <article v-for="item in items" :key="item.id" class="mobile-audit-card">
          <div class="mobile-audit-card-header">
            <AuditOutcomeIcon :outcome="item.outcome" show-label />
            <time :datetime="item.occurred_at">{{ formatGermanDateTime(item.occurred_at) }}</time>
          </div>
          <strong>{{ item.username }}</strong>
          <p>{{ eventLabel(item.event) }}</p>
          <small class="technical-value">{{ item.event }}</small>
          <dl>
            <div><dt>{{ t('adminUi.method') }}</dt><dd>{{ methodLabel(item.auth_method) }}</dd></div>
            <div><dt>{{ t('adminUi.client') }}</dt><dd>{{ item.client_ip ?? t('adminUi.notAvailable') }}</dd></div>
            <div><dt>{{ t('adminUi.clientRef') }}</dt><dd class="audit-client-ref">{{ item.client_ref ?? t('adminUi.notAvailable') }}</dd></div>
            <div><dt>{{ t('adminUi.location') }}</dt><dd>{{ item.location ?? t('adminUi.notAvailable') }}</dd></div>
            <div><dt>{{ t('adminUi.provider') }}</dt><dd>{{ item.provider ?? t('adminUi.notAvailable') }}</dd></div>
          </dl>
        </article>
        <p v-if="!items.length" class="empty">{{ t('adminUi.noEvents') }}</p>
      </div>
    </section>
  </section>
</template>
