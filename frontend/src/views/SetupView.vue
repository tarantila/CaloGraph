<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, api, localizeApiError } from '../api'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'
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
const signingOut = ref(false)
const targetSaved = ref(false)
const step = computed<OnboardingStep>(() => auth.onboardingStatus?.current_step ?? 'targets')
const fullFlow = computed(() => auth.onboardingStatus?.mode === 'full')

function showError(cause: unknown, key: string): void {
  error.value = cause instanceof ApiError ? localizeApiError(cause, key) : t(key)
}

async function advance(expected: OnboardingStep): Promise<void> {
  await auth.advanceOnboarding(expected)
  if (auth.onboardingStatus?.completed) await router.replace({ name: 'overview' })
}

async function savePersonal(): Promise<void> {
  const height = String(personal.height_cm).trim()
  await api('/settings/personal-profile', {
    method: 'PUT',
    body: JSON.stringify({
      display_name: personal.display_name.trim() || null,
      gender: personal.gender || null,
      birth_date: personal.birth_date || null,
      height_cm: height ? Number(height) : null,
      diet_type: personal.diet_type || null,
      health_notes: personal.health_notes.trim() || null,
      intolerances: personal.intolerances.trim() || null,
    }),
  })
}

async function submitPersonal(skip = false): Promise<void> {
  error.value = ''
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
  loading.value = true
  try {
    const value = await api<{
      display_name: string | null
      gender: string | null
      birth_date: string | null
      height_cm: number | string | null
      diet_type: string | null
      health_notes: string | null
      intolerances: string | null
    }>('/settings/personal-profile')
    personal.display_name = value.display_name ?? ''
    personal.gender = value.gender ?? ''
    personal.birth_date = value.birth_date ?? ''
    personal.height_cm = value.height_cm == null ? '' : String(value.height_cm)
    personal.diet_type = value.diet_type ?? ''
    personal.health_notes = value.health_notes ?? ''
    personal.intolerances = value.intolerances ?? ''
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
      <form v-else-if="step === 'personal'" class="setup-form" @submit.prevent="submitPersonal()">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>
        <fieldset>
          <legend>{{ t('setup.personalLegend') }}</legend>
          <label class="field">{{ t('accountPersonal.displayName') }}<input v-model="personal.display_name" name="display-name" /></label>
          <label class="field">{{ t('accountPersonal.gender') }}<select v-model="personal.gender" name="gender"><option value="">—</option><option value="female">{{ t('accountPersonal.genderOptions.female') }}</option><option value="male">{{ t('accountPersonal.genderOptions.male') }}</option><option value="non_binary">{{ t('accountPersonal.genderOptions.non_binary') }}</option><option value="other">{{ t('accountPersonal.genderOptions.other') }}</option><option value="prefer_not_to_say">{{ t('accountPersonal.genderOptions.prefer_not_to_say') }}</option></select></label>
          <label class="field">{{ t('accountPersonal.birthDate') }}<input v-model="personal.birth_date" name="birth-date" type="date" /></label>
          <label class="field">{{ t('accountPersonal.height') }}<input v-model="personal.height_cm" name="height-cm" type="number" min="0.01" max="300" step="0.01" /></label>
          <label class="field">{{ t('accountPersonal.diet') }}<select v-model="personal.diet_type" name="diet-type"><option value="">—</option><option value="no_special_diet">{{ t('accountPersonal.dietOptions.no_special_diet') }}</option><option value="vegetarian">{{ t('accountPersonal.dietOptions.vegetarian') }}</option><option value="vegan">{{ t('accountPersonal.dietOptions.vegan') }}</option><option value="pescetarian">{{ t('accountPersonal.dietOptions.pescetarian') }}</option><option value="other">{{ t('accountPersonal.dietOptions.other') }}</option><option value="prefer_not_to_say">{{ t('accountPersonal.dietOptions.prefer_not_to_say') }}</option></select></label>
          <label class="field">{{ t('accountPersonal.healthNotes') }}<textarea v-model="personal.health_notes" name="health-notes" /></label>
          <label class="field">{{ t('accountPersonal.intolerances') }}<textarea v-model="personal.intolerances" name="intolerances" /></label>
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
