#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}

cd "$project_root"
docker compose config --quiet
BACKUP_DIR="$backup_dir" scripts/backup-postgres.sh
if [ "${BACKUP_SECRETS:-0}" = "1" ]; then
  BACKUP_DIR="$backup_dir" scripts/backup-secrets.sh
else
  printf 'Secret backup skipped. Set BACKUP_SECRETS=1 to create an age-encrypted copy.\n'
fi

docker compose pull postgres
docker compose build --pull backend frontend
docker compose up -d --wait --remove-orphans
docker compose ps
printf 'Container-Update abgeschlossen. Anwendung und YAZIO-Status jetzt prüfen.\n'
