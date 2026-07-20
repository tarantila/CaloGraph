<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import type { Target, User } from '../types'

interface Token { id: string; label: string; token_prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null }
interface QualitySettings { calories_full_ratio: number; calories_partial_ratio: number; median_full_ratio: number; median_partial_ratio: number; complete_score: number; probably_complete_score: number; probably_incomplete_score: number }
const profile = reactive({ timezone: 'Europe/Berlin', week_starts_on: 0, preferred_weight_unit: 'kg' as 'kg' | 'lb', raw_payload_retention_days: 0 })
const quality = reactive<QualitySettings>({ calories_full_ratio: 0.6, calories_partial_ratio: 0.35, median_full_ratio: 0.5, median_partial_ratio: 0.3, complete_score: 7, probably_complete_score: 5, probably_incomplete_score: 3 })
const target = reactive({ valid_from: new Date().toISOString().slice(0, 10), calories_kcal: 2200, protein_g: 140, carbs_g: null as number | null, fat_g: null as number | null, fiber_g: null as number | null, water_ml: null as number | null })
const targets = ref<Target[]>([])
const tokens = ref<Token[]>([])
const tokenLabel = ref('iPhone')
const newToken = ref('')
const message = ref('')

async function load() {
  const [user, targetResult, tokenResult, qualityResult] = await Promise.all([api<User>('/settings/profile'), api<Target[]>('/settings/targets'), api<Token[]>('/settings/tokens'), api<QualitySettings>('/settings/tracking-quality')])
  profile.timezone = user.timezone
  profile.week_starts_on = user.week_starts_on
  profile.preferred_weight_unit = user.preferred_weight_unit
  profile.raw_payload_retention_days = user.raw_payload_retention_days
  Object.assign(quality, qualityResult)
  targets.value = targetResult
  tokens.value = tokenResult
}
onMounted(load)

async function saveProfile() { await api('/settings/profile', { method: 'PUT', body: JSON.stringify(profile) }); message.value = 'Profil gespeichert.' }
async function saveQuality() { await api('/settings/tracking-quality', { method: 'PUT', body: JSON.stringify(quality) }); message.value = 'Vollständigkeitsregeln gespeichert.' }
async function saveTarget() { await api('/settings/targets', { method: 'POST', body: JSON.stringify(target) }); message.value = 'Neue Zielversion gespeichert.'; await load() }
async function createToken() { const result = await api<{ token: string }>('/settings/tokens', { method: 'POST', body: JSON.stringify({ label: tokenLabel.value }) }); newToken.value = result.token; await load() }
async function revokeToken(id: string) { await api(`/settings/tokens/${id}`, { method: 'DELETE' }); await load() }
</script>

<template>
  <div class="page-heading"><div><h1>Einstellungen</h1><p>Ziele werden historisiert und verändern frühere Auswertungen nicht.</p></div></div>
  <p v-if="message" role="status">{{ message }}</p>
  <div class="content-grid">
    <section class="card form-card">
      <h2>Profil</h2>
      <form class="form-grid" @submit.prevent="saveProfile">
        <label class="field">IANA-Zeitzone<input v-model="profile.timezone" required /></label>
        <label class="field">Wochenbeginn<select v-model.number="profile.week_starts_on"><option :value="0">Montag</option><option :value="6">Sonntag</option></select></label>
        <label class="field">Gewichtseinheit<select v-model="profile.preferred_weight_unit"><option value="kg">Kilogramm</option><option value="lb">Pfund</option></select></label>
        <label class="field">JSON-Rohimporte aufbewahren (Tage)<input v-model.number="profile.raw_payload_retention_days" type="number" min="0" max="3650" /><small>0 deaktiviert die Speicherung vollständig. Große XML-/ZIP-Dateien werden nicht zusätzlich dupliziert.</small></label>
        <button class="button" type="submit">Profil speichern</button>
      </form>
    </section>
    <section class="card form-card">
      <h2>Neue Zielversion</h2>
      <form class="form-grid" @submit.prevent="saveTarget">
        <label class="field">Gültig ab<input v-model="target.valid_from" type="date" required /></label>
        <label class="field">Kalorienziel<input v-model.number="target.calories_kcal" type="number" min="1" required /></label>
        <label class="field">Eiweißziel (g)<input v-model.number="target.protein_g" type="number" min="0" required /></label>
        <label class="field">Kohlenhydrate (g)<input v-model.number="target.carbs_g" type="number" min="0" /></label>
        <label class="field">Fett (g)<input v-model.number="target.fat_g" type="number" min="0" /></label>
        <label class="field">Wasser (ml)<input v-model.number="target.water_ml" type="number" min="0" /></label>
        <button class="button" type="submit">Zielversion anlegen</button>
      </form>
    </section>
  </div>
  <section class="card table-card"><h2 style="padding: 1rem 1rem 0">Zielhistorie</h2><div class="table-scroll"><table><thead><tr><th>Gültig ab</th><th>Gültig bis</th><th class="number">Kalorien</th><th class="number">Eiweiß</th></tr></thead><tbody><tr v-for="item in targets" :key="item.id"><td>{{ item.valid_from }}</td><td>{{ item.valid_to ?? 'offen' }}</td><td class="number">{{ item.calories_kcal }} kcal</td><td class="number">{{ item.protein_g }} g</td></tr></tbody></table></div></section>
  <section class="card form-card" style="margin-top: 1rem">
    <h2>Tracking-Vollständigkeit</h2>
    <form class="form-grid" @submit.prevent="saveQuality">
      <label class="field">Kalorien: volle Punkte ab<input v-model.number="quality.calories_full_ratio" type="number" min="0.01" max="2" step="0.01" /></label>
      <label class="field">Kalorien: Teilpunkte ab<input v-model.number="quality.calories_partial_ratio" type="number" min="0.01" max="2" step="0.01" /></label>
      <label class="field">Median: volle Punkte ab<input v-model.number="quality.median_full_ratio" type="number" min="0.01" max="2" step="0.01" /></label>
      <label class="field">Median: Teilpunkte ab<input v-model.number="quality.median_partial_ratio" type="number" min="0.01" max="2" step="0.01" /></label>
      <label class="field">Vollständig ab Punkten<input v-model.number="quality.complete_score" type="number" min="1" max="8" /></label>
      <label class="field">Wahrscheinlich vollständig ab<input v-model.number="quality.probably_complete_score" type="number" min="1" max="8" /></label>
      <label class="field">Wahrscheinlich unvollständig ab<input v-model.number="quality.probably_incomplete_score" type="number" min="1" max="8" /></label>
      <button class="button" type="submit">Regeln speichern</button>
    </form>
  </section>
  <section class="card form-card" style="margin-top: 1rem">
    <h2>Import-Tokens</h2>
    <p>Tokens werden nur einmal angezeigt und danach ausschließlich gehasht gespeichert.</p>
    <div class="filters"><label class="field">Bezeichnung<input v-model="tokenLabel" /></label><button class="button" type="button" @click="createToken">Token erzeugen</button></div>
    <div v-if="newToken" class="card" style="padding: 1rem; margin-top: 1rem"><strong>Jetzt sicher kopieren:</strong><code style="display: block; overflow-wrap: anywhere; margin-top: .5rem">{{ newToken }}</code></div>
    <div class="table-scroll"><table><thead><tr><th>Bezeichnung</th><th>Präfix</th><th>Letzte Verwendung</th><th></th></tr></thead><tbody><tr v-for="token in tokens" :key="token.id"><td>{{ token.label }}</td><td><code>{{ token.token_prefix }}…</code></td><td>{{ token.last_used_at ? new Date(token.last_used_at).toLocaleString('de-DE') : 'nie' }}</td><td><button v-if="!token.revoked_at" class="text-button" type="button" @click="revokeToken(token.id)">Widerrufen</button><span v-else>Widerrufen</span></td></tr></tbody></table></div>
  </section>
</template>
