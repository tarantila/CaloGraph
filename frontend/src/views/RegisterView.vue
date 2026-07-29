<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, ApiError } from '../api'

const router = useRouter()
const username = ref('')
const password = ref('')
const passwordRepeat = ref('')
const error = ref('')
const initializing = ref(true)
const invitationReady = ref(false)
const loading = ref(false)

onMounted(async () => {
  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const token = fragment.get('token')
  if (window.location.hash) {
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${window.location.search}`,
    )
  }

  try {
    if (token) {
      await api('/auth/invitation/exchange', {
        method: 'POST',
        body: JSON.stringify({ token }),
      })
      invitationReady.value = true
    } else {
      const state = await api<{ valid: boolean }>('/auth/invitation/status')
      invitationReady.value = state.valid
    }
    if (!invitationReady.value) {
      error.value = 'Die Einladung ist ungültig oder abgelaufen.'
    }
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Einladung konnte nicht geprüft werden.'
  } finally {
    initializing.value = false
  }
})

async function submit() {
  if (!invitationReady.value) return
  error.value = ''
  if (password.value !== passwordRepeat.value) {
    error.value = 'Die Passwörter stimmen nicht überein.'
    return
  }
  loading.value = true
  try {
    await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({
        username: username.value,
        password: password.value,
      }),
    })
    await router.replace({ name: 'login', query: { registered: '1' } })
  } catch (cause) {
    error.value =
      cause instanceof ApiError ? cause.message : 'Konto konnte nicht erstellt werden.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="card login-card" aria-labelledby="register-title">
      <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
      <h1 id="register-title">CaloGraph-Konto erstellen</h1>
      <p>Deine Daten, Auswertungen und YAZIO-Verbindung bleiben von allen anderen Konten getrennt.</p>
      <p v-if="initializing" aria-live="polite">Einladung wird geprüft …</p>
      <form v-if="invitationReady" @submit.prevent="submit">
        <label class="field">Benutzername<input v-model="username" autocomplete="username" required /></label>
        <label class="field">Passwort<input v-model="password" type="password" minlength="12" autocomplete="new-password" required /></label>
        <label class="field">Passwort wiederholen<input v-model="passwordRepeat" type="password" minlength="12" autocomplete="new-password" required /></label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button" type="submit" :disabled="loading">
          {{ loading ? 'Konto wird erstellt …' : 'Konto erstellen' }}
        </button>
      </form>
      <div v-else-if="error" class="error" role="alert">{{ error }}</div>
    </section>
  </main>
</template>
