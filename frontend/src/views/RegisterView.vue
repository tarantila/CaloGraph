<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, ApiError, localizeApiError } from '../api'
import { i18n } from '../i18n'
const router = useRouter()
const t = i18n.global.t.bind(i18n.global)
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
      error.value = t('auth.invalidInvitation')
    }
  } catch (cause) {
    error.value =
      cause instanceof ApiError
        ? localizeApiError(cause, 'auth.invitationCheckFailed')
        : t('auth.invitationCheckFailed')
  } finally {
    initializing.value = false
  }
})

async function submit() {
  if (!invitationReady.value) return
  error.value = ''
  if (password.value !== passwordRepeat.value) {
    error.value = t('auth.passwordMismatch')
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
      cause instanceof ApiError
        ? localizeApiError(cause, 'auth.registrationFailed', {
            problemTypeFallbacks: {
              'urn:calograph:problem:validation-error': 'auth.passwordPolicy',
            },
          })
        : t('auth.registrationFailed')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="card login-card" aria-labelledby="register-title">
      <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
      <h1 id="register-title">{{ t('auth.createAccount') }}</h1>
      <p>{{ t('auth.isolation') }}</p>
      <p v-if="initializing" aria-live="polite">{{ t('auth.invitationChecking') }}</p>
      <form v-if="invitationReady" @submit.prevent="submit">
        <label class="field">{{ t('auth.username') }}<input v-model="username" autocomplete="username" required /></label>
        <label class="field">{{ t('auth.password') }}<input v-model="password" type="password" minlength="15" autocomplete="new-password" required /></label>
        <label class="field">{{ t('auth.passwordRepeat') }}<input v-model="passwordRepeat" type="password" minlength="15" autocomplete="new-password" required /></label>
        <p class="muted">{{ t('auth.passwordHint') }}</p>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button" type="submit" :disabled="loading">
          {{ loading ? t('auth.accountCreating') : t('auth.createAccountButton') }}
        </button>
      </form>
      <div v-else-if="error" class="error" role="alert">{{ error }}</div>
    </section>
  </main>
</template>
