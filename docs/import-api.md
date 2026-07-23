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

## YAZIO-Exporter-JSON

`POST /api/v1/import/yazio` akzeptiert die `days.json` und
`nutrients.json` von `yazio-exporter`.
Eine Prüfung ohne Speicherung ist über
`POST /api/v1/import/yazio/validate` möglich. Beide Endpunkte verwenden wie der
Apple-Health-JSON-Import ein Import-Token.

Im Browser können dieselben Dateien unter **Importe** hochgeladen werden.
CaloGraph aggregiert die vier Makronährstofffelder über alle Mahlzeiten und
übernimmt die unterstützten Vitamine und Mineralstoffe als Tageswerte.
Aktivitätsenergie, Schritte und Wasser werden bewusst nicht verarbeitet. Namen
von Mahlzeiten, Produkten und Rezepten werden nicht gespeichert.

Zeitstempel benötigen einen UTC-Offset. Einheiten werden kontrolliert in kcal,
g, mg und µg konvertiert. Negative, unendliche oder nicht
numerische Werte werden abgewiesen.

## Idempotenz

Bei stabiler `id` aktualisiert ein erneuter Import den bestehenden Datensatz. Ohne ID entsteht ein SHA-256-Fingerprint aus Benutzer, Adapter, Metrik, Zeiten, Wert, Einheit und Quellenkennung. Derselbe Payload kann beliebig oft übertragen werden.

`POST /api/v1/import/apple-health/validate` führt Mapping und Validierung ohne Persistenz aus. Antworten enthalten Batch-ID, Status und Zähler für empfangene, neue, aktualisierte, übersprungene, fehlerhafte und unbekannte Datensätze.

Angemeldete Benutzer sehen ihre letzten Importläufe über `GET /api/v1/imports`.
`GET /api/v1/imports/{batch_id}` ergänzt bis zu 100 sichere Fehlerdetails mit
Eintragsposition, Metrik, Fehlercode und verständlicher Beschreibung. Beide
Endpunkte sind strikt auf die Importläufe des angemeldeten Benutzers begrenzt.

Gesundheitswerte werden nicht in Fehlerantworten oder normalen Logs ausgegeben.
