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

## Review-Fix-Nachweis

Die unabhängige Task-Review identifizierte vier Punkte; alle wurden testgetrieben korrigiert:

- Google Activity/Weight bleiben bis zur späteren Registry/UI-Aufgabe aus den auswählbaren Provider-Maps und Source-Type-Sets entfernt. Für die Ingestion gibt es separate stabile interne Source-Konstanten/-Gruppen.
- Die Service-Seitenfilter werden aus dem User-IANA-Tagesschnitt berechnet und als UTC-Grenzen an den Google-Client übergeben. Der DST-Test für `Europe/Berlin` prüft die lokale halb-offene Tagesgrenze.
- Activity akzeptiert ausschließlich die offizielle DTO-Einheit `kcal`; eine andere Energieeinheit wird vor jeder Persistenz abgewiesen.
- Nicht unterstützte Weight-Einheiten wurden aus der Alias-Allowlist entfernt; die Allowlist entspricht damit exakt den vorhandenen Konversionen.

### Fix-RED

Nach dem Schreiben der Review-Regressionstests und vor den Korrekturen:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_scalar_sync.py -q
```

Ergebnis: Collection-Fehler wegen des noch fehlenden internen Symbols
`GOOGLE_HEALTH_ACTIVITY_SOURCE_TYPE` in `app.activity`.

### Fix-GREEN

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_scalar_sync.py -q
```

Ergebnis:

```text
..........                                                               [100%]
10 passed
```

Die Tests decken zusätzlich die internen Source-Konstanten, lokale DST-Grenzen,
halb-offene Tagesbereiche sowie Activity-Unit-Rejection ab.

### Fix-angrenzende Tests

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_importers.py tests/test_yazio_provider.py -q
```

Ergebnis:

```text
.................................................................        [100%]
65 passed
```

### Statischer Fix-Nachweis

Die nachgelagerte statische Prüfung verlangte den fehlenden `UUID`-Import,
Entfernung eines ungenutzten DTO-Imports und Ruff-konforme Importreihenfolge.
Nach diesen ausschließlich statischen Korrekturen:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml build backend-ci
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  ruff check app/activity.py app/weight.py \
  app/services/google_health_scalar_sync.py \
  tests/test_google_health_scalar_sync.py
```

Ergebnis:

```text
All checks passed!
```

Der fokussierte Scalar-Testlauf wurde mit demselben neu gebauten Image erneut
ausgeführt:

```text
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm backend-ci \
  pytest tests/test_google_health_scalar_sync.py -q
```

Ergebnis:

```text
..........                                                               [100%]
10 passed
```
