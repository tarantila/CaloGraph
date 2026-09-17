<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { ApiError, api, localizeApiError } from '../api'
import { i18n } from '../i18n'

const t = i18n.global.t.bind(i18n.global)

type ProviderKey = 'apple_health' | 'google_health' | 'yazio'
type ProviderStatus = 'available' | 'disabled' | 'not_configured' | 'reauth_required' | 'no_data'

interface PreferenceResponse {
  data_area: string
  provider_key: ProviderKey
}

interface Availability {
  provider_key: ProviderKey
  available: boolean
  status: ProviderStatus
}

interface AvailabilityResponse {
  data_area: string
  providers: Availability[]
}

const selectedProvider = ref<ProviderKey | ''>('')
const providers = ref<Availability[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')

const selectedAvailability = computed(() => providers.value.find((item) => item.provider_key === selectedProvider.value))
const canSave = computed(() => !selectedProvider.value || selectedAvailability.value?.available === true)

function providerLabel(providerKey: ProviderKey): string {
  return t(`providerPreferencesUi.providers.${providerKey}`)
}

function statusLabel(status: ProviderStatus): string {
  return t(`providerPreferencesUi.status.${status}`)
}

async function load(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [preferenceResponse, availabilityResponse] = await Promise.all([
      api<{ preferences: PreferenceResponse[] }>('/settings/provider-preferences'),
      api<AvailabilityResponse>('/settings/provider-availability/nutrition'),
    ])
    providers.value = availabilityResponse.providers
    selectedProvider.value = preferenceResponse.preferences.find((item) => item.data_area === 'nutrition')?.provider_key ?? ''
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'providerPreferencesUi.loadFailed')
      : t('providerPreferencesUi.loadFailed')
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!canSave.value) return
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    if (selectedProvider.value) {
      await api<PreferenceResponse>('/settings/provider-preferences/nutrition', {
        method: 'PUT',
        body: JSON.stringify({ provider_key: selectedProvider.value }),
      })
    } else {
      await api<void>('/settings/provider-preferences/nutrition', { method: 'DELETE' })
    }
    message.value = t('providerPreferencesUi.saved')
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'providerPreferencesUi.saveFailed')
      : t('providerPreferencesUi.saveFailed')
  } finally {
    saving.value = false
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
  <section v-else class="card form-card" :aria-busy="saving">
    <div v-if="error" class="card error" role="alert">{{ error }}</div>
    <p v-if="message" class="setup-notice" role="status">{{ message }}</p>
    <form class="form-grid" @submit.prevent="save">
      <label class="field">
        <span>{{ t('providerPreferencesUi.providerLabel') }}</span>
        <select v-model="selectedProvider" name="nutrition-provider">
          <option value="">{{ t('providerPreferencesUi.useLegacy') }}</option>
          <option v-for="provider in providers" :key="provider.provider_key" :value="provider.provider_key">
            {{ providerLabel(provider.provider_key) }} · {{ statusLabel(provider.status) }}
          </option>
        </select>
      </label>
      <p class="table-secondary">{{ t('providerPreferencesUi.selectionHelp') }}</p>
      <p v-if="selectedAvailability && !selectedAvailability.available" class="import-message error" role="alert">
        {{ t('providerPreferencesUi.unavailable') }}
      </p>
      <button class="button compact-action" type="submit" :disabled="saving || !canSave">
        {{ saving ? t('providerPreferencesUi.saving') : t('common.save') }}
      </button>
    </form>
  </section>
</template>
