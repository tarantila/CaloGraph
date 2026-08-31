<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import DateInput from '../components/DateInput.vue'
import { ApiError, api, localizeApiError } from '../api'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import {
  normalizeDietType,
  normalizeGender,
  PROFILE_DIET_OPTIONS,
  PROFILE_GENDER_OPTIONS,
} from '../profile-options'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'
import { useProfilePreferences } from '../composables/useProfilePreferences'
import {
  centimetersToFeetInches,
  feetInchesToCentimeters,
  weightUnitToUnitSystem,
  type UnitSystem,
} from '../units'
import type { OnboardingStep } from '../types'

const t = i18n.global.t.bind(i18n.global)
const auth = useAuthStore()
const router = useRouter()
const target = reactive(createEmptyTargetDraft())
const personal = reactive({
  display_name: '',
  gender: '',
  birth_date: '',
  height_cm: '' as string | number,
  diet_type: '',
  health_notes: '',
  intolerances: '',
})
const error = ref('')
const saving = ref(false)
const loading = ref(false)
const profileLoaded = ref(false)
const signingOut = ref(false)
const targetSaved = ref(false)
const step = computed<OnboardingStep>(() => auth.onboardingStatus?.current_step ?? 'targets')
const fullFlow = computed(() => auth.onboardingStatus?.mode === 'full')
const birthdayMax = computed(() => isoDateInTimeZone(auth.user?.timezone ?? 'UTC'))
const profilePreferences = useProfilePreferences()
const unitSystem = computed<UnitSystem>(() => (
  weightUnitToUnitSystem(profilePreferences.profile.preferred_weight_unit)
))
const imperialFeet = ref('')
const imperialInches = ref('')
const imperialBaseline = reactive({
  canonical: null as number | null,
  feet: '',
  inches: '',
})

function showError(cause: unknown, key: string): void {
  error.value = cause instanceof ApiError ? localizeApiError(cause, key) : t(key)
}

async function advance(expected: OnboardingStep): Promise<void> {
  await auth.advanceOnboarding(expected)
  if (auth.onboardingStatus?.completed) await router.replace({ name: 'overview' })
}

function canonicalHeightValue(): number | null {
  const raw = String(personal.height_cm).trim()
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
  if (value !== null) personal.height_cm = value
}

function heightForPayload(): number | null {
  if (unitSystem.value === 'metric') {
    const raw = String(personal.height_cm).trim()
    return raw ? Number(raw) : null
  }
  return imperialHeightValue()
}

function validatePersonalHeight(): boolean {
  const rawHeight = unitSystem.value === 'metric'
    ? String(personal.height_cm).trim()
    : `${imperialFeet.value} ${imperialInches.value}`.trim()
  const height = heightForPayload()
  if (rawHeight && (height === null || height <= 0 || height > 300)) {
    error.value = t('accountPersonal.heightRange')
    return false
  }
  return true
}

watch(unitSystem, (value) => {
  if (value === 'imperial') syncImperialHeight()
})

async function savePersonal(): Promise<void> {
  await api('/settings/personal-profile', {
    method: 'PUT',
    body: JSON.stringify({
      display_name: personal.display_name.trim() || null,
      gender: normalizeGender(personal.gender) || null,
      birth_date: personal.birth_date || null,
      height_cm: heightForPayload(),
      diet_type: normalizeDietType(personal.diet_type) || null,
      health_notes: personal.health_notes.trim() || null,
      intolerances: personal.intolerances.trim() || null,
    }),
  })
  try {
    await auth.reconcileAchievements(true)
  } catch {
    // Saving personal data remains successful when reconciliation is unavailable.
  }
}

async function submitPersonal(skip = false): Promise<void> {
  error.value = ''
  if (!skip && !validatePersonalHeight()) return
  saving.value = true
  try {
    if (!skip) await savePersonal()
    await advance('personal')
  } catch (cause) {
    showError(cause, 'setup.stepFailed')
  } finally {
    saving.value = false
  }
}

async function submitTarget(): Promise<void> {
  error.value = ''
  saving.value = true
  target.valid_from = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  try {
    if (!targetSaved.value) {
      await saveTargetDraft(target)
      targetSaved.value = true
    }
    if (fullFlow.value) await advance('targets')
    else {
      auth.completeTargetSetup()
      await router.replace({ name: 'overview' })
    }
  } catch (cause) {
    error.value = cause instanceof TargetValidationError
      ? cause.message
      : cause instanceof ApiError
        ? localizeApiError(cause, 'setup.setupFailed')
        : t('setup.setupFailed')
  } finally {
    saving.value = false
  }
}

async function skipSecurity(): Promise<void> {
  error.value = ''
  saving.value = true
  try {
    await advance('security')
  } catch (cause) {
    showError(cause, 'setup.stepFailed')
  } finally {
    saving.value = false
  }
}

async function signOut(): Promise<void> {
  error.value = ''
  signingOut.value = true
  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch (cause) {
    showError(cause, 'setup.logoutFailed')
  } finally {
    signingOut.value = false
  }
}

async function loadProfile(): Promise<void> {
  profileLoaded.value = false
  loading.value = true
  try {
    const [value, preferencesLoaded] = await Promise.all([
      api<{
        display_name: string | null
        gender: string | null
        birth_date: string | null
        height_cm: number | string | null
        diet_type: string | null
        health_notes: string | null
        intolerances: string | null
      }>('/settings/personal-profile'),
      profilePreferences.load(),
    ])
    if (!preferencesLoaded) return
    personal.display_name = value.display_name ?? ''
    personal.gender = normalizeGender(value.gender)
    personal.birth_date = value.birth_date ?? ''
    personal.height_cm = value.height_cm == null ? '' : String(value.height_cm)
    personal.diet_type = normalizeDietType(value.diet_type)
    personal.health_notes = value.health_notes ?? ''
    personal.intolerances = value.intolerances ?? ''
    if (unitSystem.value === 'imperial') syncImperialHeight()
    profileLoaded.value = true
  } catch (cause) {
    showError(cause, 'setup.loadFailed')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (step.value === 'completed') void router.replace({ name: 'overview' })
  else if (step.value === 'personal') void loadProfile()
})
onBeforeUnmount(() => {
  profilePreferences.invalidate()
})
</script>
<template>
  <main class="setup-page">
    <section class="card setup-card" aria-labelledby="setup-title" :aria-busy="saving || loading">
      <header class="setup-heading">
        <img class="setup-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <div>
          <small>{{ t('setup.smallTitle') }}</small>
          <h1 id="setup-title">{{ fullFlow ? t(`setup.steps.${step}`) : t('setup.legacyTitle') }}</h1>
        </div>
      </header>
      <p class="setup-introduction">
        {{ fullFlow ? t('setup.stepIntroduction') : t('setup.legacyIntroduction') }}
      </p>
      <div v-if="loading" role="status" aria-live="polite">{{ t('setup.loading') }}</div>
      <section v-else-if="step === 'personal' && !profileLoaded" class="setup-form" aria-live="assertive">
        <div class="setup-error" role="alert">{{ error || t('setup.loadFailed') }}</div>
        <button class="button" type="button" @click="loadProfile">{{ t('accountPersonal.retry') }}</button>
      </section>
      <form v-else-if="step === 'personal' && profileLoaded" class="setup-form" @submit.prevent="submitPersonal()">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>
        <fieldset>
          <legend>{{ t('setup.personalLegend') }}</legend>
          <label class="field">{{ t('accountPersonal.displayName') }}<input v-model="personal.display_name" name="display-name" /></label>
          <label class="field">{{ t('accountPersonal.gender') }}<select v-model="personal.gender" name="gender"><option v-for="option in PROFILE_GENDER_OPTIONS" :key="option.value" :value="option.value">{{ t(option.label) }}</option></select></label>
          <label class="field">{{ t('accountPersonal.birthDate') }}<DateInput v-model="personal.birth_date" name="birth_date" autocomplete="bday" :max="birthdayMax" :disabled="saving" /></label>
          <label v-if="unitSystem === 'metric'" class="field">
            <span>{{ t('accountPersonal.heightMetric') }}</span>
            <input v-model="personal.height_cm" name="height-cm" type="number" min="0.01" max="300" step="0.01" :placeholder="t('accountPersonal.heightPlaceholder')" :disabled="saving" />
          </label>
          <fieldset v-else class="height-imperial-fields">
            <legend>{{ t('accountPersonal.heightImperial') }}</legend>
            <div class="height-imperial-grid">
              <label class="field">
                <span>{{ t('accountPersonal.heightFeet') }}</span>
                <input v-model="imperialFeet" name="height-feet" type="number" min="0" max="9" step="1" inputmode="numeric" :placeholder="t('accountPersonal.heightFeetPlaceholder')" :disabled="saving" @input="updateImperialHeight" />
              </label>
              <label class="field">
                <span>{{ t('accountPersonal.heightInches') }}</span>
                <input v-model="imperialInches" name="height-inches" type="number" min="0" max="11.999" step="0.001" inputmode="decimal" :placeholder="t('accountPersonal.heightInchesPlaceholder')" :disabled="saving" @input="updateImperialHeight" />
              </label>
            </div>
          </fieldset>
          <label class="field">{{ t('accountPersonal.diet') }}<select v-model="personal.diet_type" name="diet-type"><option v-for="option in PROFILE_DIET_OPTIONS" :key="option.value" :value="option.value">{{ t(option.label) }}</option></select></label>
          <label class="field">{{ t('accountPersonal.intolerances') }}<textarea v-model="personal.intolerances" name="intolerances" /></label>
          <label class="field">{{ t('accountPersonal.healthNotes') }}<textarea v-model="personal.health_notes" name="health-notes" /></label>
        </fieldset>
        <button class="button setup-submit" type="submit" :disabled="saving || signingOut">
          {{ saving ? t('setup.saving') : t('setup.continue') }}
        </button>
        <button class="button secondary" type="button" :disabled="saving || signingOut" @click="submitPersonal(true)">{{ t('setup.skip') }}</button>
      </form>
      <form v-else-if="step === 'targets' || !fullFlow" class="setup-form" @submit.prevent="submitTarget">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>
        <label class="field">{{ t('setup.caloriesPerDay') }}<span class="unit-input"><input v-model.number="target.calories_kcal" name="calories-kcal" type="number" :min="TARGET_LIMITS.caloriesMin" step="1" required /><span>kcal</span></span></label>
        <label class="field">{{ t('setup.proteinPerDay') }}<span class="unit-input"><input v-model.number="target.protein_g" name="protein-g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" required /><span>g</span></span></label>
        <details class="setup-optional"><summary>{{ t('setup.moreGoals') }}</summary><div class="setup-optional-grid">
          <label class="field">{{ t('setup.maintenance') }}<span class="unit-input"><input v-model.number="target.maintenance_kcal" name="maintenance-kcal" type="number" :min="TARGET_LIMITS.maintenanceMin" step="0.001" /><span>{{ t('common.kcal') }}</span></span><small>{{ t('setup.optional') }}</small></label>
          <label class="field">{{ t('setup.carbs') }}<span class="unit-input"><input v-model.number="target.carbs_g" name="carbs-g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /><span>{{ t('common.grams') }}</span></span></label>
          <label class="field">{{ t('setup.fat') }}<span class="unit-input"><input v-model.number="target.fat_g" name="fat-g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /><span>{{ t('common.grams') }}</span></span></label>
          <label class="field">{{ t('setup.fiber') }}<span class="unit-input"><input v-model.number="target.fiber_g" name="fiber-g" type="number" :min="TARGET_LIMITS.nutrientMin" step="1" /><span>{{ t('common.grams') }}</span></span></label>
        </div></details>
        <button class="button setup-submit" type="submit" :disabled="saving || signingOut">{{ saving ? t('setup.saving') : fullFlow ? t('setup.continue') : t('setup.finish') }}</button>
      </form>
      <section v-else-if="step === 'security'" class="setup-form">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>
        <p>{{ t('setup.securityDescription') }}</p>
        <RouterLink class="button secondary" to="/konto/sicherheit">{{ t('setup.configureSecurity') }}</RouterLink>
        <button class="button setup-submit" type="button" :disabled="saving || signingOut" @click="skipSecurity">{{ saving ? t('setup.saving') : t('setup.skipSecurity') }}</button>
      </section>
      <section v-else class="setup-form">
        <p role="status">{{ t('setup.completed') }}</p>
      </section>
      <button class="setup-signout" type="button" :disabled="saving || signingOut" @click="signOut">
        {{ signingOut ? t('setup.loggingOut') : t('navigation.logout') }}
      </button>
    </section>
  </main>
</template>
