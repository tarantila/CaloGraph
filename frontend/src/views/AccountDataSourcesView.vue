<script setup lang="ts">
import { PhArrowDown, PhArrowUp, PhPlus, PhTrash } from '@phosphor-icons/vue'
import { onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)

type DataArea = 'nutrition' | 'weight' | 'activity_energy'
type ProviderKey = 'apple_health' | 'google_health' | 'health_auto_export' | 'yazio'
type ProviderStatus = 'available' | 'disabled' | 'not_configured' | 'reauth_required' | 'no_data'

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

const areas: Array<{ key: DataArea; title: string; description: string }> = [
  { key: 'nutrition', title: 'providerPreferencesUi.nutritionTitle', description: 'providerPreferencesUi.nutritionDescription' },
  { key: 'weight', title: 'providerPreferencesUi.weightTitle', description: 'providerPreferencesUi.weightDescription' },
  { key: 'activity_energy', title: 'providerPreferencesUi.activityTitle', description: 'providerPreferencesUi.activityDescription' },
]
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
const addSelections = ref<Record<DataArea, ProviderKey | ''>>({
  nutrition: '',
  weight: '',
  activity_energy: '',
})
const loading = ref(true)
const savingAreas = ref<Set<DataArea>>(new Set())
const error = ref('')
const message = ref('')

function providerLabel(providerKey: ProviderKey): string {
  return t(`providerPreferencesUi.providers.${providerKey}`)
}

function statusLabel(status: ProviderStatus): string {
  return t(`providerPreferencesUi.status.${status}`)
}

function availability(area: DataArea, providerKey: ProviderKey): Availability | undefined {
  return providers.value[area].find((item) => item.provider_key === providerKey)
}

function availableProviders(area: DataArea): Availability[] {
  return providers.value[area].filter((provider) => (
    provider.available && !priorities.value[area].includes(provider.provider_key)
  ))
}

function isUnavailable(area: DataArea, providerKey: ProviderKey): boolean {
  return availability(area, providerKey)?.available !== true
}

function isSaving(area: DataArea): boolean {
  return savingAreas.value.has(area)
}

function canSave(area: DataArea): boolean {
  return priorities.value[area].every((providerKey) => !isUnavailable(area, providerKey))
}

function addProvider(area: DataArea): void {
  const providerKey = addSelections.value[area]
  if (!providerKey || isUnavailable(area, providerKey) || priorities.value[area].includes(providerKey)) return
  priorities.value[area] = [...priorities.value[area], providerKey]
  addSelections.value[area] = ''
  message.value = ''
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
  message.value = ''
}

function removeProvider(area: DataArea, index: number): void {
  priorities.value[area] = priorities.value[area].filter((_, itemIndex) => itemIndex !== index)
  message.value = ''
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [preferenceResponse, ...availabilityResponses] = await Promise.all([
      api<{ preferences: PreferenceResponse[] }>('/settings/provider-preferences'),
      ...areas.map(({ key }) => api<AvailabilityResponse>(`/settings/provider-availability/${key}`)),
    ])
    for (const area of areas) {
      priorities.value[area.key] = []
      providers.value[area.key] = []
      addSelections.value[area.key] = ''
    }
    for (const response of availabilityResponses) providers.value[response.data_area] = response.providers
    for (const preference of preferenceResponse.preferences) {
      if (preference.data_area in priorities.value) {
        priorities.value[preference.data_area] = [
          ...priorities.value[preference.data_area],
          preference.provider_key,
        ]
      }
    }
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'providerPreferencesUi.loadFailed')
      : t('providerPreferencesUi.loadFailed')
  } finally {
    loading.value = false
  }
}

async function save(area: DataArea): Promise<void> {
  if (!canSave(area) || isSaving(area)) return
  savingAreas.value = new Set([...savingAreas.value, area])
  error.value = ''
  message.value = ''
  try {
    const providerKeys = priorities.value[area]
    if (providerKeys.length > 0) {
      await api<PreferenceResponse>(`/settings/provider-preferences/${area}`, {
        method: 'PUT',
        body: JSON.stringify({ provider_keys: providerKeys }),
      })
    } else {
      await api<void>(`/settings/provider-preferences/${area}`, { method: 'DELETE' })
    }
    message.value = t('providerPreferencesUi.saved')
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'providerPreferencesUi.saveFailed')
      : t('providerPreferencesUi.saveFailed')
  } finally {
    const nextSavingAreas = new Set(savingAreas.value)
    nextSavingAreas.delete(area)
    savingAreas.value = nextSavingAreas
  }
}

onMounted(() => { void load() })
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
    <div v-if="error" class="card error" role="alert">{{ error }}</div>
    <p v-if="message" class="setup-notice" role="status">{{ message }}</p>
    <section
      v-for="area in areas"
      :key="area.key"
      class="card form-card provider-priority-card"
      :data-area="area.key"
      :aria-busy="isSaving(area.key)"
    >
      <header class="provider-priority-card-header">
        <div>
          <h2>{{ t(area.title) }}</h2>
          <p class="table-secondary">{{ t(area.description) }}</p>
        </div>
        <span class="provider-priority-fallback">{{ t('providerPreferencesUi.fallbackHint') }}</span>
      </header>
      <form class="provider-priority-form" @submit.prevent="save(area.key)">
        <ol
          v-if="priorities[area.key].length"
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
                <span
                  class="status-badge provider-status-badge"
                  :class="isUnavailable(area.key, providerKey) ? 'inactive' : 'success'"
                >
                  {{ statusLabel(availability(area.key, providerKey)?.status ?? 'no_data') }}
                </span>
                <span v-if="isUnavailable(area.key, providerKey)" class="status-detail">
                  {{ t('providerPreferencesUi.unavailable') }}
                </span>
              </div>
            </div>
            <div class="provider-priority-actions">
              <button
                class="icon-button priority-action"
                type="button"
                :aria-label="t('providerPreferencesUi.moveUp', { provider: providerLabel(providerKey) })"
                :disabled="index === 0 || isSaving(area.key)"
                @click="moveProvider(area.key, index, -1)"
              >
                <PhArrowUp :size="18" aria-hidden="true" />
              </button>
              <button
                class="icon-button priority-action"
                type="button"
                :aria-label="t('providerPreferencesUi.moveDown', { provider: providerLabel(providerKey) })"
                :disabled="index === priorities[area.key].length - 1 || isSaving(area.key)"
                @click="moveProvider(area.key, index, 1)"
              >
                <PhArrowDown :size="18" aria-hidden="true" />
              </button>
              <button
                class="icon-button priority-action remove"
                type="button"
                :aria-label="t('providerPreferencesUi.removeProvider', { provider: providerLabel(providerKey) })"
                :disabled="isSaving(area.key)"
                @click="removeProvider(area.key, index)"
              >
                <PhTrash :size="18" aria-hidden="true" />
              </button>
            </div>
          </li>
        </ol>
        <p v-else class="provider-priority-empty">{{ t('providerPreferencesUi.empty') }}</p>

        <div class="provider-priority-add">
          <label class="field">
            <span>{{ t('providerPreferencesUi.addProviderLabel') }}</span>
            <select
              v-model="addSelections[area.key]"
              :name="`${area.key}-add-provider`"
              :disabled="!availableProviders(area.key).length || isSaving(area.key)"
            >
              <option value="">{{ t('providerPreferencesUi.addProviderPlaceholder') }}</option>
              <option
                v-for="provider in availableProviders(area.key)"
                :key="provider.provider_key"
                :value="provider.provider_key"
              >
                {{ providerLabel(provider.provider_key) }} · {{ statusLabel(provider.status) }}
              </option>
            </select>
          </label>
          <button
            class="button secondary compact-action"
            type="button"
            :aria-label="t('providerPreferencesUi.addProvider')"
            :disabled="!addSelections[area.key] || isSaving(area.key)"
            @click="addProvider(area.key)"
          >
            <PhPlus :size="17" aria-hidden="true" />
            {{ t('providerPreferencesUi.addProvider') }}
          </button>
        </div>
        <p v-if="priorities[area.key].some((providerKey) => isUnavailable(area.key, providerKey))" class="provider-priority-warning" role="alert">
          {{ t('providerPreferencesUi.unavailableSaveHint') }}
        </p>
        <button class="button compact-action provider-priority-save" type="submit" :disabled="isSaving(area.key) || !canSave(area.key)">
          {{ isSaving(area.key) ? t('providerPreferencesUi.saving') : t('providerPreferencesUi.save') }}
        </button>
      </form>
    </section>
  </div>
</template>
