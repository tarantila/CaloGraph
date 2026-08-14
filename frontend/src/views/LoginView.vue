<script setup lang="ts">
import { PhArrowLeft, PhFingerprint, PhLockKey } from '@phosphor-icons/vue'
import { nextTick, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, ApiTransportError, localizeApiError } from '../api'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import { isPasskeySupported, WebAuthnError } from '../webauthn'

const auth = useAuthStore()
const t = i18n.global.t.bind(i18n.global)
const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const mfaCode = ref('')
const error = ref('')
const status = ref(route.query.passwordChanged ? t('auth.passwordChanged') : '')
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
    error.value =
      cause instanceof ApiError || cause instanceof ApiTransportError
        ? localizeApiError(cause, 'auth.loginFailed')
        : t('auth.loginFailed')
  }
}

async function submitMfa() {
  error.value = ''
  try {
    await auth.verifyMfa(mfaCode.value)
    await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    error.value =
      cause instanceof ApiError || cause instanceof ApiTransportError
        ? localizeApiError(cause, 'auth.mfaFailed')
        : t('auth.mfaFailed')
  }
}

async function submitPasskey() {
  error.value = ''
  try {
    await auth.loginWithPasskey()
    await router.replace(String(route.query.next ?? '/'))
  } catch (cause) {
    if (cause instanceof WebAuthnError) {
      error.value =
        cause.code === 'authentication-cancelled'
          ? t('auth.passkeyCancelled')
          : cause.code === 'unsupported'
            ? t('auth.passkeyUnsupported')
            : t('auth.passkeyFailed')
    } else if (cause instanceof ApiError || cause instanceof ApiTransportError) {
      error.value = localizeApiError(cause, 'auth.passkeyFailed', {
        problemTypeFallbacks: {
          'urn:calograph:problem:invalid-credentials': 'auth.passkeyFailed',
        },
        preserveDetail: true,
      })
    } else if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
      error.value = t('auth.passkeyCancelled')
    } else {
      error.value = t('auth.passkeyFailed')
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
          {{ t('auth.passwordLogin') }}
        </button>
        <button
          v-if="passkeySupported"
          class="login-method-button"
          type="button"
          :disabled="auth.loading"
          @click="submitPasskey"
        >
          <PhFingerprint :size="20" weight="regular" aria-hidden="true" />
          {{ auth.loading ? t('auth.passkeyChecking') : t('auth.passkeyLogin') }}
        </button>
      </div>
      <div v-if="status" class="login-success" role="status">{{ status }}</div>
      <div v-if="error" class="error" role="alert">{{ error }}</div>
    </section>

    <section v-else class="card login-card login-form-card" aria-labelledby="login-title">
      <button class="login-back-button" type="button" :aria-label="t('auth.backToMethods')" @click="showMethodSelection">
        <PhArrowLeft :size="18" weight="bold" aria-hidden="true" />
        {{ t('auth.back') }}
      </button>
      <div class="login-form-heading">
        <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <div>
          <small>CaloGraph</small>
          <h1 id="login-title">{{ t('auth.loginTitle') }}</h1>
        </div>
      </div>
      <div v-if="route.query.registered" class="login-success" role="status">
        {{ t('auth.accountCreated') }}
      </div>
      <div v-if="status" class="login-success" role="status">{{ status }}</div>
      <form v-if="!auth.mfaRequired" @submit.prevent="submit">
        <label class="field">
          {{ t('auth.username') }}
          <input ref="usernameInput" v-model="username" autocomplete="username" required />
        </label>
        <label class="field">
          {{ t('auth.password') }}
          <input v-model="password" type="password" autocomplete="current-password" required />
        </label>
        <div v-if="error" class="error" role="alert">{{ error }}</div>
        <button class="button login-submit" type="submit" :disabled="auth.loading">
          {{ auth.loading ? t('auth.loginRunning') : t('auth.login') }}
        </button>
      </form>
      <form v-else @submit.prevent="submitMfa">
        <p>{{ t('auth.mfaHelp') }}</p>
        <label class="field">
          {{ t('auth.securityCode') }}
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
          {{ auth.loading ? t('auth.codeChecking') : t('auth.completeLogin') }}
        </button>
      </form>
    </section>
  </main>
</template>
