<script setup lang="ts">
import { PhArrowLeft, PhFingerprint, PhLockKey } from '@phosphor-icons/vue'
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, ApiTransportError, api, localizeApiError } from '../api'
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
type BootstrapState = 'checking' | 'required' | 'normal' | 'success' | 'race' | 'failed'
const bootstrapState = ref<BootstrapState>('checking')
const bootstrapError = ref('')
const bootstrapUsername = ref('')
const bootstrapPassword = ref('')
const bootstrapPasswordRepeat = ref('')
const bootstrapSubmitting = ref(false)
const bootstrapUsernameInput = ref<HTMLInputElement | null>(null)
const bootstrapHeading = ref<HTMLElement | null>(null)
async function loadBootstrapStatus(): Promise<void> {
  bootstrapState.value = 'checking'
  bootstrapError.value = ''
  try {
    const result = await api<{ setup_required?: boolean }>('/auth/bootstrap/status')
    bootstrapState.value = result?.setup_required === true ? 'required' : 'normal'
    if (bootstrapState.value === 'required') {
      await nextTick()
      bootstrapUsernameInput.value?.focus()
    }
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 404) {
      bootstrapState.value = 'normal'
      return
    }
    bootstrapState.value = 'failed'
    bootstrapError.value = t('auth.bootstrapStatusFailed')
  }
}

async function submitBootstrap(): Promise<void> {
  if (bootstrapSubmitting.value) return
  bootstrapError.value = ''
  if (bootstrapPassword.value !== bootstrapPasswordRepeat.value) {
    bootstrapError.value = t('auth.passwordMismatch')
    return
  }
  bootstrapSubmitting.value = true
  try {
    await api('/auth/bootstrap', {
      method: 'POST',
      body: JSON.stringify({
        username: bootstrapUsername.value,
        password: bootstrapPassword.value,
      }),
    })
    bootstrapPassword.value = ''
    bootstrapPasswordRepeat.value = ''
    bootstrapState.value = 'success'
    await nextTick()
    bootstrapHeading.value?.focus()
  } catch (cause) {
    if (cause instanceof ApiError && cause.status === 409) {
      bootstrapPassword.value = ''
      bootstrapPasswordRepeat.value = ''
      bootstrapState.value = 'race'
      await nextTick()
      bootstrapHeading.value?.focus()
      return
    }
    bootstrapError.value =
      cause instanceof ApiError || cause instanceof ApiTransportError
        ? localizeApiError(cause, 'auth.bootstrapFailed')
        : t('auth.bootstrapFailed')
  } finally {
    bootstrapSubmitting.value = false
}
}

async function continueToLogin(): Promise<void> {
  bootstrapState.value = 'normal'
  passwordFormVisible.value = true
  await nextTick()
  usernameInput.value?.focus()
}

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
onMounted(() => {
  void loadBootstrapStatus()
})

</script>

<template>
  <main class="login-page">
    <section v-if="bootstrapState === 'checking'" class="card login-card" aria-labelledby="bootstrap-checking-title">
      <h1 id="bootstrap-checking-title" class="sr-only">{{ t('auth.bootstrapChecking') }}</h1>
      <div class="login-success" role="status" aria-live="polite">{{ t('auth.bootstrapChecking') }}</div>
    </section>
    <section v-else-if="bootstrapState === 'failed'" class="card login-card" aria-labelledby="bootstrap-error-title">
      <h1 id="bootstrap-error-title" class="sr-only">{{ t('auth.bootstrapStatusFailed') }}</h1>
      <div class="error" role="alert">{{ bootstrapError }}</div>
      <button class="button login-submit" type="button" @click="loadBootstrapStatus">{{ t('common.tryAgain') }}</button>
    </section>
    <section v-else-if="bootstrapState === 'required'" class="card login-card login-form-card" aria-labelledby="bootstrap-title">
      <div class="login-form-heading">
        <img class="login-logo" src="/branding/calograph-app-logo-256.png" alt="" aria-hidden="true" />
        <div>
          <small>Ersteinrichtung</small>
          <h1 id="bootstrap-title" ref="bootstrapHeading" tabindex="-1">{{ t('auth.bootstrapTitle') }}</h1>
        </div>
      </div>
      <p>{{ t('auth.bootstrapIntroduction') }}</p>
      <p class="login-admin-note">{{ t('auth.bootstrapAdminNote') }}</p>
      <form :aria-busy="bootstrapSubmitting" @submit.prevent="submitBootstrap">
        <label class="field">
          {{ t('auth.username') }}
          <input ref="bootstrapUsernameInput" v-model="bootstrapUsername" autocomplete="username" autocapitalize="none" spellcheck="false" required :disabled="bootstrapSubmitting" />
        </label>
        <label class="field">
          {{ t('auth.password') }}
          <input v-model="bootstrapPassword" type="password" autocomplete="new-password" minlength="15" aria-describedby="bootstrap-password-hint" required :disabled="bootstrapSubmitting" />
        </label>
        <p id="bootstrap-password-hint" class="field-hint">{{ t('auth.passwordHint') }}</p>
        <label class="field">
          {{ t('auth.passwordRepeat') }}
          <input v-model="bootstrapPasswordRepeat" type="password" autocomplete="new-password" minlength="15" aria-describedby="bootstrap-password-hint" required :disabled="bootstrapSubmitting" />
        </label>
        <div v-if="bootstrapError" class="error" role="alert">{{ bootstrapError }}</div>
        <button class="button login-submit" type="submit" :disabled="bootstrapSubmitting">
          {{ bootstrapSubmitting ? t('auth.bootstrapCreating') : t('auth.bootstrapCreate') }}
        </button>
      </form>
    </section>
    <section v-else-if="bootstrapState === 'success' || bootstrapState === 'race'" class="card login-card" aria-labelledby="bootstrap-result-title">
      <h1 id="bootstrap-result-title" ref="bootstrapHeading" tabindex="-1">{{ bootstrapState === 'success' ? t('auth.bootstrapSuccessTitle') : t('auth.bootstrapRaceTitle') }}</h1>
      <p role="status" aria-live="polite">{{ bootstrapState === 'success' ? t('auth.bootstrapSuccess') : t('auth.bootstrapRace') }}</p>
      <button class="button login-submit" type="button" @click="continueToLogin">{{ t('auth.toLogin') }}</button>
    </section>
    <section
      v-else-if="!passwordFormVisible"
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
