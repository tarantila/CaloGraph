<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError } from '../api'
import { useAuthStore } from '../stores/auth'

const username = ref('')
const password = ref('')
const error = ref('')
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

async function submit() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    error.value = cause instanceof ApiError ? cause.message : 'Anmeldung ist fehlgeschlagen.'
  }
}
</script>

<template>
  <main class="login-page">
    <section class="card login-card" aria-labelledby="login-title">
      <div class="brand-mark" aria-hidden="true">C</div>
      <h1 id="login-title">Willkommen bei CaloGraph</h1>
      <p>Deine Gesundheitsdaten bleiben auf deiner eigenen Infrastruktur.</p>
      <form @submit.prevent="submit">
        <label class="field">Benutzername<input v-model="username" autocomplete="username" required /></label>
        <label class="field">Passwort<input v-model="password" type="password" autocomplete="current-password" required /></label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button" type="submit" :disabled="auth.loading">
          {{ auth.loading ? 'Anmeldung läuft …' : 'Anmelden' }}
        </button>
      </form>
    </section>
  </main>
</template>

