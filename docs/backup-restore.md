# Backup und Restore

## Backup

```bash
BACKUP_DIR=/sicherer/verschluesselter/pfad scripts/backup-postgres.sh
```

Das Skript verwendet `pg_dump --format=custom`, schreibt atomar über eine temporäre Datei und setzt restriktive Berechtigungen. Prüfe regelmäßig, ob die Datei außerhalb des Docker-Hosts wiederherstellbar ist.

PostgreSQL 18 speichert den versionsspezifischen Cluster unterhalb des auf `/var/lib/postgresql` eingehängten Volumes. Bei einem Wechsel der PostgreSQL-Hauptversion ist deshalb ein dokumentiertes `pg_upgrade` oder Dump/Restore erforderlich; nur den Image-Tag zu ändern ist nicht ausreichend.

## Restore

1. Anwendung stoppen und vorhandenes Volume separat sichern.
2. Leere Datenbank mit derselben PostgreSQL-Hauptversion bereitstellen.
3. Dump prüfen und wiederherstellen:

```bash
docker compose stop frontend backend
docker compose exec -T postgres pg_restore --list < /sicherer/pfad/calograph.dump
docker compose exec -T postgres pg_restore --clean --if-exists --no-owner --dbname="$POSTGRES_DB" < /sicherer/pfad/calograph.dump
docker compose run --rm backend alembic upgrade head
docker compose up -d
docker compose ps
```

Die Variablen vorher aus `.env` laden oder den konkreten Datenbanknamen einsetzen. Restore zuerst in einer isolierten Umgebung testen. Ein Alembic-Downgrade ersetzt kein Backup.
