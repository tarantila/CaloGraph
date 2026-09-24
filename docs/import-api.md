# Import API

## Authentication

JSON imports use `Authorization: Bearer cg_…`. Tokens carry only the `import`
scope, are shown once, and are stored exclusively as hashes.


## Google Health per-user integration

Google Health is an optional, user-owned integration. Set
`GOOGLE_HEALTH_ENABLED=true` to expose the integration; this flag does not
configure a shared Google account and does not require an operator-global
client ID, client secret, refresh token, or other Google credential.

Each CaloGraph user supplies a Google OAuth **Web application** client pair in
the account integration settings. The client ID and client secret are stored
for that user only, with the secret encrypted at rest. OAuth state and the
PKCE verifier are also user-bound, short-lived, and single-use. The redirect
URI must be registered exactly as:

```text
{CALOGRAPH_PUBLIC_URL}/api/v1/google-health/oauth/callback
```

For example, `CALOGRAPH_PUBLIC_URL=https://app.example.test` produces
`https://app.example.test/api/v1/google-health/oauth/callback`. The callback
URI contains no secret and must be identical in the Google Cloud OAuth client
and in CaloGraph.

The authorization request asks for exactly these read-only scopes:

- `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`
- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`

Save a client ID and client secret together through the account integration
settings. Replacing the pair invalidates the local authorization for this
connection and requires OAuth reauthorization; it does not revoke consent at
Google. The retained refresh-token ciphertext is not used with the new client
pair. OAuth must complete successfully, including all three scopes and
a refresh token, before synchronization is available.

Disconnect removes the user's refresh token and granted scopes but keeps the
user's client pair for a later reconnect. Delete credentials removes the
client ID, encrypted client secret, refresh token, granted scopes, and related
connection state. These operations affect only the authenticated user.

The connection test is a bounded, read-only provider check for the
authenticated user. It performs small GET requests for the supported domains,
returns only a safe status and error category, closes the provider client, and
never stores health samples, ingestion runs, or other health history.

Synchronization retries only transient provider failures (`rate_limited`,
`provider_error`, and `transient_error`). `retry_attempt` is the one-based
attempt currently running or most recently completed, while
`retry_max_attempts` is the fixed maximum of 3. Retries use 1 second and then
2 seconds of backoff; after the third failed attempt the state is terminal
`failed` and no further retry is scheduled. Authentication, scope,
credential, malformed-response, and persistence errors are not retried.

Credential values, refresh/access tokens, authorization codes, PKCE verifiers,
and provider payloads are never returned in API responses, error details, or
normal logs. Status and synchronization responses expose only bounded state,
counts, timestamps, and safe error categories.

## Health Auto Export v2

`POST /api/v1/import/apple-health` accepts both `{ "metrics": [...] }` and
`{ "data": { "metrics": [...] } }`. A data point typically contains `qty`,
`date` or `startDate`, and may include `endDate`, `id`, `source`, and
`sourceBundle`.

Supported activity-energy measurements are normalized to
`active_energy_kcal` in kcal. They affect analytics only after the user selects
the originating source in a target version; importing them never changes a
budget by itself.

## Source-neutral CaloGraph sync format v1

```json
{
  "samples": [
    {
      "id": "stable-client-uuid",
      "type": "dietary_energy_consumed",
      "value": 612.5,
      "unit": "kcal",
      "start_at": "2026-07-18T12:00:00+02:00",
      "end_at": "2026-07-18T12:00:00+02:00",
      "timezone": "Europe/Berlin",
      "source_name": "YAZIO",
      "source_identifier": "com.yazio.ios"
    }
  ]
}
```

## YAZIO exporter JSON

`POST /api/v1/import/yazio` accepts `days.json` and `nutrients.json` produced by
`yazio-exporter`. Use `POST /api/v1/import/yazio/validate` to validate without
storing data. Both endpoints use an import token like the Apple Health JSON
endpoint.

The same files can be uploaded under **Importe** in the browser. CaloGraph
aggregates the four macronutrient fields across all meals, imports supported
vitamins and minerals as daily values, and accepts daily `activity_energy` as
`active_energy_kcal` when supplied. Steps and water are deliberately ignored.
Meal, product, and recipe names are not stored.

Timestamps require a UTC offset. Energy values use `kcal` as the canonical
unit: `kcal` is unchanged, `cal` means a small calorie and converts with
factor `0.001`, and `kJ`/`J` use the existing physical conversions. Other
units are converted in a controlled manner to `g`, `mg`, and µg. Negative,
infinite, non-numeric values and values outside the database ranges are
rejected. Source fields, external identifiers, units, client identifiers, and
IANA timezones are validated against the corresponding storage contract before
persistence.

Structurally unusable JSON receives a generic `422` response without echoing
submitted fields. A well-formed export can still contain individual invalid
measurements; those are omitted and reported through `valid_with_errors` or
`completed_with_errors` so the remaining valid measurements are not discarded.

## Browser file imports

`POST /api/v1/import/apple-health/file` accepts one Apple Health `export.xml`
or ZIP of up to 500 MiB by default. The ZIP may expand to at most 512 MiB, must
contain exactly one `export.xml`, and is checked for unsafe paths, entry count,
and per-entry and aggregate compression ratio. Its `export.xml` is decompressed
and parsed once through a bounded stream; ZIP integrity is verified before any
samples or import state are committed. The entry may be stored or Deflate
compressed. XML is parsed incrementally and accepted samples are persisted in
configurable batches of 500.

`POST /api/v1/import/yazio/file` accepts one YAZIO JSON file of up to 10 MiB.
The frontend proxy admits at most two concurrent large Apple Health uploads
globally and at most one per client IP. `client_body_timeout 60s` remains
defense-in-depth against bodies with no progress; it is not an absolute
upload deadline. The application independently limits file imports per user
and client IP. Only one import or validation may process data for a user at a
time.

The relevant settings are `MAX_UPLOAD_BYTES`,
`NGINX_MAX_UPLOAD_BYTES`, `BACKEND_TMPFS_BYTES`,
`MAX_ZIP_UNCOMPRESSED_BYTES`, `MAX_ZIP_ENTRIES`, `MAX_IMPORT_RECORDS`,
`MAX_IMPORT_SAMPLES`, `MAX_IMPORT_ERRORS`, `MAX_IMPORT_UNKNOWN_TYPES`,
`IMPORT_BATCH_SIZE`, and the `FILE_IMPORT_*` rate limits. Production startup
requires `BACKEND_TMPFS_BYTES >= 2 * NGINX_MAX_UPLOAD_BYTES + 16 MiB` for the
two global upload slots. Raise the record limit deliberately if a legitimate
long-running Apple Health history exceeds the default one million XML records.

## Idempotency

With a stable `id`, a later import updates the existing record. Without an ID,
CaloGraph creates a SHA-256 fingerprint from user, adapter, metric, timestamps,
value, unit, and source identifier. The same payload can be sent repeatedly.

Large non-ZIP file imports checkpoint completed batches. If their parsing or
reading fails after a checkpoint, the response and import history use
`partial_failed`. Already committed samples remain available, while the final
unfinished batch is discarded. ZIP integrity, XML, and import-limit failures
roll back their entire import. Resubmitting the same file is the recovery
procedure and does not duplicate previously committed samples.

`POST /api/v1/import/apple-health/validate` performs mapping and validation
without persistence. Responses contain the batch ID, status, and counts for
received, inserted, updated, skipped, failed, and unknown records.

Authenticated users can view their latest runs through `GET /api/v1/imports`.
`GET /api/v1/imports/{batch_id}` adds up to 100 safe error details with item
position, metric, error code, and a readable description. Both endpoints are
strictly restricted to the authenticated user's import batches.

Import tokens belong to an active account. Deactivation revokes all existing
tokens; an inactive account is rejected before token activity or import data
can be written. Reactivation does not restore revoked tokens.

Health values are never included in error responses or normal logs.
