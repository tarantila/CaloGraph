# Data model

- `users`: account, language, IANA time zone, first weekday, preferred weight
  unit, administrator flag, and lifecycle state. `is_active=true` requires
  `deactivated_at=NULL`; an inactive account requires a UTC deactivation
  timestamp.
- `user_provider_priorities`: ordered per-user providers by functional data
  area; composite primary key `(user_id, data_area, priority)`. Versioned,
  effective-dated `source_priority_policies` and `source_priority_rules`
  preserve the source chain used for historical resolution.
- `ai_provider_connections`: per-user AI provider configuration, keyed by the
  composite primary key `(user_id, id)`; model, endpoint, enablement, timeout,
  and configuration version are stored separately from credentials.
  `ai_provider_credentials` stores only encrypted key bytes and uses the
  composite key `(user_id, connection_id)` as a cascading foreign key to the
  owning connection, preventing credentials from crossing user boundaries.
- `ai_subscription_credentials`: one owner-bound encrypted experimental
  ChatGPT-plan grant per provider connection. Tokens and the immutable issuer,
  transport, client, subject and workspace binding are encrypted separately;
  both encrypted envelopes bind their owner and connection, and token envelopes
  also bind the current credential generation. Plaintext metadata is limited to
  lifecycle state, credential version and expiry. Claims are committed before
  refresh or revocation I/O. An interrupted or ambiguous claim is not
  automatically retried. Application deletion requires disconnect before live
  credentials can be removed; database foreign keys cascade connection deletion.
- `ai_subscription_device_authorizations`: owner-scoped, single-use device sign-in
  attempts, bound to the provider connection and snapshots of its configuration
  and credential generation. Device codes and private polling metadata are
  encrypted; browser responses expose only the short user code and fixed
  verification URI. Durable starting, polling and exchange claims prevent
  replay after interruption. Small owner-scoped authorization receipts outlive
  pruned attempts and connection deletion, preventing delayed requests from
  resurrecting an authorization ID.
- `ai_provider_checks`: account-scoped idempotent metadata-check claims and
  bounded normalized outcomes; records the configuration and privacy versions.
  The composite connection foreign key cascades deletion. A claim without a
  result is never automatically repeated after a worker interruption.
- `ai_active_connections`: one optional active AI connection per account. Both
  account and composite connection foreign keys cascade deletion. Disabling the
  selected connection clears this preference in the same transaction.
- `ai_consents`: immutable, versioned data permissions scoped to an account and
  provider connection, purpose, data domains, optional meal names and history
  scope. Each resource snapshots the provider, model, endpoint and connection
  version; configuration changes invalidate it. Revocation records a UTC
  timestamp without reactivating an old resource on retry. The composite
  connection foreign key cascades on connection/account deletion.
- `ai_consent_revocations`: retained account-local permission IDs and UTC
  invalidation timestamps. These contain no provider credentials or health data,
  survive connection deletion and prevent delayed requests from recreating a
  withdrawn permission. Only account deletion cascades these records.
- `ai_connection_retirements`: account-local deleted connection IDs and UTC
  retirement timestamps. A deleted UUID cannot be recreated, so a delayed
  permission request cannot bind to a different destination under an old ID and
  configuration version. Account deletion removes these records.
- `expert_preferences`: optional account-local selection of Nutrition Expert
  data domains and meal-name inclusion. The service supplies canonical defaults
  when no row exists; identical writes do not increment the configuration
  version. The row contains no health values and is removed with its owner.
- `expert_conversations`: account-scoped immutable initial chat/analysis mode,
  timestamps, a legacy-compatible title and nullable `title_source` provenance.
  Only titles marked `title_source='generated'` are exposed; unmarked values may be historical
  first-message excerpts and remain hidden. Conversation identity receipts survive deletion and prevent reuse of a
  previously consumed UUID; only account deletion removes the receipts.
- `expert_runs`: immutable client request identity plus connection, preference and
  consent versions, optional routing version and ordered route snapshot,
  claim/dispatch/completion timestamps, bounded context snapshot and structured
  answer. Conversation deletion cascades run content; separate account-scoped run
  identity receipts retain only the run UUID and timestamp so a deleted or
  completed request cannot be replayed. Account deletion removes receipts. A
  partial unique index permits at most one queued/running run per account.
  Credentials are never stored in these records.
- `expert_run_provider_attempts`: per-run ordered provider dispatch claims with
  snapshotted connection/configuration/model and the provider's own consent ID. A
  missing consent is recorded as skipped. Each attempt is durably claimed before
  I/O and is never automatically replayed after interruption. Provider attempt
  views expose only connection/provider/model provenance and safe state/error codes.
- `expert_attachments`: private account- and conversation-scoped PDF/image bytes,
  bounded metadata and SHA-256 used only for immutable upload replay checks.
  Metadata APIs defer and never return file bytes, hashes or owner IDs. Each run
  records only explicitly submitted attachment UUIDs; conversation deletion
  removes its file bytes and metadata.
- `expert_attachment_identities`: owner-scoped consumed attachment UUIDs and
  timestamps. Receipts survive file/conversation deletion and prevent a late
  streamed upload from resurrecting a deleted or previously consumed ID. They
  contain no filename, digest, file bytes or health data; only account deletion
  removes them. `expert_conversations.uses_attachments` records successful use
  without changing the attachment permission requirement.
- `ai_consents.include_attachments`: explicit immutable permission for external
  PDFs/images, defaulting to false for existing and new grants. Existing grants
  do not authorize document transfer.
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
