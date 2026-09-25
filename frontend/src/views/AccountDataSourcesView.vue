<script setup lang="ts">
import { getActivePinia } from 'pinia'
import { PhArrowDown, PhArrowUp, PhFire, PhForkKnife, PhScales } from '@phosphor-icons/vue'
import { onMounted, onUnmounted, ref, watch, type Component } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'

const t = i18n.global.t.bind(i18n.global)
let auth: ReturnType<typeof useAuthStore> | null = null
let stopAuthSessionWatch: (() => void) | undefined

type DataArea = 'nutrition' | 'weight' | 'activity_energy'
type ProviderKey = 'apple_health' | 'google_health' | 'health_auto_export' | 'yazio' | 'withings'
type ProviderStatus = 'available' | 'disabled' | 'error' | 'not_configured' | 'reauth_required' | 'no_data'
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface PreferenceResponse {
  data_area: DataArea
  provider_key: ProviderKey
}

interface Availability {
  provider_key: ProviderKey
  available: boolean
  status: ProviderStatus
}

interface AvailabilityResponse {
  data_area: DataArea
  providers: Availability[]
}

interface SaveResult {
  status: 'saved' | 'failed' | 'superseded'
}

interface PendingSave {
  providerKeys: ProviderKey[]
  sessionKey: string
  resolve: (result: SaveResult) => void
}

interface AreaSaveState {
  active: Promise<void> | null
  pending: PendingSave | null
  sessionKey: string | null
}
const areas = [
  { key: 'nutrition', title: 'providerPreferencesUi.nutritionTitle', icon: PhForkKnife },
  { key: 'activity_energy', title: 'providerPreferencesUi.activityTitle', icon: PhFire },
  { key: 'weight', title: 'providerPreferencesUi.weightTitle', icon: PhScales },
] as const satisfies ReadonlyArray<{ key: DataArea; title: string; icon: Component }>

const saveStateRegistry = api as typeof api & {
  __calographProviderPrioritySaveStates?: Record<DataArea, AreaSaveState>
}
const saveStates = saveStateRegistry.__calographProviderPrioritySaveStates ??= {
  nutrition: { active: null, pending: null, sessionKey: null },
  weight: { active: null, pending: null, sessionKey: null },
  activity_energy: { active: null, pending: null, sessionKey: null },
}
const priorities = ref<Record<DataArea, ProviderKey[]>>({
  nutrition: [],
  weight: [],
  activity_energy: [],
})
const providers = ref<Record<DataArea, Availability[]>>({
  nutrition: [],
  weight: [],
  activity_energy: [],
})
const loading = ref(true)
const saveStatus = ref<Record<DataArea, SaveStatus>>({
  nutrition: 'idle',
  weight: 'idle',
  activity_energy: 'idle',
})
const loadingError = ref('')
const saveGenerations: Record<DataArea, number> = {
  nutrition: 0,
  weight: 0,
  activity_energy: 0,
}
const saveTimers: Record<DataArea, ReturnType<typeof setTimeout> | null> = {
  nutrition: null,
  weight: null,
  activity_energy: null,
}
let viewMounted = false

function providerLabel(providerKey: ProviderKey): string {
  return t(`providerPreferencesUi.providers.${providerKey}`)
}

function statusLabel(status: ProviderStatus): string {
  return t(`providerPreferencesUi.status.${status}`)
}

function saveStatusLabel(status: SaveStatus): string {
  if (status === 'saving') return t('providerPreferencesUi.saving')
  if (status === 'saved') return t('providerPreferencesUi.saved')
  return t('providerPreferencesUi.saveFailed')
}

function availability(area: DataArea, providerKey: ProviderKey): Availability | undefined {
  return providers.value[area].find((item) => item.provider_key === providerKey)
}

function isSaving(area: DataArea): boolean {
  return saveStatus.value[area] === 'saving'
}

function currentSessionKey(): string {
  const userId = auth?.user?.id ?? 'anonymous'
  const sessionToken = typeof sessionStorage === 'undefined'
    ? 'server'
    : sessionStorage.getItem('calograph_csrf') ?? 'anonymous'
  return `${userId}:${sessionToken}`
}

function bindAreaSaveSession(area: DataArea, sessionKey: string): AreaSaveState {
  const state = saveStates[area]
  if (state.sessionKey !== sessionKey) {
    if (state.pending) state.pending.resolve({ status: 'superseded' })
    state.pending = null
    state.sessionKey = sessionKey
  }
  return state
}

async function drainAreaSaves(area: DataArea, state: AreaSaveState): Promise<void> {
  while (state.pending) {
    const pending = state.pending
    state.pending = null
    if (pending.sessionKey !== state.sessionKey) {
      pending.resolve({ status: 'superseded' })
      continue
    }
    try {
      await api<PreferenceResponse>(`/settings/provider-preferences/${area}`, {
        method: 'PUT',
        body: JSON.stringify({ provider_keys: pending.providerKeys }),
      })
      pending.resolve({ status: 'saved' })
    } catch {
      pending.resolve({ status: 'failed' })
    }
  }
  state.active = null
}

function queueAreaSave(area: DataArea, providerKeys: ProviderKey[]): Promise<SaveResult> {
  const state = bindAreaSaveSession(area, currentSessionKey())
  const sessionKey = state.sessionKey!
  const result = new Promise<SaveResult>((resolve) => {
    if (state.pending) state.pending.resolve({ status: 'superseded' })
    state.pending = { providerKeys: [...providerKeys], sessionKey, resolve }
  })
  if (!state.active) state.active = drainAreaSaves(area, state)
  return result
}

function waitForAreaSave(area: DataArea): Promise<void> {
  const state = bindAreaSaveSession(area, currentSessionKey())
  return state.active ?? Promise.resolve()
}

function clearSaveTimer(area: DataArea): void {
  if (saveTimers[area]) {
    clearTimeout(saveTimers[area]!)
    saveTimers[area] = null
  }
}

function setSaveStatus(area: DataArea, status: SaveStatus): void {
  saveStatus.value = { ...saveStatus.value, [area]: status }
}

async function persist(area: DataArea, providerKeys: ProviderKey[]): Promise<void> {
  const generation = ++saveGenerations[area]
  clearSaveTimer(area)
  setSaveStatus(area, 'saving')
  const result = await queueAreaSave(area, providerKeys)
  if (!viewMounted || generation !== saveGenerations[area] || result.status === 'superseded') return

  if (result.status === 'failed') {
    setSaveStatus(area, 'error')
    return
  }

  setSaveStatus(area, 'saved')
  saveTimers[area] = setTimeout(() => {
    saveTimers[area] = null
    if (viewMounted && generation === saveGenerations[area]) setSaveStatus(area, 'idle')
  }, 2_000)
}

function moveProvider(area: DataArea, index: number, direction: -1 | 1): void {
  const targetIndex = index + direction
  const current = priorities.value[area]
  if (targetIndex < 0 || targetIndex >= current.length) return
  const next = [...current]
  const movedProvider = next[index]
  next[index] = next[targetIndex]
  next[targetIndex] = movedProvider
  priorities.value[area] = next
  void persist(area, next)
}

async function load(): Promise<void> {
  loading.value = true
  loadingError.value = ''
  try {
    await Promise.all(areas.map(({ key }) => waitForAreaSave(key)))
    const [preferenceResponse, ...availabilityResponses] = await Promise.all([
      api<{ preferences: PreferenceResponse[] }>('/settings/provider-preferences'),
      ...areas.map(({ key }) => api<AvailabilityResponse>(`/settings/provider-availability/${key}`)),
    ])
    if (!viewMounted) return

    for (const area of areas) {
      priorities.value[area.key] = []
      providers.value[area.key] = []
    }
    for (const response of availabilityResponses) providers.value[response.data_area] = response.providers
    for (const area of areas) {
      const supportedProviders = providers.value[area.key]
        .map(({ provider_key }) => provider_key)
        .filter((providerKey, index, all) => all.indexOf(providerKey) === index)
      const supported = new Set(supportedProviders)
      const persisted = preferenceResponse.preferences
        .filter((preference) => preference.data_area === area.key && supported.has(preference.provider_key))
        .map((preference) => preference.provider_key)
        .filter((providerKey, index, all) => all.indexOf(providerKey) === index)
      const persistedSet = new Set(persisted)
      priorities.value[area.key] = [
        ...persisted,
        ...supportedProviders.filter((providerKey) => !persistedSet.has(providerKey)),
      ]
    }
  } catch (cause) {
    if (viewMounted) {
      loadingError.value = cause instanceof ApiError
        ? localizeApiError(cause, 'providerPreferencesUi.loadFailed')
        : t('providerPreferencesUi.loadFailed')
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const pinia = getActivePinia()
  auth = pinia ? useAuthStore(pinia) : null
  stopAuthSessionWatch = auth
    ? watch(() => auth!.user?.id ?? null, () => {
        const sessionKey = currentSessionKey()
        for (const area of areas) {
          bindAreaSaveSession(area.key, sessionKey)
          saveGenerations[area.key] += 1
          clearSaveTimer(area.key)
          setSaveStatus(area.key, 'idle')
        }
      })
    : undefined
  viewMounted = true
  void load()
})

onUnmounted(() => {
  viewMounted = false
  for (const area of areas) clearSaveTimer(area.key)
  stopAuthSessionWatch?.()
  stopAuthSessionWatch = undefined
  auth = null
})
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('providerPreferencesUi.title') }}</h1>
      <p>{{ t('providerPreferencesUi.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('common.loading') }}
  </div>
  <div v-else class="provider-preference-grid">
    <div v-if="loadingError" class="card error" role="alert">{{ loadingError }}</div>
    <section
      v-for="area in areas"
      :key="area.key"
      class="card form-card provider-priority-card"
      :data-area="area.key"
      :aria-busy="isSaving(area.key)"
    >
      <header class="provider-priority-card-header">
        <div class="provider-priority-card-title">
          <component :is="area.icon" class="provider-priority-section-icon" :size="20" weight="duotone" aria-hidden="true" />
          <h2>{{ t(area.title) }}</h2>
        </div>
      </header>
      <div class="provider-priority-form">
        <ol
          class="provider-priority-list"
          :aria-label="t('providerPreferencesUi.priorityListLabel', { area: t(area.title) })"
        >
          <li
            v-for="(providerKey, index) in priorities[area.key]"
            :key="providerKey"
            class="provider-priority-row"
            :data-provider-key="providerKey"
          >
            <div class="provider-priority-main">
              <span class="provider-priority-rank" aria-hidden="true">{{ index + 1 }}</span>
              <div class="provider-priority-label">
                <strong>{{ providerLabel(providerKey) }}</strong>
              </div>
              <span
                v-if="
                  availability(area.key, providerKey)?.available === true
                    || (
                      providerKey === 'withings'
                      && availability(area.key, providerKey)?.status === 'error'
                    )"
                class="status-badge provider-status-badge"
                :class="providerKey === 'withings' && availability(area.key, providerKey)?.status === 'error' ? 'inactive' : 'success'"
              >
                {{ statusLabel(providerKey === 'withings' && availability(area.key, providerKey)?.status === 'error' ? 'error' : 'available') }}
              </span>
            </div>
            <div class="provider-priority-actions">
              <button
                class="icon-button priority-action"
                type="button"
                :aria-label="t('providerPreferencesUi.moveUp', { provider: providerLabel(providerKey) })"
                :disabled="index === 0"
                @click="moveProvider(area.key, index, -1)"
              >
                <PhArrowUp :size="18" aria-hidden="true" />
              </button>
              <button
                class="icon-button priority-action"
                type="button"
                :aria-label="t('providerPreferencesUi.moveDown', { provider: providerLabel(providerKey) })"
                :disabled="index === priorities[area.key].length - 1"
                @click="moveProvider(area.key, index, 1)"
              >
                <PhArrowDown :size="18" aria-hidden="true" />
              </button>
            </div>
          </li>
        </ol>
        <p
          v-if="saveStatus[area.key] !== 'idle'"
          class="provider-priority-save-status"
          :class="saveStatus[area.key]"
          :role="saveStatus[area.key] === 'error' ? 'alert' : 'status'"
          aria-live="polite"
        >
          {{ saveStatusLabel(saveStatus[area.key]) }}
        </p>
      </div>
    </section>
  </div>
</template>
