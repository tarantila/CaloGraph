# Datenmodell

- `users`: Konto, Sprache, IANA-Zeitzone, Wochenbeginn und Aktivstatus.
- `user_sessions`: gehashter Sitzungsschlüssel, gehashter CSRF-Schlüssel, Ablauf und Widerruf.
- `api_tokens`: Bezeichnung, Präfix, HMAC-Hash, Scopes, Ablauf und Widerruf.
- `nutrition_targets`: historisierte Ziele mit halb-offenem Zeitraum `valid_from <= Tag < valid_to`.
- `tracking_quality_settings`: konfigurierbare Schwellen der Vollständigkeitsheuristik.
- `health_samples`: kanonischer Wert samt Originalwert, UTC-Zeiten, lokalem Datum, Quelle, Fingerprint und Import-Batch.
- `import_batches`, `import_errors`, `raw_import_payloads`: Bericht, sicherer Fehlerkontext und optionale komprimierte Rohdaten.
- `tracking_overrides`: manuelle Vollständigkeitsmarkierung pro lokalem Tag.
- `rate_limit_buckets`: gehashte Identifikatoren in Minutenfenstern.

Stabile externe IDs sind je Benutzer, Adapter und Quellenkennung eindeutig. Zusätzlich verhindert `(user_id, fingerprint)` Duplikate ohne externe ID. Dezimalwerte vermeiden Rundungsfehler. Zeitpunkte werden in UTC gespeichert; `local_date` wird beim Import mit der Benutzerzeitzone bestimmt.

