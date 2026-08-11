<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError } from '../api'
import { isoDateInTimeZone } from '../date-format'
import { useAuthStore } from '../stores/auth'
import {
  createEmptyTargetDraft,
  saveTargetDraft,
  TARGET_LIMITS,
  TargetValidationError,
} from '../target-form'

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
      cause instanceof ApiError || cause instanceof TargetValidationError
        ? cause.message
        : 'Die Einrichtung konnte nicht abgeschlossen werden.'
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
    error.value =
      cause instanceof ApiError ? cause.message : 'Abmelden ist fehlgeschlagen.'
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
          <small>CaloGraph einrichten</small>
          <h1 id="setup-title">Willkommen bei CaloGraph</h1>
        </div>
      </header>

      <p class="setup-introduction">
        Bevor es losgeht, benötigen wir zwei Werte von dir. Sie werden verwendet, um deine
        Kalorien- und Proteinauswertungen zu berechnen.
      </p>

      <form class="setup-form" @submit.prevent="submit">
        <div v-if="error" class="setup-error" role="alert">{{ error }}</div>

        <label class="field">
          Kalorienbudget pro Tag
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
          Proteinziel pro Tag
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
          <summary>Weitere Ziele festlegen</summary>
          <div class="setup-optional-grid">
            <label class="field">
              Erhaltungsbedarf
              <span class="unit-input">
                <input
                  v-model.number="target.maintenance_kcal"
                  name="maintenance-kcal"
                  type="number"
                  :min="target.calories_kcal ?? TARGET_LIMITS.caloriesMin"
                  step="1"
                />
                <span>kcal</span>
              </span>
            </label>
            <label class="field">
              Kohlenhydrate
              <span class="unit-input">
                <input
                  v-model.number="target.carbs_g"
                  name="carbs-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>g</span>
              </span>
            </label>
            <label class="field">
              Fett
              <span class="unit-input">
                <input
                  v-model.number="target.fat_g"
                  name="fat-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>g</span>
              </span>
            </label>
            <label class="field">
              Ballaststoffe
              <span class="unit-input">
                <input
                  v-model.number="target.fiber_g"
                  name="fiber-g"
                  type="number"
                  :min="TARGET_LIMITS.nutrientMin"
                  step="1"
                />
                <span>g</span>
              </span>
            </label>
          </div>
        </details>

        <p class="setup-note">
          Du kannst diese Werte später jederzeit unter „Budgets & Ziele“ ändern.
        </p>

        <button class="button setup-submit" type="submit" :disabled="saving || signingOut">
          {{ saving ? 'Einrichtung wird gespeichert …' : 'Einrichtung abschließen' }}
        </button>
        <button
          class="setup-signout"
          type="button"
          :disabled="saving || signingOut"
          @click="signOut"
        >
          {{ signingOut ? 'Abmeldung läuft …' : 'Abmelden' }}
        </button>
      </form>
    </section>
  </main>
</template>
