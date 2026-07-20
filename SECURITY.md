# Sicherheitsrichtlinie

CaloGraph verarbeitet sensible Gesundheitsdaten. Sicherheitsprobleme bitte nicht in einem öffentlichen Issue mit realen Payloads, Tokens, Passwörtern, Logs oder Screenshots melden. Nutze den privaten Sicherheitskanal des Repository-Hosters oder kontaktiere den Betreiber direkt.

## Unterstützte Versionen

Das Projekt befindet sich in der MVP-Phase. Sicherheitskorrekturen werden für den aktuellen Stand des `main`-Branches bereitgestellt. Abhängigkeiten und Container-Basisimages müssen regelmäßig aktualisiert und anschließend vollständig getestet werden.

## Sichere Installation

- Anwendung nicht ohne TLS öffentlich bereitstellen.
- unabhängige zufällige Datenbank-, Session- und Rate-Limit-Secrets verwenden.
- `COOKIE_SECURE=true` hinter HTTPS setzen.
- Import-Tokens pro Gerät anlegen, regelmäßig erneuern und bei Verlust sofort widerrufen.
- Datenbank-Volume und Backups verschlüsseln und nur dem Betreiber zugänglich machen.
- Logs und Backups niemals an öffentliche Supportkanäle anhängen.
- Reverse-Proxy-Vertrauensgrenzen gemäß `docs/reverse-proxy.md` konfigurieren.

Das vollständige Bedrohungsmodell steht in [docs/threat-model.md](docs/threat-model.md).

