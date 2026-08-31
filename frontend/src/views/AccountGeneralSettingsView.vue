<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { ApiError, localizeApiError } from '../api'
import { i18n } from '../i18n'
import {
  useProfilePreferences,
  type SupportedLanguage,
} from '../composables/useProfilePreferences'
import {
  unitSystemToWeightUnit,
  weightUnitToUnitSystem,
  type UnitSystem,
} from '../units'
const t = i18n.global.t.bind(i18n.global)
const { profile, loaded, load, save, invalidate } = useProfilePreferences()
const unitSystem = computed<UnitSystem>({
  get: () => weightUnitToUnitSystem(profile.preferred_weight_unit),
  set: (value) => { profile.preferred_weight_unit = unitSystemToWeightUnit(value) },
})
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const message = ref('')

const fallbackTimezones = [
  'UTC',
  'Europe/Berlin',
  'Europe/Vienna',
  'Europe/Zurich',
  'Europe/Amsterdam',
  'Europe/Paris',
  'Europe/London',
  'Europe/Madrid',
  'Europe/Rome',
  'Europe/Warsaw',
  'Europe/Athens',
  'Europe/Helsinki',
  'Europe/Bucharest',
  'Europe/Kyiv',
  'Europe/Istanbul',
  'Africa/Cairo',
  'Africa/Johannesburg',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Bangkok',
  'Asia/Singapore',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Australia/Sydney',
  'Pacific/Auckland',
  'America/St_Johns',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'America/Anchorage',
  'Pacific/Honolulu',
  'America/Sao_Paulo',
]

function supportedTimezones(): string[] {
  try {
    return Intl.supportedValuesOf('timeZone')
  } catch {
    return fallbackTimezones
  }
}

const timezoneOptions = computed(() =>
  [...new Set(['UTC', profile.timezone, ...supportedTimezones()])].sort((left, right) =>
    left.localeCompare(right, i18n.global.locale.value),
  ),
)

async function loadProfile(): Promise<void> {
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    await load()
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountGeneral.loadFailed', { preserveDetail: false })
      : t('accountGeneral.loadFailed')
  } finally {
    loading.value = false
  }
}

async function saveProfile(): Promise<void> {
  error.value = ''
  message.value = ''
  saving.value = true
  try {
    const result = await save()
    if (result) message.value = t('accountGeneral.saved')
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountGeneral.saveFailed', { preserveDetail: false })
      : t('accountGeneral.saveFailed')
  } finally {
    saving.value = false
  }
}

onMounted(() => { void loadProfile() })
onBeforeUnmount(() => { invalidate() })

const languages: readonly SupportedLanguage[] = ['de', 'en']
const unitSystems: readonly UnitSystem[] = ['metric', 'imperial']
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('accountGeneral.title') }}</h1>
      <p>{{ t('accountGeneral.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('accountGeneral.loading') }}
  </div>
  <template v-else>
    <section v-if="error && !loaded" class="card account-feedback error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button compact-action" type="button" @click="loadProfile">{{ t('accountGeneral.retry') }}</button>
    </section>

    <section v-if="loaded" class="card form-card account-form-card" :aria-busy="saving">
      <h2>{{ t('accountGeneral.formTitle') }}</h2>
      <p>{{ t('accountGeneral.formDescription') }}</p>
      <form class="form-grid account-form-grid" @submit.prevent="saveProfile">
        <label class="field">
          <span>{{ t('accountGeneral.language') }}</span>
          <select v-model="profile.language" name="language" :disabled="saving">
            <option v-for="value in languages" :key="value" :value="value">
              {{ t(`accountGeneral.languageOptions.${value}`) }}
            </option>
          </select>
        </label>

        <label class="field">
          <span>{{ t('accountGeneral.timezone') }}</span>
          <select v-model="profile.timezone" name="timezone" required :disabled="saving">
            <option v-for="timezone in timezoneOptions" :key="timezone" :value="timezone">
              {{ timezone.replaceAll('_', ' ') }}
            </option>
          </select>
          <small>{{ t('accountGeneral.timezoneHelp') }}</small>
        </label>

        <label class="field">
          <span>{{ t('accountGeneral.weekStartsOn') }}</span>
          <select v-model.number="profile.week_starts_on" name="week_starts_on" :disabled="saving">
            <option :value="0">{{ t('accountGeneral.weekDays.monday') }}</option>
            <option :value="6">{{ t('accountGeneral.weekDays.sunday') }}</option>
          </select>
        </label>

        <label class="field">
          <span>{{ t('accountGeneral.unitSystem') }}</span>
          <select v-model="unitSystem" name="unit_system" :disabled="saving">
            <option v-for="value in unitSystems" :key="value" :value="value">
              {{ t(`accountGeneral.unitSystemOptions.${value}`) }}
            </option>
          </select>
        </label>

        <div v-if="error" class="account-form-error" role="alert" aria-live="assertive">{{ error }}</div>
        <p v-if="message" class="account-form-success" role="status" aria-live="polite">{{ message }}</p>
        <button class="button compact-action account-submit" type="submit" :disabled="saving">
          {{ saving ? t('accountGeneral.saving') : t('accountGeneral.save') }}
        </button>
      </form>
    </section>
  </template>
</template>
