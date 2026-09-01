<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'

import { api, ApiError, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import { formatGermanDateTime, isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import type { YazioStatus } from '../types'
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const yazio = ref<YazioStatus | null>(null)
const yazioEmail = ref('')
const yazioPassword = ref('')
const yazioHistoryFrom = ref('')
const yazioHistoryTo = ref('')
const savingYazio = ref(false)
const yazioMessage = ref('')
const yazioError = ref('')
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
const yazioAvailable = computed(() => yazio.value?.available !== false)
const yazioStatusLabel = computed(() => {
  if (!yazioAvailable.value) return t('settings.serverDisabled')
  if (!yazio.value?.configured) return t('settings.notConfigured')
  const historicalState = yazio.value.historical_sync?.state
  if (historicalState === 'pending') return t('settings.firstImportWaiting')
  if (historicalState === 'running') return t('settings.firstImportRunning')
  if (historicalState === 'failed') return t('settings.firstImportFailed')
  if (!yazio.value.sync_enabled) return t('settings.paused')
  return t('settings.active', { hours: (yazio.value.sync_interval_minutes ?? 360) / 60, days: yazio.value.sync_days ?? 7 })
})
const yazioHistoricalSyncActive = computed(() => {
  const state = yazio.value?.historical_sync?.state
  return yazioAvailable.value && (state === 'pending' || state === 'running')
})
const yazioHistoricalSyncFailed = computed(
  () => yazio.value?.historical_sync?.state === 'failed',
)

async function load(): Promise<void> {
  const generation = ++loadGeneration
  stopYazioPolling()
  loading.value = true
  loaded.value = false
  error.value = ''
  initialSetupSaved.value = false
  try {
    const result = await api<YazioStatus>('/yazio/status')
    if (generation !== loadGeneration) return
    yazio.value = result
    yazioMessage.value = ''
    yazioError.value = ''
    if (!result.configured) yazioHistoryTo.value = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
    loaded.value = true
    scheduleYazioPolling()
  } catch (cause) {
    if (generation !== loadGeneration) return
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'settingsUi.loadFailed')
      : t('settingsUi.loadFailed')
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
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

    <section v-if="loaded" class="card form-card yazio-connection-card" :aria-busy="savingYazio">
      <div class="yazio-card-title">
        <span class="yazio-icon" aria-hidden="true"></span>
        <h2>{{ t('settingsUi.yazioTitle') }}</h2>
      </div>
      <p>{{ t('settingsUi.yazioDescription') }}</p>
      <div v-if="yazioError" class="card error" role="alert">{{ yazioError }}</div>
      <p v-if="yazioMessage" class="setup-notice" role="status">{{ yazioMessage }}</p>
      <div v-if="initialSetupSaved" class="setup-notice" role="status">
        <p>{{ t('settingsUi.initialImport') }}</p>
        <RouterLink class="text-button" :to="{ name: 'account-imports' }">{{ t('settingsUi.toImports') }}</RouterLink>
      </div>
      <p><strong>{{ t('settingsUi.statusLabel') }}</strong> {{ yazioStatusLabel }}</p>
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
    </section>
  </template>
</template>
