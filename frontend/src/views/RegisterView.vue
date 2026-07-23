<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, ApiError } from '../api'

const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const passwordRepeat = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
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
        invitation_token: String(route.params.token),
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
      <form @submit.prevent="submit">
        <label class="field">Benutzername<input v-model="username" autocomplete="username" required /></label>
        <label class="field">Passwort<input v-model="password" type="password" minlength="12" autocomplete="new-password" required /></label>
        <label class="field">Passwort wiederholen<input v-model="passwordRepeat" type="password" minlength="12" autocomplete="new-password" required /></label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button" type="submit" :disabled="loading">
          {{ loading ? 'Konto wird erstellt …' : 'Konto erstellen' }}
        </button>
      </form>
    </section>
  </main>
</template>
