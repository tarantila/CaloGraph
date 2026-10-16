import type { ApiProblem, BackupStatus } from './types'
import { i18n } from './i18n'
import {
  AUTH_SESSION_CHANGED_PROBLEM,
  captureAuthIdentity,
  isAuthIdentityCurrent,
  type AuthIdentitySnapshot,
} from './auth-session'

let csrfToken: string | null =
  typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem('calograph_csrf')
let csrfTokenGeneration = 0
let authenticationExpiredHandler: (() => void) | null = null
let csrfRefreshEntry: {
  csrfGeneration: number
  identityGeneration: number | null
  promise: Promise<string>
} | null = null
const publicAuthenticationPaths: Record<string, true> = {
  '/auth/login': true,
  '/auth/mfa/totp/verify': true,
  '/auth/passkey/options': true,
  '/auth/passkey/verify': true,
  '/auth/register': true,
  '/auth/invitation/exchange': true,
  '/auth/recovery/complete': true,
  '/auth/bootstrap': true,
}

const SESSION_EXPIRED_PROBLEM = 'urn:calograph:problem:session-expired'
const CSRF_VALIDATION_FAILED_PROBLEM = 'urn:calograph:problem:csrf-validation-failed'
const apiProblemMessages: Record<string, string> = {
  'urn:calograph:problem:invalid-credentials': 'errors.invalidCredentials',
  [SESSION_EXPIRED_PROBLEM]: 'errors.sessionExpired',
  [AUTH_SESSION_CHANGED_PROBLEM]: 'errors.sessionExpired',
  'urn:calograph:problem:invalid-mfa-code': 'errors.invalidMfa',
  'urn:calograph:problem:invalid-invitation': 'errors.invalidInvitation',
  'urn:calograph:problem:username-taken': 'errors.usernameTaken',
  'urn:calograph:problem:validation-error': 'errors.validation',
  'urn:calograph:problem:invalid-timezone': 'errors.invalidTimezone',
  'urn:calograph:problem:rate-limited': 'errors.rateLimited',
  'urn:calograph:problem:bootstrap-already-initialized': 'auth.bootstrapRace',
  'urn:calograph:problem:data-export-busy': 'errors.dataExportBusy',
  'urn:calograph:problem:admin-reauthentication-failed': 'errors.adminReauthFailed',
  'urn:calograph:problem:admin-required': 'errors.adminRequired',
  'urn:calograph:problem:user-not-found': 'errors.userNotFound',
  'urn:calograph:problem:user-self-action': 'errors.userSelfAction',
  'urn:calograph:problem:last-admin': 'errors.lastAdmin',
  'urn:calograph:problem:target-active': 'errors.targetActive',
  'urn:calograph:problem:user-operation-busy': 'errors.userOperationBusy',
  'urn:calograph:problem:target-confirmation': 'errors.targetConfirmation',
  'urn:calograph:problem:invalid-current-password': 'errors.invalidCurrentPassword',
  'urn:calograph:problem:password-policy': 'errors.passwordPolicy',
  'urn:calograph:problem:last-target-required': 'errors.lastTargetRequired',
  'urn:calograph:problem:target-version-not-found': 'errors.targetVersionNotFound',
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
    public retryAfter?: string,
    public problemType?: string,
    public problemTitle?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class ApiTransportError extends Error {
  constructor(message = 'Could not connect to CaloGraph') {
    super(message)
    this.name = 'ApiTransportError'
  }
}

const IDEMPOTENT_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])
const GET_RETRY_DELAY_MS = 250
function waitForGetRetry(): Promise<void> {
  return new Promise((resolve) => globalThis.setTimeout(resolve, GET_RETRY_DELAY_MS))
}

async function fetchWithTransportRetry(
  input: RequestInfo | URL,
  init: RequestInit,
  method: string,
): Promise<Response> {
  try {
    return await fetch(input, init)
  } catch {
    if (!IDEMPOTENT_METHODS.has(method)) {
      throw new ApiTransportError('Could not connect to CaloGraph')
    }
    await waitForGetRetry()
    try {
      return await fetch(input, init)
    } catch {
      throw new ApiTransportError('Could not connect to CaloGraph')
    }
  }
}


export interface ApiErrorLocalizationOptions {
  problemTypeFallbacks?: Record<string, string>
  preserveDetail?: boolean
  preserveDetailForProblemTypes?: string[]
}

export function localizeApiError(
  error: unknown,
  fallbackKey = 'errors.generic',
  options: ApiErrorLocalizationOptions = {},
): string {
  if (error instanceof ApiTransportError) return i18n.global.t('errors.connectionFailed')
  if (!(error instanceof ApiError)) return i18n.global.t(fallbackKey)
  const key = error.problemType
    ? options.problemTypeFallbacks?.[error.problemType] ?? apiProblemMessages[error.problemType]
    : undefined
  const preserveDetail =
    Boolean(error.message)
    && (options.preserveDetail ?? !error.problemType)
    && (!error.problemType || options.preserveDetailForProblemTypes?.includes(error.problemType))
  if (preserveDetail) return error.message
  if (key) return i18n.global.t(key)
  return i18n.global.t(fallbackKey)
}

export function setCsrfToken(value: string | null): void {
  if (value !== csrfToken) csrfTokenGeneration += 1
  csrfToken = value
  if (typeof sessionStorage === 'undefined') return
  if (value) sessionStorage.setItem('calograph_csrf', value)
  else sessionStorage.removeItem('calograph_csrf')
}

export function setAuthenticationExpiredHandler(handler: (() => void) | null): void {
  authenticationExpiredHandler = handler
}

export function notifyAuthenticationExpired(): void {
  authenticationExpiredHandler?.()
}

function authSessionChangedError(): ApiError {
  return new ApiError(
    'Authentication session changed',
    409,
    undefined,
    undefined,
    AUTH_SESSION_CHANGED_PROBLEM,
  )
}

function assertAuthIdentity(snapshot: AuthIdentitySnapshot, requireUser = false): void {
  if (!isAuthIdentityCurrent(snapshot) || (requireUser && !snapshot.userId)) {
    throw authSessionChangedError()
  }
}

async function verifyServerIdentity(snapshot: AuthIdentitySnapshot): Promise<void> {
  assertAuthIdentity(snapshot, true)
  const response = await fetchWithTransportRetry(
    '/api/v1/auth/me',
    { credentials: 'include' },
    'GET',
  )
  if (!response.ok) throw authSessionChangedError()
  const user = (await response.json()) as { id?: unknown }
  if (user.id !== snapshot.userId) throw authSessionChangedError()
  assertAuthIdentity(snapshot, true)
}

async function requestCsrf(
  requestCsrfGeneration: number,
  identitySnapshot?: AuthIdentitySnapshot,
): Promise<string> {
  const response = await fetchWithTransportRetry(
    '/api/v1/auth/csrf',
    { credentials: 'include' },
    'GET',
  )
  if (!response.ok) {
    const expired = response.status === 401
    if (
      expired
      && requestCsrfGeneration === csrfTokenGeneration
      && (!identitySnapshot || isAuthIdentityCurrent(identitySnapshot))
    ) {
      notifyAuthenticationExpired()
    }
    throw new ApiError(
      expired ? 'Session expired' : `HTTP ${response.status}`,
      response.status,
      undefined,
      undefined,
      expired ? SESSION_EXPIRED_PROBLEM : undefined,
      undefined,
    )
  }
  const data = (await response.json()) as { csrf_token: string }
  if (requestCsrfGeneration !== csrfTokenGeneration) {
    throw new ApiError('Session expired', 401, undefined, undefined, SESSION_EXPIRED_PROBLEM)
  }
  if (identitySnapshot) assertAuthIdentity(identitySnapshot)
  return data.csrf_token
}

function refreshCsrf(identitySnapshot?: AuthIdentitySnapshot): Promise<string> {
  const csrfGeneration = csrfTokenGeneration
  const identityGeneration = identitySnapshot?.generation ?? null
  if (
    csrfRefreshEntry?.csrfGeneration === csrfGeneration
    && csrfRefreshEntry.identityGeneration === identityGeneration
  ) return csrfRefreshEntry.promise
  const promise = requestCsrf(csrfGeneration, identitySnapshot)
  csrfRefreshEntry = { csrfGeneration, identityGeneration, promise }
  void promise.then(
    () => {
      if (csrfRefreshEntry?.promise === promise) csrfRefreshEntry = null
    },
    () => {
      if (csrfRefreshEntry?.promise === promise) csrfRefreshEntry = null
    },
  )
  return promise
}

function installRefreshedCsrfToken(
  token: string,
  requestCsrfGeneration: number,
  identitySnapshot: AuthIdentitySnapshot,
): void {
  assertAuthIdentity(identitySnapshot)
  if (requestCsrfGeneration === csrfTokenGeneration) {
    setCsrfToken(token)
    return
  }
  if (csrfToken === token) return
  throw new ApiError('Session expired', 401, undefined, undefined, SESSION_EXPIRED_PROBLEM)
}

export async function ensureCsrfToken(
  identitySnapshot: AuthIdentitySnapshot = captureAuthIdentity(),
): Promise<string> {
  assertAuthIdentity(identitySnapshot, true)
  if (csrfToken) return csrfToken
  const requestCsrfGeneration = csrfTokenGeneration
  const token = await refreshCsrf(identitySnapshot)
  await verifyServerIdentity(identitySnapshot)
  installRefreshedCsrfToken(token, requestCsrfGeneration, identitySnapshot)
  return csrfToken ?? token
}
function isReplayableBody(body: BodyInit | null | undefined): boolean {
  return body == null || typeof body === 'string'
}

async function readApiProblem(response: Response): Promise<ApiProblem> {
  try {
    return (await response.json()) as ApiProblem
  } catch {
    return { status: response.status }
  }
}

function createApiError(
  response: Response,
  problem: ApiProblem,
  requestCsrfGeneration: number,
  path: string,
  identitySnapshot?: AuthIdentitySnapshot,
): ApiError {
  if (
    response.status === 401
    && requestCsrfGeneration === csrfTokenGeneration
    && (!identitySnapshot || isAuthIdentityCurrent(identitySnapshot))
    && path !== '/auth/me'
    && !publicAuthenticationPaths[path]
  ) {
    notifyAuthenticationExpired()
  }
  return new ApiError(
    problem.detail ?? `HTTP ${response.status}`,
    response.status,
    problem.request_id,
    response.headers.get('Retry-After') ?? undefined,
    problem.type,
    problem.title,
  )
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const mutating = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  const publicMutation =
    path === '/auth/login' ||
    path === '/auth/mfa/totp/verify' ||
    path === '/auth/passkey/options' ||
    path === '/auth/passkey/verify' ||
    path === '/auth/register' ||
    path === '/auth/invitation/exchange' ||
    path === '/auth/recovery/complete' ||
    path === '/auth/bootstrap'
  const identitySnapshot = mutating && !publicMutation ? captureAuthIdentity() : undefined
  let requestCsrfGeneration = csrfTokenGeneration
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (identitySnapshot) {
    assertAuthIdentity(identitySnapshot)
    let requestToken = csrfToken
    if (!requestToken) {
      const refreshGeneration = csrfTokenGeneration
      requestToken = await refreshCsrf(identitySnapshot)
      if (identitySnapshot.userId) await verifyServerIdentity(identitySnapshot)
      installRefreshedCsrfToken(requestToken, refreshGeneration, identitySnapshot)
    }
    assertAuthIdentity(identitySnapshot)
    headers.set('X-CSRF-Token', requestToken)
    requestCsrfGeneration = csrfTokenGeneration
  }
  if (identitySnapshot) assertAuthIdentity(identitySnapshot)
  let response = await fetchWithTransportRetry(
    `/api/v1${path}`,
    { ...options, headers, credentials: 'include' },
    method,
  )

  if (identitySnapshot) assertAuthIdentity(identitySnapshot)
  if (!response.ok) {
    let problem = await readApiProblem(response)
    const canSelfHeal =
      mutating
      && !publicMutation
      && isReplayableBody(options.body)
      && response.status === 403
      && problem.type === CSRF_VALIDATION_FAILED_PROBLEM
    if (canSelfHeal) {
      if (!identitySnapshot) throw authSessionChangedError()
      assertAuthIdentity(identitySnapshot, true)
      setCsrfToken(null)
      const refreshGeneration = csrfTokenGeneration
      const refreshed = await refreshCsrf(identitySnapshot)
      await verifyServerIdentity(identitySnapshot)
      assertAuthIdentity(identitySnapshot, true)
      installRefreshedCsrfToken(refreshed, refreshGeneration, identitySnapshot)
      headers.set('X-CSRF-Token', refreshed)
      requestCsrfGeneration = csrfTokenGeneration
      assertAuthIdentity(identitySnapshot, true)
      response = await fetchWithTransportRetry(
        `/api/v1${path}`,
        { ...options, headers, credentials: 'include' },
        method,
      )
      if (identitySnapshot) assertAuthIdentity(identitySnapshot)
      if (!response.ok) problem = await readApiProblem(response)
    }
    if (!response.ok) {
      throw createApiError(response, problem, requestCsrfGeneration, path, identitySnapshot)
    }
  }
  if (identitySnapshot) assertAuthIdentity(identitySnapshot)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function getBackupStatus(): Promise<BackupStatus> {
  return api<BackupStatus>('/admin/backup-status')
}
