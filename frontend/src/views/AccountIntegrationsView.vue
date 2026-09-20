<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import { api, ApiError, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import { formatGermanDateTime, isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { GoogleHealthStatus, ImportSummary, YazioStatus } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const yazio = ref<YazioStatus | null>(null)
const google = ref<GoogleHealthStatus | null>(null)
const yazioEmail = ref('')
const yazioPassword = ref('')
const yazioHistoryFrom = ref('')
const yazioHistoryTo = ref('')
const savingYazio = ref(false)
const syncingYazio = ref(false)
const googleActionBusy = ref(false)
const yazioMessage = ref('')
const yazioError = ref('')
const yazioLoadError = ref('')
const syncMessage = ref('')
const syncError = ref('')
const syncWarning = ref('')
const googleError = ref('')
const googleMessage = ref('')
const initialSetupSaved = ref(false)
const error = ref('')
const loading = ref(true)
const loaded = ref(false)
let loadGeneration = 0
let yazioPollTimer: ReturnType<typeof setTimeout> | null = null
let yazioPollInFlight = false
let yazioPollGeneration = 0
let integrationsMounted = true

const yazioCredentialsComplete = computed(
  () => Boolean(yazioEmail.value.trim()) && Boolean(yazioPassword.value)
    && (yazio.value?.configured === true || Boolean(yazioHistoryFrom.value && yazioHistoryTo.value)),
)
const yazioAvailable = computed(() => yazio.value?.available === true)
const yazioStatusLabel = computed(() => {
  if (!yazio.value) return t('accountIntegrations.notAvailable')
  if (!yazio.value.configured) return t('settings.notConfigured')
  if (yazio.value?.scheduler_enabled === false) return t('accountIntegrations.schedulerPaused')
  const historicalState = yazio.value.historical_sync?.state
  if (historicalState === 'pending') return t('settings.firstImportWaiting')
  if (historicalState === 'running') return t('settings.firstImportRunning')
  if (historicalState === 'failed') return t('settings.firstImportFailed')
  if (!yazio.value.sync_enabled) return t('settings.paused')
  return t('settings.active', { hours: (yazio.value.sync_interval_minutes ?? 360) / 60, days: yazio.value.sync_days ?? 7 })
})
const yazioHistoricalSyncActive = computed(() => {
  const state = yazio.value?.historical_sync?.state
  return yazioAvailable.value && yazio.value?.scheduler_enabled !== false && (state === 'pending' || state === 'running')
})
const yazioHistoricalSyncFailed = computed(
  () => yazio.value?.historical_sync?.state === 'failed',
)
const googleCanConnect = computed(() => {
  const state = google.value?.state
  return google.value?.available === true && google.value.configured === true && state !== 'active'
})
const googleStatusLabel = computed(() => {
  switch (google.value?.state) {
    case 'active': return t('accountIntegrations.googleActive')
    case 'reauth_required': return t('accountIntegrations.googleReauth')
    case 'scope_missing': return t('accountIntegrations.googleScopeMissing')
    case 'not_connected': return t('accountIntegrations.googleNotConnected')
    case 'disabled': return t('accountIntegrations.googleDisabled')
    default: return t('accountIntegrations.notAvailable')
  }
})

function timestampLabel(value: string | null | undefined): string {
  return value ? formatGermanDateTime(value) : t('accountIntegrations.notAvailable')
}
async function load(): Promise<void> {
  const generation = ++loadGeneration
  stopYazioPolling()
  loading.value = true
  loaded.value = false
  error.value = ''
  yazioLoadError.value = ''
  googleError.value = ''
  googleMessage.value = ''
  initialSetupSaved.value = false
  yazio.value = null
  google.value = null
  const callbackState = typeof window === 'undefined'
    ? null
    : new URLSearchParams(window.location.search).get('google_health')
  if (callbackState === 'connected') googleMessage.value = t('accountIntegrations.googleConnected')
  if (callbackState === 'error') googleError.value = t('accountIntegrations.googleCallbackFailed')
  const [yazioResult, googleResult] = await Promise.allSettled([
    api<YazioStatus>('/yazio/status'),
    api<GoogleHealthStatus>('/google-health/status'),
  ])
  if (generation !== loadGeneration) return
  if (yazioResult.status === 'fulfilled') {
    yazio.value = yazioResult.value
    if (!yazioResult.value.configured) yazioHistoryTo.value = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
    scheduleYazioPolling()
  } else {
    yazioLoadError.value = yazioResult.reason instanceof ApiError
      ? localizeApiError(yazioResult.reason, 'settingsUi.loadFailed')
      : t('settingsUi.loadFailed')
  }
  if (googleResult.status === 'fulfilled') {
    google.value = googleResult.value
  } else {
    googleError.value = googleResult.reason instanceof ApiError
      ? localizeApiError(googleResult.reason, 'accountIntegrations.googleLoadFailed')
      : t('accountIntegrations.googleLoadFailed')
  }
  yazioMessage.value = ''
  yazioError.value = ''
  syncMessage.value = ''
  syncError.value = ''
  syncWarning.value = ''
  loaded.value = true
  loading.value = false
}

function stopYazioPolling(): void {
  if (yazioPollTimer) {
    clearTimeout(yazioPollTimer)
    yazioPollTimer = null
  }
  yazioPollGeneration += 1
}

function scheduleYazioPolling(): void {
  if (
    !integrationsMounted
    || !yazioHistoricalSyncActive.value
    || yazioPollTimer
  ) return
  yazioPollTimer = setTimeout(() => {
    yazioPollTimer = null
    void pollYazioStatus()
  }, 5000)
}

async function pollYazioStatus(): Promise<void> {
  if (
    !integrationsMounted
    || !yazioHistoricalSyncActive.value
    || yazioPollInFlight
  ) return
  yazioPollInFlight = true
  const generation = yazioPollGeneration
  try {
    const result = await api<YazioStatus>('/yazio/status')
    if (generation !== yazioPollGeneration) return
    yazio.value = result
  } catch {
    // The next scheduled status request retries without replacing the page state.
  } finally {
    yazioPollInFlight = false
    scheduleYazioPolling()
  }
}

async function saveYazio(): Promise<void> {
  error.value = ''
  if (!yazioCredentialsComplete.value) return
  const isNewConnection = !yazio.value?.configured
  if (isNewConnection && yazioHistoryFrom.value > yazioHistoryTo.value) {
    yazioError.value = t('settingsUi.invalidRange')
    return
  }
  savingYazio.value = true
  yazioError.value = ''
  yazioMessage.value = ''
  try {
    yazio.value = await api<YazioStatus>('/yazio/connection', {
      method: 'PUT',
      body: JSON.stringify({
        email: yazioEmail.value.trim(),
        password: yazioPassword.value,
        ...(isNewConnection
          ? { from_date: yazioHistoryFrom.value, end_date: yazioHistoryTo.value }
          : {}),
      }),
    })
    yazioEmail.value = ''
    yazioPassword.value = ''
    yazioMessage.value = isNewConnection
      ? t('settings.connectionSaved')
      : t('settings.connectionUpdated')
    initialSetupSaved.value = isNewConnection
    scheduleYazioPolling()
  } catch (cause) {
    yazioError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'settingsUi.yazioSaveFailed', { preserveDetail: false })
      : t('settingsUi.yazioSaveFailed')
  } finally {
    savingYazio.value = false
  }
}

async function syncYazio(): Promise<void> {
  if (
    syncingYazio.value
    || !yazioAvailable.value
    || !yazio.value?.configured
  ) return
  syncingYazio.value = true
  syncMessage.value = ''
  syncError.value = ''
  syncWarning.value = ''
  try {
    const result = await api<ImportSummary>('/yazio/sync', { method: 'POST' })
    syncMessage.value = t('accountIntegrations.manualSyncResult', {
      new: result.inserted,
      updated: result.updated,
      unchanged: result.skipped,
    })
    try {
      yazio.value = await api<YazioStatus>('/yazio/status')
      scheduleYazioPolling()
    } catch {
      syncWarning.value = t('accountIntegrations.manualSyncRefreshFailed')
    }
  } catch (cause) {
    syncError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountIntegrations.manualSyncFailed')
      : t('accountIntegrations.manualSyncFailed')
  } finally {
    syncingYazio.value = false
  }
}

async function connectGoogle(): Promise<void> {
  if (googleActionBusy.value || !googleCanConnect.value) return
  googleActionBusy.value = true
  googleError.value = ''
  try {
    const result = await api<{ authorization_url: string }>('/google-health/oauth/start', { method: 'POST' })
    window.location.assign(result.authorization_url)
  } catch (cause) {
    googleError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountIntegrations.googleConnectFailed')
      : t('accountIntegrations.googleConnectFailed')
  } finally {
    googleActionBusy.value = false
  }
}

onBeforeUnmount(() => {
  integrationsMounted = false
  ++loadGeneration
  stopYazioPolling()
})

void load()
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('accountIntegrations.title') }}</h1>
      <p>{{ t('accountIntegrations.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('common.loading') }}
  </div>
  <template v-else>
    <section v-if="error && !loaded" class="card account-feedback error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button compact-action" type="button" @click="load">{{ t('common.tryAgain') }}</button>
    </section>

    <template v-if="loaded">
      <section class="card form-card integration-card yazio-connection-card" :aria-busy="savingYazio || syncingYazio" aria-labelledby="yazio-integration-title">
        <div class="yazio-card-title">
          <span class="yazio-icon" aria-hidden="true"></span>
          <h2 id="yazio-integration-title">{{ t('settingsUi.yazioTitle') }}</h2>
        </div>
        <p>{{ t('settingsUi.yazioDescription') }}</p>
        <div class="integration-panel yazio-credentials-panel">
          <h3>{{ t('accountIntegrations.credentialsTitle') }}</h3>
          <div v-if="yazioLoadError" class="card error" role="alert">
            <p>{{ yazioLoadError }}</p>
            <button class="button compact-action" type="button" @click="load">{{ t('common.tryAgain') }}</button>
          </div>
          <div v-if="yazioError" class="card error" role="alert">{{ yazioError }}</div>
          <p v-if="yazioMessage" class="setup-notice" role="status">{{ yazioMessage }}</p>
          <div v-if="initialSetupSaved" class="setup-notice" role="status">
            <p>{{ t('settingsUi.initialImport') }}</p>
            <RouterLink class="text-button" :to="{ name: 'account-imports' }">{{ t('settingsUi.toImports') }}</RouterLink>
          </div>
          <form class="form-grid yazio-credential-form" @submit.prevent="saveYazio">
            <label class="field">
              <span>{{ t('settingsUi.email') }}</span>
              <input
                v-model="yazioEmail"
                name="yazio-email"
                type="email"
                autocomplete="email"
                :disabled="!yazioAvailable"
                :placeholder="yazio?.configured ? t('settingsUi.credentialStoredPlaceholder') : t('settingsUi.emailPlaceholder')"
                required
              />
            </label>
            <label class="field">
              <span>{{ t('settingsUi.passwordLabel') }}</span>
              <input
                v-model="yazioPassword"
                name="yazio-password"
                type="password"
                autocomplete="current-password"
                :disabled="!yazioAvailable"
                :placeholder="yazio?.configured ? t('settingsUi.credentialStoredPlaceholder') : t('settingsUi.passwordLabel')"
                required
              />
            </label>
            <template v-if="!yazio?.configured">
              <label class="field">
                {{ t('settingsUi.firstImportFrom') }}
                <DateInput v-model="yazioHistoryFrom" required :disabled="!yazioAvailable" />
              </label>
              <label class="field">
                {{ t('settingsUi.to') }}
                <DateInput v-model="yazioHistoryTo" required :disabled="!yazioAvailable" />
              </label>
              <p class="table-secondary">{{ t('settingsUi.historyHelp') }}</p>
            </template>
            <button
              class="button compact-action"
              type="submit"
              :disabled="savingYazio || !yazioCredentialsComplete || !yazioAvailable"
            >
              {{ savingYazio ? t('settingsUi.checkConnection') : yazio?.configured ? t('settingsUi.updateConnection') : t('settingsUi.setupConnection') }}
            </button>
          </form>
        </div>
        <div class="integration-panel yazio-status-panel">
          <h3>{{ t('accountIntegrations.statusTitle') }}</h3>
          <dl class="integration-details">
            <div><dt>{{ t('settingsUi.statusLabel') }}</dt><dd>{{ yazioStatusLabel }}</dd></div>
            <div><dt>{{ t('accountIntegrations.scheduler') }}</dt><dd>{{ yazio?.scheduler_enabled !== false && yazio?.sync_enabled ? t('accountIntegrations.schedulerActive') : t('accountIntegrations.schedulerPaused') }}</dd></div>
            <div><dt>{{ t('accountIntegrations.lastAttempt') }}</dt><dd>{{ timestampLabel(yazio?.last_attempt_at) }}</dd></div>
            <div><dt>{{ t('accountIntegrations.lastSuccess') }}</dt><dd>{{ timestampLabel(yazio?.last_success_at) }}</dd></div>
            <div><dt>{{ t('accountIntegrations.nextSync') }}</dt><dd>{{ timestampLabel(yazio?.next_sync_at) }}</dd></div>
          </dl>
          <p v-if="yazio?.last_error" class="import-message error" role="alert">
            <strong>{{ t('accountIntegrations.lastError') }}:</strong> {{ yazio.last_error }}
          </p>
          <p v-if="yazioHistoricalSyncFailed" class="import-message error" role="alert">
            {{ t('settingsUi.firstImportFailedMessage') }}
            <RouterLink :to="{ name: 'account-imports' }">{{ t('settingsUi.detailsUnderImports') }}</RouterLink>
          </p>
          <p
            v-if="yazio?.historical_sync?.state === 'completed' && yazio?.historical_sync.completed_at"
            class="table-secondary"
          >
            {{ t('settingsUi.firstImportCompleted') }}
            {{ formatGermanDateTime(yazio.historical_sync.completed_at) }}
          </p>
        </div>
        <div class="integration-panel yazio-manual-sync-panel">
          <h3>{{ t('accountIntegrations.manualSyncTitle') }}</h3>
          <p>{{ t('accountIntegrations.manualSyncDescription') }}</p>
          <button
            class="button secondary compact-action"
            type="button"
            :disabled="syncingYazio || !yazioAvailable || !yazio?.configured"
            @click="syncYazio"
          >
            {{ syncingYazio ? t('accountIntegrations.manualSyncRunning') : t('accountIntegrations.manualSyncAction') }}
          </button>
          <p v-if="syncMessage" class="setup-notice" role="status">{{ syncMessage }}</p>
          <p v-if="syncWarning" class="import-message warning" role="status">{{ syncWarning }}</p>
          <p v-if="syncError" class="import-message error" role="alert">{{ syncError }}</p>
        </div>
      </section>

      <section class="card form-card integration-card google-health-card" :aria-busy="googleActionBusy" aria-labelledby="google-health-title">
        <h2 id="google-health-title">{{ t('accountIntegrations.googleTitle') }}</h2>
        <p>{{ t('accountIntegrations.googleDescription') }}</p>
        <p><strong>{{ t('accountIntegrations.googleStatus') }}:</strong> {{ googleStatusLabel }}</p>
        <p v-if="google?.last_success_at" class="table-secondary">
          {{ t('accountIntegrations.lastSuccess') }}: {{ timestampLabel(google.last_success_at) }}
        </p>
        <p v-if="google?.last_error" class="import-message error" role="alert">{{ google.last_error }}</p>
        <div v-if="googleError" class="import-message error" role="alert">
          <p>{{ googleError }}</p>
          <button v-if="!google" class="button compact-action" type="button" @click="load">{{ t('common.tryAgain') }}</button>
        </div>
        <p v-if="googleMessage" class="setup-notice" role="status">{{ googleMessage }}</p>
        <button
          v-if="googleCanConnect"
          class="button compact-action"
          type="button"
          :disabled="googleActionBusy"
          @click="connectGoogle"
        >
          {{ googleActionBusy ? t('accountIntegrations.googleConnecting') : google?.state === 'not_connected' ? t('accountIntegrations.googleConnect') : t('accountIntegrations.googleReconnect') }}
        </button>
      </section>

      <section class="card form-card integration-card apple-health-card" aria-labelledby="apple-health-title">
        <h2 id="apple-health-title">{{ t('accountIntegrations.appleTitle') }}</h2>
        <p>{{ t('accountIntegrations.appleDescription') }}</p>
        <p class="table-secondary">{{ t('accountIntegrations.appleExport') }}</p>
        <RouterLink class="button secondary compact-action" :to="{ name: 'account-imports' }">
          {{ t('accountIntegrations.openImports') }}
        </RouterLink>
      </section>
    </template>
  </template>
</template>
