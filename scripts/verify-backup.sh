#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${1:-}

if [ -z "$backup_file" ] || [ ! -f "$backup_file" ]; then
  printf 'Aufruf: %s /pfad/zu/calograph.dump\n' "$0" >&2
  exit 1
fi

backup_dir=$(CDPATH= cd -- "$(dirname -- "$backup_file")" && pwd)
backup_name=$(basename "$backup_file")
absolute_backup="$backup_dir/$backup_name"
checksum_file="$absolute_backup.sha256"

if [ -f "$checksum_file" ]; then
  (
    cd "$backup_dir"
    sha256sum --check "$(basename "$checksum_file")"
  )
else
  printf 'Hinweis: Keine SHA-256-Datei gefunden; prüfe nur das Dump-Format.\n'
fi

cd "$project_root"
docker compose exec -T postgres pg_restore --list < "$absolute_backup" > /dev/null
printf 'Backup ist lesbar: %s\n' "$absolute_backup"
