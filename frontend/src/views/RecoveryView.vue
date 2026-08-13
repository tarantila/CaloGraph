<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, ApiError, localizeApiError } from '../api'
import { i18n } from '../i18n'
const router = useRouter()
const t = i18n.global.t.bind(i18n.global)


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
    error.value = t('auth.passwordMismatch')
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
      error.value = localizeApiError(cause, 'errors.generic', {
        problemTypeFallbacks: {
          'urn:calograph:problem:invalid-invitation': 'auth.recoveryTokenInvalid',
          'urn:calograph:problem:validation-error': 'auth.passwordPolicy',
        },
      })
      const retryAfter = Number(cause.retryAfter)
      retryAfterSeconds.value =
        cause.status === 429 && Number.isFinite(retryAfter) && retryAfter > 0
          ? Math.ceil(retryAfter)
          : null
    } else {
      error.value = t('errors.generic')
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
      <h1 id="recovery-title">{{ t('auth.recoveryTitle') }}</h1>

      <div v-if="success" class="login-success" role="status">
        <strong>{{ t('auth.passwordChanged') }}</strong>
        <p>{{ t('auth.recoveryDisabled') }}</p>
        <RouterLink class="button" :to="{ name: 'login' }">{{ t('auth.toLogin') }}</RouterLink>
      </div>

      <form v-else @submit.prevent="submit">
        <label class="field">
          {{ t('auth.recoveryToken') }}
          <input
            v-model="recoveryToken"
            type="password"
            autocomplete="off"
            required
          />
        </label>
        <p v-if="receivedFromFragment" class="muted">{{ t('auth.tokenRemoved') }}</p>
        <label class="field">
          {{ t('auth.newPassword') }}
          <input v-model="newPassword" type="password" minlength="15" autocomplete="new-password" required />
        </label>
        <label class="field">
          {{ t('auth.repeatNewPassword') }}
          <input v-model="passwordRepeat" type="password" minlength="15" autocomplete="new-password" required />
        </label>
        <p class="muted">{{ t('auth.passwordHint') }}</p>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <p v-if="retryAfterSeconds" class="muted" role="status">
          {{ t('auth.retryIn', { seconds: retryAfterSeconds }) }}
        </p>
        <button class="button login-submit" type="submit" :disabled="submitting">
          {{ submitting ? t('auth.passwordChanging') : t('auth.changePassword') }}
        </button>
      </form>
    </section>
  </main>
</template>
