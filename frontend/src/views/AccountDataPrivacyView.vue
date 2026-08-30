<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { api, ApiError, localizeApiError, notifyAuthenticationExpired } from '../api'
import { useProfilePreferences } from '../composables/useProfilePreferences'
import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
const {
  profile,
  loaded,
  load: loadProfilePreferences,
  save: saveProfilePreferences,
  invalidate,
} = useProfilePreferences()

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')
const exportingData = ref(false)
const dataExportError = ref('')
const portableFile = ref<File | null>(null)
const portablePreview = ref<Record<string, number | string> | null>(null)
const portableImportError = ref('')
const portableImporting = ref(false)

async function loadPage(): Promise<void> {
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    await loadProfilePreferences()
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountData.loadFailed', { preserveDetail: false })
      : t('accountData.loadFailed')
  } finally {
    loading.value = false
  }
}

async function saveRetention(): Promise<void> {
  error.value = ''
  message.value = ''
  saving.value = true
  try {
    const result = await saveProfilePreferences()
    if (result) message.value = t('accountData.saved')
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountData.saveFailed', { preserveDetail: false })
      : t('accountData.saveFailed')
  } finally {
    saving.value = false
  }
}

const exportStatusCookiePrefix = 'calograph_export_status_'
type ExportStatus = 'accepted' | 'busy' | 'unauthenticated'

function exportStatusCookieName(downloadId: string): string {
  return `${exportStatusCookiePrefix}${downloadId.replaceAll('-', '')}`
}

function clearExportStatusCookie(downloadId: string) {
  document.cookie = `${exportStatusCookieName(downloadId)}=; Max-Age=0; Path=/`
}

function readExportStatus(downloadId: string): ExportStatus | null {
  const cookieName = exportStatusCookieName(downloadId)
  const value = document.cookie
    .split('; ')
    .find((entry) => entry.startsWith(`${cookieName}=`))
    ?.slice(cookieName.length + 1)
  return value === 'accepted' || value === 'busy' || value === 'unauthenticated'
    ? value
    : null
}

async function waitForExportStatus(downloadId: string): Promise<ExportStatus | null> {
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    const status = readExportStatus(downloadId)
    if (status) return status
    await new Promise((resolve) => window.setTimeout(resolve, 50))
  }
  return null
}

async function exportUserData() {
  if (exportingData.value) return
  exportingData.value = true
  const downloadId = crypto.randomUUID()
  clearExportStatusCookie(downloadId)
  const link = document.createElement('a')
  link.href = `/api/v1/settings/export?download_id=${encodeURIComponent(downloadId)}`
  link.setAttribute('download', '')
  document.body.append(link)
  link.click()
  link.remove()
  try {
    const status = await waitForExportStatus(downloadId)
    if (status === 'busy') {
      dataExportError.value = localizeApiError(new ApiError(
        'Ein anderer Datenexport läuft bereits. Bitte versuche es in Kürze erneut.',
        429,
        undefined,
        '30',
        'urn:calograph:problem:data-export-busy',
      ))
    } else if (status === 'unauthenticated') {
      notifyAuthenticationExpired()
    } else if (status === null) {
      dataExportError.value = t('accountData.dataExportFailed')
    }
  } finally {
    clearExportStatusCookie(downloadId)
    exportingData.value = false
  }
}

function selectPortableFile(event: Event) {
  const input = event.target as HTMLInputElement
  portableFile.value = input.files?.[0] ?? null
  portablePreview.value = null
  portableImportError.value = ''
}

async function previewPortableImport() {
  if (!portableFile.value || portableImporting.value) return
  portableImporting.value = true
  portableImportError.value = ''
  try {
    const form = new FormData()
    form.append('file', portableFile.value)
    portablePreview.value = await api<Record<string, number | string>>('/import/calo/preview', {
      method: 'POST',
      body: form,
    })
  } catch (cause) {
    portableImportError.value = cause instanceof ApiError ? localizeApiError(cause) : t('accountData.portableImportFailed')
  } finally {
    portableImporting.value = false
  }
}

async function applyPortableImport() {
  if (!portableFile.value || portableImporting.value || !portablePreview.value) return
  portableImporting.value = true
  portableImportError.value = ''
  try {
    const form = new FormData()
    form.append('file', portableFile.value)
    await api('/import/calo/apply', { method: 'POST', body: form })
    const refreshed = await loadProfilePreferences()
    if (!refreshed) {
      portableImportError.value = t('accountData.portableImportFailed')
      return
    }
    message.value = t('accountData.portableImportCompleted')
    portablePreview.value = null
    portableFile.value = null
  } catch (cause) {
    portableImportError.value = cause instanceof ApiError ? localizeApiError(cause) : t('accountData.portableImportFailed')
  } finally {
    portableImporting.value = false
  }
}

onMounted(() => { void loadPage() })
onBeforeUnmount(() => { invalidate() })
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('accountData.title') }}</h1>
      <p>{{ t('accountData.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('accountData.loading') }}
  </div>
  <template v-else>
    <section v-if="error && !loaded" class="card account-feedback error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button compact-action" type="button" :disabled="loading" @click="loadPage">{{ t('accountData.retry') }}</button>
    </section>

    <template v-if="loaded">
      <div class="account-page-stack">
        <p v-if="message" class="account-form-success" role="status" aria-live="polite">{{ message }}</p>

        <section class="card form-card account-form-card" :aria-busy="saving">
          <h2>{{ t('accountData.retentionTitle') }}</h2>
          <p>{{ t('accountData.retentionDescription') }}</p>
          <form class="form-grid account-form-grid" @submit.prevent="saveRetention">
            <label class="field">
              <span>{{ t('accountData.retention') }}</span>
              <input
                v-model.number="profile.raw_payload_retention_days"
                name="raw_payload_retention_days"
                type="number"
                min="0"
                max="3650"
                required
                :disabled="saving"
              />
              <small>{{ t('accountData.retentionHelp') }}</small>
            </label>
            <div v-if="error" class="account-form-error" role="alert" aria-live="assertive">{{ error }}</div>
            <button class="button compact-action account-submit" type="submit" :disabled="saving">
              {{ saving ? t('accountData.saving') : t('accountData.save') }}
            </button>
          </form>
        </section>

        <section class="card form-card account-data-card" :aria-busy="exportingData || portableImporting">
          <h2>{{ t('accountData.dataControlsTitle') }}</h2>
          <div class="account-data-section">
            <h3>{{ t('accountData.dataExportTitle') }}</h3>
            <p>{{ t('accountData.dataExportDescription') }}</p>
            <p class="table-secondary">{{ t('accountData.dataExportPrivacy') }}</p>
            <div v-if="dataExportError" class="error" role="alert" aria-live="assertive">{{ dataExportError }}</div>
            <button class="button account-action-button compact-action" type="button" :disabled="exportingData" @click="exportUserData">
              {{ exportingData ? t('accountData.dataExportRunning') : t('accountData.dataExportAction') }}
            </button>
          </div>
          <div class="account-data-section">
            <h3>{{ t('accountData.csvExportTitle') }}</h3>
            <p>{{ t('accountData.csvExportDescription') }}</p>
            <a class="button account-action-button compact-action" href="/api/v1/settings/csv-export" download="">{{ t('accountData.csvExportAction') }}</a>
          </div>
          <div class="account-data-section">
            <h3>{{ t('accountData.portableImportTitle') }}</h3>
            <p>{{ t('accountData.portableImportDescription') }}</p>
            <div class="portable-import-actions">
              <label class="account-file-picker" for="portable-file-input">
                <span class="button secondary compact-action">{{ t('accountData.chooseFile') }}</span>
                <span
                  v-if="portableFile"
                  class="selected-file-name"
                  :title="portableFile.name"
                >{{ portableFile.name }}</span>
                <input
                  id="portable-file-input"
                  type="file"
                  accept=".zip,application/zip"
                  :disabled="portableImporting"
                  @change="selectPortableFile"
                />
              </label>
              <button class="button account-action-button compact-action backup-validate-button" type="button" :disabled="!portableFile || portableImporting" @click="previewPortableImport">
                {{ portableImporting ? t('accountData.portableImportRunning') : t('accountData.portableImportPreview') }}
              </button>
            </div>
            <div v-if="portableImportError" class="error" role="alert" aria-live="assertive">{{ portableImportError }}</div>
            <div v-if="portablePreview" class="setup-notice" role="status" aria-live="polite">
              <p>{{ t('accountData.portableImportFound', { samples: portablePreview.health_samples, targets: portablePreview.targets, overrides: portablePreview.tracking_overrides }) }}</p>
              <button class="button account-action-button compact-action" type="button" :disabled="portableImporting" @click="applyPortableImport">{{ t('accountData.portableImportApply') }}</button>
            </div>
          </div>
        </section>
      </div>
    </template>
  </template>
</template>
