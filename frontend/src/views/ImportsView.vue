<script setup lang="ts">
import {
  PhCheckCircle,
  PhDatabase,
  PhDownloadSimple,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { api, ApiError, ensureCsrfToken } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDateTime } from '../date-format'
import type { ImportBatch } from '../types'

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
const details = ref<Record<string, ImportDetail>>({})
const expandedId = ref<string | null>(null)
const selected = ref<File | null>(null)
const progress = ref(0)
const uploading = ref(false)
const loading = ref(true)
const error = ref('')
const message = ref('')
const messageIsError = ref(false)
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
const maxJsonBytes = 10 * 1024 * 1024
const maxAppleHealthBytes = 500 * 1024 * 1024

async function load() {
  error.value = ''
  try {
    imports.value = await api<ImportBatch[]>('/imports')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Importläufe konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
}
onMounted(load)

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
      ? 'Die YAZIO-Datei darf höchstens 10 MB groß sein.'
      : 'Der Apple-Health-Export darf höchstens 500 MB groß sein.'
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
        const counts = `${result.inserted} neu · ${result.updated} aktualisiert · ${result.skipped} unverändert${result.failed ? ` · ${result.failed} fehlerhaft` : ''}`
        message.value =
          result.status === 'partial_failed'
            ? `Teilweise importiert: ${counts}. Du kannst dieselbe Datei erneut importieren, um fortzufahren.`
            : counts
        messageIsError.value = result.status === 'partial_failed' || result.failed > 0
        if (result.status !== 'partial_failed') selected.value = null
        await load()
      } else {
        messageIsError.value = true
        try {
          message.value = (JSON.parse(xhr.responseText) as { detail: string }).detail
        } catch {
          message.value = 'Upload ist fehlgeschlagen.'
        }
      }
    }
    xhr.onerror = () => {
      uploading.value = false
      messageIsError.value = true
      message.value = 'Netzwerkfehler beim Upload.'
    }
    xhr.send(form)
  } catch (cause) {
    uploading.value = false
    messageIsError.value = true
    message.value =
      cause instanceof ApiError ? cause.message : 'Import konnte nicht gestartet werden.'
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
    message.value =
      cause instanceof ApiError ? cause.message : 'Importdetails konnten nicht geladen werden.'
  }
}

function sourceLabel(source: string) {
  const labels: Record<string, string> = {
    yazio_export_v1: 'YAZIO',
    health_auto_export_v2: 'Health Auto Export',
    calograph_sync_v1: 'CaloGraph Sync',
    apple_health_xml: 'Apple Health',
    synthetic_demo: 'Demodaten',
  }
  return labels[source] ?? source
}

function fileSize(value: number) {
  if (value < 1024 * 1024) return `${integer.format(value / 1024)} KB`
  return `${new Intl.NumberFormat('de-DE', { maximumFractionDigits: 1 }).format(value / 1024 / 1024)} MB`
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
    <div><h1>Datenimport</h1><p>Historische Apple-Health- und YAZIO-Dateien sowie alle bisherigen Importläufe.</p></div>
    <span class="page-context">{{ imports.length }} Importläufe</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <template v-else>
    <section class="import-layout">
      <article class="card import-upload-card">
        <div class="import-upload-icon"><PhDownloadSimple :size="25" weight="duotone" /></div>
        <div>
          <h2>Historischen Export importieren</h2>
          <p>Unterstützt werden Apple Health als <code>export.xml</code> oder ZIP bis 500 MB und YAZIO als <code>days.json</code> oder <code>nutrients.json</code> bis 10 MB. Die Dateien verlassen deine Infrastruktur nicht.</p>
        </div>
        <label class="import-file-picker">
          <span>Datei auswählen</span>
          <small>XML, ZIP oder JSON</small>
          <input type="file" accept=".xml,.zip,.json,application/xml,application/zip,application/json" @change="selectFile" />
        </label>
        <div v-if="selected" class="selected-file">
          <PhDatabase :size="18" weight="duotone" />
          <span><strong>{{ selected.name }}</strong><small>{{ fileSize(selected.size) }}</small></span>
        </div>
        <button class="button import-submit" type="button" :disabled="!selected || uploading" @click="upload">
          {{ uploading ? (progress >= 100 ? 'Wird verarbeitet …' : `Upload ${progress} %`) : 'Datei importieren' }}
        </button>
        <progress v-if="uploading" class="metric-progress import-progress" :value="progress" max="100">{{ progress }} %</progress>
        <p v-if="message" :class="['import-message', { error: messageIsError }]" :role="messageIsError ? 'alert' : 'status'">{{ message }}</p>
      </article>

      <div class="import-stat-stack" aria-label="Importkennzahlen">
        <article class="card insight-card">
          <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
          <span><small>Importläufe</small><strong>{{ imports.length }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
          <span><small>Übernommene Werte</small><strong>{{ integer.format(importedRecords) }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
          <span><small>Fehlerhafte Werte</small><strong>{{ integer.format(importErrors) }}</strong></span>
        </article>
        <article class="card insight-card">
          <span class="insight-icon blue"><PhWarningCircle :size="20" weight="duotone" /></span>
          <span><small>Läufe mit Hinweisen</small><strong>{{ importsWithIssues.length }}</strong></span>
        </article>
      </div>
    </section>

    <div v-if="loading" class="dashboard-loading">Importläufe werden geladen …</div>
    <section v-else class="card table-card">
      <div class="section-card-header">
        <div><h2>Importverlauf</h2><p>„Unverändert“ bedeutet, dass ein bereits vorhandener Wert erkannt und nicht doppelt gespeichert wurde.</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>Zeitpunkt</th><th>Quelle</th><th>Status</th><th class="number">Empfangen</th><th class="number">Neu</th><th class="number">Aktualisiert</th><th class="number">Unverändert</th><th class="number">Fehler</th><th></th></tr></thead>
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
                <td><button class="text-button" type="button" @click="toggleDetails(batch)">{{ expandedId === batch.id ? 'Schließen' : 'Details' }}</button></td>
              </tr>
              <tr v-if="expandedId === batch.id" class="import-detail-row">
                <td colspan="9">
                  <div v-if="!details[batch.id]" class="import-detail-loading">Details werden geladen …</div>
                  <div v-else class="import-detail">
                    <div v-if="details[batch.id].error_message" class="import-detail-alert">{{ details[batch.id].error_message }}</div>
                    <div v-if="details[batch.id].unknown_types.length">
                      <strong>Unbekannte Datentypen</strong>
                      <p>{{ details[batch.id].unknown_types.join(', ') }}</p>
                    </div>
                    <div v-if="details[batch.id].errors.length">
                      <strong>Fehlerhafte Datensätze</strong>
                      <ul>
                        <li v-for="(item, index) in details[batch.id].errors" :key="`${item.item_index}-${index}`">
                          {{ item.item_index == null ? '' : `Eintrag ${item.item_index}: ` }}{{ item.safe_detail }}
                          <small>{{ item.metric_type || item.error_code }}</small>
                        </li>
                      </ul>
                    </div>
                    <div v-if="!details[batch.id].error_message && !details[batch.id].unknown_types.length && !details[batch.id].errors.length" class="quality-ok">
                      <PhCheckCircle :size="18" weight="fill" /> Keine Fehlerdetails für diesen Lauf.
                    </div>
                  </div>
                </td>
              </tr>
            </template>
            <tr v-if="!imports.length"><td colspan="9" class="empty">Noch keine Importläufe.</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
