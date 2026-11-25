<script setup lang="ts">
import { PhArrowDown, PhArrowUp, PhTrash } from '@phosphor-icons/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '../api'
import { captureAuthIdentity, isAuthIdentityCurrent } from '../auth-session'
import type { AIRoutingEntryPut, AIRoutingPut, AIRoutingView, ProviderConnectionView } from '../generated/ai-contracts'
import { i18n } from '../i18n'

const props = defineProps<{ connections: readonly ProviderConnectionView[]; paused?: boolean }>()
const emit = defineEmits<{
  busy: [value: boolean]
  changed: [value: AIRoutingView]
  configure: [value: ProviderConnectionView]
  remove: [value: ProviderConnectionView]
}>()
const t = i18n.global.t.bind(i18n.global)
const routing = ref<AIRoutingView | null>(null)
const busy = ref(false)
const error = ref('')
const saved = ref(false)
const uncertain = ref(false)
const firstEnabled = computed(() => routing.value?.entries.find(item => item.enabled)?.connection_id)
const disabled = computed(() => busy.value || props.paused || uncertain.value)
const identity = captureAuthIdentity()
let disposed = false
let pendingRead = false
let timer: ReturnType<typeof setTimeout> | undefined

function alive(): boolean { return !disposed && isAuthIdentityCurrent(identity) }
function label(id: string): string {
  const item = props.connections.find(item => item.id === id)
  return item ? t(`accountAISetup.providers.${item.provider_kind}`) : ''
}
function connection(id: string): ProviderConnectionView | undefined { return props.connections.find(item => item.id === id) }
function customLabel(id: string): string {
  const item = connection(id)
  return item && item.label !== label(id) && !(item.provider_kind === 'chatgpt_plan' && item.label === 'ChatGPT plan') ? item.label : ''
}
function apply(value: AIRoutingView): void {
  routing.value = value
  uncertain.value = false
  emit('changed', value)
}
function clearSaved(): void {
  if (timer) clearTimeout(timer)
  saved.value = false
}
function showSaved(): void {
  clearSaved()
  saved.value = true
  timer = setTimeout(() => { saved.value = false }, 4000)
}
async function load(): Promise<void> {
  if (!alive()) return
  if (busy.value || props.paused) { pendingRead = true; return }
  pendingRead = false
  busy.value = true; emit('busy', true)
  error.value = ''
  try {
    const value = await api<AIRoutingView>('/settings/ai/routing')
    if (alive()) apply(value)
  } catch {
    if (alive()) { uncertain.value = true; error.value = t('providerRouting.loadFailed') }
  } finally {
    busy.value = false; emit('busy', false)
    if (pendingRead && alive() && !props.paused) void load()
  }
}
function matches(value: AIRoutingView, entries: readonly AIRoutingEntryPut[]): boolean {
  return value.entries.length === entries.length && value.entries.every((item, index) => item.connection_id === entries[index]?.connection_id && item.enabled === entries[index]?.enabled)
}
async function save(entries: AIRoutingEntryPut[]): Promise<void> {
  if (!alive() || disabled.value || !routing.value) return
  const previous = routing.value
  const payload: AIRoutingPut = { expected_configuration_version: routing.value.configuration_version, entries }
  routing.value = {
    ...previous,
    entries: entries.map((entry, position) => ({ ...previous.entries.find(item => item.connection_id === entry.connection_id)!, ...entry, position })),
  }
  clearSaved(); error.value = ''
  busy.value = true; emit('busy', true)
  try {
    const value = await api<AIRoutingView>('/settings/ai/routing', { method: 'PUT', body: JSON.stringify(payload) })
    if (alive()) { apply(value); showSaved() }
  } catch {
    if (!alive()) return
    // A lost response may already have persisted the order. Reconcile before
    // allowing another versioned mutation; never blindly repeat a stale write.
    try {
      const value = await api<AIRoutingView>('/settings/ai/routing')
      if (!alive()) return
      apply(value)
      if (matches(value, entries)) showSaved()
      else error.value = t('providerRouting.saveFailed')
    } catch {
      if (alive()) { routing.value = previous; uncertain.value = true; error.value = t('providerRouting.unknown') }
    }
  } finally {
    busy.value = false; emit('busy', false)
    if (pendingRead && alive() && !props.paused) void load()
  }
}
function entries(): AIRoutingEntryPut[] { return routing.value?.entries.map(item => ({ connection_id: item.connection_id, enabled: item.enabled })) ?? [] }
function toggle(id: string): void {
  const desired = entries().map(item => item.connection_id === id ? { ...item, enabled: !item.enabled } : item)
  void save(desired)
}
function move(index: number, delta: number): void {
  const desired = entries()
  const target = index + delta
  if (target < 0 || target >= desired.length) return
  const item = desired.splice(index, 1)[0]
  if (item) desired.splice(target, 0, item)
  void save(desired)
}
function configure(id: string): void { const item = connection(id); if (item) emit('configure', item) }
function remove(id: string): void { const item = connection(id); if (item) emit('remove', item) }

watch(() => props.connections.map(item => `${item.id}:${item.configuration_version}:${item.enabled}`).join('|'), () => { pendingRead = true; void load() })
watch(() => props.paused, value => { if (!value && pendingRead && !busy.value) void load() })
onMounted(() => { void load() })
onBeforeUnmount(() => { disposed = true; clearSaved() })
</script>

<template>
  <section class="card form-card routing-settings" aria-labelledby="provider-routing-heading" :aria-busy="busy">
    <h2 id="provider-routing-heading">{{ t('providerRouting.title') }}</h2>
    <p class="help">{{ t('providerRouting.help') }}</p>
    <p v-if="!routing && busy" class="help" role="status">{{ t('common.loading') }}</p>
    <ol v-if="routing" class="routing-list">
      <li v-for="(item, index) in routing.entries" :key="item.connection_id" class="routing-row" :class="{ preferred: item.enabled && firstEnabled === item.connection_id }">
        <span class="routing-position" aria-hidden="true">{{ index + 1 }}.</span>
        <div class="routing-copy">
          <strong>{{ label(item.connection_id) }}</strong>
          <small v-if="customLabel(item.connection_id)">{{ customLabel(item.connection_id) }}</small>
          <small v-if="item.model_id">{{ item.model_id }}</small>
          <small v-if="!item.ready">{{ t('providerRouting.notReady') }}</small>
          <small v-else>{{ t(!item.enabled ? 'providerRouting.off' : firstEnabled === item.connection_id ? 'providerRouting.preferred' : 'providerRouting.backup') }}</small>
        </div>
        <div class="routing-controls">
          <input class="routing-switch" :data-connection-id="item.connection_id" type="checkbox" role="switch" :aria-label="t('providerRouting.enable', { provider: label(item.connection_id) })" :checked="item.enabled" :disabled="disabled || (!item.ready && !item.enabled)" @change="toggle(item.connection_id)" />
          <button class="routing-icon button secondary" type="button" :aria-label="t('providerRouting.up', { provider: label(item.connection_id) })" :disabled="disabled || index === 0" @click="move(index, -1)"><PhArrowUp :size="16" aria-hidden="true" /></button>
          <button class="routing-icon button secondary" type="button" :aria-label="t('providerRouting.down', { provider: label(item.connection_id) })" :disabled="disabled || index === routing.entries.length - 1" @click="move(index, 1)"><PhArrowDown :size="16" aria-hidden="true" /></button>
          <button class="routing-icon button secondary danger" type="button" :aria-label="t('providerRouting.remove', { provider: label(item.connection_id) })" :disabled="disabled" @click="remove(item.connection_id)"><PhTrash :size="16" aria-hidden="true" /></button>
        </div>
        <button class="configure-button" type="button" :disabled="disabled" @click="configure(item.connection_id)">{{ t('accountAISetup.configure') }}</button>
      </li>
    </ol>
    <p v-if="routing && !firstEnabled" class="help">{{ t('providerRouting.none') }}</p>
    <p v-if="saved" class="saved" role="status">{{ t('providerRouting.saved') }}</p>
    <div v-if="error" class="routing-error" role="alert"><p>{{ error }}</p><button v-if="uncertain" class="button secondary compact-action" type="button" :disabled="busy || paused" @click="load">{{ t('common.tryAgain') }}</button></div>
  </section>
</template>

<style scoped>
.routing-settings { display: grid; gap: .75rem; }
.routing-settings h2, .routing-settings p { margin: 0; }
.help { color: var(--muted-strong); font-size: .8rem; line-height: 1.55; }
.routing-list { display: grid; gap: .5rem; margin: .25rem 0 0; padding: 0; list-style: none; }
.routing-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; align-items: center; gap: .65rem; padding: .65rem .75rem; border: 1px solid var(--border); border-radius: .6rem; }
.routing-row.preferred { border-color: var(--accent); }
.routing-position { color: var(--muted-strong); font-size: .8rem; }
.routing-copy { min-width: 0; overflow-wrap: anywhere; }
.routing-copy strong { font-size: .84rem; font-weight: 600; }
.routing-copy small { display: block; color: var(--muted-strong); font-size: .76rem; line-height: 1.5; }
.routing-controls { display: flex; align-items: center; gap: .4rem; }
.routing-icon.button { display: grid; place-items: center; flex: 0 0 2.25rem; width: 2.25rem; height: 2.25rem; min-height: 0; padding: 0; line-height: 1; }
.routing-icon svg { display: block; }
.configure-button { padding: .4rem; color: var(--accent); font-size: .8rem; text-decoration: underline; }
.configure-button:disabled { opacity: .5; cursor: not-allowed; }
.routing-switch { position: relative; flex: 0 0 auto; width: 2.5rem; height: 1.4rem; margin: 0 .25rem 0 0; appearance: none; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-soft); cursor: pointer; }
.routing-switch::after { position: absolute; top: .12rem; left: .12rem; width: 1.1rem; height: 1.1rem; border-radius: 50%; background: var(--surface); box-shadow: 0 1px 3px rgb(0 0 0 / .2); content: ''; }
.routing-switch:checked { border-color: var(--accent); background: var(--accent); }
.routing-switch:checked::after { transform: translateX(1.08rem); }
.routing-switch:focus-visible { outline: 3px solid color-mix(in srgb, var(--accent) 28%, transparent); outline-offset: 2px; }
.routing-switch:disabled { cursor: not-allowed; opacity: .55; }
.saved { color: var(--success); font-size: .8rem; }
.routing-error { display: grid; gap: .5rem; justify-items: start; color: var(--danger); font-size: .8rem; }
@media (max-width: 700px) { .routing-row { grid-template-columns: auto minmax(0, 1fr) auto; } .routing-controls { grid-column: 2 / -1; grid-row: 2; } .configure-button { grid-column: 3; grid-row: 1; } }
</style>
