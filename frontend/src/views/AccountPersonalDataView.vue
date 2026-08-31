<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { api, ApiError, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()

type Gender = 'female' | 'male' | 'non_binary' | 'other' | 'prefer_not_to_say'
type DietType = 'no_special_diet' | 'vegetarian' | 'vegan' | 'pescetarian' | 'other' | 'prefer_not_to_say'

interface PersonalProfile {
  display_name: string | null
  gender: Gender | null
  birth_date: string | null
  height_cm: number | string | null
  diet_type: DietType | null
  health_notes: string | null
  intolerances: string | null
}

interface PersonalForm {
  display_name: string
  gender: '' | Gender
  birth_date: string
  height_cm: string | number
  diet_type: '' | DietType
  health_notes: string
  intolerances: string
}

const genders: readonly Gender[] = ['female', 'male', 'non_binary', 'other', 'prefer_not_to_say']
const dietTypes: readonly DietType[] = ['no_special_diet', 'vegetarian', 'vegan', 'pescetarian', 'other', 'prefer_not_to_say']
const form = reactive<PersonalForm>({
  display_name: '',
  gender: '',
  birth_date: '',
  height_cm: '',
  diet_type: '',
  health_notes: '',
  intolerances: '',
})
const loading = ref(true)
const loaded = ref(false)
const saving = ref(false)
const error = ref('')
const message = ref('')
let loadGeneration = 0

function accountToday(): string {
  return isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
}

const birthdayMax = ref(accountToday())
let birthdayDayTimer: number | null = null

function refreshBirthdayMax(): void {
  birthdayMax.value = accountToday()
}

function hydrate(value: PersonalProfile): void {
  form.display_name = value.display_name ?? ''
  form.gender = value.gender ?? ''
  form.birth_date = value.birth_date ?? ''
  form.height_cm = value.height_cm == null ? '' : String(value.height_cm)
  form.diet_type = value.diet_type ?? ''
  form.health_notes = value.health_notes ?? ''
  form.intolerances = value.intolerances ?? ''
}

async function load(): Promise<void> {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await api<PersonalProfile>('/settings/personal-profile')
    if (generation !== loadGeneration) return
    hydrate(result)
    loaded.value = true
  } catch (cause) {
    if (generation !== loadGeneration) return
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountPersonal.loadFailed', { preserveDetail: false })
      : t('accountPersonal.loadFailed')
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

function validate(): boolean {
  if (form.birth_date && form.birth_date > birthdayMax.value) {
    error.value = t('accountPersonal.birthDateFuture')
    return false
  }
  const heightValue = String(form.height_cm).trim()
  if (heightValue) {
    const height = Number(heightValue)
    if (!Number.isFinite(height) || height <= 0 || height > 300) {
      error.value = t('accountPersonal.heightRange')
      return false
    }
  }
  return true
}

function payload(): PersonalProfile {
  const height = String(form.height_cm).trim()
  return {
    display_name: form.display_name.trim() || null,
    gender: form.gender || null,
    birth_date: form.birth_date || null,
    height_cm: height ? Number(height) : null,
    diet_type: form.diet_type || null,
    health_notes: form.health_notes.trim() || null,
    intolerances: form.intolerances.trim() || null,
  }
}

async function save(): Promise<void> {
  error.value = ''
  message.value = ''
  if (!validate()) return

  saving.value = true
  try {
    const result = await api<PersonalProfile>('/settings/personal-profile', {
      method: 'PUT',
      body: JSON.stringify(payload()),
    })
    hydrate(result)
    message.value = t('accountPersonal.saved')
    try {
      await auth.reconcileAchievements(true)
    } catch {
      // Saving personal data remains successful when reconciliation is unavailable.
    }
  } catch (cause) {
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountPersonal.saveFailed', { preserveDetail: false })
      : t('accountPersonal.saveFailed')
  } finally {
    saving.value = false
  }
}
onMounted(() => {
  refreshBirthdayMax()
  birthdayDayTimer = window.setInterval(refreshBirthdayMax, 60_000)
  void load()
})
onBeforeUnmount(() => {
  ++loadGeneration
  if (birthdayDayTimer !== null) window.clearInterval(birthdayDayTimer)
})
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('accountPersonal.title') }}</h1>
      <p>{{ t('accountPersonal.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('accountPersonal.loading') }}
  </div>
  <template v-else>
    <section v-if="error && !loaded" class="card account-feedback error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button class="button compact-action" type="button" @click="load">{{ t('accountPersonal.retry') }}</button>
    </section>

    <section v-if="loaded" class="card form-card account-form-card" :aria-busy="saving">
      <h2>{{ t('accountPersonal.formTitle') }}</h2>
      <p>{{ t('accountPersonal.disclosure') }}</p>
      <form class="form-grid account-form-grid" @submit.prevent="save">
        <label class="field">
          <span>{{ t('accountPersonal.displayName') }}</span>
          <input
            v-model="form.display_name"
            name="display_name"
            type="text"
            maxlength="120"
            autocomplete="name"
            :placeholder="t('accountPersonal.displayNamePlaceholder')"
            :disabled="saving"
          />
          <small>{{ t('accountPersonal.displayNameHelp') }}</small>
        </label>

        <label class="field">
          <span>{{ t('accountPersonal.gender') }}</span>
          <select v-model="form.gender" name="gender" :disabled="saving">
            <option value="">{{ t('accountPersonal.notSpecified') }}</option>
            <option v-for="value in genders" :key="value" :value="value">
              {{ t(`accountPersonal.genderOptions.${value}`) }}
            </option>
          </select>
        </label>

        <label class="field">
          <span>{{ t('accountPersonal.birthDate') }}</span>
          <DateInput
            v-model="form.birth_date"
            name="birth_date"
            autocomplete="bday"
            :max="birthdayMax"
            :disabled="saving"
          />
        </label>

        <label class="field">
          <span>{{ t('accountPersonal.height') }}</span>
          <input
            v-model="form.height_cm"
            name="height_cm"
            type="number"
            min="0.01"
            max="300"
            step="0.01"
            inputmode="decimal"
            :placeholder="t('accountPersonal.heightPlaceholder')"
            :disabled="saving"
          />
          <small>{{ t('accountPersonal.heightHelp') }}</small>
        </label>

        <label class="field">
          <span>{{ t('accountPersonal.diet') }}</span>
          <select v-model="form.diet_type" name="diet_type" :disabled="saving">
            <option value="">{{ t('accountPersonal.notSpecified') }}</option>
            <option v-for="value in dietTypes" :key="value" :value="value">
              {{ t(`accountPersonal.dietOptions.${value}`) }}
            </option>
          </select>
        </label>

        <label class="field full">
          <span>{{ t('accountPersonal.healthNotes') }}</span>
          <textarea
            v-model="form.health_notes"
            name="health_notes"
            maxlength="4000"
            rows="5"
            :placeholder="t('accountPersonal.healthNotesPlaceholder')"
            :disabled="saving"
          />
          <small>{{ t('accountPersonal.healthNotesHelp') }}</small>
        </label>

        <label class="field full">
          <span>{{ t('accountPersonal.intolerances') }}</span>
          <textarea
            v-model="form.intolerances"
            name="intolerances"
            maxlength="2000"
            rows="4"
            :placeholder="t('accountPersonal.intolerancesPlaceholder')"
            :disabled="saving"
          />
          <small>{{ t('accountPersonal.intolerancesHelp') }}</small>
        </label>

        <div v-if="error && !loading" class="account-form-error" role="alert" aria-live="assertive">{{ error }}</div>
        <p v-if="message" class="account-form-success" role="status" aria-live="polite">{{ message }}</p>
        <button class="button compact-action account-submit" type="submit" :disabled="saving">
          {{ saving ? t('accountPersonal.saving') : t('accountPersonal.save') }}
        </button>
      </form>
    </section>
  </template>
</template>
