# Produktiver Docker-Betrieb

## Mindestcheckliste

- `.env` gehört dem Betreiber und hat Dateirechte `0600`.
- `POSTGRES_PASSWORD`, `SESSION_SECRET`, `RATE_LIMIT_SECRET` und
  `CREDENTIAL_ENCRYPTION_KEY` sind unabhängige Zufallswerte.
- PostgreSQL bleibt ohne Host-Port im internen Docker-Netz.
- Port `8180` bleibt auf `127.0.0.1` gebunden; externer Zugriff läuft nur über
  einen TLS-Reverse-Proxy.
- Bei HTTPS stehen `COOKIE_SECURE=true`, eine exakte HTTPS-Origin in
  `TRUSTED_ORIGINS` und der öffentliche Host in `TRUSTED_HOSTS`.
- `ENABLE_HSTS=true` wird erst gesetzt, wenn die Domain dauerhaft nur per HTTPS
  erreichbar ist.
- Backups liegen verschlüsselt außerhalb des Docker-Hosts und ein Restore wurde
  praktisch getestet.
- Docker- und Host-Sicherheitsupdates werden regelmäßig eingespielt.

## Laufzeit

Die Compose-Dienste verwenden `restart: unless-stopped`, interne
Gesundheitsprüfungen, `no-new-privileges`, schreibgeschützte Dateisysteme für
Frontend und Python-Dienste sowie begrenzte Docker-Logrotation. Der
YAZIO-Scheduler schreibt einen Heartbeat in sein flüchtiges `/tmp`; seine
Healthcheck erkennt einen hängenden Scheduler unabhängig vom Backend.

Status prüfen:

```bash
docker compose config --quiet
docker compose ps
docker compose logs --no-color --tail=100 backend yazio-scheduler frontend
curl --fail http://127.0.0.1:8180/health
```

## Netzwerk und TLS

Eine Beispielkonfiguration steht in
[reverse-proxy.md](reverse-proxy.md). CaloGraph sollte nicht direkt über einen
öffentlichen HTTP-Port veröffentlicht werden. Firewall und Reverse Proxy
dürfen nur die wirklich benötigten Ports freigeben.

## Speicher und Überwachung

Docker-Logs rotieren pro Dienst in höchstens drei Dateien mit je 10 MB. Das
ersetzt keine Überwachung des freien Speicherplatzes. Besonders zu beobachten
sind das PostgreSQL-Volume, der verschlüsselte Backup-Speicher und fehlgeschlagene
YAZIO-Läufe im Datenstatus.

## Updates

Der empfohlene Ablauf ist in [backup-restore.md](backup-restore.md)
dokumentiert und über `scripts/update-containers.sh` reproduzierbar. Vor jedem
Update wird ein validierter Datenbankdump erstellt. Quellcode-Updates und
Container-Updates bleiben getrennte, nachvollziehbare Schritte.
