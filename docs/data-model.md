# Data model

- `users`: account, language, IANA time zone, first weekday, preferred weight
  unit, administrator flag, and lifecycle state. `is_active=true` requires
  `deactivated_at=NULL`; an inactive account requires a UTC deactivation
  timestamp.
- `user_profiles`: optional one-to-one personal information for a user:
  display name, gender, birth date, height, diet, voluntary health notes, and
  intolerances. All content fields are nullable; the user foreign key is the
  primary key.
- `user_sessions`: hashed session and CSRF keys, expiration, and revocation.
- `user_totp_credentials`: encrypted TOTP seed, activation time, and the last
  accepted time step used to reject replay.
- `mfa_recovery_codes`: HMAC-only one-time recovery codes and consumption time.
- `webauthn_user_handles`: random, stable WebAuthn user handles that do not
  expose usernames to authenticators.
- `passkey_credentials`: user-scoped WebAuthn credential public keys, device
  metadata, backup state, signature counter, and last use.
- `webauthn_challenges`: short-lived, single-use registration and
  authentication challenges; registration rows are bound to a user session.
- `user_invitations`: hashed one-time invitation tokens, expiration, creator,
  recipient, and revocation.
- `api_tokens`: label, prefix, HMAC hash, scopes, expiration, and revocation.
- `account_recovery_tokens`: HMAC-only one-time administrator-issued recovery
  tokens with creation, expiration, consumption, and revocation timestamps.
- `nutrition_targets`: versioned calorie budgets, independent optional
  maintenance-calorie estimates, nutrient targets, and a versioned optional
  activity-energy mode with exactly one selected source, using the half-open
  interval `valid_from <= day < valid_to`. Calorie budgets and finite, positive
  maintenance estimates have no ordering relationship.
- `tracking_quality_settings`: legacy settings from the former completeness
  heuristic; no longer used for new analysis.
- `health_samples`: canonical and original values, UTC timestamps, local date,
  source, fingerprint, and import batch.
- `import_batches`, `import_errors`, `raw_import_payloads`: import reporting,
  safe error context, and optional compressed raw data.
- `tracking_overrides`: optional manual data-status override per local day.
- `yazio_connections`: one encrypted, user-scoped YAZIO connection with
  scheduling, last-run status, and an opaque source identifier derived only
  from the internal user UUID. Email-derived hashes are not stored.
- `rate_limit_buckets`: hashed identifiers in minute windows.

Stable external IDs are unique per user, adapter, and source identifier.
`(user_id, fingerprint)` additionally prevents duplicates without an external
ID. Decimal values avoid rounding errors. Timestamps are stored in UTC;
`local_date` is calculated during import using the user's time zone.

Deleting an inactive user relies on database foreign-key cascades for all
user-owned profile, authentication, recovery, import, nutrition, target,
tracking, and YAZIO rows. Related rate-limit buckets use HMAC keys rather than
foreign keys and are removed explicitly by the lifecycle service in the same
transaction.
