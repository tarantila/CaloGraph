#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${BACKUP_FILE:-${1:-}}
database_name=${POSTGRES_DB:-calograph}

if [ -z "$backup_file" ] || [ ! -f "$backup_file" ]; then
  printf 'Aufruf: CONFIRM_RESTORE=calograph %s /pfad/zu/calograph.dump\n' "$0" >&2
  exit 1
fi
if [ "${CONFIRM_RESTORE:-}" != "calograph" ]; then
  printf 'Restore abgebrochen. Setze CONFIRM_RESTORE=calograph bewusst für diesen destruktiven Vorgang.\n' >&2
  exit 1
fi

cd "$project_root"
scripts/verify-backup.sh "$backup_file"
docker compose stop frontend backend yazio-scheduler
docker compose exec -T postgres pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --exit-on-error \
  --dbname="$database_name" < "$backup_file"
docker compose run --rm --no-deps backend alembic upgrade head
docker compose up -d --wait
docker compose ps
printf 'Restore abgeschlossen. Anmeldung, Datenstatus und YAZIO-Verbindung prüfen.\n'
