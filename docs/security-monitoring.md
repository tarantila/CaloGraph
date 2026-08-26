# Security monitoring and audit events

CaloGraph writes security-relevant application events as one JSON object per
line on the `backend` or `yazio-scheduler` standard output stream. These events
are separate from ordinary request and scheduler messages and can be selected
by their `event` field.

```json
{"actor_ref":"b0f6a7f319c80bbb","client_ref":"3f67e4f455445cbb","event":"auth.login.failed","outcome":"failure","reason":"invalid_credentials","request_id":"4c74058b4d224db4adff9a9b7ff8fdb4","timestamp":"2026-08-03T12:00:00.000Z"}
```

The event name and optional fields are allowlisted in the application. Values
that could contain free text are rejected. In particular, security events must
never contain:

- usernames, email addresses, passwords, or YAZIO credentials;
- session, API, invitation, MFA, recovery, or registration tokens;
- imported health values, raw payloads, filenames, or arbitrary exception text;
- database URLs or secret-file contents.

`actor_ref`, `client_ref`, and `target_ref` are 16-character HMAC references
derived with the rate-limit secret. They allow events from the same installation
to be correlated without writing the underlying identifier. Rotating that
secret intentionally breaks correlation with older events. Request IDs are
limited to 32 lowercase hexadecimal characters.

## Covered events

The event stream covers:

- successful and failed password, passkey, and MFA authentication;
- password, MFA, passkey, session, invitation, registration, and API-token changes;
- successful, rejected, and transaction-failed administrative user lifecycle
  actions (`admin.user.deactivated`, `admin.user.reactivated`,
  `admin.user.deleted`, `admin.user.recovery_issued`,
  `admin.authenticators.reset`, `admin.user.lifecycle_rejected`, and
  `admin.user.lifecycle_failed`);
- successful and rejected password recovery completion;
- rate-limit decisions for every HTTP endpoint using the shared limiter;
- CLI user creation, recovery issuance, and authenticator reset;
- YAZIO connection changes, historical synchronization scheduling, and manual
  or scheduled synchronization outcomes;
- import starts, completions, partial failures, validation outcomes, and HTTP rejection;
- otherwise unhandled application request failures.

Import events contain only the source type, batch reference, and aggregate
counters. Failure reasons are fixed identifiers such as `record_limit`,
`invalid_xml`, or `database_error`.

Ausgewählte Authentifizierungs- und administrative Ereignisse werden zusätzlich
für die konfigurierte Aufbewahrungsdauer in der Datenbank als
`security_audit_events` gespeichert. Die persistente Historie enthält keine
Passwörter, Tokens, MFA-/Passkey-Materialien, Gesundheitswerte oder
Exportinhalte. Die Standardaufbewahrung beträgt 90 Tage und kann über
`SECURITY_AUDIT_RETENTION_DAYS` begrenzt angepasst werden.

## Release status lookup

Der Versionsvergleich mit GitHub ist standardmäßig deaktiviert
(`RELEASE_STATUS_ENABLED=false`). Bei Aktivierung fragt der Admin-Systemstatus
die öffentliche GitHub-Release-API mit einer kurzen, gecachten Anfrage ab.
Dabei werden keine Benutzer- oder Gesundheitsdaten übertragen; GitHub sieht
jedoch die ausgehende Server-IP und den CaloGraph-Versionswert. Bei Fehlern
bleibt der Status `unknown`.

## Flüchtige App-Logs

Das Admin-Center zeigt unter **App-Logs** einen begrenzten In-Process-Puffer
technischer Request-Ereignisse. Er umfasst höchstens 500 Einträge und ist nach
einem Backend-Neustart leer. Er enthält nur Level, Zeitpunkt, Aktion,
Status, Dauer und Request-ID. Request Bodies, Cookies, Authorization-Header,
Passwörter, API-Tokens, YAZIO-Zugangsdaten, MFA-Secrets und Gesundheitsdaten
werden dort nicht gespeichert. App-Logs sind keine persistente Security-Audit-
Historie und lesen weder Docker-Logs noch Host-Journald.

## Client addresses and request correlation

Security-Audit-Ereignisse speichern die normalisierte Client-IP nur, wenn sie
an der vertrauenswürdigen Proxy-Grenze korrekt ermittelt wurde. `client_ref`
bleibt eine getrennte 16-stellige HMAC-Referenz für technische Korrelation.
Der öffentliche Reverse Proxy überschreibt `X-Real-IP` und
`X-Forwarded-For`; beliebige Forwarding-Ketten aus dem Internet werden nicht
übernommen.

Eine optionale GeoIP-Anreicherung ist standardmäßig deaktiviert:
`SECURITY_AUDIT_GEOIP_PROVIDER=disabled`. Bei
`SECURITY_AUDIT_GEOIP_PROVIDER=ipwhois` werden externe öffentliche IP-Adressen
bei der Anzeige im Admin-Center an ipwho.is übertragen. Ergebnisse werden
begrenzt im Prozess gecacht; private und lokale Adressen werden nie extern
aufgelöst. GeoIP-Ausfälle dürfen Login, Audit-Speicherung oder die Admin-Seite
nicht blockieren.
Fail2ban should read the public Nginx or HAProxy access log, where the validated
client address is available, rather than trying to ban an HMAC `client_ref`.
A concrete filter and jail matching CaloGraph's documented JSON Nginx format
are provided in [Reverse proxy and TLS](reverse-proxy.md#optional-fail2ban).
At minimum, alert on repeated `401` and `429` responses for authentication,
invitation exchange, MFA, passkey, and YAZIO connection routes. Keep application
rate limits enabled; Fail2ban is an additional host-level control, not a
replacement.

## Retention and alerting

Compose keeps at most three 10 MiB log files per service. This protects disk
space but is not an audit-retention guarantee. Internet-facing installations
should forward security events and public access logs to a host or remote log
collector that the application containers cannot modify.

Define and document a retention period that matches the installation's privacy
requirements. A practical starting point is 30 days for access logs and 90 days
for pseudonymous security events, with shorter periods where incident response
does not require them. Restrict log access, encrypt the storage, monitor collector
failure and disk usage, and test that the following conditions generate alerts:

- repeated login, MFA, invitation, token, import, or YAZIO rate limits;
- repeated authentication failures followed by a successful login;
- administrative MFA reset, invitation revocation, API-token changes, and any
  user deactivation, reactivation, deletion, or rejected lifecycle action;
- partial imports, rejected oversized uploads, and repeated YAZIO failures;
- unhandled request failures.

Do not attach production logs to public issues. When diagnostic sharing is
necessary, remove request IDs and pseudonymous references as well as ordinary
personal data.
