export const AUTH_SESSION_REVISION_KEY = 'calograph_auth_session_revision'
export const AUTH_SESSION_CHANGED_PROBLEM = 'urn:calograph:problem:auth-session-changed'

export interface AuthIdentitySnapshot {
  userId: string | null
  generation: number
  revision: string | null
}

let storageAvailable = typeof window !== 'undefined'
let sessionRevision = readStoredRevision()
let currentUserId: string | null = null
let identityGeneration = 0

function readStoredRevision(): string | null {
  if (!storageAvailable) return null
  try {
    return window.localStorage.getItem(AUTH_SESSION_REVISION_KEY)
  } catch {
    storageAvailable = false
    return null
  }
}

function writeStoredRevision(revision: string): boolean {
  if (!storageAvailable) return false
  try {
    window.localStorage.setItem(AUTH_SESSION_REVISION_KEY, revision)
    return true
  } catch {
    storageAvailable = false
    return false
  }
}

function createRevision(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

export function setAuthenticatedIdentity(userId: string | null): void {
  if (currentUserId === userId) return
  currentUserId = userId
  identityGeneration += 1
}

export function publishAuthSessionChange(userId: string | null): void {
  const revision = createRevision()
  const persisted = writeStoredRevision(revision)
  if (persisted) sessionRevision = revision
  currentUserId = userId
  identityGeneration += 1
}

export function acceptAuthSessionRevision(revision: string): boolean {
  const latestRevision = readStoredRevision() ?? revision
  if (latestRevision === sessionRevision) return false
  sessionRevision = latestRevision
  currentUserId = null
  identityGeneration += 1
  return true
}

export function captureAuthIdentity(): AuthIdentitySnapshot {
  return {
    userId: currentUserId,
    generation: identityGeneration,
    revision: sessionRevision,
  }
}

export function isAuthIdentityCurrent(snapshot: AuthIdentitySnapshot): boolean {
  if (
    snapshot.userId !== currentUserId
    || snapshot.generation !== identityGeneration
    || snapshot.revision !== sessionRevision
  ) return false
  if (!storageAvailable) return true
  return readStoredRevision() === snapshot.revision || !storageAvailable
}

export function subscribeToAuthSessionChanges(listener: (revision: string) => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  const handleStorage = (event: StorageEvent) => {
    if (event.key === AUTH_SESSION_REVISION_KEY && event.newValue) listener(event.newValue)
  }
  window.addEventListener('storage', handleStorage)
  return () => window.removeEventListener('storage', handleStorage)
}
