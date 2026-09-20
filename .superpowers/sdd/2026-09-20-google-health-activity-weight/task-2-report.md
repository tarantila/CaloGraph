# Task 2 Bericht — typisierter Google-v4-Datapoint-Client

## Status

Task 2 ist implementiert. Der bestehende Nutrition-Log-Client bleibt kompatibel; zusätzlich stehen bounded, readonly Datapoint-Abfragen für `active-energy-burned` und `weight` zur Verfügung.

## Umsetzung

- Feste v4-Pfade in `google_health/constants.py` ergänzt:
  - `/users/me/dataTypes/active-energy-burned/dataPoints`
  - `/users/me/dataTypes/weight/dataPoints`
- `GoogleHealthHTTPTransport.get_data_points(...)` ergänzt. Der Transport verwendet ausschließlich GET, den festen Health-Host, explizite Timeouts und die bestehende Response-Größenbegrenzung.
- `GoogleHealthClient.get_data_points_page(...)` ergänzt mit Allowlist für `nutrition-log`, `active-energy-burned` und `weight`.
- Neue readonly DTOs ergänzt: `GoogleHealthDataPoint`, `ActiveEnergyBurnedDataPoint`, `WeightDataPoint` und `GoogleHealthDataPointPage` (inklusive bounded Name/ID, Provider-Zeitstempeln, Wert, Einheit, Data Source und optionalen UTC-Offsets).
- Parser validiert Typen, numerische Bounds, Einheiten, Zeitgrenzen, UTC-Offsets, Seitengröße, Seitentoken und maximale Seitengröße. Providerstatus und Transportfehler werden ohne Rohpayload bzw. Exception-Cause abgebildet.
- Nutrition-Log-Fassade bleibt erhalten; sie kann weiterhin den bisherigen `get_nutrition_log`-Transport verwenden und fällt für einen generischen Transport auf `get_data_points(data_type="nutrition-log")` zurück.
- Task-1-OAuth-Semantik und Scope-Konstanten wurden nicht verändert.

## RED-Nachweis

Neue Tests wurden vor der Implementierung gegen den unveränderten Client ausgeführt:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -v /tmp/calog-pre-task2-client.py:/app/app/google_health/client.py:ro \
  backend-ci pytest tests/test_google_health_client.py -q
```

Ergebnis: RED bei der Collection (`ImportError: cannot import name 'GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH'`), da der Pre-Task-2-Client die neuen Datapoint-Symbole und API nicht besitzt.

## GREEN-Nachweis

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci

docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_client.py tests/test_google_health_no_persistence.py \
  tests/test_google_health_nutrition_log.py -q
```

Ergebnis: `75 passed`.

## Sicherheit/Begrenzungen

Es gibt keine Write-API, keine Health-Connect-/Android-Bridge-/Google-Fit-Pfade, keine Rohpayloads in Fehlern oder Logs und keine unbounded Pagination. Datapoint-Identitäten und Provider-Metadaten sind vor der DTO-Erzeugung begrenzt.
