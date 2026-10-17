import { defineStore } from 'pinia'
import { onScopeDispose, ref, watch } from 'vue'

import { ApiError, ApiTransportError, api, setCsrfToken } from '../api'
import {
  acceptAuthSessionRevision,
  captureAuthIdentity,
  isAuthIdentityCurrent,
  publishAuthSessionChange,
  setAuthenticatedIdentity,
  subscribeToAuthSessionChanges,
} from '../auth-session'
import { applyUserLocale, PUBLIC_LOCALE, setLocale } from '../i18n'
import type { Achievement, OnboardingStatus, User } from '../types'
import {
  authenticateWithPasskey,
  type WebAuthnOptionsResponse,
} from '../webauthn'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loading = ref(false)
  const mfaRequired = ref(false)
  const onboardingStatus = ref<OnboardingStatus | null>(null)
  // Kept as a read/write compatibility signal for existing consumers; new
  // routing decisions use onboardingStatus.
  const needsTargetSetup = ref<boolean | null>(null)
  const sessionRestoreUnavailable = ref(false)
  const newlyUnlockedAchievements = ref<Achievement[]>([])
  let profileUpdateGeneration = 0
  let profileUpdatePending = false
  let profileUpdateQueue: Promise<void> = Promise.resolve()
  let reconciledUserId: string | null = null
  let deferredAuthSessionRestore = false

  watch(user, (value) => setAuthenticatedIdentity(value?.id ?? null), { flush: 'sync' })

  function setAuthenticatedUser(value: User, applyLocale = true, publishSession = false): void {
    const previousUserId = user.value?.id ?? null
    profileUpdateGeneration += 1
    if (previousUserId !== value.id) {
      reconciledUserId = null
      setOnboardingStatus(null)
    }
    if (publishSession || (previousUserId !== null && previousUserId !== value.id)) {
      publishAuthSessionChange(value.id)
    } else {
      setAuthenticatedIdentity(value.id)
    }
    sessionRestoreUnavailable.value = false
    deferredAuthSessionRestore = false
    user.value = value
    if (applyLocale) applyUserLocale(value.language)
  }

  function setOnboardingStatus(value: OnboardingStatus | null): void {
    onboardingStatus.value = value
    needsTargetSetup.value = value === null ? null : value.required
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
  function enqueueProfileRead<T>(
    operation: () => Promise<T>,
  ): Promise<{ generation: number; value: T }> {
    const run = async (): Promise<{ generation: number; value: T }> => {
      const generation = profileUpdateGeneration
      return { generation, value: await operation() }
    }
    const queued = profileUpdateQueue.then(run, run)
    profileUpdateQueue = queued.then(() => undefined, () => undefined)
    return queued
  }
  function enqueueProfileUpdate<T>(
    generation: number,
    operation: () => Promise<T>,
    onSuccess?: (value: T) => void,
  ): Promise<T | null> {
    const run = async (): Promise<T | null> => {
      if (!isCurrentProfileUpdate(generation)) return null
      profileUpdatePending = true
      try {
        const result = await operation()
        if (isCurrentProfileUpdate(generation)) onSuccess?.(result)
        return result
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
    setAuthenticatedIdentity(value.id)
    user.value = value
    applyUserLocale(value.language)
    return true
  }

  function commitProfileUpdate(generation: number, value: User): boolean {
    if (!isCurrentProfileUpdate(generation)) return false
    profileUpdatePending = false
    profileUpdateGeneration += 1
    setAuthenticatedIdentity(value.id)
    user.value = value
    applyUserLocale(value.language)
    return true
  }

  function clearSession(options: {
    notifyOtherTabs?: boolean
    preserveMfa?: boolean
    forceRevision?: boolean
  } = {}): void {
    const notifyOtherTabs = options.notifyOtherTabs ?? true
    if (notifyOtherTabs && (user.value !== null || options.forceRevision)) {
      publishAuthSessionChange(null)
    } else {
      setAuthenticatedIdentity(null)
    }
    profileUpdateGeneration += 1
    profileUpdatePending = false
    user.value = null
    if (!options.preserveMfa) mfaRequired.value = false
    setOnboardingStatus(null)
    sessionRestoreUnavailable.value = false
    newlyUnlockedAchievements.value = []
    reconciledUserId = null
    setCsrfToken(null)
    setLocale(PUBLIC_LOCALE)
  }

  function clearSessionRestoreError(): void {
    sessionRestoreUnavailable.value = false
  }

  async function reconcileAchievements(force = false): Promise<void> {
    const currentUser = user.value
    const reconcileGeneration = currentProfileUpdateGeneration()
    if (
      !currentUser ||
      (onboardingStatus.value?.required ?? needsTargetSetup.value !== false) ||
      (!force && reconciledUserId === currentUser.id)
    ) return
    try {
      const result = await api<{
        achievements: Achievement[]
        newly_unlocked: Achievement[]
      }>('/achievements/reconcile', { method: 'POST' })
      if (
        !user.value ||
        user.value.id !== currentUser.id ||
        currentProfileUpdateGeneration() !== reconcileGeneration
      ) {
        return
      }
      newlyUnlockedAchievements.value = result.newly_unlocked ?? []
      reconciledUserId = currentUser.id
    } catch {
      // Reconciliation is retried at the next authenticated bootstrap.
    }
  }


  async function ensureUser(applyLocale = true, notifyOtherTabs = true): Promise<boolean> {
    const generation = currentProfileUpdateGeneration()
    const requestIdentity = captureAuthIdentity()
    sessionRestoreUnavailable.value = false
    try {
      const loadedUser = await api<User>('/auth/me')
      if (
        !isCurrentProfileUpdate(generation)
        || profileUpdatePending
        || !isAuthIdentityCurrent(requestIdentity)
      ) return Boolean(user.value)
      const accountChanged = user.value !== null && user.value.id !== loadedUser.id
      setAuthenticatedUser(loadedUser, applyLocale, notifyOtherTabs && accountChanged)
    } catch (error) {
      if (
        !isCurrentProfileUpdate(generation)
        || profileUpdatePending
        || !isAuthIdentityCurrent(requestIdentity)
      ) return Boolean(user.value)
      if (error instanceof ApiError && error.status === 401) {
        clearSession({ notifyOtherTabs })
        return false
      }
      if (!user.value && (error instanceof ApiTransportError || error instanceof ApiError)) {
        sessionRestoreUnavailable.value = true
      }
      return Boolean(user.value)
    }
    const restoreGeneration = currentProfileUpdateGeneration()
    const restoreIdentity = captureAuthIdentity()
    if (onboardingStatus.value === null) {
      try {
        const status = await api<OnboardingStatus>('/settings/onboarding')
        let normalizedStatus: OnboardingStatus
        if (Array.isArray(status)) {
          normalizedStatus = {
            mode: 'legacy',
            required: status.length === 0,
            completed: status.length > 0,
            current_step: status.length > 0 ? 'completed' : 'targets',
          }
        } else {
          if (
            !status ||
            (status.mode !== 'full' && status.mode !== 'legacy') ||
            typeof status.required !== 'boolean' ||
            typeof status.completed !== 'boolean'
          ) {
            throw new Error('invalid onboarding status')
          }
          normalizedStatus = status
        }
        if (
          currentProfileUpdateGeneration() !== restoreGeneration
          || profileUpdatePending
          || !isAuthIdentityCurrent(restoreIdentity)
        ) {
          return Boolean(user.value)
        }
        setOnboardingStatus(normalizedStatus)
      } catch {
        if (!isAuthIdentityCurrent(restoreIdentity)) return Boolean(user.value)
        // Compatibility with servers from before the onboarding endpoint.
        try {
          const targets = await api<unknown[]>('/settings/targets')
          if (
            currentProfileUpdateGeneration() !== restoreGeneration
            || profileUpdatePending
            || !isAuthIdentityCurrent(restoreIdentity)
          ) {
            return Boolean(user.value)
          }
          setOnboardingStatus({
            mode: 'legacy',
            required: targets.length === 0,
            completed: targets.length > 0,
            current_step: targets.length > 0 ? 'completed' : 'targets',
          })
        } catch {
          return Boolean(user.value)
        }
      }
    }
    await reconcileAchievements()
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
        clearSession({ notifyOtherTabs: false })
        mfaRequired.value = true
        return false
      }
      setAuthenticatedUser(result.user, true, true)
      setOnboardingStatus(null)
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
      setAuthenticatedUser(result.user, true, true)
      setOnboardingStatus(null)
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
      setAuthenticatedUser(result.user, true, true)
      setCsrfToken(result.csrf_token)
      setOnboardingStatus(null)
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
    if (deferredAuthSessionRestore) {
      deferredAuthSessionRestore = false
      void ensureUser(true, false)
    }
  }

  function completeTargetSetup(): void {
    if (onboardingStatus.value?.mode === 'full') return
    setOnboardingStatus({
      mode: 'legacy',
      required: false,
      completed: true,
      current_step: 'completed',
    })
  }

  async function advanceOnboarding(expectedStep: OnboardingStatus['current_step']): Promise<OnboardingStatus> {
    const requestedUserId = user.value?.id
    const requestedGeneration = currentProfileUpdateGeneration()
    const status = await api<OnboardingStatus>('/settings/onboarding/advance', {
      method: 'POST',
      body: JSON.stringify({ expected_step: expectedStep }),
    })
    if (
      !requestedUserId ||
      user.value?.id !== requestedUserId ||
      currentProfileUpdateGeneration() !== requestedGeneration
    ) {
      throw new Error('stale onboarding response')
    }
    setOnboardingStatus(status)
    if (status.completed) await reconcileAchievements(true)
    return status
  }

  async function logout(): Promise<void> {
    await api<void>('/auth/logout', { method: 'POST' })
    clearSession({ forceRevision: true })
  }

  const unsubscribeFromAuthSession = subscribeToAuthSessionChanges((revision) => {
    if (!acceptAuthSessionRevision(revision)) return
    const preserveMfa = mfaRequired.value
    deferredAuthSessionRestore = preserveMfa
    clearSession({ notifyOtherTabs: false, preserveMfa })
    if (!preserveMfa) void ensureUser(true, false)
  })
  onScopeDispose(unsubscribeFromAuthSession)

  return {
    user,
    loading,
    mfaRequired,
    onboardingStatus,
    needsTargetSetup,
    sessionRestoreUnavailable,
    newlyUnlockedAchievements,
    ensureUser,
    reconcileAchievements,
    applyCurrentUserLocale,
    login,
    loginWithPasskey,
    verifyMfa,
    cancelMfa,
    completeTargetSetup,
    advanceOnboarding,
    beginProfileUpdate,
    currentProfileUpdateGeneration,
    enqueueProfileRead,
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
