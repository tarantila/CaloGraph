<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { useRouter } from 'vue-router'

import { api, ApiError, localizeApiError } from '../api'
import { formatGermanDateTime, formatGermanInstantDate } from '../date-format'
import { i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'
import {
  createPasskey,
  isPasskeySupported,
  type WebAuthnOptionsResponse,
} from '../webauthn'

interface Token {
  id: string
  label: string
  token_prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

interface MfaStatus {
  totp_enabled: boolean
  totp_setup_pending: boolean
  recovery_codes_remaining: number
}

interface TotpSetup {
  secret: string
  provisioning_uri: string
  qr_svg_data_url: string
}

interface Passkey {
  id: string
  label: string
  device_type: string
  backed_up: boolean
  created_at: string
  last_used_at: string | null
}

const router = useRouter()
const auth = useAuthStore()
const t = i18n.global.t.bind(i18n.global)

const loading = ref(true)
const loaded = ref(false)
const error = ref('')
const message = ref('')
const tokens = ref<Token[]>([])
const tokenLabel = ref('iPhone')
const newToken = ref('')
const managingToken = ref(false)
const tokenError = ref('')

const passwordCurrent = ref('')
const passwordNew = ref('')
const passwordConfirmation = ref('')
const passwordChangeError = ref('')
const changingPassword = ref(false)

const mfa = ref<MfaStatus | null>(null)
const totpSetup = ref<TotpSetup | null>(null)
const mfaCurrentPassword = ref('')
const mfaCode = ref('')
const recoveryCodes = ref<string[]>([])
const managingMfa = ref(false)

const passkeys = ref<Passkey[]>([])
const passkeySupported = isPasskeySupported()
const passkeyLabel = ref('')
const passkeyPassword = ref('')
const passkeyCode = ref('')
const managingPasskey = ref(false)

let loadGeneration = 0
let securityMounted = true

const passwordConfirmationMatches = computed(() => passwordNew.value === passwordConfirmation.value)

function isCurrentLoad(generation: number): boolean {
  return securityMounted && generation === loadGeneration
}

async function fetchSecurity(generation: number): Promise<boolean> {
  const [tokensResult, mfaResult, passkeyResult] = await Promise.all([
    api<Token[]>('/settings/tokens'),
    api<MfaStatus>('/settings/mfa'),
    api<Passkey[]>('/settings/passkeys'),
  ])
  if (!isCurrentLoad(generation)) return false
  tokens.value = tokensResult
  mfa.value = mfaResult
  passkeys.value = passkeyResult
  return true
}

async function loadPage(): Promise<void> {
  const generation = ++loadGeneration
  loading.value = true
  loaded.value = false
  error.value = ''
  message.value = ''
  try {
    if (await fetchSecurity(generation)) loaded.value = true
  } catch (cause) {
    if (!isCurrentLoad(generation)) return
    error.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountSecurity.loadFailed', { preserveDetail: false })
      : t('accountSecurity.loadFailed')
  } finally {
    if (isCurrentLoad(generation)) loading.value = false
  }
}

async function refreshSecurity(): Promise<boolean> {
  const generation = ++loadGeneration
  try {
    return await fetchSecurity(generation)
  } catch (cause) {
    if (!isCurrentLoad(generation)) return false
    throw cause
  }
}

onMounted(() => { void loadPage() })
onBeforeUnmount(() => {
  securityMounted = false
  ++loadGeneration
})

async function changePassword() {
  passwordChangeError.value = ''
  if (!passwordConfirmationMatches.value) {
    passwordChangeError.value = t('auth.passwordMismatch')
    return
  }
  changingPassword.value = true
  try {
    await api<void>('/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        current_password: passwordCurrent.value,
        new_password: passwordNew.value,
      }),
    })
    auth.clearSession()
    await router.replace({ name: 'login', query: { passwordChanged: '1' } })
  } catch (cause) {
    if (securityMounted) {
      passwordChangeError.value =
        cause instanceof ApiError
          ? localizeApiError(cause, 'settingsUi.passwordChangeFailed')
          : t('settingsUi.passwordChangeFailed')
    }
  } finally {
    passwordCurrent.value = ''
    passwordNew.value = ''
    passwordConfirmation.value = ''
    changingPassword.value = false
  }
}

async function createToken() {
  if (managingToken.value) return
  managingToken.value = true
  tokenError.value = ''
  try {
    const result = await api<{ token: string }>('/settings/tokens', {
      method: 'POST',
      body: JSON.stringify({ label: tokenLabel.value }),
    })
    if (!securityMounted) return
    newToken.value = result.token
    await refreshSecurity()
  } catch (cause) {
    if (!securityMounted) return
    tokenError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountSecurity.tokenCreateFailed', { preserveDetail: false })
      : t('accountSecurity.tokenCreateFailed')
  } finally {
    if (securityMounted) managingToken.value = false
  }
}

async function revokeToken(id: string) {
  if (managingToken.value) return
  managingToken.value = true
  tokenError.value = ''
  try {
    await api(`/settings/tokens/${id}`, { method: 'DELETE' })
    await refreshSecurity()
  } catch (cause) {
    if (!securityMounted) return
    tokenError.value = cause instanceof ApiError
      ? localizeApiError(cause, 'accountSecurity.tokenRevokeFailed', { preserveDetail: false })
      : t('accountSecurity.tokenRevokeFailed')
  } finally {
    if (securityMounted) managingToken.value = false
  }
}

async function beginTotpSetup() {
  managingMfa.value = true
  error.value = ''
  message.value = ''
  try {
    const setup = await api<TotpSetup>('/settings/mfa/totp/setup', {
      method: 'POST',
      body: JSON.stringify({ current_password: mfaCurrentPassword.value }),
    })
    if (!securityMounted) return
    totpSetup.value = setup
    mfaCurrentPassword.value = ''
    recoveryCodes.value = []
    message.value = t('settingsUi.mfaScan')
  } catch (cause) {
    if (securityMounted) {
      error.value =
        cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaSetupFailed') : t('settingsUi.mfaSetupFailed')
    }
  } finally {
    if (securityMounted) managingMfa.value = false
  }
}

async function confirmTotpSetup() {
  managingMfa.value = true
  error.value = ''
  try {
    const result = await api<{ recovery_codes: string[] }>('/settings/mfa/totp/confirm', {
      method: 'POST',
      body: JSON.stringify({ code: mfaCode.value }),
    })
    if (!securityMounted) return
    recoveryCodes.value = result.recovery_codes
    mfaCode.value = ''
    totpSetup.value = null
    if (await refreshSecurity() && securityMounted) message.value = t('settingsUi.mfaEnabled')
  } catch (cause) {
    if (securityMounted) {
      error.value =
        cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaConfirmFailed') : t('settingsUi.mfaConfirmFailed')
    }
  } finally {
    if (securityMounted) managingMfa.value = false
  }
}

async function regenerateRecoveryCodes() {
  managingMfa.value = true
  error.value = ''
  try {
    const result = await api<{ recovery_codes: string[] }>(
      '/settings/mfa/totp/recovery-codes',
      {
        method: 'POST',
        body: JSON.stringify({
          current_password: mfaCurrentPassword.value,
          code: mfaCode.value,
        }),
      },
    )
    if (!securityMounted) return
    recoveryCodes.value = result.recovery_codes
    mfaCurrentPassword.value = ''
    mfaCode.value = ''
    if (await refreshSecurity() && securityMounted) message.value = t('settingsUi.codesRegenerated')
  } catch (cause) {
    if (securityMounted) {
      error.value =
        cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.codesRegenerateFailed') : t('settingsUi.codesRegenerateFailed')
    }
  } finally {
    if (securityMounted) managingMfa.value = false
  }
}

async function disableTotp() {
  managingMfa.value = true
  error.value = ''
  try {
    await api('/settings/mfa/totp', {
      method: 'DELETE',
      body: JSON.stringify({
        current_password: mfaCurrentPassword.value,
        code: mfaCode.value,
      }),
    })
    if (!securityMounted) return
    recoveryCodes.value = []
    mfaCurrentPassword.value = ''
    mfaCode.value = ''
    if (await refreshSecurity() && securityMounted) message.value = t('settingsUi.mfaDisabled')
  } catch (cause) {
    if (securityMounted) {
      error.value =
        cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.mfaDisableFailed') : t('settingsUi.mfaDisableFailed')
    }
  } finally {
    if (securityMounted) managingMfa.value = false
  }
}

async function copyRecoveryCodes() {
  await navigator.clipboard.writeText(recoveryCodes.value.join('\n'))
  if (securityMounted) message.value = t('settingsUi.codesCopied')
}

async function registerPasskey() {
  managingPasskey.value = true
  error.value = ''
  message.value = ''
  try {
    const options = await api<WebAuthnOptionsResponse>('/settings/passkeys/options', {
      method: 'POST',
      body: JSON.stringify({
        current_password: passkeyPassword.value,
        code: passkeyCode.value || null,
      }),
    })
    const credential = await createPasskey(options.public_key)
    if (!securityMounted) return
    await api<Passkey>('/settings/passkeys', {
      method: 'POST',
      body: JSON.stringify({
        challenge_id: options.challenge_id,
        label: passkeyLabel.value,
        credential,
      }),
    })
    if (!securityMounted) return
    passkeyLabel.value = ''
    passkeyPassword.value = ''
    passkeyCode.value = ''
    await refreshSecurity()
    if (securityMounted) message.value = t('settingsUi.passkeyCreated')
  } catch (cause) {
    if (!securityMounted) return
    if (cause instanceof DOMException && cause.name === 'NotAllowedError') {
      error.value = t('settingsUi.passkeyCancelled')
    } else {
      error.value =
        cause instanceof ApiError
          ? localizeApiError(cause, 'settingsUi.passkeyCreateFailed', { preserveDetail: true })
          : t('settingsUi.passkeyCreateFailed')
    }
  } finally {
    if (securityMounted) managingPasskey.value = false
  }
}

async function removePasskey(id: string) {
  managingPasskey.value = true
  error.value = ''
  message.value = ''
  try {
    await api(`/settings/passkeys/${id}`, {
      method: 'DELETE',
      body: JSON.stringify({
        current_password: passkeyPassword.value,
        code: passkeyCode.value || null,
      }),
    })
    if (!securityMounted) return
    passkeyPassword.value = ''
    passkeyCode.value = ''
    await refreshSecurity()
    if (securityMounted) message.value = t('settingsUi.passkeyRemoved')
  } catch (cause) {
    if (securityMounted) {
      error.value =
        cause instanceof ApiError ? localizeApiError(cause, 'settingsUi.passkeyRemoveFailed', { preserveDetail: true }) : t('settingsUi.passkeyRemoveFailed')
    }
  } finally {
    if (securityMounted) managingPasskey.value = false
  }
}

function passkeyDeviceLabel(passkey: Passkey) {
  if (passkey.backed_up || passkey.device_type === 'multi_device') {
    return t('settingsUi.passkeySynced')
  }
  return t('settingsUi.passkeyThisDevice')
}
</script>

<template>
  <div class="page-heading">
    <div>
      <h1>{{ t('accountSecurity.title') }}</h1>
      <p>{{ t('accountSecurity.description') }}</p>
    </div>
  </div>

  <div v-if="loading" class="dashboard-loading" role="status" aria-live="polite">
    {{ t('accountSecurity.loading') }}
  </div>
  <template v-else>
    <section v-if="error" class="card account-feedback error" role="alert" aria-live="assertive">
      <p>{{ error }}</p>
      <button v-if="!loaded" class="button compact-action" type="button" :disabled="loading" @click="loadPage">
        {{ t('accountSecurity.retry') }}
      </button>
    </section>

    <template v-if="loaded">
      <div class="account-page-stack">
        <p v-if="message" class="account-form-success" role="status" aria-live="polite">{{ message }}</p>

        <section class="card form-card security-section password-change-card" :aria-busy="changingPassword" aria-labelledby="account-security-password-title">
          <h2 id="account-security-password-title">{{ t('settingsUi.passwordChangeTitle') }}</h2>
          <p class="security-description">{{ t('settingsUi.passwordChangeDescription') }}</p>
          <p class="table-secondary security-description">{{ t('auth.passwordHint') }}</p>
          <form class="form-grid" @submit.prevent="changePassword">
            <label class="field">
              {{ t('settingsUi.currentPassword') }}
              <input v-model="passwordCurrent" type="password" autocomplete="current-password" required />
            </label>
            <label class="field">
              {{ t('auth.newPassword') }}
              <input v-model="passwordNew" type="password" autocomplete="new-password" minlength="15" required />
            </label>
            <label class="field">
              {{ t('auth.repeatNewPassword') }}
              <input v-model="passwordConfirmation" type="password" autocomplete="new-password" minlength="15" required />
            </label>
            <div v-if="passwordChangeError" class="error" role="alert">{{ passwordChangeError }}</div>
            <button class="button compact-action password-submit" type="submit" :disabled="changingPassword">
              {{ changingPassword ? t('auth.passwordChanging') : t('auth.changePassword') }}
            </button>
          </form>
        </section>

        <section class="card form-card security-section mfa-card" :aria-busy="managingMfa" aria-labelledby="account-security-mfa-title">
          <h2 id="account-security-mfa-title">{{ t('settingsUi.mfaTitle') }}</h2>
          <p class="security-description">{{ t('settingsUi.mfaDescription') }}</p>

          <template v-if="!mfa?.totp_enabled">
            <p v-if="mfa?.totp_setup_pending && !totpSetup">{{ t('settingsUi.mfaPending') }}</p>
            <form v-if="!totpSetup" class="form-grid" @submit.prevent="beginTotpSetup">
              <label class="field">
                {{ t('settingsUi.password') }}
                <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
              </label>
              <button class="button compact-action" type="submit" :disabled="managingMfa">
                {{ managingMfa ? t('settingsUi.prepareSetup') : t('settingsUi.setupAuthenticator') }}
              </button>
            </form>

            <div v-else class="mfa-setup">
              <ol>
                <li>{{ t('settingsUi.scanQr') }}</li>
                <li>{{ t('settingsUi.enterCode') }}</li>
              </ol>
              <img class="mfa-qr" :src="totpSetup.qr_svg_data_url" :alt="t('settingsUi.qrAlt')" />
              <details>
                <summary>{{ t('settingsUi.manualKey') }}</summary>
                <code class="mfa-secret">{{ totpSetup.secret }}</code>
              </details>
              <form class="form-grid" @submit.prevent="confirmTotpSetup">
                <label class="field">
                  {{ t('settingsUi.code') }}
                  <input v-model="mfaCode" inputmode="numeric" autocomplete="one-time-code" minlength="6" maxlength="6" required />
                </label>
                <button class="button compact-action" type="submit" :disabled="managingMfa">
                  {{ managingMfa ? t('settingsUi.codeChecking') : t('settingsUi.enableTotp') }}
                </button>
              </form>
            </div>
          </template>

          <template v-else>
            <p><strong>{{ t('settingsUi.status') }}</strong> {{ t('settingsUi.active') }} · {{ t('settingsUi.recoveryCodesAvailable', { count: mfa.recovery_codes_remaining }) }}</p>
            <p>{{ t('settingsUi.changesNeedFactor') }}</p>
            <form class="form-grid" @submit.prevent>
              <label class="field">
                {{ t('settingsUi.password') }}
                <input v-model="mfaCurrentPassword" type="password" autocomplete="current-password" required />
              </label>
              <label class="field">
                {{ t('auth.securityCode') }}
                <input v-model="mfaCode" autocomplete="one-time-code" maxlength="64" required />
              </label>
              <div class="filters">
                <button class="button secondary compact-action" type="button" :disabled="managingMfa" @click="regenerateRecoveryCodes">
                  {{ t('settingsUi.regenerate') }}
                </button>
                <button class="text-button danger" type="button" :disabled="managingMfa" @click="disableTotp">
                  {{ t('settingsUi.disable') }}
                </button>
              </div>
            </form>
          </template>

          <div v-if="recoveryCodes.length" class="recovery-codes" role="status" aria-live="polite">
            <strong>{{ t('settingsUi.storeCodes') }}</strong>
            <code v-for="code in recoveryCodes" :key="code">{{ code }}</code>
            <button class="button secondary compact-action" type="button" @click="copyRecoveryCodes">{{ t('settingsUi.copyAll') }}</button>
          </div>
        </section>

        <section class="card form-card security-section passkey-card" :aria-busy="managingPasskey" aria-labelledby="account-security-passkeys-title">
          <h2 id="account-security-passkeys-title">{{ t('settingsUi.passkeysTitle') }}</h2>
          <p class="security-description">{{ t('settingsUi.passkeysDescription') }}</p>
          <p v-if="!passkeySupported" class="passkey-unavailable">{{ t('settingsUi.passkeysUnavailable') }}</p>

          <div v-if="passkeys.length" class="passkey-list">
            <article v-for="passkey in passkeys" :key="passkey.id" class="passkey-item">
              <div>
                <strong>{{ passkey.label }}</strong>
                <small>
                  {{ passkeyDeviceLabel(passkey) }} · {{ t('settingsUi.created') }}
                  {{ formatGermanInstantDate(passkey.created_at) }}
                  <template v-if="passkey.last_used_at">
                    · {{ t('settingsUi.lastUsed') }} {{ formatGermanDateTime(passkey.last_used_at) }}
                  </template>
                </small>
              </div>
              <button
                class="text-button danger"
                type="button"
                :disabled="managingPasskey"
                @click="removePasskey(passkey.id)"
              >
                {{ t('settingsUi.remove') }}
              </button>
            </article>
          </div>
          <p v-else>{{ t('settingsUi.noPasskeys') }}</p>

          <form class="form-grid" @submit.prevent="registerPasskey">
            <label class="field">
              {{ t('settingsUi.label') }}
              <input
                v-model="passkeyLabel"
                maxlength="100"
                :placeholder="t('settingsUi.placeholder')"
                :disabled="!passkeySupported"
                required
              />
            </label>
            <label class="field">
              {{ t('settingsUi.password') }}
              <input
                v-model="passkeyPassword"
                type="password"
                autocomplete="current-password"
                required
              />
              <small>{{ t('settingsUi.passwordRemoveHelp') }}</small>
            </label>
            <label v-if="mfa?.totp_enabled" class="field">
              {{ t('auth.securityCode') }}
              <input
                v-model="passkeyCode"
                autocomplete="one-time-code"
                maxlength="64"
                required
              />
            </label>
            <button
              class="button compact-action passkey-submit"
              type="submit"
              :disabled="!passkeySupported || managingPasskey"
            >
              {{ managingPasskey ? t('settingsUi.processing') : t('settingsUi.setupPasskey') }}
            </button>
          </form>
        </section>

        <section class="card form-card security-section token-card" :aria-busy="managingToken" aria-labelledby="account-security-tokens-title">
          <h2 id="account-security-tokens-title">{{ t('settingsUi.tokensTitle') }}</h2>
          <p class="security-description">{{ t('settingsUi.tokensDescription') }}</p>
          <div class="token-create-row">
            <label class="field">
              {{ t('settingsUi.tokenLabel') }}
              <input v-model="tokenLabel" :disabled="managingToken" />
            </label>
            <button class="button compact-action token-create-button" type="button" :disabled="managingToken" @click="createToken">
              {{ managingToken ? t('accountSecurity.creatingToken') : t('settingsUi.createToken') }}
            </button>
          </div>
          <div v-if="tokenError" class="error" role="alert" aria-live="assertive">{{ tokenError }}</div>
          <div v-if="newToken" class="card token-result" role="status" aria-live="polite">
            <strong>{{ t('settingsUi.copyNow') }}</strong>
            <code>{{ newToken }}</code>
          </div>
          <div class="table-scroll token-table">
            <table>
              <thead>
                <tr>
                  <th>{{ t('settingsUi.tokenLabel') }}</th>
                  <th>{{ t('settingsUi.prefix') }}</th>
                  <th>{{ t('settingsUi.lastUsedLabel') }}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="token in tokens" :key="token.id">
                  <td>{{ token.label }}</td>
                  <td><code>{{ token.token_prefix }}…</code></td>
                  <td>{{ token.last_used_at ? formatGermanDateTime(token.last_used_at) : t('settingsUi.never') }}</td>
                  <td>
                    <button v-if="!token.revoked_at" class="text-button danger" type="button" :disabled="managingToken" @click="revokeToken(token.id)">
                      {{ t('settingsUi.revoke') }}
                    </button>
                    <span v-else>{{ t('settingsUi.revoked') }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </template>
  </template>
</template>
