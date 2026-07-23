#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
database_name=${POSTGRES_DB:-calograph}
database_user=${POSTGRES_USER:-calograph}
final_path="$backup_dir/calograph-$timestamp.dump"

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.calograph-$timestamp.XXXXXX.partial")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

cd "$project_root"
docker compose exec -T postgres pg_dump \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner > "$temporary_path"

test -s "$temporary_path"
docker compose exec -T postgres pg_restore --list < "$temporary_path" > /dev/null
chmod 600 "$temporary_path"
mv "$temporary_path" "$final_path"
checksum=$(sha256sum "$final_path" | cut -d ' ' -f 1)
printf '%s  %s\n' "$checksum" "$(basename "$final_path")" > "$final_path.sha256"
chmod 600 "$final_path.sha256"
trap - EXIT HUP INT TERM
printf 'Backup erstellt: %s\n' "$final_path"
printf 'Prüfsumme erstellt: %s\n' "$final_path.sha256"
