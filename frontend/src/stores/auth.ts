import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api, setCsrfToken } from '../api'
import type { User } from '../types'
import {
  authenticateWithPasskey,
  type WebAuthnOptionsResponse,
} from '../webauthn'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const mfaRequired = ref(false)

  function clearSession(): void {
    user.value = null
    mfaRequired.value = false
    setCsrfToken(null)
  }

  async function ensureUser(): Promise<boolean> {
    try {
      user.value = await api<User>('/auth/me')
      return true
    } catch {
      clearSession()
      return false
    }
  }

  async function login(username: string, password: string): Promise<boolean> {
    loading.value = true
    try {
      const result = await api<
        | { mfa_required: true }
        | { mfa_required: false; user: User; csrf_token: string }
      >('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      if (result.mfa_required) {
        user.value = null
        setCsrfToken(null)
        mfaRequired.value = true
        return false
      }
      user.value = result.user
      setCsrfToken(result.csrf_token)
      mfaRequired.value = false
      return true
    } finally {
      loading.value = false
    }
  }

  async function verifyMfa(code: string): Promise<void> {
    loading.value = true
    try {
      const result = await api<{
        mfa_required: false
        user: User
        csrf_token: string
      }>('/auth/mfa/totp/verify', {
        method: 'POST',
        body: JSON.stringify({ code }),
      })
      user.value = result.user
      setCsrfToken(result.csrf_token)
      mfaRequired.value = false
    } finally {
      loading.value = false
    }
  }

  async function loginWithPasskey(): Promise<void> {
    loading.value = true
    try {
      const options = await api<WebAuthnOptionsResponse>('/auth/passkey/options', {
        method: 'POST',
      })
      const credential = await authenticateWithPasskey(options.public_key)
      const result = await api<{
        mfa_required: false
        user: User
        csrf_token: string
      }>('/auth/passkey/verify', {
        method: 'POST',
        body: JSON.stringify({
          challenge_id: options.challenge_id,
          credential,
        }),
      })
      user.value = result.user
      setCsrfToken(result.csrf_token)
      mfaRequired.value = false
    } finally {
      loading.value = false
    }
  }

  function cancelMfa(): void {
    mfaRequired.value = false
  }

  async function logout(): Promise<void> {
    await api<void>('/auth/logout', { method: 'POST' })
    user.value = null
    setCsrfToken(null)
  }

  return {
    user,
    loading,
    mfaRequired,
    ensureUser,
    login,
    loginWithPasskey,
    verifyMfa,
    cancelMfa,
    clearSession,
    logout,
  }
})
