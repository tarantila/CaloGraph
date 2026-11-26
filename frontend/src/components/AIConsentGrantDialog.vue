<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { api } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import { CONSENT_POLICY_VERSION } from '../generated/ai-contracts'
import type { AIConsentPage, AIConsentPut, AIConsentView, ExpertPreferencesView, ProviderConnectionView } from '../generated/ai-contracts'
import { i18n } from '../i18n'
import { clientUuid } from '../uuid'
import ConfirmDialog from './ConfirmDialog.vue'

const props = defineProps<{ connection: ProviderConnectionView; paused?: boolean }>()
const emit = defineEmits<{ close: []; changed: []; busy: [value: boolean] }>()
const t = i18n.global.t.bind(i18n.global)
const preferences = ref<ExpertPreferencesView | null>(null)
const documents = ref(false)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const identity = captureAuthIdentity()
const connectionId = props.connection.id
const connectionVersion = props.connection.configuration_version
const pendingIds = new Map<string, string>()
let disposed = false
function alive(): boolean { return !disposed && isAuthIdentityCurrent(identity) }
const description = computed(() => preferences.value ? t('providerRouting.consentDescription', {
  provider: t(`accountAISetup.providers.${props.connection.provider_kind}`),
  model: props.connection.model_id || '',
  destination: props.connection.base_url || t('providerRouting.providerDestination'),
  categories: preferences.value.data_domains.map(item => t(`accountAISetup.domains.${item}`)).join(', '),
  mealNames: preferences.value.include_meal_names ? t('accountAISetup.mealNames') : t('providerRouting.noMealNames'),
}) : t('common.loading'))

async function readGrants(): Promise<AIConsentView[]> {
  let after: string | null = null
  const grants: AIConsentView[] = []
  do {
    if (!alive()) throw new Error('stale session')
    const query = new URLSearchParams({ connection_id: connectionId, limit: '50' })
    if (after) query.set('after', after)
    const page = await api<AIConsentPage>(`/settings/ai/consents?${query.toString()}`)
    if (!alive()) throw new Error('stale session')
    grants.push(...page.items)
    after = page.next_after
  } while (after)
  return grants
}
function matches(grant: AIConsentView, payload: AIConsentPut): boolean {
  return grant.connection_id === payload.connection_id && grant.configuration_version === payload.configuration_version
    && grant.state === 'active' && grant.revoked_at === null && grant.policy_version === payload.policy_version
    && grant.purpose === payload.purpose && grant.include_meal_names === payload.include_meal_names
    && grant.include_attachments === payload.include_attachments && grant.history.mode === 'all_available'
    && [...grant.data_domains].sort().join('|') === [...payload.data_domains].sort().join('|')
}
async function confirm(): Promise<void> {
  if (!alive() || busy.value || loading.value || props.paused || !preferences.value) return
  if (props.connection.id !== connectionId || props.connection.configuration_version !== connectionVersion) { error.value = t('providerRouting.consentChanged'); return }
  busy.value = true; emit('busy', true); error.value = ''
  let grantMayHaveChanged = false
  try {
    const current = await api<ExpertPreferencesView>('/settings/expert')
    if (!alive()) return
    if (current.configuration_version !== preferences.value.configuration_version) {
      preferences.value = current
      error.value = t('providerRouting.consentChanged')
      return
    }
    let grants = await readGrants()
    for (const includeAttachments of documents.value ? [false, true] : [false]) {
      for (const purpose of ['nutrition_chat', 'nutrition_check_in'] as const) {
        if (!alive()) return
        const payload: AIConsentPut = {
          connection_id: connectionId, configuration_version: connectionVersion, purpose,
          data_domains: current.data_domains, include_meal_names: current.include_meal_names,
          include_attachments: includeAttachments, history: { mode: 'all_available', start: null, end: null },
          policy_version: CONSENT_POLICY_VERSION,
        }
        if (grants.some(grant => matches(grant, payload))) continue
        const key = `${current.configuration_version}:${purpose}:${includeAttachments}`
        const id = pendingIds.get(key) ?? clientUuid()
        pendingIds.set(key, id)
        try {
          grantMayHaveChanged = true
          const result = await api<AIConsentView>(`/settings/ai/consents/${id}`, { method: 'PUT', body: JSON.stringify(payload) })
          if (!matches(result, payload)) throw new Error('consent not active')
          grants.push(result)
        } catch {
          // Resolve a committed response loss or a concurrent equivalent grant.
          // Keep the original ID for a retry, so a revoked grant is never restored.
          grants = await readGrants()
          if (!grants.some(grant => matches(grant, payload))) throw new Error('consent not confirmed')
        }
      }
    }
    if (alive()) { emit('changed'); emit('close') }
  } catch {
    if (alive()) {
      if (grantMayHaveChanged) emit('changed')
      error.value = t('providerRouting.consentFailed')
    }
  }
  finally { busy.value = false; emit('busy', false) }
}
onMounted(async () => {
  try { if (!alive()) return; const value = await api<ExpertPreferencesView>('/settings/expert'); if (alive()) preferences.value = value }
  catch { if (alive()) error.value = t('providerRouting.consentLoadFailed') }
  finally { if (alive()) loading.value = false }
})
onBeforeUnmount(() => { disposed = true })
</script>

<template>
  <ConfirmDialog :open="true" :title="t('providerRouting.consentTitle')" :description="description" :confirm-label="t('providerRouting.consentConfirm')" :pending="busy" :confirm-disabled="loading || paused || !preferences" :error="error" @confirm="confirm" @close="!busy && emit('close')">
    <label class="document-consent"><input v-model="documents" type="checkbox" :disabled="loading || busy || paused" /><span>{{ t('providerRouting.documents') }}</span></label>
    <p class="consent-note">{{ t('providerRouting.consentNote') }}</p>
  </ConfirmDialog>
</template>

<style scoped>
.document-consent { display: flex; align-items: flex-start; gap: .6rem; font-size: .85rem; line-height: 1.5; }
.document-consent input { flex: 0 0 auto; margin-top: .25rem; }
.consent-note { color: var(--muted-strong); font-size: .8rem; line-height: 1.5; }
</style>
