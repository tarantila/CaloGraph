<script setup lang="ts">
import { onMounted, ref } from 'vue'

import DateInput from '../components/DateInput.vue'
import { ApiError, api, localizeApiError } from '../api'
import { formatGermanDateTime, shiftIsoDate } from '../date-format'
import { i18n } from '../i18n'

interface AppLogItem {
  occurred_at: string
  level: string
  action: string
  duration_ms: number
  request_id: string
  status: number
}

const t = i18n.global.t.bind(i18n.global)
const items = ref<AppLogItem[]>([])
const loading = ref(true)
const error = ref('')
const requestId = ref('')
const action = ref('')
const level = ref('')
const since = ref('')
const until = ref('')
const bufferLimit = ref(500)

async function load() {
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({ limit: '100' })
  if (requestId.value.trim()) params.set('request_id', requestId.value.trim())
  if (action.value.trim()) params.set('action', action.value.trim())
  if (level.value) params.set('level', level.value)
  if (since.value) params.set('since', `${since.value}T00:00:00Z`)
  if (until.value) params.set('until', `${shiftIsoDate(until.value, 1)}T00:00:00Z`)
  try {
    const result = await api<{ items: AppLogItem[]; buffer_limit: number; persistence: string }>(`/admin/logs?${params}`)
    items.value = result.items
    bufferLimit.value = result.buffer_limit
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause) : t('errors.generic')
  } finally {
    loading.value = false
  }
}

onMounted(() => { void load() })
</script>

<template>
  <section class="page-section">
    <h1>{{ t('adminUi.logsTitle') }}</h1>
    <p class="page-description">{{ t('adminUi.logsDescription') }}</p>
    <p class="admin-operational-note">
      {{ t('adminUi.logsBuffer', { limit: bufferLimit }) }}
      · {{ t('adminUi.logsPersistence') }}
    </p>
    <p v-if="loading">{{ t('common.loading') }}</p>
    <div v-else-if="error" class="error" role="alert">{{ error }}</div>
    <section v-else class="card admin-panel" aria-labelledby="admin-logs-panel">
      <div class="admin-panel-header">
        <div>
          <h2 id="admin-logs-panel">{{ t('adminUi.logsTitle') }}</h2>
        </div>
        <form class="filters" @submit.prevent="load">
          <label class="field">{{ t('adminUi.logsRequestId') }}<input v-model="requestId" maxlength="32" /></label>
          <label class="field">{{ t('adminUi.logsAction') }}<input v-model="action" maxlength="128" /></label>
          <label class="field">{{ t('adminUi.logsLevel') }}<select v-model="level"><option value="">{{ t('adminUi.all') }}</option><option value="INFO">INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></label>
          <label class="field">{{ t('common.from') }}<DateInput v-model="since" /></label>
          <label class="field">{{ t('common.to') }}<DateInput v-model="until" /></label>
          <button class="button secondary compact-action compact-apply" type="submit">{{ t('common.apply') }}</button>
        </form>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ t('adminUi.logsLevel') }}</th><th>{{ t('adminUi.logsAction') }}</th><th>{{ t('adminUi.time') }}</th><th>{{ t('adminUi.logsRequestId') }}</th><th>{{ t('adminUi.status') }}</th><th>{{ t('adminUi.duration') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in items" :key="`${item.occurred_at}-${item.request_id}`">
              <td><span class="status-badge" :class="item.level === 'ERROR' ? 'inactive' : 'success'">{{ item.level }}</span></td>
              <td><code>{{ item.action }}</code></td>
              <td>{{ formatGermanDateTime(item.occurred_at) }}</td>
              <td><code>{{ item.request_id }}</code></td>
              <td>{{ item.status }}</td>
              <td>{{ item.duration_ms }} ms</td>
            </tr>
            <tr v-if="!items.length"><td colspan="6" class="empty">{{ t('adminUi.noEvents') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>
