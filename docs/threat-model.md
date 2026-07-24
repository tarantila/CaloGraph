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
  Fernet encryption. The key exists only in `.env`, is not passed in process
  arguments, and must be protected and backed up together with database
  backups.
- **Unofficial third-party interface:** isolated adapter, pinned exporter
  version, bounded retrieval periods, and Apple Health as an independent
  fallback path.
- **Duplicate sources:** do not use Apple Health and YAZIO for the same time
  range. Cross-source deduplication would hide provenance.
- **Manipulated JSON:** Pydantic and adapter validation, finite non-negative
  decimal values, allowlisted metrics, and database constraints.
- **Malicious ZIP/XML:** total and individual size limits, entry count,
  compression ratio, path checks, exactly one `export.xml`, no DTD, entities,
  or network access, and streaming processing.
- **XSS:** Vue escaping, no arbitrary HTML rendering, local assets, and a strict
  CSP.
- **CSRF:** SameSite cookie, separate CSRF header, and origin validation for
  state-changing browser endpoints.
- **Forged forwarded headers:** Uvicorn accepts proxy metadata only from
  `TRUSTED_PROXY_NETWORKS`; wildcard trust is rejected, and the backend has no
  published host port.
- **Brute force:** PostgreSQL-backed per-minute limits; the reverse proxy may
  add further limits.
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
