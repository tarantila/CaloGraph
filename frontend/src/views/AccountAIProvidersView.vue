<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, ApiTransportError } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import { useAuthStore } from '../stores/auth'
import AIConsentSettingsCard from '../components/AIConsentSettingsCard.vue'
import AIRoutingSettingsCard from '../components/AIRoutingSettingsCard.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { CONNECTION_PRIVACY_VERSION } from '../generated/ai-contracts'
import type { AISettingsView, ConnectionCheckView, ProviderCatalog, ProviderConnectionPage, ProviderConnectionPut, ProviderConnectionView, ProviderKind } from '../generated/ai-contracts'
import { i18n } from '../i18n'
import { clientUuid } from '../uuid'

const t = i18n.global.t.bind(i18n.global)
const identity = captureAuthIdentity()
const auth = useAuthStore()
let disposed = false
function alive(): boolean { return !disposed && !sessionChanged.value && isAuthIdentityCurrent(identity) }
const sessionChanged = ref(false)
watch(() => auth.user?.id ?? null, value => {
  if (value === identity.userId) return
  sessionChanged.value = true
  apiKey.value = ''
  connections.value = []
  checks.value = {}
  catalog.value = null
  loading.value = false
}, { flush: 'sync' })
const catalog = ref<ProviderCatalog | null>(null)
const connections = ref<ProviderConnectionView[]>([])
const checks = ref<Record<string, ConnectionCheckView | null>>({})
const activeId = ref<string | null>(null)
const loading = ref(true)
const busy = ref(false)
const routingBusy = ref(false)
const consentBusy = ref(false)
const enabledConnectionIds = ref<string[]>([])
const loadError = ref('')
const error = ref('')
const message = ref('')
const provider = ref<ProviderKind>('openai_api')
const editingId = ref<string | null>(null)
const baseUrl = ref('')
const keyMode = ref<'keep' | 'replace' | 'remove'>('replace')
const apiKey = ref('')
const modelChoice = ref('')
const manualModel = ref('')
const models = ref<string[]>([])
const consent = ref(false)
const accessResult = ref<ConnectionCheckView | null>(null)
const accessSaved = ref(false)
const formHeading = ref<HTMLElement | null>(null)
const deleteTarget = ref<ProviderConnectionView | null>(null)
const deleteError = ref('')
let connectionId = clientUuid()
let pendingCheck: { id: string; connectionId: string; version: number } | null = null
let hydrating = false
let feedbackTimer: ReturnType<typeof setTimeout> | undefined

const current = computed(() => connections.value.find(item => item.id === editingId.value) ?? null)
const controlsBusy = computed(() => busy.value || routingBusy.value || consentBusy.value)
const customEndpoint = computed(() => provider.value === 'local' || provider.value === 'gateway')
const nativeKeyRequired = computed(() => ['openai_api', 'anthropic', 'gemini'].includes(provider.value))
const hasKey = computed(() => keyMode.value === 'replace' ? Boolean(apiKey.value.trim()) : keyMode.value === 'keep' && Boolean(current.value?.has_credential))
const hasAccess = computed(() => provider.value !== 'chatgpt_plan' && (!nativeKeyRequired.value || hasKey.value) && (!customEndpoint.value || Boolean(baseUrl.value.trim())))
const privacySupported = computed(() => catalog.value?.privacy_version === CONNECTION_PRIVACY_VERSION)
const chosenModel = computed(() => modelChoice.value === '__manual__' ? manualModel.value.trim() : modelChoice.value)
const modelOptions = computed(() => Array.from(new Set([...models.value, ...(current.value?.model_id ? [current.value.model_id] : [])])))
const endpointNeedsKey = computed(() => Boolean(customEndpoint.value && current.value?.has_credential && current.value.base_url !== baseUrl.value.trim() && keyMode.value === 'keep'))
const availableProviders = computed(() => catalog.value?.providers.filter(item => item.availability === 'available' && item.kind !== 'chatgpt_plan') ?? [])
const chatgptPlanned = computed(() => catalog.value?.providers.some(item => item.kind === 'chatgpt_plan' && item.availability === 'planned') ?? false)

function providerLabel(kind: ProviderKind): string { return t(`accountAISetup.providers.${kind}`) }
function reloadSession(): void { window.location.reload() }
function showMessage(value: string): void {
  if (feedbackTimer) clearTimeout(feedbackTimer)
  message.value = value
  feedbackTimer = setTimeout(() => { message.value = ''; feedbackTimer = undefined }, 4_000)
}
function currentCheck(item: ProviderConnectionView): ConnectionCheckView | null {
  const check = checks.value[item.id]
  return check?.configuration_version === item.configuration_version ? check : null
}
function checkLabel(status: ConnectionCheckView['status']): string { return t(`accountAISetup.results.${status}`) }
function resetAccess(): void { consent.value = false; accessResult.value = null; models.value = []; pendingCheck = null }
watch([baseUrl, apiKey, keyMode], () => { if (!hydrating) resetAccess() }, { flush: 'sync' })

function edit(item: ProviderConnectionView | null, kind: ProviderKind = provider.value): void {
  hydrating = true
  provider.value = item?.provider_kind ?? kind
  editingId.value = item?.id ?? null
  connectionId = item?.id ?? clientUuid()
  baseUrl.value = item?.base_url ?? ''
  apiKey.value = ''
  keyMode.value = item?.has_credential ? 'keep' : 'replace'
  modelChoice.value = item?.model_id ?? ''
  manualModel.value = ''
  accessSaved.value = Boolean(item && !item.model_id)
  resetAccess()
  const check = item ? currentCheck(item) : null
  accessResult.value = check
  models.value = check ? [...check.models] : []
  error.value = ''; message.value = ''
  hydrating = false
}
function chooseProvider(): void { edit(connections.value.find(item => item.provider_kind === provider.value) ?? null) }
function configure(item: ProviderConnectionView): void {
  edit(item)
  void nextTick(() => { formHeading.value?.focus(); formHeading.value?.scrollIntoView({ block: 'start', behavior: 'smooth' }) })
}
async function load(): Promise<void> {
  if (!alive()) return
  loading.value = true; loadError.value = ''
  try {
    const [providers, page, preferences] = await Promise.all([
      api<ProviderCatalog>('/settings/ai/providers'), api<ProviderConnectionPage>('/settings/ai/connections'), api<AISettingsView>('/settings/ai/preferences'),
    ])
    if (!alive()) return
    catalog.value = providers; connections.value = [...page.items]
    activeId.value = preferences.active_connection_id
    const results = await Promise.all(page.items.map(async item => {
      if (!alive()) return [item.id, null] as const
      try { return [item.id, await api<ConnectionCheckView | null>(`/settings/ai/connections/${item.id}/checks/latest`)] as const }
      catch { return [item.id, null] as const }
    }))
    if (!alive()) return
    checks.value = Object.fromEntries(results)
    edit(connections.value.find(item => item.id === activeId.value) ?? connections.value[0] ?? null)
  } catch { if (alive()) loadError.value = t('accountAISetup.loadFailed') }
  finally { if (alive()) loading.value = false }
}
function applyConnection(saved: ProviderConnectionView): void {
  connections.value = connections.value.some(item => item.id === saved.id) ? connections.value.map(item => item.id === saved.id ? saved : item) : [...connections.value, saved]
  editingId.value = saved.id
  if (!saved.enabled && activeId.value === saved.id) activeId.value = null
  hydrating = true
  apiKey.value = ''; keyMode.value = saved.has_credential ? 'keep' : 'replace'
  hydrating = false
}
async function persist(discovery: boolean): Promise<ProviderConnectionView> {
  if (!alive()) throw new Error('stale session')
  const kind = provider.value
  if (kind === 'chatgpt_plan' || endpointNeedsKey.value) throw new Error('invalid setup')
  const model = discovery ? current.value?.model_id ?? null : chosenModel.value || null
  const credentialUnchanged = keyMode.value === 'keep' || (!current.value?.has_credential && keyMode.value === 'replace' && !apiKey.value)
  const accessUnchanged = credentialUnchanged && (!customEndpoint.value || baseUrl.value.trim() === current.value?.base_url)
  const payload: ProviderConnectionPut = {
    label: current.value?.label ?? providerLabel(kind), provider_kind: kind,
    model_id: model, base_url: customEndpoint.value ? baseUrl.value.trim() : null,
    enabled: discovery ? Boolean(current.value?.enabled && model && accessUnchanged) : Boolean(model && (!nativeKeyRequired.value || hasKey.value)),
    timeout_seconds: current.value?.timeout_seconds ?? 30,
  }
  if (keyMode.value === 'replace' && apiKey.value) payload.api_key = apiKey.value
  else if (keyMode.value === 'remove') payload.api_key = null
  if (!alive()) throw new Error('stale session')
  const saved = await api<ProviderConnectionView>(`/settings/ai/connections/${connectionId}`, { method: 'PUT', body: JSON.stringify(payload) })
  if (!alive()) throw new Error('stale session')
  applyConnection(saved)
  return saved
}
async function loadModels(): Promise<void> {
  if (!alive()) return
  if (controlsBusy.value || !hasAccess.value || !consent.value || !privacySupported.value) return
  if (endpointNeedsKey.value) { error.value = t('accountAISetup.destinationKey'); return }
  busy.value = true; error.value = ''; message.value = ''
  try {
    const saved = await persist(true)
    if (!alive()) return
    accessSaved.value = true
    if (!pendingCheck || pendingCheck.connectionId !== saved.id || pendingCheck.version !== saved.configuration_version) pendingCheck = { id: clientUuid(), connectionId: saved.id, version: saved.configuration_version }
    const result = await api<ConnectionCheckView>(`/settings/ai/connections/${saved.id}/checks/${pendingCheck.id}`, {
      method: 'PUT', body: JSON.stringify({ configuration_version: saved.configuration_version, privacy_version: CONNECTION_PRIVACY_VERSION }),
    })
    if (!alive()) return
    checks.value = { ...checks.value, [saved.id]: result }; accessResult.value = result; models.value = [...result.models]
    pendingCheck = null; consent.value = false
    if (chosenModel.value && ['available', 'model_not_listed', 'manual_selection'].includes(result.status)) await saveModel(true)
  } catch (cause) {
    if (!(cause instanceof ApiTransportError)) pendingCheck = null
    if (alive()) error.value = t('accountAISetup.connectFailed')
  } finally { busy.value = false }
}
async function saveModel(afterSuccessfulCheck = false): Promise<void> {
  if (!alive() || (!afterSuccessfulCheck && controlsBusy.value) || !chosenModel.value || endpointNeedsKey.value) return
  // Model changes must not silently submit an API key still being edited.
  if (keyMode.value === 'remove' || (keyMode.value === 'replace' && apiKey.value.trim())) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const id = current.value?.id ?? connectionId
    const payload: ProviderConnectionPut = {
      label: current.value?.label ?? providerLabel(provider.value),
      provider_kind: provider.value as ProviderConnectionPut['provider_kind'],
      model_id: chosenModel.value,
      base_url: customEndpoint.value ? baseUrl.value.trim() : null,
      enabled: Boolean((!nativeKeyRequired.value || current.value?.has_credential) && chosenModel.value),
      timeout_seconds: current.value?.timeout_seconds ?? 30,
    }
    if (!alive()) return
    const saved = await api<ProviderConnectionView>(`/settings/ai/connections/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
    if (!alive()) return
    applyConnection(saved)
    showMessage(t('accountAISetup.saved'))
  } catch { if (alive()) error.value = t('accountAISetup.saveFailed') }
  finally { if (alive()) busy.value = false }
}
async function removeKey(): Promise<void> {
  if (!alive() || !current.value || controlsBusy.value || !current.value.has_credential) return
  busy.value = true; error.value = ''; message.value = ''
  try {
    const item = current.value
    const payload: ProviderConnectionPut = {
      label: item.label, provider_kind: item.provider_kind as ProviderConnectionPut['provider_kind'],
      model_id: item.model_id, base_url: item.base_url, enabled: false, timeout_seconds: item.timeout_seconds, api_key: null,
    }
    if (!alive()) return
    const saved = await api<ProviderConnectionView>(`/settings/ai/connections/${item.id}`, { method: 'PUT', body: JSON.stringify(payload) })
    if (!alive()) return
    applyConnection(saved)
    showMessage(t('accountAISetup.keyRemoved'))
  } catch { if (alive()) error.value = t('accountAISetup.saveFailed') }
  finally { if (alive()) busy.value = false }
}
async function remove(): Promise<void> {
  if (!alive()) return
  const target = deleteTarget.value
  if (!target || controlsBusy.value) return
  busy.value = true; deleteError.value = ''
  try {
    if (!alive()) return
    await api<void>(`/settings/ai/connections/${target.id}`, { method: 'DELETE' })
    if (!alive()) return
    connections.value = connections.value.filter(item => item.id !== target.id)
    if (activeId.value === target.id) activeId.value = null
    if (editingId.value === target.id) edit(null, target.provider_kind)
    deleteTarget.value = null
  } catch { if (alive()) deleteError.value = t('accountAISetup.saveFailed') }
  finally { busy.value = false }
}
onMounted(() => { void load() })
onBeforeUnmount(() => { disposed = true; apiKey.value = ''; if (feedbackTimer) clearTimeout(feedbackTimer) })
</script>

<template>
  <section class="account-page-stack ai-provider-settings">
    <header class="page-heading"><div><h1>{{ t('accountAIProviders.title') }}</h1><p>{{ t('accountAISetup.description') }}</p></div></header>
    <aside class="card form-card setup-notice privacy-notice" :aria-label="t('accountNav.dataPrivacy')">
      <p class="help">{{ t('accountAIProviders.privacyTitle') }}. {{ t('accountAISetup.privacy') }}</p>
    </aside>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <div v-else-if="sessionChanged" class="card form-card" role="alert"><p>{{ t('accountAIProviders.sessionChanged') }}</p><button class="button secondary" type="button" @click="reloadSession">{{ t('accountAIProviders.reloadSession') }}</button></div>
    <div v-else-if="loadError" class="card form-card" role="alert"><p>{{ loadError }}</p><button class="button" @click="load">{{ t('common.tryAgain') }}</button></div>
    <template v-else>
      <section class="card form-card" aria-labelledby="ai-setup-heading">
        <h2 id="ai-setup-heading" ref="formHeading" tabindex="-1">{{ t('accountAISetup.setupTitle') }}</h2>
        <label class="field provider-picker"><span>{{ t('accountAISetup.provider') }}</span><select v-model="provider" name="ai-provider" :disabled="controlsBusy" @change="chooseProvider"><option v-for="item in availableProviders" :key="item.kind" :value="item.kind">{{ providerLabel(item.kind) }}</option></select></label>
        <aside v-if="chatgptPlanned" class="provider-notice"><strong>{{ t('accountAISetup.chatgpt') }}</strong></aside>
        <form class="ai-setup-form" @submit.prevent>
          <fieldset :disabled="controlsBusy">
            <label v-if="customEndpoint" class="field"><span>{{ t('accountAISetup.endpoint') }}</span><input v-model="baseUrl" name="ai-endpoint" type="url" required maxlength="2048" placeholder="http://dein-server:11434/v1" /></label>
            <p v-if="customEndpoint" class="help">{{ t('accountAISetup.endpointHelp') }}</p>
            <div class="ai-access">
              <label v-if="keyMode === 'replace'" class="field"><span>{{ t('accountAISetup.apiKey') }}{{ customEndpoint ? ' ' + t('accountAISetup.optional') : '' }}</span><input v-model="apiKey" name="ai-key" type="password" autocomplete="new-password" maxlength="8192" :placeholder="t('accountAISetup.keyPlaceholder')" /></label>
              <div v-else class="key-state"><span>{{ t(keyMode === 'keep' ? 'accountAISetup.keyStored' : 'accountAISetup.keyRemoval') }}</span><div class="button-row"><button v-if="keyMode === 'keep'" class="button secondary compact-action" type="button" @click="keyMode = 'replace'">{{ t('accountAISetup.changeKey') }}</button><button v-if="keyMode === 'keep'" class="button secondary compact-action" type="button" @click="keyMode = 'remove'">{{ t('accountAISetup.removeKey') }}</button><button v-if="keyMode === 'remove'" class="button secondary compact-action" type="button" @click="keyMode = 'keep'">{{ t('common.cancel') }}</button></div></div>
              <button v-if="keyMode === 'replace' && current?.has_credential" class="text-action" type="button" @click="apiKey = ''; keyMode = 'keep'">{{ t('accountAISetup.keepKey') }}</button>
            </div>
            <p v-if="endpointNeedsKey" class="help warning">{{ t('accountAISetup.destinationKey') }}</p>
            <div class="connect-step">
              <label class="checkbox-row"><input v-model="consent" name="ai-test-consent" type="checkbox" :disabled="!privacySupported" /><span>{{ t('accountAISetup.consent') }}</span></label>
              <p class="help">{{ t('accountAISetup.connectHelp') }}</p>
              <p v-if="customEndpoint && baseUrl.toLowerCase().startsWith('http://') && hasKey" class="help warning">{{ t('accountAIProviders.httpKeyWarning') }}</p>
              <p v-if="!privacySupported" class="help warning">{{ t('accountAIProviders.privacyVersionUnsupported') }}</p>
              <button class="button secondary compact-action" type="button" :disabled="!hasAccess || !consent || !privacySupported" @click="loadModels">{{ busy ? t('accountAISetup.connecting') : t('accountAISetup.connect') }}</button>
            </div>
            <div v-if="accessResult" class="access-result" role="status"><p>{{ checkLabel(accessResult.status) }}</p><p v-if="accessResult.models_truncated" class="help">{{ t('accountAISetup.truncated') }}</p></div>
            <p v-else-if="accessSaved" class="help" role="status">{{ t('accountAISetup.draftSaved') }}</p>
            <label class="field"><span>{{ t('accountAISetup.model') }}</span><select v-model="modelChoice" name="ai-model" :disabled="!hasAccess && !current?.model_id" @change="saveModel()"><option value="">{{ modelOptions.length ? t('accountAISetup.chooseModel') : t('accountAISetup.loadFirst') }}</option><option v-for="model in modelOptions" :key="model" :value="model">{{ model }}</option><option value="__manual__">{{ t('accountAISetup.otherModel') }}</option></select></label>
            <label v-if="modelChoice === '__manual__'" class="field"><span>{{ t('accountAISetup.manualModel') }}</span><input v-model="manualModel" name="ai-manual-model" maxlength="200" required @change="saveModel()" /></label>
            <p v-if="modelChoice === '__manual__'" class="help">{{ t('accountAISetup.manualHelp') }}</p>
            <button v-if="keyMode === 'remove'" class="button secondary compact-action" type="button" :disabled="!current?.has_credential" @click="removeKey">{{ t('accountAISetup.removeKeyNow') }}</button>
            <p v-if="error" class="account-form-error" role="alert">{{ error }}</p>
            <p v-if="message" class="account-form-success" role="status">{{ message }}</p>
          </fieldset>
        </form>
      </section>
      <AIRoutingSettingsCard v-if="connections.length" :connections="connections" :paused="busy || consentBusy" @busy="routingBusy = $event" @changed="activeId = $event.primary_connection_id; enabledConnectionIds = $event.entries.filter(item => item.enabled).map(item => item.connection_id)" @configure="configure" @remove="deleteTarget = $event" />
      <AIConsentSettingsCard v-if="connections.length" :connections="connections" :paused="busy || routingBusy" :enabled-connection-ids="enabledConnectionIds" @busy="consentBusy = $event" />
    </template>
    <ConfirmDialog v-if="deleteTarget" :open="true" :title="t('accountAIProviders.deleteTitle')" :description="t('accountAIProviders.deleteDescription')" :confirm-label="t('accountAIProviders.deleteConfirm')" danger :pending="busy" :error="deleteError" @confirm="remove" @close="deleteTarget = null" />
  </section>
</template>

<style scoped>
.ai-provider-settings { gap: 1rem; }
.page-heading { margin-bottom: 0; }
.form-card { width: 100%; }
.setup-notice { border-inline-start: .25rem solid var(--accent); }
.setup-notice p { margin: 0; }
.privacy-notice { padding-block: 1rem; }
.provider-notice { padding: 1rem; background: var(--surface-soft); border-radius: .6rem; }
.provider-picker { max-width: 30rem; margin-block: 1rem; }
.ai-setup-form fieldset { display: grid; gap: 1rem; min-width: 0; border: 0; padding: 0; margin: 0; }
.field { min-width: 0; }
.field input, .field select { width: 100%; min-width: 0; }
.ai-access { display: grid; gap: .5rem; }
.key-state { padding: .75rem; border: 1px solid var(--border); border-radius: .55rem; font-size: .82rem; }
.checkbox-row { display: flex; align-items: flex-start; gap: .6rem; font-size: .82rem; line-height: 1.5; }
.checkbox-row input { flex: 0 0 auto; margin-top: .25rem; }
.connect-step { display: grid; gap: .65rem; padding: .9rem; background: var(--surface-soft); border-radius: .6rem; }
.connect-step > button { justify-self: start; }
.button-row { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.key-state .button-row { margin-top: .6rem; }
.text-action { justify-self: start; color: var(--accent); font-size: .8rem; text-decoration: underline; }
.help { color: var(--muted-strong); font-size: .8rem; line-height: 1.55; margin: 0; }
.warning { color: var(--warning); }
.access-result { border-inline-start: 3px solid var(--accent); padding-inline-start: .75rem; }
.access-result p { margin: 0; }
@media (max-width: 600px) { .connect-step > button { width: 100%; } }
</style>
