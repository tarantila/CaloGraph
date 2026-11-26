<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import { useAuthStore } from '../stores/auth'
import type { AIConsentPage, ExpertPreferencesPut, ExpertPreferencesView, ExpertSetup } from '../generated/ai-contracts'
import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)
type Domain = ExpertPreferencesPut['data_domains'][number]
const domains: Domain[] = ['nutrition', 'targets', 'weight', 'activity', 'profile', 'health_context']
const selected = ref<Domain[]>([])
const mealNames = ref(false)
const loading = ref(true)
const saving = ref(false)
const revoking = ref(false)
const settingsError = ref('')
const saveMessage = ref('')
const permissionState = ref<'loading' | 'available' | 'none' | 'error'>('loading')
const permissionError = ref('')
const permissionMessage = ref('')
const setup = ref<ExpertSetup | null>(null)
const failedPreferences = ref<ExpertPreferencesPut | null>(null)
const savedMessage = ref(false)
const showSavingMessage = ref(false)
const hasNutrition = computed(() => selected.value.includes('nutrition'))
const hasOnlyOneDomain = (domain: Domain) => selected.value.length === 1 && selected.value[0] === domain
const saveFeedback = computed(() => saving.value
  ? (showSavingMessage.value ? t('expertSettings.saving') : '')
  : saveMessage.value)
let persisted: ExpertPreferencesPut = { data_domains: [], include_meal_names: false }
let revision = 0
let saveLoop: Promise<void> | null = null
let savedMessageTimer: ReturnType<typeof setTimeout> | undefined
let savingMessageTimer: ReturnType<typeof setTimeout> | undefined
let permissionGeneration = 0

function clearSavingMessageDelay(): void {
  if (savingMessageTimer) clearTimeout(savingMessageTimer)
  savingMessageTimer = undefined
  showSavingMessage.value = false
}

function startSavingMessageDelay(): void {
  clearSavingMessageDelay()
  savingMessageTimer = setTimeout(() => {
    savingMessageTimer = undefined
    if (alive() && saving.value) showSavingMessage.value = true
  }, 300)
}

function clearSavedMessage(): void {
  if (savedMessageTimer) clearTimeout(savedMessageTimer)
  savedMessageTimer = undefined
  savedMessage.value = false
}

function showSavedMessage(): void {
  clearSavingMessageDelay()
  clearSavedMessage()
  savedMessage.value = true
  saveMessage.value = t('expertSettings.saved')
  savedMessageTimer = setTimeout(() => {
    savedMessageTimer = undefined
    savedMessage.value = false
    saveMessage.value = ''
  }, 4_000)
}

function permissionRequestIsCurrent(generation: number, connectionId: string): boolean {
  return alive() && permissionGeneration === generation && setup.value?.connection?.id === connectionId
}

async function loadPermissions(connectionId: string): Promise<void> {
  const generation = ++permissionGeneration
  permissionState.value = 'loading'
  permissionError.value = ''
  permissionMessage.value = ''
  try {
    let after: string | null = null
    do {
      const query = new URLSearchParams({ connection_id: connectionId, limit: '50' })
      if (after) query.set('after', after)
      const page = await api<AIConsentPage>(`/settings/ai/consents?${query.toString()}`)
      if (!permissionRequestIsCurrent(generation, connectionId)) return
      if (page.items.some(consent => consent.connection_id === connectionId && consent.revoked_at === null)) {
        permissionState.value = 'available'
        return
      }
      after = page.next_after
    } while (after)
    if (permissionRequestIsCurrent(generation, connectionId)) permissionState.value = 'none'
  } catch {
    if (permissionRequestIsCurrent(generation, connectionId)) {
      permissionState.value = 'error'
      permissionError.value = t('expertSettings.permissionLoadFailed')
    }
  }
}

async function readAllPermissions(connectionId: string, generation: number): Promise<AIConsentPage['items'][number][]> {
  let after: string | null = null
  const consents: AIConsentPage['items'][number][] = []
  const seenCursors = new Set<string>()
  do {
    if (!permissionRequestIsCurrent(generation, connectionId)) throw new Error('Permission request is no longer current')
    const query = new URLSearchParams({ connection_id: connectionId, limit: '50' })
    if (after) query.set('after', after)
    const page = await api<AIConsentPage>(`/settings/ai/consents?${query.toString()}`)
    if (!permissionRequestIsCurrent(generation, connectionId)) throw new Error('Permission request is no longer current')
    consents.push(...page.items.filter(consent => consent.connection_id === connectionId))
    after = page.next_after
    if (after && seenCursors.has(after)) throw new Error('Permission pagination did not advance')
    if (after) seenCursors.add(after)
  } while (after)
  return consents
}

function retryPermissionLoad(): void {
  if (!alive()) return
  const connectionId = setup.value?.connection?.id
  if (connectionId) void loadPermissions(connectionId)
}

const auth = useAuthStore()
const ownerIdentity = captureAuthIdentity()
const sessionChanged = ref(false)
let disposed = false
function alive(): boolean {
  if (disposed) return false
  if (isAuthIdentityCurrent(ownerIdentity)) return true
  clearSavedMessage()
  clearSavingMessageDelay()
  saveMessage.value = ''
  sessionChanged.value = true
  selected.value = []; mealNames.value = false; setup.value = null; failedPreferences.value = null
  return false
}
watch(() => auth.user?.id ?? null, () => { alive() }, { flush: 'sync' })
function reloadSession(): void { window.location.reload() }
onBeforeUnmount(() => { disposed = true; clearSavedMessage(); clearSavingMessageDelay() })

function preferences(): ExpertPreferencesPut {
  return {
    data_domains: domains.filter(domain => selected.value.includes(domain)),
    include_meal_names: hasNutrition.value && mealNames.value,
  }
}
function applyPreferences(value: ExpertPreferencesPut): void {
  selected.value = [...value.data_domains]
  mealNames.value = value.include_meal_names
}
function samePreferences(left: ExpertPreferencesPut, right: ExpertPreferencesPut): boolean {
  return left.include_meal_names === right.include_meal_names
    && domains.every(domain => left.data_domains.includes(domain) === right.data_domains.includes(domain))
}

async function load(): Promise<void> {
  if (!alive()) return
  loading.value = true
  settingsError.value = ''
  try {
    const state = await api<ExpertSetup>('/expert/setup')
    if (!alive()) return
    setup.value = state
    persisted = { data_domains: [...state.preferences.data_domains], include_meal_names: state.preferences.include_meal_names }
    applyPreferences(persisted)
    permissionGeneration += 1
    if (state.connection) await loadPermissions(state.connection.id)
  } catch {
    if (alive()) settingsError.value = t('expertSettings.loadFailed')
  } finally {
    if (alive()) loading.value = false
  }
}

function scheduleSave(): void {
  if (!alive()) return
  revision += 1
  clearSavedMessage()
  settingsError.value = ''
  saveMessage.value = ''
  failedPreferences.value = null
  if (!saveLoop) {
    saveLoop = persistLatest().finally(() => {
      saveLoop = null
      if (alive() && !failedPreferences.value && !samePreferences(preferences(), persisted)) scheduleSave()
    })
  }
}

async function persistLatest(): Promise<void> {
  saving.value = true
  startSavingMessageDelay()
  try {
    while (alive()) {
      const submittedRevision = revision
      const payload = preferences()
      let saved: ExpertPreferencesView
      try {
        saved = await api<ExpertPreferencesView>('/settings/expert', { method: 'PUT', body: JSON.stringify(payload) })
      } catch {
        if (!alive()) return
        if (revision !== submittedRevision) continue
        failedPreferences.value = payload
        applyPreferences(persisted)
        settingsError.value = t('expertSettings.saveFailed')
        return
      }
      if (!alive()) return
      persisted = { data_domains: [...saved.data_domains], include_meal_names: saved.include_meal_names }
      if (revision === submittedRevision) {
        applyPreferences(persisted)
        showSavedMessage()
        return
      }
      const latest = preferences()
      if (samePreferences(latest, persisted)) {
        applyPreferences(persisted)
        showSavedMessage()
        return
      }
    }
  } finally {
    clearSavingMessageDelay()
    if (alive()) saving.value = false
  }
}

function retrySave(): void {
  if (!alive()) return
  const retry = failedPreferences.value
  if (!retry) return
  applyPreferences(retry)
  failedPreferences.value = null
  scheduleSave()
}

function toggleDomain(domain: Domain, event: Event): void {
  if (!alive()) return
  const checked = (event.target as HTMLInputElement).checked
  if (!checked && hasOnlyOneDomain(domain)) return
  selected.value = checked
    ? [...selected.value, domain]
    : selected.value.filter(item => item !== domain)
  if (!checked && domain === 'nutrition') mealNames.value = false
  scheduleSave()
}

function toggleMealNames(event: Event): void {
  if (!alive()) return
  mealNames.value = (event.target as HTMLInputElement).checked
  scheduleSave()
}

async function revoke(): Promise<void> {
  if (!alive()) return
  const connection = setup.value?.connection
  if (!connection || permissionState.value !== 'available' || revoking.value) return
  revoking.value = true
  permissionError.value = ''
  permissionMessage.value = ''
  const connectionId = connection.id
  const generation = ++permissionGeneration
  try {
    const consents = await readAllPermissions(connectionId, generation)
    for (const consent of consents) {
      if (consent.revoked_at !== null) continue
      if (!permissionRequestIsCurrent(generation, connectionId)) return
      try {
        await api<void>(`/settings/ai/consents/${consent.id}`, { method: 'DELETE' })
      } catch { /* Verify persisted state below; individual failures must not stop later revocations. */ }
    }
    const remaining = await readAllPermissions(connectionId, generation)
    if (!permissionRequestIsCurrent(generation, connectionId)) return
    if (remaining.some(consent => consent.revoked_at === null)) {
      permissionState.value = 'available'
      permissionError.value = t('expertSettings.revokeFailed')
      return
    }
    permissionState.value = 'none'
    permissionMessage.value = t('expertSettings.revoked')
  } catch {
    if (permissionRequestIsCurrent(generation, connectionId)) {
      permissionState.value = 'error'
      permissionError.value = t('expertSettings.revokeFailed')
    }
  } finally {
    if (permissionRequestIsCurrent(generation, connectionId)) revoking.value = false
  }
}
onMounted(() => { void load() })
</script>

<template>
  <section class="account-page-stack expert-settings">
    <header class="page-heading"><div><h1>{{ t('expertSettings.title') }}</h1><p>{{ t('expertSettings.description') }}</p></div></header>
    <aside class="card form-card expert-settings-notice"><p>{{ t('expertSettings.history') }}</p></aside>
    <p v-if="sessionChanged" role="alert">{{ t('expertSettings.sessionChanged') }} <button class="button secondary" @click="reloadSession">{{ t('expertSettings.reloadSession') }}</button></p>
    <p v-else-if="loading" role="status">{{ t('common.loading') }}</p>
    <p v-else-if="!setup" role="alert" class="account-form-error">{{ settingsError }} <button class="text-action" type="button" @click="load">{{ t('common.tryAgain') }}</button></p>
    <template v-else>
      <section class="card form-card" aria-labelledby="expert-domain-settings-heading">
        <h2 id="expert-domain-settings-heading">{{ t('expertSettings.domainsTitle') }}</h2>
        <div class="expert-domain-list">
          <label v-for="domain in domains" :key="domain" class="expert-setting-toggle">
            <span class="expert-setting-copy"><strong>{{ t(`expertSettings.domains.${domain}.label`) }}</strong><small>{{ t(`expertSettings.domains.${domain}.help`) }}</small></span>
            <input
              class="expert-switch"
              type="checkbox"
              role="switch"
              name="expert-domain"
              :value="domain"
              :aria-label="t(`expertSettings.domains.${domain}.label`)"
              :checked="selected.includes(domain)"
              :disabled="hasOnlyOneDomain(domain)"
              @change="toggleDomain(domain, $event)"
            />
          </label>
          <label class="expert-setting-toggle">
            <span class="expert-setting-copy"><strong>{{ t('expertSettings.mealNames') }}</strong><small>{{ t('expertSettings.mealHelp') }}</small></span>
            <input class="expert-switch" type="checkbox" role="switch" name="expert-meal-names" :aria-label="t('expertSettings.mealNames')" :checked="mealNames" :disabled="!hasNutrition" @change="toggleMealNames" />
          </label>
        </div>
        <p v-if="settingsError" class="account-form-error" role="alert">{{ settingsError }} <button v-if="failedPreferences" class="text-action" type="button" @click="retrySave">{{ t('expertSettings.retrySave') }}</button></p>
      </section>
      <section v-if="setup?.connection" class="card form-card expert-permission-settings" aria-labelledby="expert-permission-settings-heading">
        <h2 id="expert-permission-settings-heading">{{ t('expertSettings.permissionTitle') }}</h2>
        <p>{{ t('expertSettings.permissionHelp') }}</p>
        <p>{{ t(`accountAISetup.providers.${setup.connection.provider_kind}`) }} · {{ setup.connection.model_id }}</p>
        <p v-if="permissionState === 'loading'" role="status">{{ t('expertSettings.permissionLoading') }}</p>
        <p v-else-if="permissionState === 'none'" role="status" :class="{ 'permission-confirmation': permissionMessage }">{{ permissionMessage || t('expertSettings.noPermission') }}</p>
        <p v-else-if="permissionState === 'error'" class="account-form-error" role="alert">{{ permissionError }} <button class="text-action" type="button" @click="retryPermissionLoad">{{ t('common.tryAgain') }}</button></p>
        <p v-if="permissionState === 'available' && permissionError" class="account-form-error" role="alert">{{ permissionError }}</p>
        <button v-if="permissionState === 'available'" class="button secondary compact-action" type="button" :disabled="revoking" @click="revoke">{{ revoking ? t('expertSettings.revoking') : t('expertSettings.revoke') }}</button>
      </section>
    </template>
    <p v-if="saveFeedback" class="expert-save-toast" :class="{ 'is-saved': savedMessage }" role="status" aria-live="polite">{{ saveFeedback }}</p>
  </section>
</template>

<style scoped>
.expert-settings { gap: 1rem; }
.expert-permission-settings { display: grid; gap: .85rem; }
.expert-permission-settings > p { margin: 0; }
.page-heading { margin-bottom: 0; }
.expert-settings-notice { border-inline-start: .25rem solid var(--accent); padding-block: 1rem; }
.expert-settings-notice p { margin: 0; color: var(--muted-strong); font-size: .8rem; line-height: 1.55; }
.expert-domain-list { display: grid; margin-top: .5rem; }
.expert-setting-toggle { display: flex; align-items: center; justify-content: space-between; gap: 1rem; min-height: 3.5rem; padding-block: .65rem; border-top: 1px solid var(--border); cursor: pointer; }
.expert-setting-copy { display: grid; gap: .2rem; }
.expert-setting-copy strong { font-size: .85rem; font-weight: 600; }
.expert-setting-copy small { color: var(--muted-strong); font-size: .8rem; line-height: 1.5; }
.expert-switch { position: relative; flex: 0 0 auto; width: 2.5rem; height: 1.4rem; margin: 0; appearance: none; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-soft); cursor: pointer; transition: background-color .15s ease, border-color .15s ease; }
.expert-switch::after { position: absolute; top: .12rem; left: .12rem; width: 1.1rem; height: 1.1rem; border-radius: 50%; background: var(--surface); box-shadow: 0 1px 3px rgb(0 0 0 / .2); content: ''; transition: transform .15s ease; }
.expert-switch:checked { border-color: var(--accent); background: var(--accent); }
.expert-switch:checked::after { transform: translateX(1.08rem); }
.expert-switch:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent) 28%, transparent); outline-offset: 2px; }
.expert-switch:disabled { cursor: not-allowed; opacity: .55; }
.expert-save-toast { position: fixed; z-index: 20; right: 1rem; bottom: 1rem; max-width: min(28rem, calc(100vw - 2rem)); margin: 0; padding: .8rem 1rem; border: 1px solid var(--border); border-radius: .75rem; background: var(--surface-raised, var(--surface)); box-shadow: 0 8px 24px rgb(15 23 42 / .18); color: var(--muted-strong); font-size: .85rem; line-height: 1.4; }
.expert-save-toast.is-saved { color: var(--success); }
.permission-confirmation { color: var(--success); }
.text-action { color: var(--accent); font: inherit; text-decoration: underline; }
@media (max-width: 30rem) { .expert-save-toast { right: .75rem; bottom: .75rem; max-width: calc(100vw - 1.5rem); } }
@media (prefers-reduced-motion: reduce) { .expert-switch, .expert-switch::after { transition: none; } }
</style>
