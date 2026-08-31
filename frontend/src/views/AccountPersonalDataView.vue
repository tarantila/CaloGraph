<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import { api, ApiError, localizeApiError } from '../api'
import DateInput from '../components/DateInput.vue'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import {
  normalizeDietType,
  normalizeGender,
  PROFILE_DIET_OPTIONS,
  PROFILE_GENDER_OPTIONS,
  type DietType,
  type Gender,
  type ProfileDietValue,
  type ProfileGenderValue,
} from '../profile-options'
import { useAuthStore } from '../stores/auth'
import { useProfilePreferences } from '../composables/useProfilePreferences'
import {
  centimetersToFeetInches,
  feetInchesToCentimeters,
  weightUnitToUnitSystem,
  type UnitSystem,
} from '../units'
const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const profilePreferences = useProfilePreferences()
const unitSystem = computed<UnitSystem>(() => (
  weightUnitToUnitSystem(profilePreferences.profile.preferred_weight_unit)
))

interface PersonalProfile {
  display_name: string | null
  gender: Gender | string | null
  birth_date: string | null
  height_cm: number | string | null
  diet_type: DietType | string | null
  health_notes: string | null
  intolerances: string | null
}

interface PersonalForm {
  display_name: string
  gender: ProfileGenderValue
  birth_date: string
  height_cm: string | number
  diet_type: ProfileDietValue
  health_notes: string
  intolerances: string
}

const form = reactive<PersonalForm>({
  display_name: '',
  gender: '',
  birth_date: '',
  height_cm: '',
  diet_type: '',
  health_notes: '',
  intolerances: '',
})
const imperialFeet = ref('')
const imperialInches = ref('')
const imperialBaseline = reactive({
  canonical: null as number | null,
  feet: '',
  inches: '',
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

function canonicalHeightValue(): number | null {
  const raw = String(form.height_cm).trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function syncImperialHeight(): void {
  const canonical = canonicalHeightValue()
  if (canonical === null) {
    imperialFeet.value = ''
    imperialInches.value = ''
    imperialBaseline.canonical = null
    imperialBaseline.feet = ''
    imperialBaseline.inches = ''
    return
  }
  const display = centimetersToFeetInches(canonical)
  imperialFeet.value = String(display.feet)
  imperialInches.value = String(display.inches)
  imperialBaseline.canonical = canonical
  imperialBaseline.feet = imperialFeet.value
  imperialBaseline.inches = imperialInches.value
}

function imperialHeightValue(): number | null {
  const feet = String(imperialFeet.value).trim()
  const inches = String(imperialInches.value).trim()
  if (!feet && !inches) return null
  if (feet === imperialBaseline.feet && inches === imperialBaseline.inches) {
    return imperialBaseline.canonical
  }
  return feetInchesToCentimeters(feet, inches)

}
function updateImperialHeight(): void {
  const value = imperialHeightValue()
  if (value !== null) form.height_cm = value
}

function heightForPayload(): number | null {
  if (unitSystem.value === 'metric') {
    const raw = String(form.height_cm).trim()
    return raw ? Number(raw) : null
  }
  return imperialHeightValue()
}

function hydrate(value: PersonalProfile): void {
  form.display_name = value.display_name ?? ''
  form.gender = normalizeGender(value.gender)
  form.birth_date = value.birth_date ?? ''
  form.height_cm = value.height_cm == null ? '' : String(value.height_cm)
  form.diet_type = normalizeDietType(value.diet_type)
  form.health_notes = value.health_notes ?? ''
  form.intolerances = value.intolerances ?? ''
  if (unitSystem.value === 'imperial') syncImperialHeight()
}

watch(unitSystem, (value) => {
  if (value === 'imperial') syncImperialHeight()
})

async function load(): Promise<void> {
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    const [result] = await Promise.all([
      api<PersonalProfile>('/settings/personal-profile'),
      profilePreferences.load(),
    ])
    if (generation !== loadGeneration) return
    hydrate(result)
    if (unitSystem.value === 'imperial') syncImperialHeight()
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
  const rawHeight = unitSystem.value === 'metric'
    ? String(form.height_cm).trim()
    : `${imperialFeet.value} ${imperialInches.value}`.trim()
  const height = heightForPayload()
  if (rawHeight && (height === null || height <= 0 || height > 300)) {
    error.value = t('accountPersonal.heightRange')
    return false
  }
  return true
}

function payload(): PersonalProfile {
  return {
    display_name: form.display_name.trim() || null,
    gender: normalizeGender(form.gender) || null,
    birth_date: form.birth_date || null,
    height_cm: heightForPayload(),
    diet_type: normalizeDietType(form.diet_type) || null,
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
  profilePreferences.invalidate()
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
            <option v-for="option in PROFILE_GENDER_OPTIONS" :key="option.value" :value="option.value">
              {{ t(option.label) }}
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

        <label v-if="unitSystem === 'metric'" class="field">
          <span>{{ t('accountPersonal.heightMetric') }}</span>
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
        </label>

        <fieldset v-else class="height-imperial-fields">
          <legend>{{ t('accountPersonal.heightImperial') }}</legend>
          <div class="height-imperial-grid">
            <label class="field">
              <span>{{ t('accountPersonal.heightFeet') }}</span>
              <input
                v-model="imperialFeet"
                name="height_feet"
                type="number"
                min="0"
                max="9"
                step="1"
                inputmode="numeric"
                :placeholder="t('accountPersonal.heightFeetPlaceholder')"
                :disabled="saving"
                @input="updateImperialHeight"
              />
            </label>
            <label class="field">
              <span>{{ t('accountPersonal.heightInches') }}</span>
              <input
                v-model="imperialInches"
                name="height_inches"
                type="number"
                min="0"
                max="11.999"
                step="0.001"
                inputmode="decimal"
                :placeholder="t('accountPersonal.heightInchesPlaceholder')"
                :disabled="saving"
                @input="updateImperialHeight"
              />
            </label>
          </div>
        </fieldset>

        <label class="field">
          <span>{{ t('accountPersonal.diet') }}</span>
          <select v-model="form.diet_type" name="diet_type" :disabled="saving">
            <option v-for="option in PROFILE_DIET_OPTIONS" :key="option.value" :value="option.value">
              {{ t(option.label) }}
            </option>
          </select>
        </label>

        <label class="field full">
          <span>{{ t('accountPersonal.intolerances') }}</span>
          <textarea
            v-model="form.intolerances"
            name="intolerances"
            maxlength="2000"
            rows="5"
            :placeholder="t('accountPersonal.intolerancesPlaceholder')"
            :disabled="saving"
          />
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
