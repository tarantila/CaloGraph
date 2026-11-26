<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import { useAuthStore } from '../stores/auth'
import type { AIConsentPage } from '../generated/ai-contracts'
import { i18n } from '../i18n'

const props = defineProps<{ paused?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const t = i18n.global.t.bind(i18n.global)
type ViewState = 'loading' | 'none' | 'available' | 'error' | 'revoking' | 'session-changed'
type RetryAction = 'load' | 'revoke'

const auth = useAuthStore()
const ownerIdentity = captureAuthIdentity()
const state = ref<ViewState>('loading')
const error = ref('')
const success = ref('')
const retryAction = ref<RetryAction>('load')
const busy = computed(() => state.value === 'loading' || state.value === 'revoking')
const canAct = computed(() => !props.paused && (state.value === 'available' || state.value === 'error'))
const actionLabel = computed(() => {
  if (state.value === 'revoking') return t('expertConsent.revoking')
  if (state.value === 'error' && retryAction.value === 'load') return t('expertConsent.retry')
  return t('expertConsent.revoke')
})
let generation = 0
let disposed = false
let loadDeferred = false
let successTimer: ReturnType<typeof setTimeout> | undefined

function clearSuccess(): void {
  if (successTimer) clearTimeout(successTimer)
  successTimer = undefined
  success.value = ''
}

function isCurrent(expectedGeneration: number): boolean {
  return alive() && generation === expectedGeneration
}

function alive(): boolean {
  if (disposed) return false
  if (isAuthIdentityCurrent(ownerIdentity)) return true
  generation += 1
  loadDeferred = false
  clearSuccess()
  error.value = ''
  state.value = 'session-changed'
  emit('busy', false)
  return false
}

watch(() => auth.user?.id ?? null, () => { alive() }, { flush: 'sync' })

async function readAllUnrevoked(expectedGeneration: number): Promise<AIConsentPage['items'][number][]> {
  const unrevoked: AIConsentPage['items'][number][] = []
  const seenCursors = new Set<string>()
  let after: string | null = null
  do {
    if (!isCurrent(expectedGeneration)) throw new Error('The permission operation is no longer current')
    const query = new URLSearchParams({ limit: '50' })
    if (after) query.set('after', after)
    const page = await api<AIConsentPage>(`/settings/ai/consents?${query.toString()}`)
    if (!isCurrent(expectedGeneration)) throw new Error('The permission operation is no longer current')
    unrevoked.push(...page.items.filter(consent => consent.revoked_at === null))
    after = page.next_after
    if (after && seenCursors.has(after)) throw new Error('Consent pagination did not advance')
    if (after) seenCursors.add(after)
  } while (after)
  return unrevoked
}

async function load(): Promise<void> {
  if (!alive()) return
  if (props.paused) { loadDeferred = true; return }
  loadDeferred = false
  const expectedGeneration = ++generation
  clearSuccess()
  error.value = ''
  state.value = 'loading'
  emit('busy', true)
  try {
    const unrevoked = await readAllUnrevoked(expectedGeneration)
    if (!isCurrent(expectedGeneration)) return
    state.value = unrevoked.length ? 'available' : 'none'
  } catch {
    if (!isCurrent(expectedGeneration)) return
    state.value = 'error'
    retryAction.value = 'load'
    error.value = t('expertConsent.loadFailed')
  } finally {
    if (isCurrent(expectedGeneration)) emit('busy', false)
  }
}

async function revokeAll(): Promise<void> {
  if (!alive() || !canAct.value) return
  if (state.value === 'error' && retryAction.value === 'load') {
    await load()
    return
  }

  const expectedGeneration = ++generation
  clearSuccess()
  error.value = ''
  state.value = 'revoking'
  emit('busy', true)
  try {
    const current = await readAllUnrevoked(expectedGeneration)
    for (const consent of current) {
      if (!isCurrent(expectedGeneration)) return
      try {
        await api<void>(`/settings/ai/consents/${consent.id}`, { method: 'DELETE' })
      } catch { /* Re-read all pages; a lost response may still have persisted. */ }
      if (!isCurrent(expectedGeneration)) return
    }

    const remaining = await readAllUnrevoked(expectedGeneration)
    if (!isCurrent(expectedGeneration)) return
    if (remaining.length) {
      state.value = 'available'
      retryAction.value = 'revoke'
      error.value = t('expertConsent.revokeFailed')
      return
    }

    state.value = 'none'
    success.value = t('expertConsent.revoked')
    successTimer = setTimeout(() => {
      successTimer = undefined
      success.value = ''
    }, 4_000)
  } catch {
    if (!isCurrent(expectedGeneration)) return
    state.value = 'error'
    retryAction.value = 'revoke'
    error.value = t('expertConsent.revokeFailed')
  } finally {
    if (isCurrent(expectedGeneration)) emit('busy', false)
  }
}

function act(): void {
  if (!canAct.value) return
  void revokeAll()
}

function reloadSession(): void { window.location.reload() }

watch(() => props.paused, paused => { if (!paused && loadDeferred) void load() })
onMounted(() => { void load() })
onBeforeUnmount(() => {
  disposed = true
  loadDeferred = false
  generation += 1
  clearSuccess()
  emit('busy', false)
})
</script>

<template>
  <section class="card form-card expert-consent-card" aria-labelledby="expert-consent-heading">
    <h2 id="expert-consent-heading">{{ t('expertConsent.title') }}</h2>
    <p class="expert-consent-help">{{ t('expertConsent.help') }}</p>
    <p v-if="state === 'session-changed'" role="alert">{{ t('expertConsent.sessionChanged') }} <button class="text-action" type="button" @click="reloadSession">{{ t('expertConsent.reloadSession') }}</button></p>
    <p v-else-if="busy" role="status">{{ state === 'revoking' ? t('expertConsent.revoking') : t('expertConsent.loading') }}</p>
    <p v-else-if="state === 'none'" class="expert-consent-state" :class="{ success: success }" role="status">{{ success || t('expertConsent.none') }}</p>
    <p v-if="error" class="account-form-error" role="alert">{{ error }}</p>
    <button v-if="state === 'available' || state === 'error'" class="button secondary compact-action" type="button" :disabled="!canAct" @click="act">{{ actionLabel }}</button>
  </section>
</template>

<style scoped>
.expert-consent-card { display: grid; gap: .75rem; }
.expert-consent-card h2, .expert-consent-card p { margin: 0; }
.expert-consent-card .compact-action { justify-self: end; max-width: 100%; }
.expert-consent-help { color: var(--muted-strong); font-size: .8rem; line-height: 1.5; }
.expert-consent-state { color: var(--muted-strong); font-size: .8rem; }
.expert-consent-state.success { color: var(--success); }
.text-action { color: var(--accent); font: inherit; text-decoration: underline; }
</style>
