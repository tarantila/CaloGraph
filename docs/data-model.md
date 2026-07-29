# Data model

- `users`: account, language, IANA time zone, first weekday, administrator
  flag, and active status.
- `user_sessions`: hashed session and CSRF keys, expiration, and revocation.
- `user_totp_credentials`: encrypted TOTP seed, activation time, and the last
  accepted time step used to reject replay.
- `mfa_recovery_codes`: HMAC-only one-time recovery codes and consumption time.
- `user_invitations`: hashed one-time invitation tokens, expiration, creator,
  recipient, and revocation.
- `api_tokens`: label, prefix, HMAC hash, scopes, expiration, and revocation.
- `nutrition_targets`: versioned calorie budgets, optional maintenance-calorie
  estimates, and nutrient targets using the half-open interval
  `valid_from <= day < valid_to`. A maintenance estimate cannot be lower than
  the calorie budget.
- `tracking_quality_settings`: legacy settings from the former completeness
  heuristic; no longer used for new analysis.
- `health_samples`: canonical and original values, UTC timestamps, local date,
  source, fingerprint, and import batch.
- `import_batches`, `import_errors`, `raw_import_payloads`: import reporting,
  safe error context, and optional compressed raw data.
- `tracking_overrides`: optional manual data-status override per local day.
- `yazio_connections`: one encrypted, user-scoped YAZIO connection with
  scheduling and last-run status.
- `rate_limit_buckets`: hashed identifiers in minute windows.

Stable external IDs are unique per user, adapter, and source identifier.
`(user_id, fingerprint)` additionally prevents duplicates without an external
ID. Decimal values avoid rounding errors. Timestamps are stored in UTC;
`local_date` is calculated during import using the user's time zone.
