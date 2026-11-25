<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import type { AIConsentPage, ProviderConnectionView } from '../generated/ai-contracts'
import { i18n } from '../i18n'
import AIConsentGrantDialog from './AIConsentGrantDialog.vue'

const props = defineProps<{ connections: readonly ProviderConnectionView[]; paused?: boolean; enabledConnectionIds?: readonly string[] }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const t = i18n.global.t.bind(i18n.global)
type RowState = 'loading' | 'active' | 'stored' | 'none' | 'error'
const states = ref<Record<string, RowState>>({})
const errors = ref<Record<string, string>>({})
const messages = ref<Record<string, string>>({})
const revoking = ref<Record<string, boolean>>({})
const activeReads = ref(0)
const grantBusy = ref(false)
const visibleConnections = computed(() => props.connections.filter(item => ['active', 'stored', 'error'].includes(states.value[item.id] ?? '') || props.enabledConnectionIds?.includes(item.id)))
const granting = ref<ProviderConnectionView | null>(null)
const sessionChanged = ref(false)
const identity = captureAuthIdentity()
const feedbackConnections = computed(() => props.connections.filter(item => Boolean(messages.value[item.id])))
const checking = computed(() => props.connections.some(item => states.value[item.id] === 'loading'))
const noActiveConsents = computed(() => props.connections.length > 0 && props.connections.every(item => states.value[item.id] === 'none') && Object.values(errors.value).every(value => !value) && Object.values(messages.value).every(value => !value))
const currentConnectionIds = computed(() => new Set(props.connections.map(item => item.id)))
const readTokens: Record<string, number> = {}
const revokeTokens: Record<string, number> = {}
const snapshots = new Map<string, string>()
const pendingReads = new Set<string>()
let disposed = false

function providerLabel(item: ProviderConnectionView): string {
  return t(`accountAISetup.providers.${item.provider_kind}`)
}

function isPresent(id: string): boolean {
  if (disposed) return false
  if (!isAuthIdentityCurrent(identity)) {
    sessionChanged.value = true
    granting.value = null
    states.value = {}; errors.value = {}; messages.value = {}
    return false
  }
  return currentConnectionIds.value.has(id)
}

function openGrant(id: string): void {
  if (!isPresent(id) || props.paused || revoking.value[id]) return
  const connection = props.connections.find(item => item.id === id)
  if (connection?.enabled && connection.model_id && props.enabledConnectionIds?.includes(id)) granting.value = connection
}
function reloadSession(): void { window.location.reload() }

type ConsentState = 'active' | 'stored' | 'none'
async function readConnectionGrants(id: string): Promise<AIConsentPage['items'][number][]> {
  let after: string | null = null
  const grants: AIConsentPage['items'][number][] = []
  do {
    if (!isPresent(id)) throw new Error('Consent settings session changed')
    const query = new URLSearchParams({ connection_id: id, limit: '50' })
    if (after) query.set('after', after)
    const page = await api<AIConsentPage>(`/settings/ai/consents?${query.toString()}`)
    if (!isPresent(id)) throw new Error('Consent settings session changed')
    grants.push(...page.items.filter(item => item.connection_id === id))
    after = page.next_after
  } while (after)
  return grants
}

function consentState(id: string, grants: readonly AIConsentPage['items'][number][]): ConsentState {
  const configured = props.connections.find(item => item.id === id)
  if (configured?.enabled && grants.some(item => item.revoked_at === null && item.state === 'active' && item.configuration_version === configured.configuration_version)) return 'active'
  return grants.some(item => item.revoked_at === null) ? 'stored' : 'none'
}

async function fetchConnectionState(id: string): Promise<ConsentState> {
  activeReads.value += 1
  try { return consentState(id, await readConnectionGrants(id)) }
  finally { activeReads.value -= 1 }
}

async function loadConnection(id: string): Promise<void> {
  if (!isPresent(id) || props.paused || revoking.value[id]) { pendingReads.add(id); return }
  const expected = readTokens[id] = (readTokens[id] ?? 0) + 1
  states.value = { ...states.value, [id]: 'loading' }
  errors.value = { ...errors.value, [id]: '' }
  messages.value = { ...messages.value, [id]: '' }
  try {
    const state = await fetchConnectionState(id)
    if (!isPresent(id) || readTokens[id] !== expected) return
    states.value = { ...states.value, [id]: state }
  } catch {
    if (isPresent(id) && readTokens[id] === expected) {
      states.value = { ...states.value, [id]: 'error' }
      errors.value = { ...errors.value, [id]: t('accountAIProviders.consentLoadFailed') }
    }
  } finally {
    if (readTokens[id] === expected) pendingReads.delete(id)
  }
}

function syncConnections(): void {
  const activeIds = new Set(props.connections.map(item => item.id))
  for (const connection of props.connections) {
    const snapshot = `${connection.configuration_version}:${connection.enabled}:${connection.model_id ?? ''}`
    if (snapshots.get(connection.id) !== snapshot) {
      readTokens[connection.id] = (readTokens[connection.id] ?? 0) + 1
      snapshots.set(connection.id, snapshot)
      pendingReads.add(connection.id)
      states.value = { ...states.value, [connection.id]: 'loading' }
      errors.value = { ...errors.value, [connection.id]: '' }
      messages.value = { ...messages.value, [connection.id]: '' }
    }
  }
  for (const id of [...snapshots.keys()]) {
    if (activeIds.has(id)) continue
    snapshots.delete(id)
    pendingReads.delete(id)
    readTokens[id] = (readTokens[id] ?? 0) + 1
    const remainingStates = { ...states.value }; delete remainingStates[id]
    const remainingErrors = { ...errors.value }; delete remainingErrors[id]
    const remainingMessages = { ...messages.value }; delete remainingMessages[id]
    const remainingRevoking = { ...revoking.value }; delete remainingRevoking[id]
    states.value = remainingStates; errors.value = remainingErrors; messages.value = remainingMessages; revoking.value = remainingRevoking
    revokeTokens[id] = (revokeTokens[id] ?? 0) + 1
  }
  if (!props.paused) {
    for (const id of [...pendingReads]) if (activeIds.has(id)) void loadConnection(id)
  }
}

async function revoke(id: string): Promise<void> {
  const previousState = states.value[id]
  if (!isPresent(id) || props.paused || revoking.value[id] || !['active', 'stored'].includes(previousState ?? '')) return
  const revokeToken = revokeTokens[id] = (revokeTokens[id] ?? 0) + 1
  const expectedRead = readTokens[id] = (readTokens[id] ?? 0) + 1
  revoking.value = { ...revoking.value, [id]: true }
  errors.value = { ...errors.value, [id]: '' }
  messages.value = { ...messages.value, [id]: '' }
  let revokeFailed = false
  try {
    const grants = await readConnectionGrants(id)
    if (!isPresent(id) || revokeTokens[id] !== revokeToken || readTokens[id] !== expectedRead) return
    for (const grant of grants) {
      if (grant.revoked_at !== null) continue
      if (!isPresent(id) || revokeTokens[id] !== revokeToken || readTokens[id] !== expectedRead) return
      try { await api<void>(`/settings/ai/consents/${grant.id}`, { method: 'DELETE' }) }
      catch { revokeFailed = true }
      if (!isPresent(id) || revokeTokens[id] !== revokeToken || readTokens[id] !== expectedRead) return
    }
    const remaining = await readConnectionGrants(id)
    const state = consentState(id, remaining)
    if (isPresent(id) && revokeTokens[id] === revokeToken && readTokens[id] === expectedRead) {
      states.value = { ...states.value, [id]: state }
      if (state === 'none') messages.value = { ...messages.value, [id]: t('accountAIProviders.consentRevoked') }
      else errors.value = { ...errors.value, [id]: t('accountAIProviders.consentRevokeFailed') }
      if (state === 'none') errors.value = { ...errors.value, [id]: '' }
      else if (!revokeFailed) errors.value = { ...errors.value, [id]: t('accountAIProviders.consentRevokeFailed') }
    }
  } catch {
    if (isPresent(id) && revokeTokens[id] === revokeToken && readTokens[id] === expectedRead) {
      errors.value = { ...errors.value, [id]: t('accountAIProviders.consentRevokeFailed') }
    }
  } finally {
    if (revokeTokens[id] === revokeToken) {
      revoking.value = { ...revoking.value, [id]: false }
      if (pendingReads.has(id) && isPresent(id) && !props.paused) void loadConnection(id)
    }
  }
}

function grantChanged(id: string): void { pendingReads.add(id); void loadConnection(id) }

watch(() => props.connections.map(item => `${item.id}:${item.configuration_version}:${item.enabled}:${item.model_id ?? ''}`).join('|'), syncConnections)
watch(() => props.paused, value => { if (!value) syncConnections() })
watch(() => activeReads.value > 0 || grantBusy.value || Object.values(revoking.value).some(Boolean), value => emit('busy', value), { flush: 'sync' })
onMounted(syncConnections)
onBeforeUnmount(() => { disposed = true; emit('busy', false); for (const id of Object.keys(readTokens)) readTokens[id] += 1 })
</script>

<template>
  <section class="card form-card consent-settings" aria-labelledby="consent-settings-title">
    <h2 id="consent-settings-title">{{ t('accountAIProviders.consentTitle') }}</h2>
    <div v-if="sessionChanged" class="error" role="alert"><p>{{ t('accountAIProviders.sessionChanged') }}</p><button class="button secondary compact-action" type="button" @click="reloadSession">{{ t('accountAIProviders.reloadSession') }}</button></div>
    <template v-else>
      <p class="help">{{ t('accountAIProviders.consentHelp') }}</p>
      <p v-if="checking" class="help" role="status">{{ t('accountAIProviders.consentLoading') }}</p>
      <p v-if="noActiveConsents" class="help">{{ t('accountAIProviders.consentNone') }}</p>
      <p v-for="connection in feedbackConnections" :key="`feedback-${connection.id}`" class="success" role="status">{{ providerLabel(connection) }}: {{ messages[connection.id] }}</p>
      <div v-for="connection in visibleConnections" :key="connection.id" class="consent-row" :data-connection-id="connection.id">
        <div>
          <strong>{{ providerLabel(connection) }}</strong>
          <p v-if="enabledConnectionIds" class="help consent-destination">{{ connection.model_id }}<template v-if="connection.base_url"> · {{ connection.base_url }}</template></p>
          <p v-if="states[connection.id] === 'active'" class="help">{{ t('accountAIProviders.consentActive') }}</p>
          <p v-else-if="states[connection.id] === 'stored'" class="help">{{ t('accountAIProviders.consentStored') }}</p>
          <p v-else-if="states[connection.id] === 'none'" class="help">{{ t('providerRouting.consentMissing') }}</p>
          <p v-if="errors[connection.id]" class="error" role="alert">{{ errors[connection.id] }}</p>
        </div>
        <div class="consent-actions">
          <button v-if="enabledConnectionIds?.includes(connection.id) && connection.enabled && connection.model_id && !['loading', 'error'].includes(states[connection.id] ?? '')" class="button secondary compact-action" type="button" :disabled="revoking[connection.id] || paused" @click="openGrant(connection.id)">{{ t(states[connection.id] === 'active' ? 'providerRouting.consentAdjust' : 'providerRouting.consentGrant') }}</button>
          <button v-if="['active', 'stored'].includes(states[connection.id] ?? '')" class="button secondary compact-action" type="button" :disabled="revoking[connection.id] || paused" @click="revoke(connection.id)">{{ revoking[connection.id] ? t('accountAIProviders.consentRevoking') : t('accountAIProviders.consentRevoke') }}</button>
          <button v-if="states[connection.id] === 'error'" class="button secondary compact-action" type="button" :disabled="paused" @click="loadConnection(connection.id)">{{ t('common.tryAgain') }}</button>
        </div>
      </div>
      <AIConsentGrantDialog v-if="granting" :connection="granting" :paused="paused" @busy="grantBusy = $event" @changed="grantChanged(granting.id)" @close="granting = null" />
    </template>
  </section>
</template>

<style scoped>
.consent-settings { display: grid; gap: .75rem; }
.consent-settings h2, .consent-settings > p { margin: 0; }
.help { margin: 0; color: var(--muted-strong); font-size: .8rem; line-height: 1.5; }
.consent-row { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding-top: .75rem; border-top: 1px solid var(--border); }
.consent-row > div:first-child { min-width: 0; }
.consent-destination { overflow-wrap: anywhere; }
.consent-row strong { font-size: .85rem; }
.consent-actions { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.success { margin: .2rem 0 0; color: var(--success, #16834a); font-size: .8rem; }
.error { margin: .2rem 0 0; color: var(--danger, #c62828); font-size: .8rem; }
@media (max-width: 600px) { .consent-row { align-items: flex-start; flex-direction: column; } }
</style>
