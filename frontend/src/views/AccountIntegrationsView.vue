<script setup lang="ts">
import { PhAppleLogo, PhGoogleLogo } from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, ref } from 'vue'

import { api, ApiError, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import { formatGermanDateTime, isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type {
  GoogleHealthConnectionTestStatus,
  GoogleHealthDomainKey,
  GoogleHealthSyncResult,
  GoogleHealthStatus,
  ImportSummary,
  YazioStatus,
} from '../types'

const GOOGLE_BADGE_VARIANTS = new Set([
  'active',
  'completed',
  'disabled',
  'empty',
  'failed',
  'not_configured',
  'not_connected',
  'reauth_required',
  'retrying',
  'running',
  'scope_missing',
])

const googleSyncDomains: ReadonlyArray<{ key: GoogleHealthDomainKey; labelKey: string }> = [
  { key: 'nutrition', labelKey: 'accountIntegrations.googleDomainNutrition' },
  { key: 'activity_energy', labelKey: 'accountIntegrations.googleDomainActivity' },
  { key: 'weight', labelKey: 'accountIntegrations.googleDomainWeight' },
]
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const yazio = ref<YazioStatus | null>(null)
const google = ref<GoogleHealthStatus | null>(null)
const googleClientId = ref('')
const googleClientSecret = ref('')
const googleSecretVisible = ref(false)
const googleCredentialSaving = ref(false)
const googleCredentialDeleting = ref(false)
const googleDisconnecting = ref(false)
const googleConnectionTestBusy = ref(false)
const googleConnectionTestResult = ref<GoogleHealthConnectionTestStatus | null>(null)
const googleCredentialError = ref('')
const googleCredentialMessage = ref('')
const googleConnectionTestError = ref('')
const yazioEmail = ref('')
const yazioPassword = ref('')
const yazioHistoryFrom = ref('')
const yazioHistoryTo = ref('')
const savingYazio = ref(false)
const syncingYazio = ref(false)
const googleActionBusy = ref(false)
const googleSyncResult = ref<GoogleHealthSyncResult | null>(null)
const googleSyncError = ref('')
const googleSyncWarning = ref('')
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
    case 'not_configured': return t('accountIntegrations.googleNotConfigured')
    case 'reauth_required': return t('accountIntegrations.googleReauth')
    case 'scope_missing': return t('accountIntegrations.googleScopeMissing')
    case 'not_connected': return t('accountIntegrations.googleNotConnected')
    case 'disabled': return t('accountIntegrations.googleDisabled')
    default: return t('accountIntegrations.notAvailable')
  }
})
const googleCanSync = computed(() => (
  google.value?.available === true
  && google.value.configured === true
  && google.value.state === 'active'
  && google.value.sync_state !== 'running'
))

function timestampLabel(value: string | null | undefined): string {
  return value ? formatGermanDateTime(value) : t('accountIntegrations.notAvailable')
}



const googleNeedsReauth = computed(() => (
  google.value?.state === 'reauth_required' || google.value?.state === 'scope_missing'
))
const googleCredentialsComplete = computed(() => {
  const clientIdEmpty = !googleClientId.value.trim()
  const secretEmpty = !googleClientSecret.value
  if (google.value?.configured) return (clientIdEmpty && secretEmpty) || (!clientIdEmpty && !secretEmpty)
  return !clientIdEmpty && !secretEmpty
})
const googleAvailable = computed(() => google.value?.available === true)
const googleCanTest = computed(() => googleAvailable.value && google.value?.configured === true)
const googleCanDisconnect = computed(() => googleAvailable.value && google.value?.configured === true && (
  google.value?.state === 'active' || googleNeedsReauth.value
))

function googleDomainStatusLabel(status: string): string {
  if (status === 'success') return t('accountIntegrations.googleDomainSuccess')
  if (status === 'truncated') return t('accountIntegrations.googleDomainTruncated')
  if (status === 'no_data') return t('accountIntegrations.googleDomainNoData')
  if (status === 'reauth_required') return t('accountIntegrations.googleReauth')
  if (status === 'failed') return t('accountIntegrations.googleDomainFailed')
  return t('accountIntegrations.notAvailable')
}

function googleDomainErrorLabel(errorCode: string | null): string {
  if (errorCode === 'scope_missing') return t('accountIntegrations.googleScopeMissing')
  if (errorCode === 'reauth_required') return t('accountIntegrations.googleReauth')
  return t('accountIntegrations.googleDomainFailed')
}

function googleSyncStatusLabel(status: string): string {
  if (status === 'success') return t('accountIntegrations.googleSyncSuccess')
  if (status === 'partial_failure') return t('accountIntegrations.googleSyncPartialFailure')
  if (status === 'reauth_required') return t('accountIntegrations.googleReauth')
  if (status === 'failed') return t('accountIntegrations.googleDomainFailed')
  if (status === 'no_data') return t('accountIntegrations.googleDomainNoData')
  return t('accountIntegrations.notAvailable')
}

const googleBadgeLabel = computed(() => {
  if (!google.value) return t('accountIntegrations.googleEmpty')
  if (google.value.state === 'disabled' || !google.value.available) return t('accountIntegrations.googleDisabled')
  if (google.value.state === 'not_configured') return t('accountIntegrations.googleNotConfigured')
  if (google.value.sync_state === 'running' && google.value.next_retry_at) return t('accountIntegrations.googleRetrying')
  if (google.value.sync_state === 'running') return t('accountIntegrations.googleSyncing')
  if (google.value.sync_state === 'failed') return t('accountIntegrations.googleFailed')
  if (google.value.sync_state === 'completed') return t('accountIntegrations.googleSyncSuccess')
  if (googleNeedsReauth.value) return t('accountIntegrations.googleReauth')
  if (google.value.state === 'not_connected') return t('accountIntegrations.googleNotConnected')
  return t('accountIntegrations.googleActive')
})
const googleBadgeVariant = computed(() => {
  if (!google.value) return 'empty'
  if (google.value.state === 'disabled' || !google.value.available) return 'disabled'
  if (google.value.state === 'not_configured') return 'not_configured'
  if (google.value.sync_state === 'running' && google.value.next_retry_at) return 'retrying'
  if (google.value.sync_state === 'running') return 'running'
  if (google.value.sync_state === 'failed') return 'failed'
  if (google.value.sync_state === 'completed') return 'completed'
  return GOOGLE_BADGE_VARIANTS.has(google.value.state) ? google.value.state : 'empty'
})
const googleSyncStateLabel = computed(() => {
  if (!google.value) return t('accountIntegrations.googleEmpty')
  if (google.value.sync_state === 'running') return t('accountIntegrations.googleSyncing')
  if (google.value.sync_state === 'failed') return t('accountIntegrations.googleFailed')
  if (google.value.sync_state === 'completed') return t('accountIntegrations.googleSyncSuccess')
  return t('accountIntegrations.googleSyncIdle')
})

function googleRetryLabel(): string {
  if (!google.value || google.value.retry_attempt <= 0 || !google.value.next_retry_at) return ''
  return t('accountIntegrations.googleRetryAttempt', {
    attempt: google.value.retry_attempt,
    max: google.value.retry_max_attempts,
  })
}

async function saveGoogleCredentials(): Promise<void> {
  if (googleCredentialSaving.value || !googleAvailable.value) return
  googleCredentialError.value = ''
  googleCredentialMessage.value = ''
  if (!googleCredentialsComplete.value) {
    googleCredentialError.value = t('accountIntegrations.googleCredentialPairRequired')
    return
  }
  googleCredentialSaving.value = true
  try {
    google.value = await api<GoogleHealthStatus>('/google-health/credentials', {
      method: 'PUT',
      body: JSON.stringify({
        client_id: googleClientId.value.trim(),
        client_secret: googleClientSecret.value,
      }),
    })
    googleClientId.value = ''
    googleClientSecret.value = ''
    googleSecretVisible.value = false
    googleCredentialMessage.value = t('accountIntegrations.googleCredentialsSaved')
    googleConnectionTestResult.value = null
  } catch (cause) {
    googleCredentialError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountIntegrations.googleCredentialsSaveFailed', { preserveDetail: false })
      : t('accountIntegrations.googleCredentialsSaveFailed')
  } finally {
    googleCredentialSaving.value = false
  }
}

async function deleteGoogleCredentials(): Promise<void> {
  if (googleCredentialDeleting.value || !googleAvailable.value || !window.confirm(t('accountIntegrations.googleCredentialsDeleteConfirm'))) return
  googleCredentialDeleting.value = true
  googleCredentialError.value = ''
  googleCredentialMessage.value = ''
  try {
    google.value = await api<GoogleHealthStatus>('/google-health/credentials', { method: 'DELETE' })
    googleClientId.value = ''
    googleClientSecret.value = ''
    googleSecretVisible.value = false
    googleConnectionTestResult.value = null
    googleCredentialMessage.value = t('accountIntegrations.googleCredentialsDeleted')
  } catch {
    googleCredentialError.value = t('accountIntegrations.googleCredentialsDeleteFailed')
  } finally {
    googleCredentialDeleting.value = false
  }
}

async function disconnectGoogle(): Promise<void> {
  if (googleDisconnecting.value || !googleCanDisconnect.value) return
  googleDisconnecting.value = true
  googleCredentialError.value = ''
  googleCredentialMessage.value = ''
  try {
    google.value = await api<GoogleHealthStatus>('/google-health/connection', { method: 'DELETE' })
    googleConnectionTestResult.value = null
    googleCredentialMessage.value = t('accountIntegrations.googleDisconnected')
  } catch {
    googleCredentialError.value = t('accountIntegrations.googleDisconnectFailed')
  } finally {
    googleDisconnecting.value = false
  }
}

async function testGoogleConnection(): Promise<void> {
  if (googleConnectionTestBusy.value || !googleCanTest.value) return
  googleConnectionTestBusy.value = true
  googleConnectionTestResult.value = null
  googleConnectionTestError.value = ''
  try {
    const result = await api<{ status: GoogleHealthConnectionTestStatus }>('/google-health/connection/test', { method: 'POST' })
    googleConnectionTestResult.value = result.status
    if (google.value && result.status === 'reauth_required') {
      google.value = { ...google.value, state: 'reauth_required' }
    } else if (google.value && result.status === 'connected') {
      google.value = { ...google.value, state: 'active' }
    }
  } catch {
    googleConnectionTestResult.value = 'failed'
    googleConnectionTestError.value = t('accountIntegrations.googleConnectionTestFailed')
  } finally {
    googleConnectionTestBusy.value = false
  }
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
  googleSyncResult.value = null
  googleSyncError.value = ''
  googleSyncWarning.value = ''
  initialSetupSaved.value = false
  googleClientId.value = ''
  googleClientSecret.value = ''
  googleSecretVisible.value = false
  googleCredentialError.value = ''
  googleCredentialMessage.value = ''
  googleConnectionTestError.value = ''
  googleConnectionTestResult.value = null
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

async function syncGoogle(): Promise<void> {
  if (googleActionBusy.value || !googleCanSync.value) return
  googleActionBusy.value = true
  googleSyncResult.value = null
  googleSyncError.value = ''
  googleSyncWarning.value = ''
  try {
    const result = await api<GoogleHealthSyncResult>('/google-health/sync', { method: 'POST' })
    googleSyncResult.value = result
    try {
      google.value = await api<GoogleHealthStatus>('/google-health/status')
    } catch {
      googleSyncWarning.value = t('accountIntegrations.googleSyncRefreshFailed')
    }
  } catch (cause) {
    googleSyncError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountIntegrations.googleSyncFailed', { preserveDetail: false })
      : t('accountIntegrations.googleSyncFailed')
  } finally {
    googleActionBusy.value = false
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
        <div class="integration-card-header yazio-card-title">
          <span class="integration-card-icon yazio-icon" aria-hidden="true"></span>
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
            <div v-if="yazio?.scheduler_enabled !== false && yazio?.sync_enabled" class="yazio-next-sync-row"><dt>{{ t('accountIntegrations.nextSync') }}</dt><dd>{{ timestampLabel(yazio?.next_sync_at) }}</dd></div>
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

      <section class="card form-card integration-card google-health-card" :aria-busy="googleActionBusy || googleCredentialSaving || googleConnectionTestBusy || googleCredentialDeleting || googleDisconnecting" aria-labelledby="google-health-title">
        <div class="integration-card-header">
          <PhGoogleLogo class="integration-card-icon" :size="20" weight="duotone" aria-hidden="true" />
          <h2 id="google-health-title">{{ t('accountIntegrations.googleTitle') }}</h2>
          <span class="integration-status-badge google-health-status-badge" :class="`integration-status-badge--${googleBadgeVariant}`" role="status">{{ googleBadgeLabel }}</span>
        </div>
        <p>{{ t('accountIntegrations.googleDescription') }}</p>
        <div v-if="googleError" class="import-message error" role="alert">
          <p>{{ googleError }}</p>
          <button v-if="!google" class="button compact-action" type="button" @click="load">{{ t('common.tryAgain') }}</button>
        </div>
        <p><strong>{{ t('accountIntegrations.googleStatus') }}:</strong> {{ googleStatusLabel }}</p>
        <p v-if="googleNeedsReauth || googleConnectionTestResult === 'reauth_required'" class="setup-notice google-reauth-message" role="status">
          {{ t('accountIntegrations.googleReauthMessage') }}
        </p>
        <p v-if="googleMessage" class="setup-notice" role="status">{{ googleMessage }}</p>
        <form class="integration-panel google-credentials-panel" @submit.prevent="saveGoogleCredentials">
          <h3>{{ t('accountIntegrations.googleCredentialsTitle') }}</h3>
          <p class="table-secondary">{{ t('accountIntegrations.googleCredentialsDescription') }}</p>
          <div class="form-grid google-credentials-grid">
            <label class="field">
              <span>{{ t('accountIntegrations.googleClientId') }}</span>
              <input
                v-model="googleClientId"
                name="google-client-id"
                type="text"
                autocomplete="off"
                :disabled="!googleAvailable"
                :placeholder="google?.client_id_configured ? t('accountIntegrations.googleStoredPlaceholder') : t('accountIntegrations.googleClientIdPlaceholder')"
              />
            </label>
            <label class="field">
              <span>{{ t('accountIntegrations.googleClientSecret') }}</span>
              <div class="secret-input-row">
                <input
                  v-model="googleClientSecret"
                  name="google-client-secret"
                  :type="googleSecretVisible ? 'text' : 'password'"
                  autocomplete="new-password"
                  :disabled="!googleAvailable"
                  :placeholder="google?.client_secret_configured ? t('accountIntegrations.googleStoredPlaceholder') : t('accountIntegrations.googleClientSecretPlaceholder')"
                />
                <button
                  v-if="googleClientSecret"
                  class="button secondary compact-action"
                  type="button"
                  :aria-label="googleSecretVisible ? t('accountIntegrations.googleHideSecret') : t('accountIntegrations.googleShowSecret')"
                  @click="googleSecretVisible = !googleSecretVisible"
                >
                  {{ googleSecretVisible ? t('accountIntegrations.googleHideSecret') : t('accountIntegrations.googleShowSecret') }}
                </button>
              </div>
            </label>
          </div>
          <p class="table-secondary">{{ t('accountIntegrations.googleCredentialPairHelp') }}</p>
          <p v-if="googleCredentialError" class="import-message error" role="alert">{{ googleCredentialError }}</p>
          <p v-if="googleCredentialMessage" class="setup-notice" role="status">{{ googleCredentialMessage }}</p>
          <button class="button compact-action google-credentials-save" type="submit" :disabled="googleCredentialSaving || !googleAvailable || !googleCredentialsComplete">
            {{ googleCredentialSaving ? t('accountIntegrations.googleCredentialsSaving') : google?.configured ? t('accountIntegrations.googleCredentialsReplace') : t('accountIntegrations.googleCredentialsSave') }}
          </button>
          <p v-if="google?.redirect_uri" class="table-secondary google-redirect-uri">
            {{ t('accountIntegrations.googleRedirectUriHelp') }} <code>{{ google.redirect_uri }}</code>
          </p>
        </form>
        <div v-if="google?.configured" class="integration-panel google-connection-actions">
          <h3>{{ t('accountIntegrations.googleConnectionTitle') }}</h3>
          <div class="integration-action-row">
            <button class="button secondary compact-action google-health-connection-test" type="button" :disabled="googleConnectionTestBusy || !googleCanTest" @click="testGoogleConnection">
              {{ googleConnectionTestBusy ? t('accountIntegrations.googleConnectionTestRunning') : t('accountIntegrations.googleConnectionTest') }}
            </button>
            <button v-if="googleCanDisconnect" class="button secondary compact-action google-health-disconnect" type="button" :disabled="googleDisconnecting" @click="disconnectGoogle">
              {{ googleDisconnecting ? t('accountIntegrations.googleDisconnecting') : t('accountIntegrations.googleDisconnect') }}
            </button>
            <button v-if="googleAvailable && (google?.client_id_configured || google?.client_secret_configured)" class="button secondary compact-action google-health-delete-credentials" type="button" :disabled="googleCredentialDeleting" @click="deleteGoogleCredentials">
              {{ googleCredentialDeleting ? t('accountIntegrations.googleCredentialsDeleting') : t('accountIntegrations.googleCredentialsDelete') }}
            </button>
          </div>
          <p v-if="googleConnectionTestResult === 'connected'" class="setup-notice" role="status">{{ t('accountIntegrations.googleConnectionTestSuccess') }}</p>
          <p v-else-if="googleConnectionTestResult === 'reauth_required'" class="setup-notice google-reauth-message" role="status">{{ t('accountIntegrations.googleReauthMessage') }}</p>
          <p v-else-if="googleConnectionTestResult === 'failed'" class="import-message error" role="alert">{{ googleConnectionTestError || t('accountIntegrations.googleConnectionTestFailed') }}</p>
        </div>
        <div v-if="google" class="integration-panel google-status-panel">
          <h3>{{ t('accountIntegrations.googleStatusTitle') }}</h3>
          <dl class="integration-details">
            <div><dt>{{ t('accountIntegrations.googleSyncState') }}</dt><dd>{{ googleSyncStateLabel }}</dd></div>
            <div v-if="google.retry_attempt > 0 && google.next_retry_at"><dt>{{ t('accountIntegrations.googleRetryAttemptLabel') }}</dt><dd>{{ googleRetryLabel() }}</dd></div>
            <div><dt>{{ t('accountIntegrations.lastAttempt') }}</dt><dd>{{ timestampLabel(google.last_attempt_at) }}</dd></div>
            <div><dt>{{ t('accountIntegrations.lastSuccess') }}</dt><dd>{{ timestampLabel(google.last_success_at) }}</dd></div>
          </dl>
        </div>
        <button
          v-if="googleCanConnect"
          class="button compact-action google-health-connect"
          type="button"
          :disabled="googleActionBusy"
          :aria-label="googleNeedsReauth ? t('accountIntegrations.googleReauthorize') : undefined"
          @click="connectGoogle"
        >
          {{ googleActionBusy
            ? t('accountIntegrations.googleConnecting')
            : google?.state === 'not_connected'
              ? t('accountIntegrations.googleConnect')
              : googleNeedsReauth
                ? t('accountIntegrations.googleReauthorize')
                : t('accountIntegrations.googleReconnect') }}
        </button>
        <div v-if="googleCanSync" class="google-sync-panel">
          <h3>{{ t('accountIntegrations.googleSyncTitle') }}</h3>
          <p>{{ t('accountIntegrations.googleSyncDescription') }}</p>
          <button
            class="button secondary compact-action google-health-sync-button"
            type="button"
            :disabled="googleActionBusy"
            @click="syncGoogle"
          >
            {{ googleActionBusy ? t('accountIntegrations.googleSyncRunning') : t('accountIntegrations.googleSyncAction') }}
          </button>
        </div>
        <section v-if="googleSyncResult" class="setup-notice google-health-sync-result" role="status">
          <p><strong>{{ googleSyncStatusLabel(googleSyncResult.status) }}</strong></p>
          <ul class="google-sync-domain-results" :aria-label="t('accountIntegrations.googleSyncDomainsLabel')">
            <li v-for="domain in googleSyncDomains" :key="domain.key" :data-domain="domain.key">
              <strong>{{ t(domain.labelKey) }}</strong>:
              {{ googleDomainStatusLabel(googleSyncResult[domain.key].status) }} ·
              {{ t('accountIntegrations.googleSyncCounts', {
                fetched: googleSyncResult[domain.key].fetched_count,
                persisted: googleSyncResult[domain.key].persisted_count,
              }) }}
              <span v-if="googleSyncResult[domain.key].error_code">
                · {{ googleDomainErrorLabel(googleSyncResult[domain.key].error_code) }}
              </span>
            </li>
          </ul>
        </section>
        <p v-if="googleSyncWarning" class="import-message warning" role="status">{{ googleSyncWarning }}</p>
        <p v-if="googleSyncError" class="import-message error google-health-sync-error" role="alert">{{ googleSyncError }}</p>
      </section>

      <section class="card form-card integration-card apple-health-card" aria-labelledby="apple-health-title">
        <div class="integration-card-header">
          <PhAppleLogo class="integration-card-icon" :size="20" weight="duotone" aria-hidden="true" />
          <h2 id="apple-health-title">{{ t('accountIntegrations.appleTitle') }}</h2>
        </div>
        <p>{{ t('accountIntegrations.appleDescription') }}</p>
        <p class="table-secondary">{{ t('accountIntegrations.appleExport') }}</p>
        <RouterLink class="button secondary compact-action" :to="{ name: 'account-imports' }">
          {{ t('accountIntegrations.openImports') }}
        </RouterLink>
      </section>
    </template>
  </template>
</template>
