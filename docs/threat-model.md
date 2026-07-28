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
  third-party scripts. A compromised endpoint can still read data visible to
  that user.
- **Insecure backups:** the operator must encrypt backups, restrict
  permissions, and test restores.
- **Sensitive logs:** payloads and health values are excluded; request IDs are
  logged instead of user data.
- **YAZIO credentials:** manual retrieval does not persist credentials.
  Scheduled sync stores each user's email and password using authenticated
  Fernet encryption. The key exists only in `.env` and must be protected and
  backed up together with database backups. Credentials reach the isolated
  transport process through standard input rather than process arguments or
  environment variables.
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
  adding Redis or a general-purpose queue.
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
- **XSS:** Vue escaping, no arbitrary HTML rendering, local assets, and a strict
  CSP.
- **CSRF:** SameSite cookie, separate CSRF header, and origin validation for
  state-changing browser endpoints.
- **Forged forwarded headers:** Uvicorn accepts proxy metadata only from
  `TRUSTED_PROXY_NETWORKS`; wildcard trust is rejected, production rejects the
  generic Docker address pool, and the backend has no published host port.
- **Brute force and account enumeration:** independent temporary limits apply
  to all login attempts from a normalized IP address and to failed attempts for
  an HMAC-pseudonymized account identifier. PostgreSQL updates the counters
  atomically, unknown accounts still perform Argon2 verification against a
  dummy hash, expired buckets are deleted, and authentication events contain
  only pseudonymous identifiers. The reverse proxy may add further limits.
- **Forwarded invitation link:** cryptographically random invitation tokens
  stored only as hashes, one-time use, seven-day expiration, and administrator
  revocation. Plaintext is displayed only immediately after creation. The
  externally shared URL uses the operator-controlled `CALOGRAPH_PUBLIC_URL`
  instead of request headers or the current browser origin.
- **Unauthorized user administration:** user and invitation endpoints are
  restricted to administrators. The first CLI-created user becomes an
  administrator automatically; later accounts receive no administrative
  privileges by default.
- **Cross-account data access:** queries, imports, targets, tokens, and YAZIO
  connections are always restricted by the authenticated user ID. A separate
  invitation and login scenario tests this isolation path.
- **Data loss:** persistent volume, Alembic migrations, regular encrypted
  backups, and a documented restore process.

## Residual risks

Anyone who completely controls the host, database, or browser profile can read
health data. CaloGraph does not replace disk encryption, host hardening,
network segmentation, or secure backup retention.
