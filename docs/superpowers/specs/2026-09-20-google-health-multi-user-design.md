# Google Health Multi-User Credentials and Integration Status Design

**Status:** Freigegebener Architekturvertrag
**Datum:** 2026-09-20
**Scope:** CaloGraph Multi-User-Google-Health-Integration

## Ziel

Google Health verwendet pro CaloGraph-Benutzer eigene OAuth-Client-Credentials.
`GOOGLE_HEALTH_ENABLED` bleibt ausschließlich der serverweite Feature-Schalter.
Globale Google-Client-Credentials sind kein Bestandteil des Betriebsvertrags.

Die Integration muss Credentials, OAuth-Verbindung, Scope-Zustand, Connection-Test,
manuellen Sync und bounded Retry-Zustände strikt nach `user_id` isolieren.

## Verbindliche Entscheidungen

### Credential-Ownership

`GoogleHealthConnection` bleibt die per Benutzer eindeutig besitzende Verbindung
und wird um folgende Felder erweitert:

- `client_id`: user-scoped, kein Secret.
- `encrypted_client_secret`: Fernet-verschlüsselt, nullable vor Erstkonfiguration.
- `encrypted_refresh_token`: bestehend, künftig nullable bis OAuth erfolgreich war.
- `granted_scopes`, `state`, Sync-/Retry-Metadaten und Zeitstempel.

Die bestehende Fernet-Infrastruktur aus `CREDENTIAL_ENCRYPTION_KEY` wird verwendet.
Client-Secret und Refresh-Token erscheinen niemals im Klartext in Datenbank,
Logs, Security Events, API Responses, Exceptions, `repr()` oder Frontend-State.

### Kein globaler OAuth-Fallback

Die globalen Variablen `GOOGLE_HEALTH_CLIENT_ID`,
`GOOGLE_HEALTH_CLIENT_SECRET` und `GOOGLE_HEALTH_CLIENT_SECRET_FILE` werden aus
dem aktiven Google-Health-Vertrag entfernt. Sie dürfen die User-Credentials nicht
ersetzen oder überschreiben.

`GOOGLE_HEALTH_ENABLED=true` ist ohne globale OAuth-Credentials gültig, sofern
der eingeloggte Benutzer eigene Credentials gespeichert hat.

Eine vorhandene unversionierte lokale Secret-Datei wird nicht automatisch gelöscht,
ist nach der Umstellung aber nicht mehr erforderlich.

### Credential-Semantik

- Neue Credentials werden als Client-ID-/Client-Secret-Paar atomar gespeichert.
- Bei gespeicherten Credentials bedeuten leere Eingaben „beibehalten“.
- Sobald ein Teil des Paares ersetzt wird, müssen beide neuen Werte gesendet werden.
- `DELETE /google-health/credentials` löscht Client-ID, Client-Secret und
  Refresh-Token bewusst und vollständig.
- `DELETE /google-health/connection` löscht nur Refresh-Token und gewährte Scopes.
  Die Client-Credentials bleiben gespeichert.
- Nach Disconnect lautet der Zustand `not_connected`; die UI zeigt wieder
  „Mit Google verbinden“.
- Der erneute OAuth-Start verwendet die gespeicherten Client-Credentials.

### Migration vorhandener Verbindungen

Alembic-Revision `20260920_0033` ergänzt die Credential-Felder, macht den
Refresh-Token bis zum ersten OAuth nullable und erweitert den Statusvertrag um
`not_connected`.

Bestehende Refresh-Tokens werden nicht gelöscht. Sie dürfen nach dem vollständigen
globalen Schnitt jedoch nicht mit neu eingetragenen User-Credentials weiterverwendet
werden, wenn sie ursprünglich für einen anderen/globalen OAuth-Client ausgestellt
wurden. Migrierte Verbindungen bleiben daher bis zur erneuten User-Autorisierung
klar `reauth_required` beziehungsweise `not_connected`.

Erst ein erfolgreicher OAuth-Callback mit den User-Credentials darf einen aktiven
Refresh-Token herstellen. Ein Downgrade verweigert sich explizit, sobald neue
nullable Zustände nicht verlustfrei in das alte Schema überführbar wären.

## API-Vertrag

Authentifizierte Endpunkte:

```text
GET    /api/v1/google-health/status
PUT    /api/v1/google-health/credentials
DELETE /api/v1/google-health/credentials
DELETE /api/v1/google-health/connection
POST   /api/v1/google-health/connection/test
POST   /api/v1/google-health/oauth/start
GET    /api/v1/google-health/oauth/callback
POST   /api/v1/google-health/sync
```

`GET /status` enthält ausschließlich sichere Metadaten:

```text
available
configured
client_id_configured
client_secret_configured
redirect_uri
state
granted_scopes
sync_state
retry_attempt
retry_max_attempts
next_retry_at
last_attempt_at
last_success_at
last_error_category
```

Keine API-Antwort enthält Secret- oder Tokenwerte.

## OAuth-Vertrag

OAuth Start, Token Exchange und Refresh laden Client-ID und Client-Secret
über die Verbindung des aktuell authentifizierten Users. Kein Pfad liest
Credentials eines anderen Users.

OAuth State und PKCE bleiben erhalten:

- State wird nur gehasht gespeichert.
- PKCE-Verifier wird verschlüsselt gespeichert.
- Der OAuth Flow enthält `user_id`.
- Callback akzeptiert nur den Flow des aktuell authentifizierten Users.
- Redirect URI bleibt der serverweite Vertrag:
  `/api/v1/google-health/oauth/callback` am konfigurierten öffentlichen Origin.

Exakt erforderliche Read-only-Scopes:

```text
https://www.googleapis.com/auth/googlehealth.nutrition.readonly
https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
```

Unvollständige oder alte Scope-Grants führen zu `reauth_required`.
Kein Health Connect und kein Google Fit.

## Zustände

```text
disabled          Serverfeature deaktiviert
not_configured    User-Credentials fehlen
not_connected     Credentials vorhanden, kein aktiver Refresh-Token
active            Token und alle erforderlichen Scopes vorhanden
reauth_required   Token ungültig oder Scopes unvollständig
```

Der Status-Badge steht kompakt oben rechts auf der Provider-Karte:

```text
Serverseitig deaktiviert
Nicht eingerichtet
Nicht verbunden
Verbunden · vor …
Erneute Anmeldung erforderlich
Synchronisierung läuft …
Sync fehlgeschlagen · <attempt>/<max> · vor …
```

## Connection Test

„Verbindung testen“ ist eine explizite User-Aktion. Sie:

- verwendet nur die aktuelle User-Verbindung,
- prüft Credential-/Tokenfähigkeit und Scope-Vollständigkeit,
- führt kleine read-only Google-Health-Requests für die unterstützten Bereiche aus,
- persistiert keine Health-Werte,
- gibt nur Erfolg, Reauth oder eine begrenzte Fehlerkategorie zurück.

Rohpayloads, Health Values, Tokens und Secrets werden nicht zurückgegeben.

## Retry-Vertrag

Nur der manuelle Sync verwendet bounded Retry:

```text
max_attempts = 3
Versuch 1 = Initialversuch
Versuch 2/3 = höchstens zwei Retries
Backoff = 1 s, danach 2 s
```

Retrybar sind ausschließlich:

- HTTP 429 / `rate_limited`
- HTTP 5xx / `provider_unavailable`
- temporäre Providerfehler
- Transport- und Timeoutfehler

Nicht retrybar sind Credentials, 401, fehlende Scopes, Reauth, ungültige
Anfragen, andere permanente 4xx, ungültige Providerantworten und Persistenzfehler.

Es gibt keinen Scheduler und keinen persistenten Retry-Job. Die drei Versuche
laufen innerhalb eines manuellen Requests. `retry_attempt` und der Badge dürfen
nur tatsächlich ausgeführte Versuche zählen. Wenn der Request erst nach allen
Retries zurückkehrt, zeigt ein endgültiger Fehler `3/3`; Zwischenstände dürfen
nicht künstlich simuliert werden.

Während eines realen Backoffs kann `next_retry_at` gesetzt werden; nach dem
finalen Ergebnis ist es `null`. `last_error_category` enthält nur eine begrenzte
Kategorie, niemals Provider-Rohtext.

## Frontend-Vertrag

Auf `/konto/integrationen` erhält die Google-Health-Karte:

- Provider-Icon und Status-Badge oben rechts.
- Client-ID-Feld.
- Client-Secret-Feld für neue oder ersetzende Eingabe, optional sichtbarer Wert
  nur während der aktuellen Eingabe.
- Gespeicherte Werte werden nie zurückgeladen; Platzhalter lauten sinngemäß
  „Gespeichert — neu eingeben zum Ersetzen“.
- Speichern beziehungsweise Ersetzen.
- Kompakte Anzeige der tatsächlichen Redirect URI für Google Cloud.
- „Mit Google verbinden“, „Verbindung testen“, „Jetzt synchronisieren“ und
  „Verbindung trennen“ abhängig vom Zustand.
- Keine technischen Scope-Strings im normalen UI.
- Fehler und Retry-Zustand primär als kompakter Badge; Details darunter.
- Disabled-, Loading-, Error-, Empty- und Tastaturzustände bleiben zugänglich.

## Security-Abnahme

Mit mindestens zwei synthetischen Usern muss nachgewiesen werden:

- User A liest oder verändert niemals Credentials, Token oder Status von User B.
- OAuth A verwendet Client A; OAuth B verwendet Client B.
- Sync A verwendet Token A; Sync B verwendet Token B.
- Update und Disconnect A verändern B nicht.
- Secrets fehlen in GET, Logs, Security Events, Exceptions und Frontend-State.
- Leeres Update erhält gespeicherte Credentials.
- Explizite Ersetzung ersetzt nur das aktuelle User-Paar.

## Validierung

Nach der Implementierung sind mindestens auszuführen:

- gezielte Backend-Regressionstests,
- gezielte Frontend-Tests,
- vollständige Backend-Suite,
- vollständige Frontend-Suite,
- Typecheck,
- ESLint,
- Build,
- Ruff,
- MyPy,
- Alembic Heads/Migrationsprüfung,
- `git diff --check`,
- Runtime-Health und authentifizierte UI-Prüfung.

Kein echter OAuth-Flow und kein Google-Health-Sync ohne ausdrückliche
Benutzerinteraktion.
