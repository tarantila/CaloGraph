# Data model

- `users`: account, language, IANA time zone, first weekday, preferred weight
  unit, administrator flag, and lifecycle state. `is_active=true` requires
  `deactivated_at=NULL`; an inactive account requires a UTC deactivation
  timestamp.
- `user_provider_priorities`: ordered per-user providers by functional data
  area; composite primary key `(user_id, data_area, priority)`. Versioned,
  effective-dated `source_priority_policies` and `source_priority_rules`
  preserve the source chain used for historical resolution.
- Historical `user_provider_preferences` is migration input only; revision
  `20260918_0031` moves those choices and removes the table from the current schema.
- `users.active_card_ids`: canonical ordered Analysis Hub membership, including
  built-in domain cards (`weight`, `nutrition`, `micronutrients`, `activity`),
  `meals_today`, and available standalone metric IDs. New users default to the
  four domain cards; `meals_today` is inactive. Revision `20261006_0039`
  backfills each account from its prior summary order and selected metric rows.
- `user_analysis_preferences`: compatibility projection containing only
  active standalone metric keys in canonical order; built-in cards are not
  represented as rows. The `users.active_card_ids` JSON list is the source of
  truth for membership and order.
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
  maintenance-calorie estimates, nutrient targets, optional canonical target
  weight in kg, and a versioned optional activity-energy mode with exactly one
  selected source, using the half-open interval `valid_from <= day < valid_to`.
  Target weight is either absent, an exact value, or an ordered range; both
  bounds are nullable `Numeric(7,3)` and must be set together to finite values
  greater than zero with a maximum of 1000 kg. Calorie budgets and finite,
  positive maintenance estimates have no ordering relationship.
- `tracking_quality_settings`: legacy settings from the former completeness
  heuristic; no longer used for new analysis.
- `health_samples`: canonical and original values, UTC timestamps, local date,
  source, fingerprint, and import batch. Weight is stored as the canonical
  scalar metric `weight_kg` in kg; it uses the same user-scoped sample table
  without a schema migration.
- `import_batches`, `import_errors`, `raw_import_payloads`: import reporting,
  safe error context, and optional compressed raw data.
- `nutrition_ingestion_runs.projection_pending`: durable, non-null lifecycle
  acknowledgement for canonical SDK YAZIO projection work; Python and database
  defaults are `false`. It is independent of ingestion `status`, `finished_at`,
  and provider metadata. Canonical YAZIO sync marks completed evidence as pending
  until downstream projection work is acknowledged. The partial index
  `ix_nutrition_ingestion_runs_projection_pending` covers
  `(user_id, provider_key, source_instance_id, created_at, id)` for pending rows.
  Revision `20261021_0040` conservatively enrolls every completed `yazio` /
  `sdk-v22` run with a source instance and non-null, ordered requested date
  bounds for one-time replay, including previously successful projections.
  Legacy, file-import, unknown-connector and other-provider runs are not
  enrolled. Existing provenance, ingestion status/timestamps/metadata,
  `health_samples`, and `import_batches` remain unchanged.
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
user-owned profile, provider-preference, authentication, recovery, import,
nutrition, target, tracking, and YAZIO rows. Related rate-limit buckets use
HMAC keys rather than foreign keys and are removed explicitly by the lifecycle
service in the same transaction.

## Analysis demo data

The dedicated non-admin `calograph-analysis-demo` account owns the demo's
synthetic `health_samples` with source type `analysis_demo_offline`, import
batches, nutrition rows, projections, and ordinary per-user priority policy.
Authenticated use can create `user_usage_days` and `user_achievements`; those
per-user rows belong to the demo account and are removed with it.
The demo-only source/provider identity is `analysis_demo`; these rows use the
normal user-isolation boundary rather than a separate analytics data model.
Removal audits exclusive seed ownership before deleting demo-owned rows and
the account. Security audit events survive, with account references cleared
through `SET NULL` where applicable.
