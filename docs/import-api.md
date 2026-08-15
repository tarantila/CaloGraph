# Import API

## Authentication

JSON imports use `Authorization: Bearer cg_…`. Tokens carry only the `import`
scope, are shown once, and are stored exclusively as hashes.

## Health Auto Export v2

`POST /api/v1/import/apple-health` accepts both `{ "metrics": [...] }` and
`{ "data": { "metrics": [...] } }`. A data point typically contains `qty`,
`date` or `startDate`, and may include `endDate`, `id`, `source`, and
`sourceBundle`.

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
aggregates the four macronutrient fields across all meals and imports supported
vitamins and minerals as daily values. Activity energy, steps, and water are
deliberately ignored. Meal, product, and recipe names are not stored.

Timestamps require a UTC offset. Units are converted in a controlled manner to
kcal, g, mg, and µg. Negative, infinite, non-numeric values and values outside
the database ranges are rejected. Source fields, external identifiers, units,
client identifiers, and IANA timezones are validated against the corresponding
storage contract before persistence.

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
