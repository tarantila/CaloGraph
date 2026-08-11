<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, ApiError } from '../api'
const router = useRouter()


const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''))
const receivedFromFragment = fragment.has('token')
const recoveryToken = ref(fragment.get('token') ?? '')
const cleanLocation = `${window.location.pathname}${window.location.search}`
if (window.location.hash) {
  const state = window.history.state
  const cleanState = state && typeof state === 'object'
    ? {
        ...state,
        back: typeof state.back === 'string' ? state.back.replace(/#.*$/, '') : state.back,
        current: cleanLocation,
        forward: typeof state.forward === 'string' ? state.forward.replace(/#.*$/, '') : state.forward,
      }
    : state
  window.history.replaceState(cleanState, '', cleanLocation)
  void router.replace(cleanLocation)
}


const newPassword = ref('')
const passwordRepeat = ref('')
const error = ref('')
const retryAfterSeconds = ref<number | null>(null)
const success = ref(false)
const submitting = ref(false)

async function submit() {
  retryAfterSeconds.value = null
  error.value = ''
  if (newPassword.value !== passwordRepeat.value) {
    error.value = 'Die Passwörter stimmen nicht überein.'
    return
  }
  submitting.value = true
  try {
    await api('/auth/recovery/complete', {
      method: 'POST',
      body: JSON.stringify({
        recovery_token: recoveryToken.value,
        new_password: newPassword.value,
      }),
    })
    recoveryToken.value = ''
    newPassword.value = ''
    passwordRepeat.value = ''
    success.value = true
  } catch (cause) {
    if (cause instanceof ApiError) {
      error.value = cause.message
      const retryAfter = Number(cause.retryAfter)
      retryAfterSeconds.value =
        cause.status === 429 && Number.isFinite(retryAfter) && retryAfter > 0
          ? Math.ceil(retryAfter)
          : null
    } else {
      error.value = 'Das Passwort konnte nicht zurückgesetzt werden.'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="card login-card" aria-labelledby="recovery-title">
      <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
      <h1 id="recovery-title">Kontozugang wiederherstellen</h1>

      <div v-if="success" class="login-success" role="status">
        <strong>Passwort wurde geändert.</strong>
        <p>Das Konto bleibt deaktiviert. Ein Administrator muss es reaktivieren, bevor du dich anmelden kannst.</p>
        <RouterLink class="button" :to="{ name: 'login' }">Zur Anmeldung</RouterLink>
      </div>

      <form v-else @submit.prevent="submit">
        <label class="field">
          Recovery-Token
          <input
            v-model="recoveryToken"
            type="password"
            autocomplete="off"
            required
          />
        </label>
        <p v-if="receivedFromFragment" class="muted">Der Token wurde sicher aus dem Link übernommen und aus der Browseradresse entfernt.</p>
        <label class="field">
          Neues Passwort
          <input v-model="newPassword" type="password" minlength="15" autocomplete="new-password" required />
        </label>
        <label class="field">
          Neues Passwort wiederholen
          <input v-model="passwordRepeat" type="password" minlength="15" autocomplete="new-password" required />
        </label>
        <p class="muted">Mindestens 15 Zeichen. Lange Passphrasen und Passwortmanager werden unterstützt.</p>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <p v-if="retryAfterSeconds" class="muted" role="status">
          Erneut versuchen in {{ retryAfterSeconds }} Sekunden.
        </p>
        <button class="button login-submit" type="submit" :disabled="submitting">
          {{ submitting ? 'Passwort wird geändert …' : 'Passwort ändern' }}
        </button>
      </form>
    </section>
  </main>
</template>
