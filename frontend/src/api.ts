import type { ApiProblem } from './types'

let csrfToken: string | null = sessionStorage.getItem('calograph_csrf')

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public requestId?: string,
  ) {
    super(message)
  }
}

export function setCsrfToken(value: string | null): void {
  csrfToken = value
  if (value) sessionStorage.setItem('calograph_csrf', value)
  else sessionStorage.removeItem('calograph_csrf')
}

async function refreshCsrf(): Promise<string> {
  const response = await fetch('/api/v1/auth/csrf', { credentials: 'include' })
  if (!response.ok) throw new ApiError('Sitzung ist abgelaufen.', response.status)
  const data = (await response.json()) as { csrf_token: string }
  setCsrfToken(data.csrf_token)
  return data.csrf_token
}

export async function ensureCsrfToken(): Promise<string> {
  return csrfToken ?? refreshCsrf()
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const mutating = !['GET', 'HEAD', 'OPTIONS'].includes(method)
  const publicMutation =
    path === '/auth/login' ||
    path === '/auth/mfa/totp/verify' ||
    path === '/auth/register' ||
    path === '/auth/invitation/exchange'
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (mutating && !publicMutation) headers.set('X-CSRF-Token', csrfToken ?? (await refreshCsrf()))
  const response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'include' })
  if (response.status === 204) return undefined as T
  if (!response.ok) {
    let problem: ApiProblem = { detail: `HTTP ${response.status}` }
    try {
      problem = (await response.json()) as ApiProblem
    } catch {
      // Keep the status-based fallback without leaking response bodies.
    }
    throw new ApiError(problem.detail, response.status, problem.request_id)
  }
  return (await response.json()) as T
}
