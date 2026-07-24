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
kcal, g, mg, and µg. Negative, infinite, and non-numeric values are rejected.

## Idempotency

With a stable `id`, a later import updates the existing record. Without an ID,
CaloGraph creates a SHA-256 fingerprint from user, adapter, metric, timestamps,
value, unit, and source identifier. The same payload can be sent repeatedly.

`POST /api/v1/import/apple-health/validate` performs mapping and validation
without persistence. Responses contain the batch ID, status, and counts for
received, inserted, updated, skipped, failed, and unknown records.

Authenticated users can view their latest runs through `GET /api/v1/imports`.
`GET /api/v1/imports/{batch_id}` adds up to 100 safe error details with item
position, metric, error code, and a readable description. Both endpoints are
strictly restricted to the authenticated user's import batches.

Health values are never included in error responses or normal logs.
