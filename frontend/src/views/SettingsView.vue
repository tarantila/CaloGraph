<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'

import { api, ApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import UserManagement from '../components/UserManagement.vue'
import {
  formatGermanDate,
  formatGermanDateTime,
  formatGermanInstantDate,
  isoDateInTimeZone,
} from '../date-format'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'
import type { Target, User, YazioStatus } from '../types'
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
const profile = reactive({ timezone: 'Europe/Berlin', week_starts_on: 0, raw_payload_retention_days: 0 })
const target = reactive(createEmptyTargetDraft())
const targets = ref<Target[]>([])
const tokens = ref<Token[]>([])
const tokenLabel = ref('iPhone')
const newToken = ref('')
const message = ref('')
const auth = useAuthStore()
const yazio = ref<YazioStatus | null>(null)
const yazioEmail = ref('')
const yazioPassword = ref('')
const yazioHistoryFrom = ref('')
const yazioHistoryTo = ref('')
const savingYazio = ref(false)
const initialSetupSaved = ref(false)
const users = ref<User[]>([])
const invitations = ref<Invitation[]>([])
const invitationUrl = ref('')
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
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })
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
  if (!yazioAvailable.value) return 'serverseitig deaktiviert'
  if (!yazio.value?.configured) return 'noch nicht eingerichtet'
  const historicalState = yazio.value.historical_sync?.state
  if (historicalState === 'pending') return 'Erster Datenimport wartet auf den Scheduler'
  if (historicalState === 'running') return 'Erster Datenimport läuft im Hintergrund'
  if (historicalState === 'failed') return 'Erster Datenimport fehlgeschlagen'
  if (!yazio.value.sync_enabled) return 'pausiert · Zugangsdaten aktualisieren'
  return `aktiv · alle ${(yazio.value.sync_interval_minutes ?? 360) / 60} Stunden · letzte ${yazio.value.sync_days ?? 7} Tage`
})
const yazioHistoricalSyncActive = computed(() => {
  const state = yazio.value?.historical_sync?.state
  return yazioAvailable.value && (state === 'pending' || state === 'running')
})
const yazioHistoricalSyncFailed = computed(
  () => yazio.value?.historical_sync?.state === 'failed',
)

async function loadTargets() {
  const targetResult = await api<Target[]>('/settings/targets')
  targets.value = targetResult
  const currentTarget = targetResult.find((item) => item.valid_to == null) ?? targetResult[0]
  if (currentTarget) {
    target.calories_kcal = Number(currentTarget.calories_kcal)
    target.maintenance_kcal = currentTarget.maintenance_kcal == null ? null : Number(currentTarget.maintenance_kcal)
    target.protein_g = Number(currentTarget.protein_g)
    target.carbs_g = currentTarget.carbs_g == null ? null : Number(currentTarget.carbs_g)
    target.fat_g = currentTarget.fat_g == null ? null : Number(currentTarget.fat_g)
    target.fiber_g = currentTarget.fiber_g == null ? null : Number(currentTarget.fiber_g)
  }
  target.valid_from = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
}

async function loadAdmin(generation?: number) {
  const [usersResult, invitationsResult] = await Promise.all([
    api<User[]>('/users'),
    api<Invitation[]>('/users/invitations'),
  ])
  if (generation != null && generation !== loadGeneration) return
  users.value = usersResult
  invitations.value = invitationsResult
}

async function refreshAdmin() {
  try {
    await loadAdmin()
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Benutzerliste konnte nicht aktualisiert werden.'
  }
}

async function loadAccount(generation = loadGeneration) {
  const [user, tokenResult, yazioResult, mfaResult, passkeyResult] = await Promise.all([
    api<User>('/settings/profile'),
    api<Token[]>('/settings/tokens'),
    api<YazioStatus>('/yazio/status'),
    api<MfaStatus>('/settings/mfa'),
    api<Passkey[]>('/settings/passkeys'),
  ])
  if (generation !== loadGeneration) return
  profile.timezone = user.timezone
  profile.week_starts_on = user.week_starts_on
  profile.raw_payload_retention_days = user.raw_payload_retention_days
  auth.user = user
  tokens.value = tokenResult
  yazio.value = yazioResult
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
    const result = await api<YazioStatus>('/yazio/status')
    if (generation !== yazioPollGeneration || props.section !== 'account') return
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
      cause instanceof ApiError ? cause.message : 'Einstellungen konnten nicht geladen werden.'
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}
watch(() => props.section, () => { void load() }, { immediate: true })
onBeforeUnmount(() => {
  settingsMounted = false
  stopYazioPolling()
})

async function saveProfile() {
  const user = await api<User>('/settings/profile', { method: 'PUT', body: JSON.stringify(profile) })
  auth.user = user
  message.value = 'Profil gespeichert.'
}
async function saveTarget() {
  savingTarget.value = true
  error.value = ''
  message.value = ''
  try {
    await saveTargetDraft(target, targets.value)
    message.value = `Budget und Ziele ab ${formatGermanDate(target.valid_from)} gespeichert.`
    auth.completeTargetSetup()
    await loadTargets()
  } catch (cause) {
    error.value =
      cause instanceof ApiError || cause instanceof TargetValidationError
        ? cause.message
        : 'Budget und Ziele konnten nicht gespeichert werden.'
  } finally {
    savingTarget.value = false
  }
}
async function createToken() { const result = await api<{ token: string }>('/settings/tokens', { method: 'POST', body: JSON.stringify({ label: tokenLabel.value }) }); newToken.value = result.token; await load() }
async function revokeToken(id: string) { await api(`/settings/tokens/${id}`, { method: 'DELETE' }); await load() }
async function saveYazio() {
  if (!yazioCredentialsComplete.value) return
  const isNewConnection = !yazio.value?.configured
  if (isNewConnection && yazioHistoryFrom.value > yazioHistoryTo.value) {
    error.value = 'Das Von-Datum darf nicht nach dem Bis-Datum liegen.'
    return
  }
  savingYazio.value = true
  error.value = ''
  message.value = ''
  initialSetupSaved.value = false
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
    if (isNewConnection) {
      initialSetupSaved.value = true
      message.value = 'YAZIO-Verbindung gespeichert.'
    } else {
      message.value = 'YAZIO-Verbindung aktualisiert.'
    }
    scheduleYazioPolling()
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'YAZIO-Verbindung konnte nicht gespeichert werden.'
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
  message.value = 'Einladungslink kopiert.'
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
    message.value = 'Scanne jetzt den QR-Code und bestätige die Einrichtung.'
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'TOTP-Einrichtung konnte nicht gestartet werden.'
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
    message.value = 'Zwei-Faktor-Authentifizierung ist aktiviert. Sichere die Wiederherstellungscodes jetzt.'
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'TOTP-Code konnte nicht bestätigt werden.'
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
    message.value = 'Neue Wiederherstellungscodes wurden erzeugt. Die alten sind ungültig.'
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Wiederherstellungscodes konnten nicht erneuert werden.'
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
    message.value = 'Zwei-Faktor-Authentifizierung wurde deaktiviert.'
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Zwei-Faktor-Authentifizierung konnte nicht deaktiviert werden.'
  } finally {
    managingMfa.value = false
  }
}

async function copyRecoveryCodes() {
  await navigator.clipboard.writeText(recoveryCodes.value.join('\n'))
  message.value = 'Wiederherstellungscodes kopiert.'
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
    message.value = 'Passkey wurde eingerichtet. Du kannst dich jetzt ohne Passwort anmelden.'
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
      error.value = 'Passkey-Einrichtung wurde abgebrochen oder ist abgelaufen.'
    } else {
      error.value =
        cause instanceof ApiError || cause instanceof Error
          ? cause.message
          : 'Passkey konnte nicht eingerichtet werden.'
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
    message.value = 'Passkey wurde entfernt.'
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Passkey konnte nicht entfernt werden.'
  } finally {
    managingPasskey.value = false
  }
}

function passkeyDeviceLabel(passkey: Passkey) {
  if (passkey.backed_up || passkey.device_type === 'multi_device') {
    return 'synchronisiert'
  }
  return 'dieses Gerät'
}
</script>

<template>
  <div class="page-heading">
    <div v-if="props.section === 'targets'">
      <h1>Budgets & Ziele</h1>
      <p>Lege dein Kalorienbudget und deine Makronährstoffziele mit einem Gültigkeitsdatum fest.</p>
    </div>
    <div v-else>
      <h1>Konto</h1>
      <p>Verwalte dein Profil, deine persönliche YAZIO-Verbindung und deine Zugänge.</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading">Einstellungen werden geladen …</div>
  <template v-else>
    <div v-if="error" class="card error" role="alert">{{ error }}</div>
    <p v-if="message" role="status">{{ message }}</p>
    <div v-if="initialSetupSaved" class="setup-notice" role="status">
      <p>Der erste Datenimport läuft im Hintergrund. Du kannst diese Seite verlassen.</p>
      <RouterLink class="text-button" to="/importe">Zu den Importen</RouterLink>
    </div>
    <template v-if="props.section === 'targets'">
      <div class="content-grid">
        <section class="card form-card">
          <h2>Budget und Ziele festlegen</h2>
          <p v-if="!targets.length" class="setup-notice">
            Lege zuerst deine persönlichen Ziele fest. Kalorienbudget und Proteinziel werden für Budget- und
            Analytics-Auswertungen verwendet; optionale Makroziele kannst du später ergänzen.
          </p>
          <form class="form-grid" @submit.prevent="saveTarget">
            <label class="field">Gültig ab<DateInput v-model="target.valid_from" required /></label>
            <label class="field">Kalorienbudget<input v-model.number="target.calories_kcal" type="number" :min="TARGET_LIMITS.caloriesMin" step="1" required /></label>
            <label class="field">Erhaltungsbedarf (kcal)<input v-model.number="target.maintenance_kcal" type="number" :min="TARGET_LIMITS.maintenanceMin" step="0.001" /><small>Optional: geschätzte Kalorienmenge, bei der dein Gewicht ungefähr stabil bleibt.</small></label>
            <label class="field">Proteinziel (g)<input v-model.number="target.protein_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" required /></label>
            <label class="field">Kohlenhydrate (g)<input v-model.number="target.carbs_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <label class="field">Fett (g)<input v-model.number="target.fat_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <label class="field">Ballaststoffe (g)<input v-model.number="target.fiber_g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /></label>
            <button class="button" type="submit" :disabled="savingTarget">
              {{ savingTarget ? 'Wird gespeichert …' : 'Budget und Ziele speichern' }}
            </button>
          </form>
        </section>

        <section class="card form-card budget-help">
          <h2>So wird die Änderung verwendet</h2>
          <p>Das Kalorienbudget ist deine tägliche Obergrenze. Das Proteinziel wird als Wert behandelt, den du möglichst erreichen möchtest.</p>
          <p>Der optionale Erhaltungsbedarf ist deine geschätzte Kalorienmenge, bei der dein Gewicht ungefähr stabil bleibt. Dein Kalorienbudget kann darunter, darauf oder darüber liegen.</p>
          <p>Im Kalender bleibt das Budget maßgeblich: Werte bis zum Budget sind grün, Werte über dem Budget orange und Werte über Budget und Erhaltungsbedarf rot.</p>
          <p>Mit „Gültig ab“ bestimmst du den ersten Tag der neuen Werte. Gibt es für dieses Datum bereits eine Version, wird sie aktualisiert. Frühere Auswertungen behalten die damals gültigen Werte.</p>
        </section>
      </div>

      <section class="card table-card">
        <div class="section-card-header">
          <div><h2>Budget- und Zielhistorie</h2><p>Die neueste gültige Version steht oben.</p></div>
        </div>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Gültig ab</th><th>Gültig bis</th><th class="number">Kalorienbudget</th><th class="number">Erhaltungsbedarf</th><th class="number">Proteinziel</th></tr></thead>
            <tbody>
              <tr v-for="item in targets" :key="item.id">
                <td>{{ formatGermanDate(item.valid_from) }}</td>
                <td>{{ item.valid_to ? formatGermanDate(item.valid_to) : 'aktuell' }}</td>
                <td class="number">{{ integer.format(Number(item.calories_kcal)) }} kcal</td>
                <td class="number">{{ item.maintenance_kcal == null ? '–' : `${integer.format(Number(item.maintenance_kcal))} kcal` }}</td>
                <td class="number">{{ integer.format(Number(item.protein_g)) }} g</td>
              </tr>
              <tr v-if="!targets.length"><td colspan="5" class="empty">Noch keine Budgets oder Ziele vorhanden.</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <template v-else>
      <section class="card form-card">
        <h2>Profil</h2>
        <form class="form-grid" @submit.prevent="saveProfile">
          <label class="field">
            Zeitzone
            <select v-model="profile.timezone" name="timezone" required>
              <option v-for="timezone in timezoneOptions" :key="timezone" :value="timezone">
                {{ timezone.replaceAll('_', ' ') }}
              </option>
            </select>
            <small>Bestimmt Tagesgrenzen und Uhrzeiten in deinen Auswertungen.</small>
          </label>
          <label class="field">Wochenbeginn<select v-model.number="profile.week_starts_on"><option :value="0">Montag</option><option :value="6">Sonntag</option></select></label>
          <label class="field">JSON-Rohimporte aufbewahren (Tage)<input v-model.number="profile.raw_payload_retention_days" type="number" min="0" max="3650" /><small>0 deaktiviert die Speicherung vollständig. Große XML-/ZIP-Dateien werden nicht zusätzlich dupliziert.</small></label>
          <button class="button" type="submit">Profil speichern</button>
        </form>
      </section>

      <section class="card form-card mfa-card" style="margin-top: 1rem">
        <h2>Zwei-Faktor-Authentifizierung</h2>
        <p>Eine Authenticator-App schützt dein Konto zusätzlich zum Passwort. TOTP-Codes funktionieren offline.</p>

        <template v-if="!mfa?.totp_enabled">
          <p v-if="mfa?.totp_setup_pending && !totpSetup">
            Eine Einrichtung wurde begonnen, aber noch nicht bestätigt. Starte sie mit deinem Passwort erneut, um einen neuen QR-Code zu erhalten.
          </p>
          <form v-if="!totpSetup" class="form-grid" @submit.prevent="beginTotpSetup">
            <label class="field">
              Aktuelles CaloGraph-Passwort
              <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
            </label>
            <button class="button" type="submit" :disabled="managingMfa">
              {{ managingMfa ? 'Einrichtung wird vorbereitet …' : 'Authenticator einrichten' }}
            </button>
          </form>

          <div v-else class="mfa-setup">
            <ol>
              <li>Scanne den QR-Code mit deiner Authenticator-App.</li>
              <li>Gib den dort angezeigten sechsstelligen Code ein.</li>
            </ol>
            <img class="mfa-qr" :src="totpSetup.qr_svg_data_url" alt="QR-Code für die Authenticator-Einrichtung" />
            <details>
              <summary>Schlüssel manuell eingeben</summary>
              <code class="mfa-secret">{{ totpSetup.secret }}</code>
            </details>
            <form class="form-grid" @submit.prevent="confirmTotpSetup">
              <label class="field">
                Sechsstelliger Code
                <input v-model="mfaCode" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="6" required />
              </label>
              <button class="button" type="submit" :disabled="managingMfa">
                {{ managingMfa ? 'Code wird geprüft …' : 'TOTP aktivieren' }}
              </button>
            </form>
          </div>
        </template>

        <template v-else>
          <p><strong>Status:</strong> aktiv · {{ mfa.recovery_codes_remaining }} Wiederherstellungscodes verfügbar</p>
          <p>Für Änderungen brauchst du dein aktuelles Passwort und einen TOTP- oder Wiederherstellungscode.</p>
          <form class="form-grid" @submit.prevent>
            <label class="field">
              Aktuelles CaloGraph-Passwort
              <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
            </label>
            <label class="field">
              TOTP- oder Wiederherstellungscode
              <input v-model="mfaCode" autocomplete="one-time-code" maxlength="64" required />
            </label>
            <div class="filters">
              <button class="button secondary" type="button" :disabled="managingMfa" @click="regenerateRecoveryCodes">
                Codes erneuern
              </button>
              <button class="text-button danger" type="button" :disabled="managingMfa" @click="disableTotp">
                TOTP deaktivieren
              </button>
            </div>
          </form>
        </template>

        <div v-if="recoveryCodes.length" class="recovery-codes" role="status">
          <strong>Jetzt offline und sicher speichern – jeder Code funktioniert nur einmal:</strong>
          <code v-for="code in recoveryCodes" :key="code">{{ code }}</code>
          <button class="button secondary" type="button" @click="copyRecoveryCodes">Alle kopieren</button>
        </div>
      </section>

      <section class="card form-card passkey-card" style="margin-top: 1rem">
        <h2>Passkeys</h2>
        <p>Mit einem Passkey meldest du dich per Fingerabdruck, Gesichtserkennung oder Geräte-PIN an – ohne dein CaloGraph-Passwort einzugeben.</p>
        <p v-if="!passkeySupported" class="passkey-unavailable">
          Dieser Browser oder diese Verbindung unterstützt Passkeys hier nicht. Außer auf localhost ist dafür HTTPS erforderlich.
        </p>

        <div v-if="passkeys.length" class="passkey-list">
          <article v-for="passkey in passkeys" :key="passkey.id" class="passkey-item">
            <div>
              <strong>{{ passkey.label }}</strong>
              <small>
                {{ passkeyDeviceLabel(passkey) }} · erstellt
                {{ formatGermanInstantDate(passkey.created_at) }}
                <template v-if="passkey.last_used_at">
                  · zuletzt {{ formatGermanDateTime(passkey.last_used_at) }}
                </template>
              </small>
            </div>
            <button
              class="text-button danger"
              type="button"
              :disabled="managingPasskey"
              @click="removePasskey(passkey.id)"
            >
              Entfernen
            </button>
          </article>
        </div>
        <p v-else>Noch kein Passkey eingerichtet.</p>

        <form class="form-grid" @submit.prevent="registerPasskey">
          <label class="field">
            Bezeichnung
            <input
              v-model="passkeyLabel"
              maxlength="100"
              placeholder="z. B. Windows Hello"
              :disabled="!passkeySupported"
              required
            />
          </label>
          <label class="field">
            Aktuelles CaloGraph-Passwort
            <input
              v-model="passkeyPassword"
              type="password"
              autocomplete="current-password"
              required
            />
            <small>Wird auch benötigt, wenn du einen vorhandenen Passkey entfernst.</small>
          </label>
          <label v-if="mfa?.totp_enabled" class="field">
            TOTP- oder Wiederherstellungscode
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
            {{ managingPasskey ? 'Passkey wird verarbeitet …' : 'Passkey einrichten' }}
          </button>
        </form>
      </section>

      <section class="card form-card yazio-connection-card" style="margin-top: 1rem">
        <h2>Persönliche YAZIO-Verbindung</h2>
        <p>Diese Zugangsdaten gehören nur zu deinem CaloGraph-Konto und werden verschlüsselt gespeichert. Ein erneutes Speichern ersetzt ausschließlich deine eigene Verbindung.</p>
        <p><strong>Status:</strong> {{ yazioStatusLabel }}</p>
        <p v-if="yazioHistoricalSyncFailed" class="import-message error" role="alert">
          Erster Datenimport fehlgeschlagen · <RouterLink to="/importe">Details unter Importe</RouterLink>
        </p>
        <p
          v-if="yazio?.historical_sync?.state === 'completed' && yazio?.historical_sync.completed_at"
          class="table-secondary"
        >
          Erster Datenimport abgeschlossen:
          {{ formatGermanDateTime(yazio.historical_sync.completed_at) }}
        </p>
        <form class="form-grid" @submit.prevent="saveYazio">
          <label class="field">
            YAZIO-E-Mail
            <input
              v-model="yazioEmail"
              name="yazio-email"
              type="email"
              autocomplete="email"
              :disabled="!yazioAvailable"
              :placeholder="yazio?.configured ? 'E-Mail-Adresse ist gespeichert' : 'name@example.com'"
              required
            />
            <small v-if="yazio?.configured">Gespeichert · zum Ändern erneut eingeben</small>
          </label>
          <label class="field">
            YAZIO-Passwort
            <input
              v-model="yazioPassword"
              name="yazio-password"
              type="password"
              autocomplete="current-password"
              :disabled="!yazioAvailable"
              :placeholder="yazio?.configured ? 'Passwort ist gespeichert' : 'YAZIO-Passwort'"
              required
            />
            <small v-if="yazio?.configured">Gespeichert · wird niemals angezeigt</small>
          </label>
          <template v-if="!yazio?.configured">
            <label class="field">
              Erster Datenimport von
              <DateInput v-model="yazioHistoryFrom" required :disabled="!yazioAvailable" />
            </label>
            <label class="field">
              Bis
              <DateInput v-model="yazioHistoryTo" required :disabled="!yazioAvailable" />
            </label>
            <p class="table-secondary">Wähle den Zeitraum aus, aus dem deine bisherigen YAZIO-Daten übernommen werden sollen. Tage ohne Einträge werden übersprungen.</p>
          </template>
          <button
            class="button"
            type="submit"
            :disabled="savingYazio || !yazioCredentialsComplete || !yazioAvailable"
          >
            {{ savingYazio ? 'Verbindung wird geprüft …' : yazio?.configured ? 'Verbindung aktualisieren' : 'Verbindung einrichten' }}
          </button>
        </form>
      </section>

      <section class="card form-card" style="margin-top: 1rem">
        <h2>Import-Tokens</h2>
        <p>Tokens werden nur einmal angezeigt und danach ausschließlich gehasht gespeichert.</p>
        <div class="filters"><label class="field">Bezeichnung<input v-model="tokenLabel" /></label><button class="button" type="button" @click="createToken">Token erzeugen</button></div>
        <div v-if="newToken" class="card" style="padding: 1rem; margin-top: 1rem"><strong>Jetzt sicher kopieren:</strong><code style="display: block; overflow-wrap: anywhere; margin-top: .5rem">{{ newToken }}</code></div>
        <div class="table-scroll"><table><thead><tr><th>Bezeichnung</th><th>Präfix</th><th>Letzte Verwendung</th><th></th></tr></thead><tbody><tr v-for="token in tokens" :key="token.id"><td>{{ token.label }}</td><td><code>{{ token.token_prefix }}…</code></td><td>{{ token.last_used_at ? formatGermanDateTime(token.last_used_at) : 'nie' }}</td><td><button v-if="!token.revoked_at" class="text-button" type="button" @click="revokeToken(token.id)">Widerrufen</button><span v-else>Widerrufen</span></td></tr></tbody></table></div>
      </section>

      <section v-if="auth.user?.is_admin" class="card form-card" style="margin-top: 1rem">
        <h2>Benutzerverwaltung</h2>
        <p>Einladungslinks sind sieben Tage gültig und können genau einmal verwendet werden. Der eingeladene Benutzer wählt sein Passwort selbst.</p>
        <button class="button" type="button" @click="createInvitation">Einladungslink erzeugen</button>
        <div v-if="invitationUrl" class="invitation-result">
          <strong>Link jetzt sicher weitergeben:</strong>
          <code>{{ invitationUrl }}</code>
          <button class="button secondary" type="button" @click="copyInvitation">Kopieren</button>
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
            <thead><tr><th>Erstellt</th><th>Gültig bis</th><th>Status</th><th></th></tr></thead>
            <tbody><tr v-for="item in invitations" :key="item.id"><td>{{ formatGermanDateTime(item.created_at) }}</td><td>{{ formatGermanDateTime(item.expires_at) }}</td><td>{{ item.used_at ? 'Verwendet' : item.revoked_at ? 'Widerrufen' : 'Offen' }}</td><td><button v-if="!item.used_at && !item.revoked_at" class="text-button" type="button" @click="revokeInvitation(item.id)">Widerrufen</button></td></tr></tbody>
          </table>
        </div>
      </section>
    </template>
  </template>
</template>
