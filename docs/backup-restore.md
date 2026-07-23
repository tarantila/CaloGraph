# Backup, Restore und Updates

## Was gesichert werden muss

Eine vollständige CaloGraph-Sicherung besteht aus zwei getrennt zu schützenden
Teilen:

1. dem PostgreSQL-Dump mit Gesundheitsdaten, Konten und verschlüsselten
   YAZIO-Zugangsdaten;
2. der `.env` mit Datenbank-, Session-, Rate-Limit- und
   `CREDENTIAL_ENCRYPTION_KEY`.

Ohne den ursprünglichen `CREDENTIAL_ENCRYPTION_KEY` können die in der Datenbank
gespeicherten YAZIO-Zugangsdaten nicht wieder entschlüsselt werden. Datenbank
und `.env` gemeinsam ermöglichen dagegen die Entschlüsselung. Beide Sicherungen
gehören deshalb auf verschlüsselten Speicher mit restriktiven Berechtigungen,
idealerweise zusätzlich außerhalb des Docker-Hosts.

## Datenbank sichern und prüfen

```bash
BACKUP_DIR=/sicherer/verschluesselter/pfad scripts/backup-postgres.sh
scripts/verify-backup.sh /sicherer/verschluesselter/pfad/calograph-ZEITSTEMPEL.dump
```

Das Backupskript:

- verwendet `pg_dump --format=custom`;
- schreibt atomar über eine zufällige temporäre Datei;
- setzt Verzeichnisrechte `0700` und Dateirechte `0600`;
- lässt `pg_restore --list` vor der Freigabe laufen;
- erzeugt eine SHA-256-Datei zur späteren Erkennung von Übertragungsfehlern.

Die Prüfsumme erkennt versehentliche Beschädigung, ist aber keine
kryptografische Authentifizierung gegen einen Angreifer mit Schreibzugriff.

## Geheimnisse separat sichern

Nur wenn `BACKUP_DIR` tatsächlich verschlüsselt und geschützt ist:

```bash
BACKUP_DIR=/sicherer/verschluesselter/pfad scripts/backup-secrets.sh
```

Die erzeugte Datei ist eine Kopie der `.env` und muss wie ein Passworttresor
behandelt werden. Nach der Sicherung sollten Datenbankdump und
Geheimnissicherung zusätzlich auf ein zweites, getrenntes Medium kopiert
werden.

## Wiederherstellung

Der Restore überschreibt die aktuelle CaloGraph-Datenbank. Deshalb verlangt das
Skript eine bewusste Bestätigung:

```bash
CONFIRM_RESTORE=calograph \
  scripts/restore-postgres.sh /sicherer/pfad/calograph-ZEITSTEMPEL.dump
```

Das Skript validiert Dump und Prüfsumme, stoppt Frontend, Backend und
YAZIO-Scheduler, stellt die Datenbank mit `--clean --if-exists --no-owner`
wieder her, führt ausstehende Alembic-Migrationen aus und startet alle Dienste.
Die `.env` wird absichtlich nicht automatisch überschrieben.

Vor einem Restore auf einem neuen Host:

1. Repository und Docker installieren.
2. Die passende Geheimnissicherung bewusst als `.env` ablegen und
   `chmod 600 .env` setzen.
3. PostgreSQL mit derselben Hauptversion starten.
4. Restore-Skript ausführen.
5. Anmeldung, Datenstatus, letzte YAZIO-Synchronisierung und einen manuellen
   Sync prüfen.

PostgreSQL 18 speichert den versionsspezifischen Cluster unterhalb des auf
`/var/lib/postgresql` eingehängten Volumes. Bei einem Wechsel der
PostgreSQL-Hauptversion ist Dump/Restore oder `pg_upgrade` erforderlich; nur
den Image-Tag zu ändern reicht nicht.

## Wiederherstellung regelmäßig testen

Mindestens vierteljährlich sollte ein aktueller Dump auf einem isolierten
Testsystem wiederhergestellt werden. Ein erfolgreiches `pg_dump` allein beweist
nicht, dass Zugang, `.env`, Verschlüsselungsschlüssel und Anwendung gemeinsam
wiederherstellbar sind.

## Container aktualisieren

Nachdem der gewünschte Quellcode lokal vorliegt:

```bash
BACKUP_DIR=/sicherer/verschluesselter/pfad \
  BACKUP_SECRETS=1 \
  scripts/update-containers.sh
```

`BACKUP_SECRETS=1` nur für ein verschlüsseltes Ziel verwenden. Das Skript prüft
Compose, erstellt und validiert zuerst das Backup, aktualisiert das gepinnte
PostgreSQL-Image, baut Backend und Frontend mit aktuellen Basisimages neu und
wartet anschließend auf gesunde Dienste. Es führt absichtlich kein `git pull`
aus; die Auswahl des zu installierenden Commits bleibt eine separate,
kontrollierte Aktion.

Nach jedem Update:

```bash
docker compose ps
docker compose logs --no-color --tail=100 backend yazio-scheduler
docker compose exec backend python -m app.cli yazio-status --username DEIN_BENUTZER
```

Ein Alembic-Downgrade ist kein Ersatz für ein Backup.
