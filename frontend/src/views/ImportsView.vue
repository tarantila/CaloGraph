<script setup lang="ts">
import {
  PhCheckCircle,
  PhDatabase,
  PhDownloadSimple,
  PhFile,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { ApiError, api, ensureCsrfToken, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, formatGermanDateTime } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { ApiProblem, ImportBatch, YazioStatus } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()

interface ImportErrorDetail {
  item_index: number | null
  metric_type: string | null
  error_code: string
  safe_detail: string
}

interface ImportDetail extends ImportBatch {
  errors: ImportErrorDetail[]
}

const imports = ref<ImportBatch[]>([])
const yazioStatus = ref<YazioStatus | null>(null)
const historyFrom = ref('')
const historyTo = ref('')
const historicalSyncing = ref(false)
const details = ref<Record<string, ImportDetail>>({})
const expandedId = ref<string | null>(null)
const selected = ref<File | null>(null)
const progress = ref(0)
const uploading = ref(false)
const loading = ref(true)
const error = ref('')
const message = ref('')
const messageIsError = ref(false)
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const maxJsonBytes = 10 * 1024 * 1024
const maxAppleHealthBytes = 500 * 1024 * 1024

let historicalPollTimer: ReturnType<typeof setTimeout> | undefined
let isViewActive = false

function historicalSyncActive() {
  const state = yazioStatus.value?.historical_sync?.state
  return state === 'pending' || state === 'running'
}

function scheduleHistoricalStatusRefresh() {
  if (historicalPollTimer) clearTimeout(historicalPollTimer)
  if (!isViewActive || !historicalSyncActive()) return
  historicalPollTimer = setTimeout(() => {
    historicalPollTimer = undefined
    void load()
  }, 5_000)
}

async function load() {
  error.value = ''
  try {
    const [importResult, yazioResult] = await Promise.all([
      api<ImportBatch[]>('/imports'),
      api<YazioStatus>('/yazio/status'),
    ])
    imports.value = importResult
    yazioStatus.value = yazioResult
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
    if (isViewActive) scheduleHistoricalStatusRefresh()
  }
}
onMounted(() => {
  isViewActive = true
  void load()
})
onUnmounted(() => {
  isViewActive = false
  if (historicalPollTimer) clearTimeout(historicalPollTimer)
})

function selectFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  progress.value = 0
  message.value = ''
  messageIsError.value = false
  if (!file) {
    selected.value = null
    return
  }
  const isJson = file.name.toLowerCase().endsWith('.json')
  const maxBytes = isJson ? maxJsonBytes : maxAppleHealthBytes
  if (file.size > maxBytes) {
    selected.value = null
    input.value = ''
    messageIsError.value = true
    message.value = isJson
      ? t('importsExtras.tooLargeYazio')
      : t('importsExtras.tooLargeApple')
    return
  }
  selected.value = file
}

async function upload() {
  if (!selected.value) return
  uploading.value = true
  progress.value = 0
  message.value = ''
  messageIsError.value = false
  try {
    const csrf = await ensureCsrfToken()
    const form = new FormData()
    form.append('file', selected.value)
    const xhr = new XMLHttpRequest()
    const endpoint = selected.value.name.toLowerCase().endsWith('.json')
      ? '/api/v1/import/yazio/file'
      : '/api/v1/import/apple-health/file'
    xhr.open('POST', endpoint)
    xhr.setRequestHeader('X-CSRF-Token', csrf)
    xhr.withCredentials = true
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) progress.value = Math.round((event.loaded / event.total) * 100)
    }
    xhr.onload = async () => {
      uploading.value = false
      if (xhr.status >= 200 && xhr.status < 300) {
        const result = JSON.parse(xhr.responseText) as {
          status: string
          inserted: number
          updated: number
          skipped: number
          failed: number
        }
        const counts = t('importsExtras.counts', { new: result.inserted, updated: result.updated, unchanged: result.skipped, errors: result.failed })
        message.value =
          result.status === 'partial_failed'
            ? `${t('importsExtras.partial')}: ${counts}. ${t('importsExtras.resume')}`
            : counts
        messageIsError.value = result.status === 'partial_failed' || result.failed > 0
        if (result.status !== 'partial_failed') selected.value = null
        await auth.reconcileAchievements(true)
        await load()
      } else {
        messageIsError.value = true
        let problem: ApiProblem = { status: xhr.status }
        try {
          problem = JSON.parse(xhr.responseText) as ApiProblem
        } catch {
          // Keep the localized upload fallback when no problem document exists.
        }
        const error = new ApiError(
          problem.detail ?? `HTTP ${xhr.status}`,
          xhr.status,
          problem.request_id,
          xhr.getResponseHeader('Retry-After') ?? undefined,
          problem.type,
          problem.title,
        )
        message.value = localizeApiError(error, 'imports.uploadFailed')
      }
    }
    xhr.onerror = () => {
      uploading.value = false
      messageIsError.value = true
      message.value = t('importsExtras.networkFailed')
    }
    xhr.send(form)
  } catch (cause) {
    uploading.value = false
    messageIsError.value = true
    message.value = localizeApiError(cause, 'importsExtras.startFailed')
  }
}


const historicalSyncLabel = computed(() => {
  const sync = yazioStatus.value?.historical_sync
  if (!yazioStatus.value?.configured) return t('importsExtras.notConfigured')
  if (!sync || sync.state === 'idle') return t('importsExtras.syncIdle')
  if (sync.state === 'failed') {
    return yazioStatus.value.sync_enabled ? t('importsExtras.syncFailedRetry') : t('importsExtras.syncFailedCredentials')
  }
  const labels: Record<typeof sync.state, string> = {
    pending: t('importsExtras.syncPending'),
    running: t('importsExtras.syncRunning'),
    completed: t('importsExtras.syncCompleted'),
  }
  return labels[sync.state]
})

async function queueHistoricalSync() {
  if (!historyFrom.value || !historyTo.value) {
    messageIsError.value = true
    message.value = t('importsExtras.selectRange')
    return
  }
  if (historyFrom.value > historyTo.value) {
    messageIsError.value = true
    message.value = t('importsExtras.invalidRange')
    return
  }
  historicalSyncing.value = true
  message.value = ''
  messageIsError.value = false
  try {
    yazioStatus.value = await api<YazioStatus>('/yazio/sync/history/range', {
      method: 'POST',
      body: JSON.stringify({ from_date: historyFrom.value, end_date: historyTo.value }),
    })
    message.value = t('importsExtras.rangeQueued')
    scheduleHistoricalStatusRefresh()
  } catch (cause) {
    messageIsError.value = true
    message.value = localizeApiError(cause, 'importsExtras.rangeFailed')
  } finally {
    historicalSyncing.value = false
  }
}

async function toggleDetails(batch: ImportBatch) {
  if (expandedId.value === batch.id) {
    expandedId.value = null
    return
  }
  expandedId.value = batch.id
  if (details.value[batch.id]) return
  try {
    details.value[batch.id] = await api<ImportDetail>(`/imports/${batch.id}`)
  } catch (cause) {
    messageIsError.value = true
    message.value = localizeApiError(cause, 'importsExtras.detailsFailed')
  }
}

function sourceLabel(source: string) {
  const keys: Record<string, string> = {
    yazio_export_v1: 'importsUi.sourceYazio',
    health_auto_export_v2: 'importsUi.sourceHealthAutoExport',
    calograph_sync_v1: 'importsUi.sourceCaloGraph',
    apple_health_xml: 'importsUi.sourceAppleHealth',
    synthetic_demo: 'importsUi.sourceDemo',
  }
  return keys[source] ? t(keys[source]) : source
}

function fileSize(value: number) {
  if (value < 1024 * 1024) return `${integer.format(value / 1024)} KB`
  return `${createNumberFormatter({ maximumFractionDigits: 1 }).format(value / 1024 / 1024)} MB`
}

const importedRecords = computed(() =>
  imports.value.reduce((sum, item) => sum + item.inserted + item.updated, 0),
)
const importErrors = computed(() => imports.value.reduce((sum, item) => sum + item.failed, 0))
const importsWithIssues = computed(() =>
  imports.value.filter(
    (item) =>
      item.status === 'completed_with_errors' ||
      item.status === 'partial_failed' ||
      item.failed > 0 ||
      item.error_message ||
      item.unknown_types.length,
  ),
)
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('importsUi.pageTitle') }}</h1><p>{{ t('importsUi.pageDescription') }}</p></div>
    <span class="page-context">{{ imports.length }} {{ t('importsUi.runs') }}</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <template v-else>
    <section class="import-layout">
      <article class="card import-upload-card">
        <div class="import-upload-icon"><PhDownloadSimple :size="25" weight="duotone" /></div>
        <div>
          <h2>{{ t('importsUi.exportTitle') }}</h2>
          <p>{{ t('importsUi.exportHelp') }}</p>
        </div>
        <div class="import-action-column">
          <label class="import-file-picker">
            <span>{{ t('importsUi.chooseFile') }}</span>
            <small>{{ t('importsUi.fileTypes') }}</small>
            <input type="file" accept=".xml,.zip,.json,application/xml,application/zip,application/json" @change="selectFile" />
          </label>
          <button class="button compact-action import-submit" type="button" :disabled="!selected || uploading" @click="upload">
            {{ uploading ? (progress >= 100 ? t('importsUi.processing') : t('importsUi.uploadProgress', { progress })) : t('importsUi.upload') }}
          </button>
        </div>
        <div v-if="selected" class="selected-file">
          <PhFile :size="20" /><div><strong>{{ selected.name }}</strong><small>{{ fileSize(selected.size) }}</small></div>
        </div>
        <progress v-if="uploading" class="metric-progress import-progress" :value="progress" max="100">{{ progress }} %</progress>
        <p v-if="message" :class="['import-message', { error: messageIsError }]" :role="messageIsError ? 'alert' : 'status'">{{ message }}</p>
      </article>

      <div class="import-stat-stack" :aria-label="t('importsUi.statsAria')">
        <article class="card insight-card">
          <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
          <span><small>{{ t('importsUi.runs') }}</small><strong>{{ imports.length }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
          <span><small>{{ t('importsUi.importedValues') }}</small><strong>{{ integer.format(importedRecords) }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
          <span><small>{{ t('importsUi.failedValues') }}</small><strong>{{ integer.format(importErrors) }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon blue"><PhWarningCircle :size="20" weight="duotone" /></span>
          <span><small>{{ t('importsUi.runsWithIssues') }}</small><strong>{{ importsWithIssues.length }}</strong></span>
        </article>
      </div>
    </section>

    <section class="card form-card" aria-labelledby="yazio-history-heading" style="margin-top: 1rem">
      <h2 id="yazio-history-heading">{{ t('importsUi.historyTitle') }}</h2>
      <p>{{ t('importsUi.historyHelp') }}</p>
      <p><strong>{{ t('importsUi.status') }}</strong> {{ historicalSyncLabel }}</p>
      <p v-if="yazioStatus?.historical_sync?.started_at" class="table-secondary">
        {{ t('importsUi.started', { date: formatGermanDateTime(yazioStatus.historical_sync.started_at) }) }}
      </p>
      <p v-if="yazioStatus?.historical_sync?.last_error" class="import-message error" role="alert">
        {{ yazioStatus.historical_sync.last_error }}
      </p>
      <p v-if="yazioStatus?.historical_sync?.start_date && yazioStatus.historical_sync.end_date" class="table-secondary">
        {{ t('importsUi.period', { from: formatGermanDate(yazioStatus.historical_sync.start_date), to: formatGermanDate(yazioStatus.historical_sync.end_date) }) }}
      </p>
      <div class="form-grid" style="margin-top: 1rem">
        <label class="field">
          {{ t('common.from') }}
          <DateInput v-model="historyFrom" :disabled="historicalSyncing" />
        </label>
        <label class="field">
          {{ t('common.to') }}
          <DateInput v-model="historyTo" :disabled="historicalSyncing" />
        </label>
        <button
          class="button secondary compact-action"
          type="button"
          :disabled="!yazioStatus?.available || !yazioStatus?.configured || !yazioStatus?.sync_enabled || historicalSyncing || yazioStatus?.historical_sync?.state === 'pending' || yazioStatus?.historical_sync?.state === 'running'"
          @click="queueHistoricalSync"
        >
          {{ t('importsUi.queue') }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="dashboard-loading">{{ t('importsUi.loading') }}</div>
    <section v-else class="card table-card">
      <div class="section-card-header">
        <div><h2>{{ t('importsUi.history') }}</h2><p>{{ t('importsUi.unchangedHelp') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ t('importsUi.timestamp') }}</th><th>{{ t('importsUi.source') }}</th><th>{{ t('importsUi.statusColumn') }}</th><th class="number">{{ t('importsUi.received') }}</th><th class="number">{{ t('importsUi.new') }}</th><th class="number">{{ t('importsUi.updated') }}</th><th class="number">{{ t('importsUi.unchanged') }}</th><th class="number">{{ t('importsUi.errors') }}</th><th></th></tr></thead>
          <tbody>
            <template v-for="batch in imports" :key="batch.id">
              <tr>
                <td>{{ formatGermanDateTime(batch.started_at) }}</td>
                <td><strong>{{ sourceLabel(batch.source_type) }}</strong><small v-if="batch.client_identifier" class="table-secondary">{{ batch.client_identifier }}</small></td>
                <td><StatusBadge :status="batch.status" /></td>
                <td class="number">{{ integer.format(batch.received) }}</td>
                <td class="number">{{ integer.format(batch.inserted) }}</td>
                <td class="number">{{ integer.format(batch.updated) }}</td>
                <td class="number">{{ integer.format(batch.skipped) }}</td>
                <td :class="['number', 'difference-value', { over: batch.failed > 0 }]">{{ integer.format(batch.failed) }}</td>
                <td><button class="text-button" type="button" @click="toggleDetails(batch)">{{ expandedId === batch.id ? t('importsUi.close') : t('importsUi.details') }}</button></td>
              </tr>
              <tr v-if="expandedId === batch.id" class="import-detail-row">
                <td colspan="9">
                  <div v-if="!details[batch.id]" class="import-detail-loading">{{ t('importsUi.detailsLoading') }}</div>
                  <div v-else class="import-detail">
                    <div v-if="details[batch.id].error_message" class="import-detail-alert">{{ details[batch.id].error_message }}</div>
                    <div v-if="details[batch.id].unknown_types.length">
                      <strong>{{ t('importsUi.unknownTypes') }}</strong>
                      <p>{{ details[batch.id].unknown_types.join(', ') }}</p>
                    </div>
                    <div v-if="details[batch.id].errors.length">
                      <strong>{{ t('importsUi.failedRecords') }}</strong>
                      <ul>
                        <li v-for="(item, index) in details[batch.id].errors" :key="`${item.item_index}-${index}`">
                          {{ item.item_index == null ? '' : t('importsUi.entry', { index: item.item_index }) }}{{ item.safe_detail }}
                          <small>{{ item.metric_type || item.error_code }}</small>
                        </li>
                      </ul>
                    </div>
                    <div v-if="!details[batch.id].error_message && !details[batch.id].unknown_types.length && !details[batch.id].errors.length" class="quality-ok">
                      <PhCheckCircle :size="18" weight="fill" /> {{ t('importsUi.noDetails') }}
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!imports.length"><td colspan="9" class="empty">{{ t('importsUi.noRuns') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
