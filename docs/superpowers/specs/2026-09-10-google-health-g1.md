# Google Health G1 Design Specification

## Goal

Add the Google Health OAuth foundation and a bounded read-only client without importing Google Health data into the Nutrition domain.

## Scope

G1 includes Google Health OAuth, one user-owned connection, callback/session/CSRF protection, encrypted refresh-token and PKCE-verifier storage, sanitized status/error handling, and a fixed-host read-only client with one bounded Nutrition Log page operation.

G1 explicitly excludes Nutrition-domain ingestion, Google provider resolution, source-priority changes, provider-registry changes, frontend work, hydration access, and any health/nutrition payload persistence.

## Branch and baseline

The implementation branch is `feature/google-health-g1`, created directly from C0 commit `80ef3d4b22094bc58640516439ef657aea958c65`. The C0 commit must not be amended or changed.

## Official Google endpoints and scope

- Health API base: `https://health.googleapis.com/v4`
- Authorization endpoint: `https://accounts.google.com/o/oauth2/v2/auth`
- Token endpoint: `https://oauth2.googleapis.com/token`
- Required and only requested scope: `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`

The implementation must never request a write scope, a wildcard scope, or an unrelated Health scope.

## API and redirect contract

The existing API prefix is `/api/v1`:

- `GET /api/v1/google-health/status`
- `POST /api/v1/google-health/oauth/start`
- `GET /api/v1/google-health/oauth/callback`

The redirect URI is derived only from `CALOGRAPH_PUBLIC_URL` and the fixed callback path:

`<CALOGRAPH_PUBLIC_URL>/api/v1/google-health/oauth/callback`

No user-supplied redirect URL, callback path, host, or scheme is accepted. Existing production configuration must enforce HTTPS as it does for other public URLs.

## Configuration

Add disabled-by-default settings:

- `GOOGLE_HEALTH_ENABLED=false`
- Google OAuth client ID
- Google OAuth client secret
- Google OAuth client-secret file

Secret values are excluded from `repr` and `model_dump`, use the existing bounded secret-file reader, reject direct-value/file conflicts according to existing settings behavior, and are never logged. Enabling Google Health without a complete client configuration must fail safely at runtime/configuration boundaries.

## OAuth security

The start endpoint requires an authenticated active CaloGraph session and the existing CSRF-protected mutation dependency. It is rate limited by user and normalized client IP.

Each start creates a persistent, user-bound OAuth flow containing:

- UUID
- user ID
- HMAC/hash of a random state value, never the raw state
- encrypted PKCE verifier
- expiry around ten minutes
- consumed timestamp/one-time marker
- created timestamp

The authorization request uses authorization-code flow, offline access, exact redirect URI, exact read-only scope, state, and PKCE S256. `prompt=consent` is conditional: initial consent, reauthorization, missing refresh token, or scope change only; not every start.

The callback requires a valid current CaloGraph session. It must reject absent/invalid session, unknown state, expired state, replayed state, cross-user state, bad code, and missing required scope. Flow consumption is atomic/one-time and safe under concurrent callbacks. OAuth callback errors are sanitized and never expose raw provider responses, codes, state, verifier, or token values.

## Connection persistence

`GoogleHealthConnection` is one active/configured connection per user:

- UUID primary key
- user FK with cascade delete
- unique user ID
- encrypted refresh token using existing `credential_crypto`
- deterministic normalized granted scopes
- state `active` or `reauth_required`
- nullable refresh-token expiry
- attempt/success/error timestamps
- bounded sanitized last-error code
- created/updated timestamps

No access token, authorization code, client secret, raw state, or raw provider response is stored.

Successful initial authorization creates the connection. Successful reauthorization updates the existing row in place, preserving its UUID while atomically replacing encrypted refresh token, scopes, expiry, state, and error fields. Missing refresh token never produces an active connection. Missing required scope produces `scope_missing` and leaves the connection inactive/reauth-required.

## Status and security events

The status endpoint is authenticated and returns only safe availability/configuration/state/scope/timestamp/error-code information. It never returns secrets, encrypted blobs, access tokens, authorization codes, raw state, PKCE verifier, or provider payloads.

Google Health security events must be registered in both the event catalog and the persistent-audit allowlist when persistence is intended. Event refs use existing pseudonymous references. Event reasons/details are fixed allowlisted values; no raw exceptions, URLs containing codes, payloads, usernames, emails, health values, tokens, or credentials are permitted.

## Read-only client

Implement `GoogleHealthClient` with injected transport/token dependencies. The client:

- uses only the fixed Health base URL
- performs GET-only requests
- has explicit bounded timeouts
- rejects redirects
- exposes explicit methods, not a generic arbitrary `request(method, url, ...)`
- maps authentication, scope/403, rate-limit, timeout/network, 5xx, and invalid-response failures to sanitized typed errors
- never persists raw responses or logs raw response bodies

G1 requires a narrow Nutrition Log page read with bounded page size, validated `nextPageToken`, and optional civil-time filtering based on `nutrition_log.interval.civil_start_time`. The result is validated in-memory transport DTO data only. No hydration request is implemented.

## Error and refresh behavior

`invalid_grant`, expired/revoked refresh tokens, and missing required scopes transition the connection to `reauth_required` with a fixed safe reason. Network timeouts, transport failures, 429, and 5xx do not erase a valid connection. Access tokens exist only in process memory during a request and are never persisted.

## Migration and compatibility

Add one linear Alembic revision after `20260910_0028`, expected as `20260910_0029`, for Google connection and OAuth-flow tables only. It must work with SQLite and PostgreSQL conventions, include named constraints/indexes where applicable, and make no Nutrition table changes.

Existing C0 models, provider registry behavior, source-priority behavior, YAZIO behavior, and nutrition identity/source-instance behavior must remain unchanged.

## Verification gates

Offline verification must cover the specified G1 A–W behaviors: state/PKCE lifecycle, exact scopes, session/CSRF binding, replay/cross-user rejection, encryption/no-secret leakage, reauthorization identity preservation, missing-scope and invalid-grant transitions, fixed-host GET-only behavior, pagination/time filtering, sanitized errors, no payload persistence, unchanged C0 watermark, and YAZIO regression behavior.

Live OAuth/read gates run only after offline gates pass and only with a configured Google test client whose exact redirect URI is registered. If that prerequisite is absent, report the required redirect URI and mark live auth/read as open without attempting OAuth. Live smoke output may contain only aggregate counts and safe status.

Required final report lines:

- `G1 GOOGLE HEALTH OAUTH FOUNDATION: PASSED/FAILED`
- `GOOGLE HEALTH LIVE AUTH: PASSED/FAILED/OPEN`
- `GOOGLE HEALTH LIVE READ: PASSED/FAILED/OPEN`
- `NUTRITION DOMAIN MODIFIED: NO`
- `READY FOR GOOGLE HEALTH G2: YES/NO`
