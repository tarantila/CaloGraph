<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, localizeApiError } from '../api'
import { isoDateInTimeZone } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'

const t = i18n.global.t.bind(i18n.global)

const auth = useAuthStore()
const router = useRouter()
const target = reactive(createEmptyTargetDraft())
const error = ref('')
const saving = ref(false)
const signingOut = ref(false)

async function submit() {
  error.value = ''
  saving.value = true
  target.valid_from = isoDateInTimeZone(auth.user?.timezone ?? 'UTC')
  try {
    await saveTargetDraft(target)
    auth.completeTargetSetup()
    await router.replace({ name: 'overview' })
  } catch (cause) {
    error.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'setup.setupFailed')
        : cause instanceof TargetValidationError
          ? cause.message
          : t('setup.setupFailed')
  } finally {
    saving.value = false
  }
}

async function signOut() {
  error.value = ''
  signingOut.value = true
  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch (cause) {
    error.value = cause instanceof ApiError ? localizeApiError(cause, 'setup.logoutFailed') : t('setup.logoutFailed')
  } finally {
    signingOut.value = false
  }
}
</script>

<template>
  <main class="setup-page">
    <section class="card setup-card" aria-labelledby="setup-title">
      <header class="setup-heading">
        <img class="setup-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <div>
          <small>{{ t('setup.smallTitle') }}</small>
          <h1 id="setup-title">{{ t('setup.title') }}</h1>
        </div>
      </header>

      <p class="setup-introduction">{{ t('setup.introduction') }}</p>

      <form class="setup-form" @submit.prevent="submit">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>

        <label class="field">
          {{ t('setup.caloriesPerDay') }}
          <span class="unit-input">
            <input
              v-model.number="target.calories_kcal"
              name="calories-kcal"
              type="number"
              :min="TARGET_LIMITS.caloriesMin"
              step="1"
              required
            />
            <span>kcal</span>
          </span>
        </label>

        <label class="field">
          {{ t('setup.proteinPerDay') }}
          <span class="unit-input">
            <input
              v-model.number="target.protein_g"
              name="protein-g"
              type="number"
              :min="TARGET_LIMITS.nutrientMin"
              step="1"
              required
            />
            <span>g</span>
          </span>
        </label>

        <details class="setup-optional">
          <summary>{{ t('setup.moreGoals') }}</summary>
          <div class="setup-optional-grid">
            <label class="field">
              {{ t('setup.maintenance') }}
              <span class="unit-input">
                <input
                  v-model.number="target.maintenance_kcal"
                  name="maintenance-kcal"
                  type="number"
                  :min="TARGET_LIMITS.maintenanceMin"
                  step="0.001"
                />
                <span>{{ t('common.kcal') }}</span>
              </span>
              <small>{{ t('setup.optional') }}</small>
            </label>
            <label class="field">
              {{ t('setup.carbs') }}
              <span class="unit-input">
                <input
                  v-model.number="target.carbs_g"
                  name="carbs-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>{{ t('common.grams') }}</span>
              </span>
            </label>
            <label class="field">
              {{ t('setup.fat') }}
              <span class="unit-input">
                <input
                  v-model.number="target.fat_g"
                  name="fat-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>{{ t('common.grams') }}</span>
              </span>
            </label>
            <label class="field">
              {{ t('setup.fiber') }}
              <span class="unit-input">
                <input
                  v-model.number="target.fiber_g"
                  name="fiber-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>{{ t('common.grams') }}</span>
              </span>
            </label>
          </div>
        </details>

        <p class="setup-note">{{ t('setup.note') }}</p>

        <button class="button setup-submit" type="submit" :disabled="saving || signingOut">
          {{ saving ? t('setup.saving') : t('setup.finish') }}
        </button>
        <button
          class="setup-signout"
          type="button"
          :disabled="saving || signingOut"
          @click="signOut"
        >
          {{ signingOut ? t('setup.loggingOut') : t('navigation.logout') }}
        </button>
      </form>
    </section>
  </main>
</template>
