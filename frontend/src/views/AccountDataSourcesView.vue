<script setup lang="ts">
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
const selections = ref<Record<DataArea, ProviderKey | ''>>({ nutrition: '', weight: '', activity_energy: '' })
const providers = ref<Record<DataArea, Availability[]>>({ nutrition: [], weight: [], activity_energy: [] })
const loading = ref(true)
const savingArea = ref<DataArea | null>(null)
const error = ref('')
const message = ref('')

function providerLabel(providerKey: ProviderKey): string {
  return t(`providerPreferencesUi.providers.${providerKey}`)
}

function statusLabel(status: ProviderStatus): string {
  return t(`providerPreferencesUi.status.${status}`)
}

function emptyLabel(area: DataArea): string {
  return area === 'nutrition'
    ? t('providerPreferencesUi.useLegacy')
    : t('providerPreferencesUi.useNoProvider')
}

function availability(area: DataArea, providerKey: ProviderKey | ''): Availability | undefined {
  return providers.value[area].find((item) => item.provider_key === providerKey)
}

function canSave(area: DataArea): boolean {
  const provider = selections.value[area]
  return !provider || availability(area, provider)?.available === true
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [preferenceResponse, ...availabilityResponses] = await Promise.all([
      api<{ preferences: PreferenceResponse[] }>('/settings/provider-preferences'),
      ...areas.map(({ key }) => api<AvailabilityResponse>(`/settings/provider-availability/${key}`)),
    ])
    for (const response of availabilityResponses) providers.value[response.data_area] = response.providers
    for (const preference of preferenceResponse.preferences) {
      if (preference.data_area in selections.value) selections.value[preference.data_area] = preference.provider_key
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
  if (!canSave(area)) return
  savingArea.value = area
  error.value = ''
  message.value = ''
  try {
    const provider = selections.value[area]
    if (provider) {
      await api<PreferenceResponse>(`/settings/provider-preferences/${area}`, {
        method: 'PUT',
        body: JSON.stringify({ provider_key: provider }),
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
    savingArea.value = null
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
    <section v-for="area in areas" :key="area.key" class="card form-card" :aria-busy="savingArea === area.key">
      <h2>{{ t(area.title) }}</h2>
      <p class="table-secondary">{{ t(area.description) }}</p>
      <form class="form-grid" @submit.prevent="save(area.key)">
        <label class="field">
          <span>{{ t('providerPreferencesUi.providerLabel') }}</span>
          <select v-model="selections[area.key]" :name="`${area.key}-provider`">
            <option value="">{{ emptyLabel(area.key) }}</option>
            <option v-for="provider in providers[area.key]" :key="provider.provider_key" :value="provider.provider_key">
              {{ providerLabel(provider.provider_key) }} · {{ statusLabel(provider.status) }}
            </option>
          </select>
        </label>
        <p v-if="selections[area.key] && availability(area.key, selections[area.key])?.available === false" class="import-message error" role="alert">
          {{ t('providerPreferencesUi.unavailable') }}
        </p>
        <button class="button compact-action" type="submit" :disabled="savingArea !== null || !canSave(area.key)">
          {{ savingArea === area.key ? t('providerPreferencesUi.saving') : t('common.save') }}
        </button>
      </form>
    </section>
  </div>
</template>
