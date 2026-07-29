#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}
recipients_file=${BACKUP_AGE_RECIPIENTS_FILE:-}
age_bin=${AGE_BIN:-age}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
database_name=${POSTGRES_DB:-calograph}
database_user=${POSTGRES_USER:-calograph}
final_path="$backup_dir/calograph-$timestamp.dump.age"

if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to create encrypted backups.\n' >&2
  exit 1
fi
if [[ -z "$recipients_file" || ! -r "$recipients_file" ]]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable age recipients file.\n' >&2
  exit 1
fi

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.calograph-$timestamp.XXXXXX.partial")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

cd "$project_root"

# Validate that PostgreSQL can produce a structurally readable custom dump
# without writing that plaintext stream to disk.
docker compose exec -T postgres pg_dump \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner \
  | docker compose exec -T postgres pg_restore --list >/dev/null

# Create a fresh snapshot and encrypt it directly. The private age identity is
# deliberately not required on the Docker host.
docker compose exec -T postgres pg_dump \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner \
  | "$age_bin" --encrypt --recipients-file "$recipients_file" >"$temporary_path"

test -s "$temporary_path"
head -c 64 "$temporary_path" | grep -q 'age-encryption.org/v1'
chmod 600 "$temporary_path"
mv "$temporary_path" "$final_path"
checksum=$(sha256sum -- "$final_path" | awk '{print $1}')
printf '%s  %s\n' "$checksum" "$(basename "$final_path")" >"$final_path.sha256"
chmod 600 "$final_path.sha256"
trap - EXIT HUP INT TERM

if [[ -n "${BACKUP_AGE_IDENTITY_FILE:-}" ]]; then
  scripts/verify-backup.sh "$final_path"
fi

printf 'Encrypted backup created: %s\n' "$final_path"
printf 'Ciphertext checksum created: %s\n' "$final_path.sha256"
