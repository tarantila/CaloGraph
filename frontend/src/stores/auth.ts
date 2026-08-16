import { defineStore } from 'pinia'
import { ref } from 'vue'

import { ApiError, ApiTransportError, api, setCsrfToken } from '../api'
import { applyUserLocale, PUBLIC_LOCALE, setLocale } from '../i18n'
import type { Achievement, User } from '../types'
import {
  authenticateWithPasskey,
  type WebAuthnOptionsResponse,
} from '../webauthn'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const mfaRequired = ref(false)
  const needsTargetSetup = ref<boolean | null>(null)
  const sessionRestoreUnavailable = ref(false)
  const newlyUnlockedAchievements = ref<Achievement[]>([])
  let profileUpdateGeneration = 0
  let profileUpdatePending = false
  let profileUpdateQueue: Promise<void> = Promise.resolve()
  let reconciledUserId: string | null = null
  function setAuthenticatedUser(value: User, applyLocale = true): void {
    profileUpdateGeneration += 1
    if (user.value?.id !== value.id) reconciledUserId = null
    sessionRestoreUnavailable.value = false
    user.value = value
    if (applyLocale) applyUserLocale(value.language)
  }
  function applyCurrentUserLocale(): void {
    if (user.value) applyUserLocale(user.value.language)
  }

  function beginProfileUpdate(language?: string): number {
    profileUpdateGeneration += 1
    profileUpdatePending = false
    sessionRestoreUnavailable.value = false
    if (user.value && (language === 'de' || language === 'en')) {
      user.value = { ...user.value, language }
    }
    return profileUpdateGeneration
  }

  function currentProfileUpdateGeneration(): number {
    return profileUpdateGeneration
  }
  function enqueueProfileUpdate<T>(generation: number, operation: () => Promise<T>): Promise<T | null> {
    const run = async (): Promise<T | null> => {
      if (!isCurrentProfileUpdate(generation)) return null
      profileUpdatePending = true
      try {
        return await operation()
      } catch (error) {
        if (isCurrentProfileUpdate(generation)) profileUpdatePending = false
        throw error
      }
    }
    const queued = profileUpdateQueue.then(run, run)
    profileUpdateQueue = queued.then(() => undefined, () => undefined)
    return queued
  }

  function isCurrentProfileUpdate(generation: number): boolean {
    return generation === profileUpdateGeneration
  }

  function syncLoadedUser(generation: number, value: User): boolean {
    if (!isCurrentProfileUpdate(generation) || profileUpdatePending) return false
    user.value = value
    applyUserLocale(value.language)
    return true
  }

  function commitProfileUpdate(generation: number, value: User): boolean {
    if (!isCurrentProfileUpdate(generation)) return false
    profileUpdatePending = false
    profileUpdateGeneration += 1
    user.value = value
    applyUserLocale(value.language)
    return true
  }
  function clearSession(): void {
    profileUpdateGeneration += 1
    profileUpdatePending = false
    user.value = null
    mfaRequired.value = false
    needsTargetSetup.value = null
    sessionRestoreUnavailable.value = false
    newlyUnlockedAchievements.value = []
    reconciledUserId = null
    setCsrfToken(null)
    setLocale(PUBLIC_LOCALE)
  }

  function clearSessionRestoreError(): void {
    sessionRestoreUnavailable.value = false
  }

  async function reconcileAchievementsIfReady(): Promise<void> {
    if (!user.value || needsTargetSetup.value !== false || reconciledUserId === user.value.id) return
    try {
      const result = await api<{
        achievements: Achievement[]
        newly_unlocked: Achievement[]
      }>('/achievements/reconcile', { method: 'POST' })
      newlyUnlockedAchievements.value = result.newly_unlocked ?? []
      reconciledUserId = user.value.id
    } catch {
      // Reconciliation is retried at the next authenticated bootstrap.
    }
  }

  async function ensureUser(applyLocale = true): Promise<boolean> {
    const generation = currentProfileUpdateGeneration()
    sessionRestoreUnavailable.value = false
    try {
      const loadedUser = await api<User>('/auth/me')
      if (!isCurrentProfileUpdate(generation) || profileUpdatePending) return Boolean(user.value)
      setAuthenticatedUser(loadedUser, applyLocale)
    } catch (error) {
      if (!isCurrentProfileUpdate(generation) || profileUpdatePending) return Boolean(user.value)
      if (error instanceof ApiError && error.status === 401) {
        clearSession()
        return false
      }
      if (!user.value && (error instanceof ApiTransportError || error instanceof ApiError)) {
        sessionRestoreUnavailable.value = true
      }
      return Boolean(user.value)
    }
    const restoreGeneration = currentProfileUpdateGeneration()
    if (needsTargetSetup.value === null) {
      try {
        const targets = await api<unknown[]>('/settings/targets')
        if (currentProfileUpdateGeneration() !== restoreGeneration || profileUpdatePending) {
          return Boolean(user.value)
        }
        needsTargetSetup.value = targets.length === 0
      } catch {
        return Boolean(user.value)
      }
    }
    await reconcileAchievementsIfReady()
    return true
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
        clearSession()
        mfaRequired.value = true
        return false
      }
      setAuthenticatedUser(result.user)
      needsTargetSetup.value = null
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
      setAuthenticatedUser(result.user)
      needsTargetSetup.value = null
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
      setAuthenticatedUser(result.user)
      setCsrfToken(result.csrf_token)
      needsTargetSetup.value = null
      mfaRequired.value = false
    } finally {
      loading.value = false
    }
  }

  function clearAchievementNotice(): void {
    newlyUnlockedAchievements.value = []
  }
  function cancelMfa(): void {
    mfaRequired.value = false
  }

  function completeTargetSetup(): void {
    needsTargetSetup.value = false
  }

  async function logout(): Promise<void> {
    await api<void>('/auth/logout', { method: 'POST' })
    clearSession()
  }

  return {
    user,
    loading,
    mfaRequired,
    needsTargetSetup,
    sessionRestoreUnavailable,
    newlyUnlockedAchievements,
    ensureUser,
    applyCurrentUserLocale,
    login,
    loginWithPasskey,
    verifyMfa,
    cancelMfa,
    completeTargetSetup,
    beginProfileUpdate,
    currentProfileUpdateGeneration,
    enqueueProfileUpdate,
    isCurrentProfileUpdate,
    syncLoadedUser,
    commitProfileUpdate,
    clearSession,
    clearSessionRestoreError,
    clearAchievementNotice,
    logout,
  }
})
