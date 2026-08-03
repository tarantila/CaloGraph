# Threat model

## Assets

Health values, credentials, import tokens, session cookies, raw imports,
backups, and the availability of the private application.

## Risks and mitigations

- **Stolen import token:** device-specific tokens, HMAC hashing, one-time
  display, expiration and revocation, rate limiting, and TLS.
- **Public import endpoint:** bearer authentication, size limits, schema and
  unit validation, redacted responses, and reverse-proxy protection.
- **Compromised browser:** HttpOnly sessions and a small attack surface without
  third-party scripts. Production cookies use the `__Host-` prefix, `Secure`,
  `Path=/`, no `Domain`, and `SameSite=Lax`. Server-side idle and absolute
  timeouts limit a stolen cookie's lifetime. A compromised endpoint can still
  read data visible to that user.
- **Stolen password:** optional TOTP prevents a password-only login from
  creating a session. A signed five-minute, `HttpOnly`, `SameSite=Strict`
  challenge carries the login between factors. TOTP secrets use a dedicated
  backend-only encryption key, accepted time steps cannot be replayed, and
  recovery codes are one-time HMAC digests. Temporary user and IP limits apply
  to second-factor attempts. TOTP does not protect a fully compromised browser
  or host.
- **Password phishing and passkey replay:** optional passwordless passkeys bind
  signatures to the exact relying-party host and HTTPS origin and require
  authenticator user verification. Discoverable credentials avoid transmitting
  a reusable password. Challenges expire after five minutes, are claimed once
  under a database row lock, and failed verification consumes the challenge.
  Stored public keys cannot authenticate without the user's authenticator.
  Passkeys do not protect a compromised authenticated browser or device.
- **Insecure backups:** database dumps and the `.env`/secret-file bundle are
  streamed directly into authenticated `age` encryption without plaintext
  temporary backup files. The operator must keep the private identity off-host,
  maintain an offline or immutable copy, define retention, and test restores.
- **Container secret disclosure:** Compose mounts operator-controlled source
  files as service-scoped secrets under `/run/secrets`. `.env` contains only
  their paths. Containers receive only the secrets required for their role and
  no password-bearing database URL.
  The scheduler does not receive browser-session or MFA-encryption keys.
  Direct environment variables remain a legacy/test interface and cannot be
  combined ambiguously with `_FILE` settings.
- **Sensitive logs:** security events use a fixed JSON schema with allowlisted
  fields and HMAC-pseudonymized actor, client, and target references. Payloads,
  health values, credentials, tokens, filenames, and arbitrary exception text
  are excluded. The public proxy retains the validated client IP for host-level
  blocking while application events carry the correlated request ID.
- **Unsafe production defaults:** `ENVIRONMENT` must be explicit. Production
  startup validates HTTPS, secure cookies, HSTS, independent non-default
  secrets, PostgreSQL credentials, explicit host/origin allowlists, an exact
  proxy subnet, YAZIO encryption, and consistent upload capacities. Failure
  messages contain variable names rather than secret values.
- **YAZIO credentials:** manual retrieval does not persist credentials.
  Scheduled sync stores each user's email and password using authenticated
  Fernet encryption. The host-side key file is mounted only into the backend
  and scheduler and must be protected and backed up together with database
  backups. Stored source identifiers use an internal random user UUID rather
  than an email-derived digest. Credentials reach the isolated transport
  process through standard input rather than process arguments or environment
  variables.
- **Unofficial third-party interface:** isolated adapter, pinned exporter
  version, fixed provider endpoint, explicit network timeouts, rejected
  redirects, no authentication retries, parent-enforced operation deadlines,
  bounded retrieval periods, and Apple Health as an independent fallback path.
- **YAZIO resource exhaustion and credential stuffing:** independent
  PostgreSQL-backed limits for the authenticated user and normalized client IP
  protect credential setup and manual synchronization. Deployment-wide
  advisory locks allow one active operation per user and two in total.
  Repeated provider or deadline failures open a temporary shared circuit
  breaker, while authentication failures pause only the affected scheduled
  connection. `YAZIO_ENABLED` provides an operational kill switch without
  adding Redis or a general-purpose queue. Backend and scheduler receive the
  same rate-limit secret so provider failures map to the same HMAC-pseudonymized
  circuit-breaker bucket.
- **Duplicate sources:** do not use Apple Health and YAZIO for the same time
  range. Cross-source deduplication would hide provenance.
- **Manipulated JSON:** Pydantic and adapter validation, finite non-negative
  decimal values, allowlisted metrics, and database constraints.
- **Malicious ZIP/XML:** total and individual size limits, entry count,
  compression ratio, path checks, exactly one `export.xml`, no DTD, entities,
  or network access, and streaming processing. Nginx admits only one concurrent
  large upload, Starlette uses one bounded ephemeral tmpfs spool, supported
  samples are bulk-written in bounded batches, total records/errors/unknown
  types are capped, and per-user plus per-IP temporary rate limits protect
  upload and validation paths. Completed checkpoints survive as
  `partial_failed` and can be retried idempotently.
- **Container breakout and resource exhaustion:** application containers drop
  every Linux capability, PostgreSQL retains only the ownership and user-switch
  capabilities required by its official entrypoint, and
  `no-new-privileges` remains enabled. The database is confined to an
  externally isolated data network. Per-service CPU, memory, and PID ceilings
  are available as explicit deployment settings; installations that leave
  them disabled accept the remaining host-level exhaustion risk.
- **XSS:** Vue escaping, no arbitrary HTML rendering, local assets, and a strict
  CSP.
- **CSRF:** SameSite cookie, separate CSRF header, and origin validation for
  state-changing browser endpoints.
- **Forged forwarded headers:** Uvicorn accepts proxy metadata only from the
  frontend's fixed `/32`. The bundled Nginx trusts forwarded client metadata
  only from the exact edge gateway, discards untrusted forwarding chains, and
  the backend has no published host port.
- **Brute force and account enumeration:** independent temporary limits apply
  to all login attempts from a normalized IP address and to failed attempts for
  an HMAC-pseudonymized account identifier. PostgreSQL updates the counters
  atomically, unknown accounts still perform Argon2 verification against a
  dummy hash, expired buckets are deleted, and authentication events contain
  only pseudonymous identifiers. The reverse proxy may add further limits.
- **Weak new passwords:** single-factor passwords require at least 15
  characters and are compared locally against a bundled digest blocklist of
  common and breached values plus application-specific identifiers. No
  password material is sent to a third-party service. Existing Argon2id
  storage and the absence of periodic forced changes are retained.
- **Lost authentication factor:** users receive ten offline recovery codes
  during TOTP activation and can enroll multiple passkeys. An explicit
  operator CLI reset removes TOTP, recovery codes, and passkeys and revokes all
  sessions when no enrolled factor remains available.
- **Forwarded invitation link:** cryptographically random invitation tokens
  stored only as hashes, one-time use, seven-day expiration, and administrator
  revocation. Plaintext is displayed only immediately after creation. Tokens
  are carried in browser-only URL fragments, removed from the visible URL
  before a one-time exchange, invalidated by that exchange, and replaced with a
  signed ten-minute `HttpOnly`
  registration cookie. Internal access logs omit query strings and redact
  legacy invitation paths. The externally shared URL uses the
  operator-controlled `CALOGRAPH_PUBLIC_URL` instead of request headers or the
  current browser origin.
- **Unauthorized user administration:** user and invitation endpoints are
  restricted to administrators. The first CLI-created user becomes an
  administrator automatically; later accounts receive no administrative
  privileges by default.
- **Cross-account data access:** queries, imports, targets, tokens, and YAZIO
  connections are always restricted by the authenticated user ID. A separate
  invitation and login scenario tests this isolation path.
- **Data loss:** persistent volume, Alembic migrations, authenticated encrypted
  backups, an off-host immutable or offline copy, and a documented restore
  process.

## Residual risks

Anyone who completely controls the host, database, or browser profile can read
health data. CaloGraph does not replace disk encryption, host hardening,
network segmentation, or secure backup retention. Compose Secrets reduce
accidental disclosure and service overexposure; they do not protect secrets
from a fully privileged Docker-host administrator.
