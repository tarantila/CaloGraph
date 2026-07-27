<script setup lang="ts">
import { reactive, ref, watch } from 'vue'

import { api, ApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import type { Target, User, YazioStatus } from '../types'

interface Token { id: string; label: string; token_prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
interface Invitation { id: string; created_at: string; expires_at: string; used_at: string | null; revoked_at: string | null }
const props = defineProps<{ section: 'targets' | 'account' }>()
const profile = reactive({ timezone: 'Europe/Berlin', week_starts_on: 0, raw_payload_retention_days: 0 })
const target = reactive({ valid_from: new Date().toISOString().slice(0, 10), calories_kcal: 2200, maintenance_kcal: null as number | null, protein_g: 140, carbs_g: null as number | null, fat_g: null as number | null, fiber_g: null as number | null })
const targets = ref<Target[]>([])
const tokens = ref<Token[]>([])
const tokenLabel = ref('iPhone')
const newToken = ref('')
const message = ref('')
const auth = useAuthStore()
const yazio = ref<YazioStatus | null>(null)
const yazioEmail = ref('')
const yazioPassword = ref('')
const savingYazio = ref(false)
const users = ref<User[]>([])
const invitations = ref<Invitation[]>([])
const invitationUrl = ref('')
const error = ref('')
const loading = ref(true)
const savingTarget = ref(false)
const integer = new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 })

function today() {
  const current = new Date()
  const offset = current.getTimezoneOffset() * 60_000
  return new Date(current.getTime() - offset).toISOString().slice(0, 10)
}

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
  target.valid_from = today()
}

async function loadAccount() {
  const [user, tokenResult, yazioResult] = await Promise.all([
    api<User>('/settings/profile'),
    api<Token[]>('/settings/tokens'),
    api<YazioStatus>('/yazio/status'),
  ])
  profile.timezone = user.timezone
  profile.week_starts_on = user.week_starts_on
  profile.raw_payload_retention_days = user.raw_payload_retention_days
  auth.user = user
  tokens.value = tokenResult
  yazio.value = yazioResult
  if (user.is_admin) {
    ;[users.value, invitations.value] = await Promise.all([
      api<User[]>('/users'),
      api<Invitation[]>('/users/invitations'),
    ])
  }
}

async function load() {
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    if (props.section === 'targets') await loadTargets()
    else await loadAccount()
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Einstellungen konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
}
watch(() => props.section, load, { immediate: true })

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
    const existing = targets.value.some((item) => item.valid_from === target.valid_from)
    const payload = {
      ...target,
      maintenance_kcal: target.maintenance_kcal || null,
    }
    await api(
      existing ? `/settings/targets/${target.valid_from}` : '/settings/targets',
      { method: existing ? 'PUT' : 'POST', body: JSON.stringify(payload) },
    )
    message.value = `Budget und Ziele ab ${new Date(`${target.valid_from}T12:00:00`).toLocaleDateString('de-DE')} gespeichert.`
    await loadTargets()
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Budget und Ziele konnten nicht gespeichert werden.'
  } finally {
    savingTarget.value = false
  }
}
async function createToken() { const result = await api<{ token: string }>('/settings/tokens', { method: 'POST', body: JSON.stringify({ label: tokenLabel.value }) }); newToken.value = result.token; await load() }
async function revokeToken(id: string) { await api(`/settings/tokens/${id}`, { method: 'DELETE' }); await load() }
async function saveYazio() {
  savingYazio.value = true
  message.value = ''
  try {
    yazio.value = await api<YazioStatus>('/yazio/connection', {
      method: 'PUT',
      body: JSON.stringify({ email: yazioEmail.value, password: yazioPassword.value, interval_hours: 6, sync_days: 7 }),
    })
    yazioPassword.value = ''
    message.value = 'Persönliche YAZIO-Verbindung gespeichert.'
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

    <template v-if="props.section === 'targets'">
      <div class="content-grid">
        <section class="card form-card">
          <h2>Budget und Ziele festlegen</h2>
          <form class="form-grid" @submit.prevent="saveTarget">
            <label class="field">Gültig ab<input v-model="target.valid_from" type="date" required /></label>
            <label class="field">Kalorienbudget<input v-model.number="target.calories_kcal" type="number" min="1" step="1" required /></label>
            <label class="field">Erhaltungsbedarf (kcal)<input v-model.number="target.maintenance_kcal" type="number" :min="target.calories_kcal" step="1" /><small>Optional: geschätzte Kalorienmenge, bei der dein Gewicht ungefähr stabil bleibt.</small></label>
            <label class="field">Proteinziel (g)<input v-model.number="target.protein_g" type="number" min="0" step="1" required /></label>
            <label class="field">Kohlenhydrate (g)<input v-model.number="target.carbs_g" type="number" min="0" step="1" /></label>
            <label class="field">Fett (g)<input v-model.number="target.fat_g" type="number" min="0" step="1" /></label>
            <label class="field">Ballaststoffe (g)<input v-model.number="target.fiber_g" type="number" min="0" step="1" /></label>
            <button class="button" type="submit" :disabled="savingTarget">
              {{ savingTarget ? 'Wird gespeichert …' : 'Budget und Ziele speichern' }}
            </button>
          </form>
        </section>

        <section class="card form-card budget-help">
          <h2>So wird die Änderung verwendet</h2>
          <p>Das Kalorienbudget ist deine tägliche Obergrenze. Das Proteinziel wird als Wert behandelt, den du möglichst erreichen möchtest.</p>
          <p>Der optionale Erhaltungsbedarf liegt mindestens auf Höhe des Budgets. Im Kalender werden Tage bis zum Budget grün, Überschreitungen bis zum Erhaltungsbedarf orange und Werte darüber rot dargestellt.</p>
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
                <td>{{ item.valid_from }}</td>
                <td>{{ item.valid_to ?? 'aktuell' }}</td>
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
          <label class="field">IANA-Zeitzone<input v-model="profile.timezone" required /></label>
          <label class="field">Wochenbeginn<select v-model.number="profile.week_starts_on"><option :value="0">Montag</option><option :value="6">Sonntag</option></select></label>
          <label class="field">JSON-Rohimporte aufbewahren (Tage)<input v-model.number="profile.raw_payload_retention_days" type="number" min="0" max="3650" /><small>0 deaktiviert die Speicherung vollständig. Große XML-/ZIP-Dateien werden nicht zusätzlich dupliziert.</small></label>
          <button class="button" type="submit">Profil speichern</button>
        </form>
      </section>

      <section class="card form-card" style="margin-top: 1rem">
        <h2>Persönliche YAZIO-Verbindung</h2>
        <p>Diese Zugangsdaten gehören nur zu deinem CaloGraph-Konto und werden verschlüsselt gespeichert. Ein erneutes Speichern ersetzt ausschließlich deine eigene Verbindung.</p>
        <p><strong>Status:</strong> {{ yazio?.configured ? `aktiv · alle ${(yazio.sync_interval_minutes ?? 360) / 60} Stunden · letzte ${yazio.sync_days ?? 7} Tage` : 'noch nicht eingerichtet' }}</p>
        <form class="form-grid" @submit.prevent="saveYazio">
          <label class="field">YAZIO-E-Mail<input v-model="yazioEmail" type="email" autocomplete="email" required /></label>
          <label class="field">YAZIO-Passwort<input v-model="yazioPassword" type="password" autocomplete="current-password" required /></label>
          <button class="button" type="submit" :disabled="savingYazio">{{ savingYazio ? 'Verbindung wird geprüft …' : 'YAZIO verbinden' }}</button>
        </form>
      </section>

      <section class="card form-card" style="margin-top: 1rem">
        <h2>Import-Tokens</h2>
        <p>Tokens werden nur einmal angezeigt und danach ausschließlich gehasht gespeichert.</p>
        <div class="filters"><label class="field">Bezeichnung<input v-model="tokenLabel" /></label><button class="button" type="button" @click="createToken">Token erzeugen</button></div>
        <div v-if="newToken" class="card" style="padding: 1rem; margin-top: 1rem"><strong>Jetzt sicher kopieren:</strong><code style="display: block; overflow-wrap: anywhere; margin-top: .5rem">{{ newToken }}</code></div>
        <div class="table-scroll"><table><thead><tr><th>Bezeichnung</th><th>Präfix</th><th>Letzte Verwendung</th><th></th></tr></thead><tbody><tr v-for="token in tokens" :key="token.id"><td>{{ token.label }}</td><td><code>{{ token.token_prefix }}…</code></td><td>{{ token.last_used_at ? new Date(token.last_used_at).toLocaleString('de-DE') : 'nie' }}</td><td><button v-if="!token.revoked_at" class="text-button" type="button" @click="revokeToken(token.id)">Widerrufen</button><span v-else>Widerrufen</span></td></tr></tbody></table></div>
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
        <div class="table-scroll" style="margin-top: 1rem">
          <table>
            <thead><tr><th>Benutzer</th><th>Rolle</th><th>Status</th></tr></thead>
            <tbody><tr v-for="item in users" :key="item.id"><td>{{ item.username }}</td><td>{{ item.is_admin ? 'Administrator' : 'Benutzer' }}</td><td>{{ item.id === auth.user?.id ? 'Du' : 'Aktiv' }}</td></tr></tbody>
          </table>
        </div>
        <div v-if="invitations.length" class="table-scroll" style="margin-top: 1rem">
          <table>
            <thead><tr><th>Erstellt</th><th>Gültig bis</th><th>Status</th><th></th></tr></thead>
            <tbody><tr v-for="item in invitations" :key="item.id"><td>{{ new Date(item.created_at).toLocaleString('de-DE') }}</td><td>{{ new Date(item.expires_at).toLocaleString('de-DE') }}</td><td>{{ item.used_at ? 'Verwendet' : item.revoked_at ? 'Widerrufen' : 'Offen' }}</td><td><button v-if="!item.used_at && !item.revoked_at" class="text-button" type="button" @click="revokeInvitation(item.id)">Widerrufen</button></td></tr></tbody>
          </table>
        </div>
      </section>
    </template>
  </template>
</template>
