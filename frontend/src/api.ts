import type { ApiProblem } from './types'
import { i18n } from './i18n'

let csrfToken: string | null =
  typeof sessionStorage === 'undefined' ? null : sessionStorage.getItem('calograph_csrf')
let authenticationSessionGeneration = 0
let authenticationExpiredHandler: (() => void) | null = null
let csrfRefreshEntry: { generation: number; promise: Promise<string> } | null = null
const publicAuthenticationPaths: Record<string, true> = {
  '/auth/login': true,
  '/auth/mfa/totp/verify': true,
  '/auth/passkey/options': true,
  '/auth/passkey/verify': true,
  '/auth/register': true,
  '/auth/invitation/exchange': true,
  '/auth/recovery/complete': true,
}

const SESSION_EXPIRED_PROBLEM = 'urn:calograph:problem:session-expired'
const apiProblemMessages: Record<string, string> = {
  'urn:calograph:problem:invalid-credentials': 'errors.invalidCredentials',
  [SESSION_EXPIRED_PROBLEM]: 'errors.sessionExpired',
  'urn:calograph:problem:invalid-mfa-code': 'errors.invalidMfa',
  'urn:calograph:problem:invalid-invitation': 'errors.invalidInvitation',
  'urn:calograph:problem:username-taken': 'errors.usernameTaken',
  'urn:calograph:problem:validation-error': 'errors.validation',
  'urn:calograph:problem:invalid-timezone': 'errors.invalidTimezone',
  'urn:calograph:problem:rate-limited': 'errors.rateLimited',
  'urn:calograph:problem:admin-reauthentication-failed': 'errors.adminReauthFailed',
  'urn:calograph:problem:admin-required': 'errors.adminRequired',
  'urn:calograph:problem:user-not-found': 'errors.userNotFound',
  'urn:calograph:problem:user-self-action': 'errors.userSelfAction',
  'urn:calograph:problem:last-admin': 'errors.lastAdmin',
  'urn:calograph:problem:target-active': 'errors.targetActive',
  'urn:calograph:problem:user-operation-busy': 'errors.userOperationBusy',
  'urn:calograph:problem:target-confirmation': 'errors.targetConfirmation',
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
  if (value !== csrfToken) authenticationSessionGeneration += 1
  csrfToken = value
  if (typeof sessionStorage === 'undefined') return
  if (value) sessionStorage.setItem('calograph_csrf', value)
  else sessionStorage.removeItem('calograph_csrf')
}

export function setAuthenticationExpiredHandler(handler: (() => void) | null): void {
  authenticationExpiredHandler = handler
}

function notifyAuthenticationExpired(): void {
  authenticationExpiredHandler?.()
}

async function requestCsrf(requestSessionGeneration: number): Promise<string> {
  const response = await fetchWithTransportRetry(
    '/api/v1/auth/csrf',
    { credentials: 'include' },
    'GET',
  )
  if (!response.ok) {
    const expired = response.status === 401
    if (expired && requestSessionGeneration === authenticationSessionGeneration) {
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
  if (requestSessionGeneration !== authenticationSessionGeneration) {
    throw new ApiError('Session expired', 401, undefined, undefined, SESSION_EXPIRED_PROBLEM, undefined)
  }
  setCsrfToken(data.csrf_token)
  return data.csrf_token
}

function refreshCsrf(): Promise<string> {
  const generation = authenticationSessionGeneration
  if (csrfRefreshEntry?.generation === generation) return csrfRefreshEntry.promise
  const promise = requestCsrf(generation)
  csrfRefreshEntry = { generation, promise }
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

export async function ensureCsrfToken(): Promise<string> {
  return csrfToken ?? refreshCsrf()
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let requestSessionGeneration = authenticationSessionGeneration
  const method = (options.method ?? 'GET').toUpperCase()
  const mutating = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  const publicMutation =
    path === '/auth/login' ||
    path === '/auth/mfa/totp/verify' ||
    path === '/auth/passkey/options' ||
    path === '/auth/passkey/verify' ||
    path === '/auth/register' ||
    path === '/auth/invitation/exchange' ||
    path === '/auth/recovery/complete'
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (mutating && !publicMutation) {
    headers.set('X-CSRF-Token', csrfToken ?? (await refreshCsrf()))
    requestSessionGeneration = authenticationSessionGeneration
  }
  const response = await fetchWithTransportRetry(
    `/api/v1${path}`,
    { ...options, headers, credentials: 'include' },
    method,
  )
  if (response.status === 204) return undefined as T
  if (!response.ok) {
    if (
      response.status === 401
      && requestSessionGeneration === authenticationSessionGeneration
      && path !== '/auth/me'
      && !publicAuthenticationPaths[path]
    ) {
      notifyAuthenticationExpired()
    }
    let problem: ApiProblem = { status: response.status }
    try {
      problem = (await response.json()) as ApiProblem
    } catch {
      // Keep the status-based fallback without leaking response bodies.
    }
    throw new ApiError(
      problem.detail ?? `HTTP ${response.status}`,
      response.status,
      problem.request_id,
      response.headers.get('Retry-After') ?? undefined,
      problem.type,
      problem.title,
    )
  }
  return (await response.json()) as T
}
