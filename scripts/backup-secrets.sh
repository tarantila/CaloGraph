#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
source_path="$project_root/.env"
final_path="$backup_dir/calograph-secrets-$timestamp.env"

if [ ! -f "$source_path" ]; then
  printf 'Keine .env unter %s gefunden.\n' "$source_path" >&2
  exit 1
fi

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.calograph-secrets-$timestamp.XXXXXX.partial")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

cp "$source_path" "$temporary_path"
chmod 600 "$temporary_path"
mv "$temporary_path" "$final_path"
trap - EXIT HUP INT TERM
printf 'Geheimnissicherung erstellt: %s\n' "$final_path"
printf 'Nur verschlüsselt und getrennt vom Docker-Host aufbewahren.\n'
