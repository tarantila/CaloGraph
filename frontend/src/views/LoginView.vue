<script setup lang="ts">
import { PhArrowLeft, PhFingerprint, PhLockKey } from '@phosphor-icons/vue'
import { nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api'
import { useAuthStore } from '../stores/auth'
import { isPasskeySupported } from '../webauthn'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const mfaCode = ref('')
const error = ref('')
const passwordFormVisible = ref(Boolean(route.query.registered))
const usernameInput = ref<HTMLInputElement | null>(null)
const passkeySupported = isPasskeySupported()

async function showPasswordForm() {
  passwordFormVisible.value = true
  await nextTick()
  usernameInput.value?.focus()
}

function showMethodSelection() {
  auth.cancelMfa()
  passwordFormVisible.value = false
  password.value = ''
  mfaCode.value = ''
  error.value = ''
}

async function submit() {
  error.value = ''
  try {
    const completed = await auth.login(username.value, password.value)
    if (completed) await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Anmeldung ist fehlgeschlagen.'
  }
}

async function submitMfa() {
  error.value = ''
  try {
    await auth.verifyMfa(mfaCode.value)
    await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'MFA-Prüfung ist fehlgeschlagen.'
  }
}

async function submitPasskey() {
  error.value = ''
  try {
    await auth.loginWithPasskey()
    await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
      error.value = 'Passkey-Anmeldung wurde abgebrochen oder ist abgelaufen.'
    } else {
      error.value =
        cause instanceof ApiError || cause instanceof Error
          ? cause.message
          : 'Passkey-Anmeldung ist fehlgeschlagen.'
    }
  }
}
</script>

<template>
  <main class="login-page">
    <section
      v-if="!passwordFormVisible"
      class="card login-card login-choice-card"
      aria-labelledby="login-choice-title"
    >
      <div class="login-brand">
        <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <h1 id="login-choice-title">CaloGraph</h1>
      </div>
      <div class="login-methods">
        <button class="login-method-button" type="button" @click="showPasswordForm">
          <PhLockKey :size="20" weight="regular" aria-hidden="true" />
          Mit Passwort anmelden
        </button>
        <button
          v-if="passkeySupported"
          class="login-method-button"
          type="button"
          :disabled="auth.loading"
          @click="submitPasskey"
        >
          <PhFingerprint :size="20" weight="regular" aria-hidden="true" />
          {{ auth.loading ? 'Passkey wird geprüft …' : 'Mit Passkey anmelden' }}
        </button>
      </div>
      <div v-if="error" class="error" role="alert">{{ error }}</div>
    </section>

    <section v-else class="card login-card login-form-card" aria-labelledby="login-title">
      <button class="login-back-button" type="button" aria-label="Zurück zur Anmeldeauswahl" @click="showMethodSelection">
        <PhArrowLeft :size="18" weight="bold" aria-hidden="true" />
        Zurück
      </button>
      <div class="login-form-heading">
        <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <div>
          <small>CaloGraph</small>
          <h1 id="login-title">Anmelden</h1>
        </div>
      </div>
      <div v-if="route.query.registered" class="login-success" role="status">
        Konto erstellt. Du kannst dich jetzt anmelden.
      </div>
      <form v-if="!auth.mfaRequired" @submit.prevent="submit">
        <label class="field">
          Benutzername
          <input ref="usernameInput" v-model="username" autocomplete="username" required />
        </label>
        <label class="field">
          Passwort
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button login-submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? 'Anmeldung läuft …' : 'Anmelden' }}
        </button>
      </form>
      <form v-else @submit.prevent="submitMfa">
        <p>Gib den sechsstelligen Code deiner Authenticator-App oder einen Wiederherstellungscode ein.</p>
        <label class="field">
          Sicherheitscode
          <input
            v-model="mfaCode"
            autocomplete="one-time-code"
            autocapitalize="characters"
            maxlength="64"
            spellcheck="false"
            autofocus
            required
          />
        </label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button login-submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? 'Code wird geprüft …' : 'Anmeldung abschließen' }}
        </button>
      </form>
    </section>
  </main>
</template>
