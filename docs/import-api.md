# Import-API

## Authentifizierung

JSON-Importe verwenden `Authorization: Bearer cg_…`. Tokens besitzen nur den Scope `import`, werden einmal angezeigt und ausschließlich gehasht gespeichert.

## Health Auto Export v2

`POST /api/v1/import/apple-health` akzeptiert sowohl `{ "metrics": [...] }` als auch `{ "data": { "metrics": [...] } }`. Ein Punkt enthält typischerweise `qty`, `date` oder `startDate`, optional `endDate`, `id`, `source` und `sourceBundle`.

## Neutrales CaloGraph-Syncformat v1

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

Zeitstempel benötigen einen UTC-Offset. Einheiten werden kontrolliert in kcal, kg, g, mg, ml, Prozent, Minuten und count konvertiert. Negative, unendliche oder nicht numerische Werte werden abgewiesen.

## Idempotenz

Bei stabiler `id` aktualisiert ein erneuter Import den bestehenden Datensatz. Ohne ID entsteht ein SHA-256-Fingerprint aus Benutzer, Adapter, Metrik, Zeiten, Wert, Einheit und Quellenkennung. Derselbe Payload kann beliebig oft übertragen werden.

`POST /api/v1/import/apple-health/validate` führt Mapping und Validierung ohne Persistenz aus. Antworten enthalten Batch-ID, Status und Zähler für empfangene, neue, aktualisierte, übersprungene, fehlerhafte und unbekannte Datensätze.

Gesundheitswerte werden nicht in Fehlerantworten oder normalen Logs ausgegeben.

