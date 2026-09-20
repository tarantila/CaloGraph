# Task 3 Bericht — Google-v4 Activity- und Weight-Ingestion

## Status

Task 3 ist implementiert und lokal committed (`3607142`, `Add Google Health scalar ingestion`). Google Activity und Weight werden über die bestehende `HealthSample`-/`CanonicalSample`-Persistenz ingestiert. Es gibt keine zweite Messwerttabelle, keine Provider-Priority-Registrierung und keine UI-Änderung.

## Umsetzung

- `backend/app/activity.py`
  - Stabiler Source-Typ `google_health_activity_v4` ergänzt.
  - Google Health in den bestehenden Activity-Source-Gruppen ergänzt.
- `backend/app/weight.py`
  - Stabiler Source-Typ `google_health_weight_v4` ergänzt.
  - Google Health in den bestehenden Weight-Source-Gruppen ergänzt.
- `backend/app/services/google_health_scalar_sync.py`
  - `sync_google_health_activity(...)` und `sync_google_health_weight(...)` als Persistence-Boundary für die Task-2-DTOs.
  - DTO-Typen, Identität, Werte, Einheiten, Zeitstempel, Intervalle, Datumsbereich und User-/Connection-Ownership werden vor dem ersten Write validiert.
  - Activity wird als `active_energy_kcal`/`kcal` gespeichert.
  - Weight wird als `weight_kg`/`kg` gespeichert; der DTO-Originalwert und die DTO-Originaleinheit bleiben in `original_value`/`original_unit` erhalten.
  - User-Zeitzone wird für `timezone` und `local_date` verwendet; Provider-Start-/Endzeitstempel werden unverändert an `HealthSample` weitergereicht.
  - Connection-ID ist der bounded `source_identifier`; Google-Datapoint-Name ist die stabile externe Identität.
  - Wiederholte Datapoints nutzen die vorhandene Fingerprint-/External-Identity-Idempotenz von `_persist_sample_batch`.
  - `GoogleHealthScalarSyncService` iteriert die bounded Task-2-Seiten und commitet erst nach erfolgreicher Domain-Persistenz.
  - Fehlerdetails bleiben generisch und enthalten keine Provider-Payloads oder Gesundheitswerte.
- `backend/tests/test_google_health_scalar_sync.py`
  - RED/GREEN-Abdeckung für Activity-Intervalle, Idempotenz, Weight-Normalisierung, Originalfelder, Provider-Zeitstempel, Local Day, spätere-of-day-Sortierung, invalides Unit/Value/Identity und Cross-User-Connection-Isolation.

## TDD-Nachweis

### RED

Nach dem Schreiben der fokussierten Tests und vor der Implementierung:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_scalar_sync.py -q
```

Ergebnis: Collection-Fehler `ModuleNotFoundError: No module named 'app.services.google_health_scalar_sync'`. Die Tests schlugen damit wegen des fehlenden Scalar-Service wie vorgesehen fehl.

### GREEN

Nach Implementierung und Korrekturen:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci

docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_scalar_sync.py -q
```

Ergebnis:

```text
.......                                                                  [100%]
7 passed
```

### Angrenzende Persistenztests

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_importers.py tests/test_yazio_provider.py -q
```

Ergebnis:

```text
.................................................................        [100%]
65 passed
```

## Hinweise

- Der erste RED-Aufruf gegen ein nicht neu gebautes CI-Image meldete zusätzlich, dass die neue Testdatei im alten Image fehlte (`file or directory not found`). Nach dem CI-Image-Rebuild wurde der fachlich relevante RED-Fehler oben beobachtet.
- Full-Repository- und UI-/Priority-Tests wurden in diesem Task nicht ausgeführt; diese Validierung bleibt beim Main-Agent nach Integration der parallelen Tasks.
