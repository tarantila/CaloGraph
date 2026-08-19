<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { PhTrash } from '@phosphor-icons/vue'
import { useRouter } from 'vue-router'

import { api, ApiError, localizeApiError } from '../api'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DateInput from '../components/DateInput.vue'
import UserManagement from '../components/UserManagement.vue'
import {
  formatGermanDate,
  formatGermanDateTime,
  formatGermanInstantDate,
  isoDateInTimeZone,
} from '../date-format'
import { useAuthStore } from '../stores/auth'
import { createNumberFormatter, i18n } from '../i18n'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'
import type { ActivitySourceType, Target, User, YazioStatus } from '../types'
import {
  createPasskey,
  isPasskeySupported,
  type WebAuthnOptionsResponse,
} from '../webauthn'

interface Token { id: string; label: string; token_prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
interface Invitation { id: string; created_at: string; expires_at: string; used_at: string | null; revoked_at: string | null }
interface MfaStatus { totp_enabled: boolean; totp_setup_pending: boolean; recovery_codes_remaining: number }
interface TotpSetup { secret: string; provisioning_uri: string; qr_svg_data_url: string }
interface Passkey {
  id: string
  label: string
  device_type: string
  backed_up: boolean
  created_at: string
  last_used_at: string | null
}
const fallbackTimezones = [
  'UTC',
  'Europe/Berlin',
  'Europe/Vienna',
  'Europe/Zurich',
  'Europe/Amsterdam',
  'Europe/Paris',
  'Europe/London',
  'Europe/Madrid',
  'Europe/Rome',
  'Europe/Warsaw',
  'Europe/Athens',
  'Europe/Helsinki',
  'Europe/Bucharest',
  'Europe/Kyiv',
  'Europe/Istanbul',
  'Africa/Cairo',
  'Africa/Johannesburg',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Bangkok',
  'Asia/Singapore',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Pacific/Auckland',
  'America/St_Johns',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'America/Sao_Paulo',
]

function supportedTimezones() {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return fallbackTimezones
  }
}
const props = defineProps<{ section: 'targets' | 'account' }>()
const router = useRouter()
const profile = reactive({ language: 'de' as 'de' | 'en', timezone: 'Europe/Berlin', week_starts_on: 0, raw_payload_retention_days: 0 })
const t = i18n.global.t.bind(i18n.global)
const target = reactive(createEmptyTargetDraft())
const targets = ref<Target[]>([])
const activitySources = ref<ActivitySourceType[]>([])
const targetToDelete = ref<Target | null>(null)
const deletingTarget = ref(false)
const targetDeleteError = ref('')
const tokens = ref<Token[]>([])
const tokenLabel = ref('iPhone')
const newToken = ref('')
const message = ref('')
const auth = useAuthStore()
watch(
  () => auth.user?.language,
  (language) => {
    if (language === 'de' || language === 'en') profile.language = language
  },
)
const yazio = ref<YazioStatus | null>(null)
const yazioEmail = ref('')
const yazioPassword = ref('')
const yazioHistoryFrom = ref('')
const yazioHistoryTo = ref('')
const savingYazio = ref(false)
const yazioMessage = ref('')
const yazioError = ref('')
const yazioTransientImportCompleted = ref(false)
const passwordCurrent = ref('')
const passwordNew = ref('')
const passwordConfirmation = ref('')
const passwordChangeError = ref('')
const changingPassword = ref(false)
const users = ref<User[]>([])
const invitations = ref<Invitation[]>([])
const invitationUrl = ref('')
const initialSetupSaved = ref(false)
const error = ref('')
const loading = ref(true)
const savingTarget = ref(false)
const mfa = ref<MfaStatus | null>(null)
const totpSetup = ref<TotpSetup | null>(null)
const mfaCurrentPassword = ref('')
const mfaCode = ref('')
const recoveryCodes = ref<string[]>([])
const managingMfa = ref(false)
const passkeys = ref<Passkey[]>([])
const passkeySupported = isPasskeySupported()
const passkeyLabel = ref('')
const passkeyPassword = ref('')
const passkeyCode = ref('')
const managingPasskey = ref(false)
let loadGeneration = 0
let yazioPollTimer: ReturnType<typeof setTimeout> | null = null
let yazioPollInFlight = false
let yazioPollGeneration = 0
let settingsMounted = true
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const timezoneOptions = computed(() =>
  [...new Set(['UTC', profile.timezone, ...supportedTimezones()])].sort((left, right) =>
    left.localeCompare(right, 'de'),
  ),
)
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
const passwordConfirmationMatches = computed(() => passwordNew.value === passwordConfirmation.value)
const targetDeleteDescription = computed(() => {
  const item = targetToDelete.value
  if (!item) return ''
  const status = item.valid_to == null ? t('settingsUi.current') : t('settingsUi.historical')
  return t('settingsUi.targetDeleteDescription', {
    date: formatGermanDate(item.valid_from),
    status,
  })
})
const yazioHistoricalSyncFailed = computed(
  () => yazio.value?.historical_sync?.state === 'failed',
)
const selectableActivitySources = computed(() => {
  const sourceTypes = new Set(activitySources.value)
  if (target.activity_source_type) sourceTypes.add(target.activity_source_type)
  return [...sourceTypes].sort()
})
const activityEnabled = computed({
  get: () => target.activity_mode === 'full',
  set: (enabled: boolean) => {
    target.activity_mode = enabled ? 'full' : 'off'
    if (!enabled) target.activity_source_type = null
  },
})

function activityHistoryLabel(item: Target) {
  return item.activity_mode === 'full'
    ? t('activity.historyEnabled', { source: activitySourceLabel(item.activity_source_type) })
    : t('activity.historyDisabled')
}


function activitySourceLabel(sourceType: ActivitySourceType | null) {
  if (!sourceType) return '–'
  return t(`activity.source.${sourceType}`)
}

async function loadTargets() {
  const [targetResult, sourceResult] = await Promise.all([
    api<Target[]>('/settings/targets'),
    api<Array<{ source_type: ActivitySourceType }>>('/settings/activity-sources'),
  ])
  targets.value = targetResult
  activitySources.value = Array.isArray(sourceResult)
    ? sourceResult.map((item) => item.source_type)
    : []
  const currentTarget = targetResult.find((item) => item.valid_to == null) ?? targetResult[0]
  if (currentTarget) {
    target.calories_kcal = Number(currentTarget.calories_kcal)
    target.maintenance_kcal = currentTarget.maintenance_kcal == null ? null : Number(currentTarget.maintenance_kcal)
    target.activity_mode = currentTarget.activity_mode ?? 'off'
    target.activity_source_type = currentTarget.activity_source_type ?? null
    target.protein_g = Number(currentTarget.protein_g)
    target.carbs_g = currentTarget.carbs_g == null ? null : Number(currentTarget.carbs_g)
    target.fat_g = currentTarget.fat_g == null ? null : Number(currentTarget.fat_g)
    target.fiber_g = currentTarget.fiber_g == null ? null : Number(currentTarget.fiber_g)
  }
  target.valid_from = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
}

async function loadAdmin(generation = loadGeneration) {
  try {
    const [usersResult, invitationsResult] = await Promise.all([
      api<User[]>('/users'),
      api<Invitation[]>('/users/invitations'),
    ])
    if (generation !== loadGeneration) return
    users.value = usersResult
    invitations.value = invitationsResult
  } catch (cause) {
    if (generation !== loadGeneration) return
    error.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.adminLoadFailed')
        : t('settingsUi.adminLoadFailed')
  }
}

async function refreshAdmin() {
  await loadAdmin(loadGeneration)
}

async function loadAccount(generation = loadGeneration) {
  const profileGeneration = auth.currentProfileUpdateGeneration()
  const [user, tokensResult, yazioResult, mfaResult, passkeyResult] = await Promise.all([
    api<User>('/settings/profile'),
    api<Token[]>('/settings/tokens'),
    api<YazioStatus>('/yazio/status'),
    api<MfaStatus>('/settings/mfa'),
    api<Passkey[]>('/settings/passkeys'),
  ])
  if (generation !== loadGeneration || !auth.isCurrentProfileUpdate(profileGeneration)) return
  const currentLanguage = auth.user?.id === user.id ? auth.user.language : user.language
  tokens.value = tokensResult
  profile.language = currentLanguage === 'en' ? 'en' : 'de'
  profile.timezone = user.timezone
  profile.week_starts_on = user.week_starts_on
  profile.raw_payload_retention_days = user.raw_payload_retention_days
  auth.syncLoadedUser(profileGeneration, { ...user, language: currentLanguage })
  yazio.value = yazioResult
  yazioTransientImportCompleted.value = false
  yazioMessage.value = ''
  yazioError.value = ''
  if (!yazioResult.configured) yazioHistoryTo.value = isoDateInTimeZone(user.timezone)
  mfa.value = mfaResult
  passkeys.value = passkeyResult
  scheduleYazioPolling()
  if (user.is_admin) await loadAdmin(generation)
}

function stopYazioPolling() {
  if (yazioPollTimer) {
    clearTimeout(yazioPollTimer)
    yazioPollTimer = null
  }
  yazioPollGeneration += 1
}

function scheduleYazioPolling() {
  if (
    !settingsMounted
    || props.section !== 'account'
    || !yazioHistoricalSyncActive.value
    || yazioPollTimer
  ) return
  yazioPollTimer = setTimeout(() => {
    yazioPollTimer = null
    void pollYazioStatus()
  }, 5000)
}

async function pollYazioStatus() {
  if (
    !settingsMounted
    || props.section !== 'account'
    || !yazioHistoricalSyncActive.value
    || yazioPollInFlight
  ) return
  yazioPollInFlight = true
  const generation = yazioPollGeneration
  try {
    const previousState = yazio.value?.historical_sync?.state
    const result = await api<YazioStatus>('/yazio/status')
    if (generation !== yazioPollGeneration || props.section !== 'account') return
    if (
      (previousState === 'pending' || previousState === 'running')
      && result.historical_sync?.state === 'completed'
    ) {
      yazioTransientImportCompleted.value = true
    }
    if (result.historical_sync?.state !== 'completed') {
      yazioTransientImportCompleted.value = false
    }
    yazio.value = result
  } catch {
    // The next scheduled status request retries without replacing the page state.
  } finally {
    yazioPollInFlight = false
    scheduleYazioPolling()
  }
}

async function load() {
  const generation = ++loadGeneration
  stopYazioPolling()
  loading.value = true
  error.value = ''
  message.value = ''
  initialSetupSaved.value = false
  try {
    if (props.section === 'targets') await loadTargets()
    else await loadAccount(generation)
  } catch (cause) {
    if (generation !== loadGeneration) return
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.loadFailed') : t('settingsUi.loadFailed')
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}
watch(() => props.section, () => { void load() }, { immediate: true })
onBeforeUnmount(() => {
  settingsMounted = false
  ++loadGeneration
  auth.beginProfileUpdate()
  stopYazioPolling()
})

async function saveProfile() {
  const generation = auth.beginProfileUpdate()
  const user = await auth.enqueueProfileUpdate(generation, () =>
    api<User>('/settings/profile', { method: 'PUT', body: JSON.stringify(profile) }),
  )
  if (!user || !auth.commitProfileUpdate(generation, user)) return
  message.value = t('settings.saved')
}
async function changePassword() {
  passwordChangeError.value = ''
  if (!passwordConfirmationMatches.value) {
    passwordChangeError.value = t('auth.passwordMismatch')
    return
  }
  changingPassword.value = true
  try {
    await api<void>('/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: passwordCurrent.value,
        new_password: passwordNew.value,
      }),
    })
    auth.clearSession()
    await router.replace({ name: 'login', query: { passwordChanged: '1' } })
  } catch (cause) {
    passwordChangeError.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.passwordChangeFailed')
        : t('settingsUi.passwordChangeFailed')
  } finally {
    passwordCurrent.value = ''
    passwordNew.value = ''
    passwordConfirmation.value = ''
    changingPassword.value = false
  }
}

function openTargetDelete(item: Target) {
  if (targets.value.length <= 1) return
  targetDeleteError.value = ''
  targetToDelete.value = item
}

function closeTargetDelete() {
  if (!deletingTarget.value) {
    targetToDelete.value = null
    targetDeleteError.value = ''
  }
}

async function confirmTargetDelete() {
  const item = targetToDelete.value
  if (!item) return
  deletingTarget.value = true
  targetDeleteError.value = ''
  try {
    await api<void>(`/settings/targets/${item.valid_from}`, { method: 'DELETE' })
    await loadTargets()
    targetToDelete.value = null
    message.value = t('settingsUi.targetDeleted')
  } catch (cause) {
    targetDeleteError.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.targetDeleteFailed')
        : t('settingsUi.targetDeleteFailed')
  } finally {
    deletingTarget.value = false
  }
}

function targetDeleteLabel(item: Target) {
  return targets.value.length <= 1
    ? t('settingsUi.targetDeleteUnavailable', { date: formatGermanDate(item.valid_from) })
    : t('settingsUi.deleteTarget', { date: formatGermanDate(item.valid_from) })
}

async function saveTarget() {
  error.value = ''
  message.value = ''
  savingTarget.value = true
  try {
    await saveTargetDraft(target, targets.value)
    message.value = t('settingsUi.targetSaved', { date: formatGermanDate(target.valid_from) })
    auth.completeTargetSetup()
    await loadTargets()
  } catch (cause) {
    error.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.targetSaveFailed')
        : cause instanceof TargetValidationError
          ? cause.message
          : t('settingsUi.targetSaveFailed')
  } finally {
    savingTarget.value = false
  }
}
async function createToken() { const result = await api<{ token: string }>('/settings/tokens', { method: 'POST', body: JSON.stringify({ label: tokenLabel.value }) }); newToken.value = result.token; await load() }
async function revokeToken(id: string) { await api(`/settings/tokens/${id}`, { method: 'DELETE' }); await load() }
async function saveYazio() {
  error.value = ''
  message.value = ''
  if (!yazioCredentialsComplete.value) return
  const isNewConnection = !yazio.value?.configured
  if (isNewConnection && yazioHistoryFrom.value > yazioHistoryTo.value) {
    yazioError.value = t('settingsUi.invalidRange')
    return
  }
  savingYazio.value = true
  yazioError.value = ''
  yazioMessage.value = ''
  yazioTransientImportCompleted.value = false
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
    yazioError.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'settingsUi.yazioSaveFailed', { preserveDetail: false })
        : t('settingsUi.yazioSaveFailed')
  } finally {
    savingYazio.value = false
  }
}
async function createInvitation() {
  const result = await api<{ token: string; invitation_url: string }>('/users/invitations', {
    method: 'POST',
    body: JSON.stringify({ expires_in_days: 7 }),
  })
  invitationUrl.value = result.invitation_url
  await load()
}
async function copyInvitation() {
  await navigator.clipboard.writeText(invitationUrl.value)
  message.value = t('settingsUi.invitationCopied')
}
async function revokeInvitation(id: string) {
  await api(`/users/invitations/${id}`, { method: 'DELETE' })
  await load()
}

async function beginTotpSetup() {
  managingMfa.value = true
  error.value = ''
  message.value = ''
  try {
    totpSetup.value = await api<TotpSetup>('/settings/mfa/totp/setup', {
      method: 'POST',
      body: JSON.stringify({ current_password: mfaCurrentPassword.value }),
    })
    mfaCurrentPassword.value = ''
    recoveryCodes.value = []
    message.value = t('settingsUi.mfaScan')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaSetupFailed') : t('settingsUi.mfaSetupFailed')
  } finally {
    managingMfa.value = false
  }
}

async function confirmTotpSetup() {
  managingMfa.value = true
  error.value = ''
  try {
    const result = await api<{ recovery_codes: string[] }>('/settings/mfa/totp/confirm', {
      method: 'POST',
      body: JSON.stringify({ code: mfaCode.value }),
    })
    recoveryCodes.value = result.recovery_codes
    mfaCode.value = ''
    totpSetup.value = null
    await loadAccount()
    message.value = t('settingsUi.mfaEnabled')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaConfirmFailed') : t('settingsUi.mfaConfirmFailed')
  } finally {
    managingMfa.value = false
  }
}

async function regenerateRecoveryCodes() {
  managingMfa.value = true
  error.value = ''
  try {
    const result = await api<{ recovery_codes: string[] }>(
      '/settings/mfa/totp/recovery-codes',
      {
        method: 'POST',
        body: JSON.stringify({
          current_password: mfaCurrentPassword.value,
          code: mfaCode.value,
        }),
      },
    )
    recoveryCodes.value = result.recovery_codes
    mfaCurrentPassword.value = ''
    mfaCode.value = ''
    await loadAccount()
    message.value = t('settingsUi.codesRegenerated')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.codesRegenerateFailed') : t('settingsUi.codesRegenerateFailed')
  } finally {
    managingMfa.value = false
  }
}

async function disableTotp() {
  managingMfa.value = true
  error.value = ''
  try {
    await api('/settings/mfa/totp', {
      method: 'DELETE',
      body: JSON.stringify({
        current_password: mfaCurrentPassword.value,
        code: mfaCode.value,
      }),
    })
    recoveryCodes.value = []
    mfaCurrentPassword.value = ''
    mfaCode.value = ''
    await loadAccount()
    message.value = t('settingsUi.mfaDisabled')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaDisableFailed') : t('settingsUi.mfaDisableFailed')
  } finally {
    managingMfa.value = false
  }
}

async function copyRecoveryCodes() {
  await navigator.clipboard.writeText(recoveryCodes.value.join('\n'))
  message.value = t('settingsUi.codesCopied')
}

async function registerPasskey() {
  managingPasskey.value = true
  error.value = ''
  message.value = ''
  try {
    const options = await api<WebAuthnOptionsResponse>('/settings/passkeys/options', {
      method: 'POST',
      body: JSON.stringify({
        current_password: passkeyPassword.value,
        code: passkeyCode.value || null,
      }),
    })
    const credential = await createPasskey(options.public_key)
    await api<Passkey>('/settings/passkeys', {
      method: 'POST',
      body: JSON.stringify({
        challenge_id: options.challenge_id,
        label: passkeyLabel.value,
        credential,
      }),
    })
    passkeyLabel.value = ''
    passkeyPassword.value = ''
    passkeyCode.value = ''
    await loadAccount()
    message.value = t('settingsUi.passkeyCreated')
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
      error.value = t('settingsUi.passkeyCancelled')
    } else {
      error.value =
        cause instanceof ApiError
          ? localizeApiError(cause, 'settingsUi.passkeyCreateFailed', { preserveDetail: true })
          : t('settingsUi.passkeyCreateFailed')
    }
  } finally {
    managingPasskey.value = false
  }
}

async function removePasskey(id: string) {
  managingPasskey.value = true
  error.value = ''
  message.value = ''
  try {
    await api(`/settings/passkeys/${id}`, {
      method: 'DELETE',
      body: JSON.stringify({
        current_password: passkeyPassword.value,
        code: passkeyCode.value || null,
      }),
    })
    passkeyPassword.value = ''
    passkeyCode.value = ''
    await loadAccount()
    message.value = t('settingsUi.passkeyRemoved')
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.passkeyRemoveFailed', { preserveDetail: true }) : t('settingsUi.passkeyRemoveFailed')
  } finally {
    managingPasskey.value = false
  }
}

function passkeyDeviceLabel(passkey: Passkey) {
  if (passkey.backed_up || passkey.device_type === 'multi_device') {
    return t('settingsUi.passkeySynced')
  }
  return t('settingsUi.passkeyThisDevice')
}
</script>
<template>
  <div class="page-heading">
    <div v-if="props.section === 'targets'">
      <h1>{{ t('settings.targets') }}</h1>
      <p>{{ t('settings.targetsDescription') }}</p>
    </div>
    <div v-else>
      <h1>{{ t('settings.account') }}</h1>
      <p>{{ t('settings.accountDescription') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading">{{ t('settingsUi.loading') }}</div>
  <template v-else>
    <div v-if="error" class="card error" role="alert">{{ error }}</div>
    <p v-if="message" role="status">{{ message }}</p>
    <template v-if="props.section === 'targets'">
      <div class="content-grid">
        <section class="card form-card">
          <h2>{{ t('settingsUi.targetsTitle') }}</h2>
          <p v-if="!targets.length" class="setup-notice">
            {{ t('settingsUi.initialTargetsDescription') }}
          </p>
          <form class="form-grid" @submit.prevent="saveTarget">
            <label class="field">{{ t('settingsUi.validFrom') }}<DateInput v-model="target.valid_from" required /></label>
            <label class="field">{{ t('settingsUi.calorieBudget') }}<input v-model.number="target.calories_kcal" type="number" :min="TARGET_LIMITS.caloriesMin" step="1" required /></label>
            <label class="field">{{ t('settingsUi.maintenance') }}<input v-model.number="target.maintenance_kcal" type="number" :min="TARGET_LIMITS.maintenanceMin" step="0.001" /><small>{{ t('settingsUi.maintenanceHelp') }}</small></label>
            <fieldset class="field full activity-target-settings">
              <legend class="activity-card-header">
                <span>{{ t('activity.title') }}</span>
                <span
                  :class="['activity-status-badge', { active: activityEnabled }]"
                >{{ activityEnabled ? t('activity.statusActive') : t('activity.statusDisabled') }}</span>
              </legend>
              <p class="activity-description">{{ t('activity.description') }}</p>
              <label class="activity-toggle-row">
                <span>{{ t('activity.enabled') }}</span>
                <input
                  v-model="activityEnabled"
                  type="checkbox"
                  role="switch"
                  :disabled="!selectableActivitySources.length"
                />
              </label>
              <div v-if="activityEnabled" class="activity-source-settings">
                <label class="field">
                  {{ t('activity.sourceLabel') }}
                  <select v-model="target.activity_source_type" name="activity-source" required>
                    <option :value="null" disabled>{{ t('activity.sourcePlaceholder') }}</option>
                    <option v-for="sourceType in selectableActivitySources" :key="sourceType" :value="sourceType">
                      {{ activitySourceLabel(sourceType) }}
                    </option>
                  </select>
                </label>
              </div>
              <small v-if="!selectableActivitySources.length" class="activity-source-unavailable">
                {{ t('activity.noSources') }}
              </small>
            </fieldset>
            <label class="field">{{ t('settingsUi.proteinTarget') }}<input v-model.number="target.protein_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" required /></label>
            <label class="field">{{ t('settingsUi.carbsTarget') }}<input v-model.number="target.carbs_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <label class="field">{{ t('settingsUi.fatTarget') }}<input v-model.number="target.fat_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <label class="field">{{ t('settingsUi.fiberTarget') }}<input v-model.number="target.fiber_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <button class="button" type="submit" :disabled="savingTarget">
              {{ savingTarget ? t('settingsUi.saving') : t('settingsUi.saveTargets') }}
            </button>
          </form>
        </section>

        <section class="card form-card budget-help">
          <h2>{{ t('settingsUi.budgetHelpTitle') }}</h2>
          <p>{{ t('settingsUi.budgetHelpBudget') }}</p>
          <p>{{ t('settingsUi.budgetHelpMaintenance') }}</p>
          <p>{{ t('settingsUi.budgetHelpCalendar') }}</p>
          <p>{{ t('settingsUi.budgetHelpValidFrom') }}</p>
        </section>
      </div>

      <section class="card table-card">
        <div class="section-card-header">
          <div><h2>{{ t('settingsUi.historyTitle') }}</h2><p>{{ t('settingsUi.historyDescription') }}</p></div>
        </div>
        <div class="table-scroll">
          <table>
            <thead><tr><th>{{ t('settingsUi.validFrom') }}</th><th>{{ t('common.to') }}</th><th class="number">{{ t('settingsUi.calorieBudget') }}</th><th class="number">{{ t('settingsUi.maintenance') }}</th><th>{{ t('activity.title') }}</th><th class="number">{{ t('settingsUi.proteinTarget') }}</th><th class="actions">{{ t('common.actions') }}</th></tr></thead>
            <tbody>
              <tr v-for="item in targets" :key="item.id">
                <td>{{ formatGermanDate(item.valid_from) }}</td>
                <td>{{ item.valid_to ? formatGermanDate(item.valid_to) : t('settingsUi.current') }}</td>
                <td class="number">{{ integer.format(Number(item.calories_kcal)) }} {{ t('common.kcal') }}</td>
                <td class="number">{{ item.maintenance_kcal == null ? '–' : `${integer.format(Number(item.maintenance_kcal))} ${t('common.kcal')}` }}</td>
                <td>{{ activityHistoryLabel(item) }}</td>
                <td class="number">{{ integer.format(Number(item.protein_g)) }} {{ t('common.grams') }}</td>
                <td class="actions">
                  <button
                    class="icon-button danger"
                    type="button"
                    :aria-label="targetDeleteLabel(item)"
                    :title="targetDeleteLabel(item)"
                    :disabled="targets.length <= 1"
                    @click="openTargetDelete(item)"
                  >
                    <PhTrash :size="18" weight="duotone" aria-hidden="true" />
                  </button>
                </td>
              </tr>
              <tr v-if="!targets.length"><td colspan="7" class="empty">{{ t('settingsUi.noTargets') }}</td></tr>
            </tbody>
          </table>
        </div>
      </section>
      <ConfirmDialog
        v-if="targetToDelete !== null"
        :open="true"
        :title="t('settingsUi.targetDeleteTitle')"
        :description="targetDeleteDescription"
        :confirm-label="t('common.delete')"
        :danger="true"
        :pending="deletingTarget"
        :error="targetDeleteError"
        @confirm="confirmTargetDelete"
        @close="closeTargetDelete"
      />
    </template>

    <template v-else>
      <section class="card form-card">
        <h2>{{ t('settings.profile') }}</h2>
        <form class="form-grid" @submit.prevent="saveProfile">
          <label class="field">
            {{ t('settings.language') }}
            <select v-model="profile.language" name="language" required>
              <option value="de">{{ t('language.german') }}</option>
              <option value="en">{{ t('language.english') }}</option>
            </select>
          </label>
          <label class="field">
            {{ t('settings.timezone') }}
            <select v-model="profile.timezone" name="timezone" required>
              <option v-for="timezone in timezoneOptions" :key="timezone" :value="timezone">
                {{ timezone.replaceAll('_', ' ') }}
              </option>
            </select>
            <small>{{ t('settings.timezoneHelp') }}</small>
          </label>
          <label class="field">
            {{ t('settings.weekStart') }}
            <select v-model.number="profile.week_starts_on">
              <option :value="0">{{ t('settings.monday') }}</option>
              <option :value="6">{{ t('settings.sunday') }}</option>
            </select>
          </label>
          <label class="field">
            {{ t('settings.retention') }}
            <input v-model.number="profile.raw_payload_retention_days" type="number" min="0" max="3650" />
            <small>{{ t('settings.retentionHelp') }}</small>
          </label>
          <button class="button" type="submit">{{ t('settings.saveProfile') }}</button>
        </form>
      </section>
      <section class="card form-card password-change-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.passwordChangeTitle') }}</h2>
        <p>{{ t('settingsUi.passwordChangeDescription') }}</p>
        <p class="table-secondary">{{ t('auth.passwordHint') }}</p>
        <form class="form-grid" @submit.prevent="changePassword">
          <label class="field">
            {{ t('settingsUi.currentPassword') }}
            <input v-model="passwordCurrent" type="password" autocomplete="current-password" required />
          </label>
          <label class="field">
            {{ t('auth.newPassword') }}
            <input v-model="passwordNew" type="password" autocomplete="new-password" minlength="15" required />
          </label>
          <label class="field">
            {{ t('auth.repeatNewPassword') }}
            <input v-model="passwordConfirmation" type="password" autocomplete="new-password" minlength="15" required />
          </label>
          <div v-if="passwordChangeError" class="error" role="alert">{{ passwordChangeError }}</div>
          <button class="button" type="submit" :disabled="changingPassword">
            {{ changingPassword ? t('auth.passwordChanging') : t('auth.changePassword') }}
          </button>
        </form>
      </section>

      <section class="card form-card mfa-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.mfaTitle') }}</h2>
        <p>{{ t('settingsUi.mfaDescription') }}</p>

        <template v-if="!mfa?.totp_enabled">
          <p v-if="mfa?.totp_setup_pending && !totpSetup">{{ t('settingsUi.mfaPending') }}</p>
          <form v-if="!totpSetup" class="form-grid" @submit.prevent="beginTotpSetup">
            <label class="field">
              {{ t('settingsUi.password') }}
              <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
            </label>
            <button class="button" type="submit" :disabled="managingMfa">
              {{ managingMfa ? t('settingsUi.prepareSetup') : t('settingsUi.setupAuthenticator') }}
            </button>
          </form>

          <div v-else class="mfa-setup">
            <ol>
              <li>{{ t('settingsUi.scanQr') }}</li>
              <li>{{ t('settingsUi.enterCode') }}</li>
            </ol>
            <img class="mfa-qr" :src="totpSetup.qr_svg_data_url" :alt="t('settingsUi.qrAlt')" />
            <details>
              <summary>{{ t('settingsUi.manualKey') }}</summary>
              <code class="mfa-secret">{{ totpSetup.secret }}</code>
            </details>
            <form class="form-grid" @submit.prevent="confirmTotpSetup">
              <label class="field">
                {{ t('settingsUi.code') }}
                <input v-model="mfaCode" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="6" required />
              </label>
              <button class="button" type="submit" :disabled="managingMfa">
                {{ managingMfa ? t('settingsUi.codeChecking') : t('settingsUi.enableTotp') }}
              </button>
            </form>
          </div>
        </template>

        <template v-else>
          <p><strong>{{ t('settingsUi.status') }}</strong> {{ t('settingsUi.active') }} · {{ t('settingsUi.recoveryCodesAvailable', { count: mfa.recovery_codes_remaining }) }}</p>
          <p>{{ t('settingsUi.changesNeedFactor') }}</p>
          <form class="form-grid" @submit.prevent>
            <label class="field">
              {{ t('settingsUi.password') }}
              <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
            </label>
            <label class="field">
              {{ t('auth.securityCode') }}
              <input v-model="mfaCode" autocomplete="one-time-code" maxlength="64" required />
            </label>
            <div class="filters">
              <button class="button secondary" type="button" :disabled="managingMfa" @click="regenerateRecoveryCodes">
                {{ t('settingsUi.regenerate') }}
              </button>
              <button class="text-button danger" type="button" :disabled="managingMfa" @click="disableTotp">
                {{ t('settingsUi.disable') }}
              </button>
            </div>
          </form>
        </template>

        <div v-if="recoveryCodes.length" class="recovery-codes" role="status">
          <strong>{{ t('settingsUi.storeCodes') }}</strong>
          <code v-for="code in recoveryCodes" :key="code">{{ code }}</code>
          <button class="button secondary" type="button" @click="copyRecoveryCodes">{{ t('settingsUi.copyAll') }}</button>
        </div>
      </section>

      <section class="card form-card passkey-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.passkeysTitle') }}</h2>
        <p>{{ t('settingsUi.passkeysDescription') }}</p>
        <p v-if="!passkeySupported" class="passkey-unavailable">{{ t('settingsUi.passkeysUnavailable') }}</p>

        <div v-if="passkeys.length" class="passkey-list">
          <article v-for="passkey in passkeys" :key="passkey.id" class="passkey-item">
            <div>
              <strong>{{ passkey.label }}</strong>
              <small>
                {{ passkeyDeviceLabel(passkey) }} · {{ t('settingsUi.created') }}
                {{ formatGermanInstantDate(passkey.created_at) }}
                <template v-if="passkey.last_used_at">
                  · {{ t('settingsUi.lastUsed') }} {{ formatGermanDateTime(passkey.last_used_at) }}
                </template>
              </small>
            </div>
            <button
              class="text-button danger"
              type="button"
              :disabled="managingPasskey"
              @click="removePasskey(passkey.id)"
            >
              {{ t('settingsUi.remove') }}
            </button>
          </article>
        </div>
        <p v-else>{{ t('settingsUi.noPasskeys') }}</p>

        <form class="form-grid" @submit.prevent="registerPasskey">
          <label class="field">
            {{ t('settingsUi.label') }}
            <input
              v-model="passkeyLabel"
              maxlength="100"
              :placeholder="t('settingsUi.placeholder')"
              :disabled="!passkeySupported"
              required
            />
          </label>
          <label class="field">
            {{ t('settingsUi.password') }}
            <input
              v-model="passkeyPassword"
              type="password"
              autocomplete="current-password"
              required
            />
            <small>{{ t('settingsUi.passwordRemoveHelp') }}</small>
          </label>
          <label v-if="mfa?.totp_enabled" class="field">
            {{ t('auth.securityCode') }}
            <input
              v-model="passkeyCode"
              autocomplete="one-time-code"
              maxlength="64"
              required
            />
          </label>
          <button
            class="button"
            type="submit"
            :disabled="!passkeySupported || managingPasskey"
          >
            {{ managingPasskey ? t('settingsUi.processing') : t('settingsUi.setupPasskey') }}
          </button>
        </form>
      </section>

      <section class="card form-card yazio-connection-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.yazioTitle') }}</h2>
        <p>{{ t('settingsUi.yazioDescription') }}</p>
        <div v-if="yazioError" class="card error" role="alert">{{ yazioError }}</div>
        <p v-if="yazioMessage" class="setup-notice" role="status">{{ yazioMessage }}</p>
        <div v-if="initialSetupSaved" class="setup-notice" role="status">
          <p>{{ t('settingsUi.initialImport') }}</p>
          <RouterLink class="text-button" to="/importe">{{ t('settingsUi.toImports') }}</RouterLink>
        </div>
        <p><strong>{{ t('settingsUi.statusLabel') }}</strong> {{ yazioStatusLabel }}</p>
        <p v-if="yazioHistoricalSyncFailed" class="import-message error" role="alert">
          {{ t('settingsUi.firstImportFailedMessage') }}
          <RouterLink to="/importe">{{ t('settingsUi.detailsUnderImports') }}</RouterLink>
        </p>
        <p
          v-if="yazio?.historical_sync?.state === 'completed' && yazio?.historical_sync.completed_at"
          class="table-secondary"
        >
          {{ t('settingsUi.firstImportCompleted') }}
          {{ formatGermanDateTime(yazio.historical_sync.completed_at) }}
        </p>
        <form class="form-grid" @submit.prevent="saveYazio">
          <label class="field">
            {{ t('settingsUi.email') }}
            <input
              v-model="yazioEmail"
              name="yazio-email"
              type="email"
              autocomplete="email"
              :disabled="!yazioAvailable"
              :placeholder="yazio?.configured ? t('settingsUi.emailStored') : t('settingsUi.emailPlaceholder')"
              required
            />
            <small v-if="yazio?.configured">{{ t('settingsUi.storedEdit') }}</small>
          </label>
          <label class="field">
            {{ t('settingsUi.passwordLabel') }}
            <input
              v-model="yazioPassword"
              name="yazio-password"
              type="password"
              autocomplete="current-password"
              :disabled="!yazioAvailable"
              :placeholder="yazio?.configured ? t('settingsUi.passwordStored') : t('settingsUi.passwordLabel')"
              required
            />
            <small v-if="yazio?.configured">{{ t('settingsUi.storedNever') }}</small>
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
            class="button"
            type="submit"
            :disabled="savingYazio || !yazioCredentialsComplete || !yazioAvailable"
          >
            {{ savingYazio ? t('settingsUi.checkConnection') : yazio?.configured ? t('settingsUi.updateConnection') : t('settingsUi.setupConnection') }}
          </button>
        </form>
      </section>

      <section class="card form-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.tokensTitle') }}</h2>
        <p>{{ t('settingsUi.tokensDescription') }}</p>
        <div class="filters"><label class="field">{{ t('settingsUi.tokenLabel') }}<input v-model="tokenLabel" /></label><button class="button" type="button" @click="createToken">{{ t('settingsUi.createToken') }}</button></div>
        <div v-if="newToken" class="card" style="padding: 1rem; margin-top: 1rem"><strong>{{ t('settingsUi.copyNow') }}</strong><code style="display: block; overflow-wrap: anywhere; margin-top: .5rem">{{ newToken }}</code></div>
        <div class="table-scroll"><table><thead><tr><th>{{ t('settingsUi.tokenLabel') }}</th><th>{{ t('settingsUi.prefix') }}</th><th>{{ t('settingsUi.lastUsedLabel') }}</th><th></th></tr></thead><tbody><tr v-for="token in tokens" :key="token.id"><td>{{ token.label }}</td><td><code>{{ token.token_prefix }}…</code></td><td>{{ token.last_used_at ? formatGermanDateTime(token.last_used_at) : t('settingsUi.never') }}</td><td><button v-if="!token.revoked_at" class="text-button" type="button" @click="revokeToken(token.id)">{{ t('settingsUi.revoke') }}</button><span v-else>{{ t('settingsUi.revoked') }}</span></td></tr></tbody></table></div>
      </section>

      <section v-if="auth.user?.is_admin" class="card form-card" style="margin-top: 1rem">
        <h2>{{ t('settingsUi.adminTitle') }}</h2>
        <p>{{ t('settingsUi.adminDescription') }}</p>
        <button class="button" type="button" @click="createInvitation">{{ t('settingsUi.createInvitation') }}</button>
        <div v-if="invitationUrl" class="invitation-result">
          <strong>{{ t('settingsUi.shareLink') }}</strong>
          <code>{{ invitationUrl }}</code>
          <button class="button secondary" type="button" @click="copyInvitation">{{ t('common.copy') }}</button>
        </div>
        <UserManagement
          :users="users"
          :current-user-id="auth.user.id"
          @refresh="refreshAdmin"
          @message="message = $event"
          @error="error = $event"
        />
        <div v-if="invitations.length" class="table-scroll" style="margin-top: 1rem">
          <table>
            <thead><tr><th>{{ t('settingsUi.adminCreated') }}</th><th>{{ t('settingsUi.validUntil') }}</th><th>{{ t('settingsUi.invitationStatus') }}</th><th></th></tr></thead>
            <tbody><tr v-for="item in invitations" :key="item.id"><td>{{ formatGermanDateTime(item.created_at) }}</td><td>{{ formatGermanDateTime(item.expires_at) }}</td><td>{{ item.used_at ? t('settingsUi.used') : item.revoked_at ? t('settingsUi.revoked') : t('settingsUi.open') }}</td><td><button v-if="!item.used_at && !item.revoked_at" class="text-button" type="button" @click="revokeInvitation(item.id)">{{ t('settingsUi.revoke') }}</button></td></tr></tbody>
          </table>
        </div>
      </section>
    </template>
  </template>
</template>
