#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}
recipients_file=${BACKUP_AGE_RECIPIENTS_FILE:-}
identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
age_bin=${AGE_BIN:-age}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
database_name=${POSTGRES_DB:-calograph}
database_user=${POSTGRES_USER:-calograph}
final_path="$backup_dir/calograph-$timestamp.dump.age"
checksum_path="$final_path.sha256"
temporary_path=
checksum_temporary_path=
final_created=0
checksum_created=0

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  rm -f "${temporary_path:-}" "${checksum_temporary_path:-}"
  if [[ "$checksum_created" -eq 1 ]]; then
    rm -f "$checksum_path"
  fi
  if [[ "$final_created" -eq 1 ]]; then
    rm -f "$final_path"
  fi
  exit "$status"
}

if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to create encrypted backups.\n' >&2
  exit 1
fi
if [[ -z "$recipients_file" || ! -r "$recipients_file" ]]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable age recipients file.\n' >&2
  exit 1
fi
if [[ -z "$identity_file" || ! -r "$identity_file" ]]; then
  printf 'BACKUP_AGE_IDENTITY_FILE must name a readable age identity.\n' >&2
  exit 1
fi

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.calograph-$timestamp.XXXXXX.partial")
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

cd "$project_root"

# Create one complete snapshot and encrypt it directly. Pipefail makes a
# failure in either pg_dump or age abort publication.
docker compose exec -T postgres pg_dump \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner \
  | "$age_bin" --encrypt --recipients-file "$recipients_file" >"$temporary_path"

test -s "$temporary_path"
head -c 64 "$temporary_path" | grep -q 'age-encryption.org/v1'
chmod 600 "$temporary_path"

# Authentication and complete PostgreSQL archive processing must succeed while
# the ciphertext still has only its randomized temporary name.
BACKUP_AGE_IDENTITY_FILE="$identity_file" \
  scripts/verify-backup.sh "$temporary_path"

ln -- "$temporary_path" "$final_path"
final_created=1
rm -f "$temporary_path"
temporary_path=

checksum=$(sha256sum -- "$final_path" | awk '{print $1}')
checksum_temporary_path=$(mktemp \
  "$backup_dir/.calograph-$timestamp.sha256.XXXXXX.partial")
printf '%s  %s\n' "$checksum" "$(basename "$final_path")" \
  >"$checksum_temporary_path"
chmod 600 "$checksum_temporary_path"
ln -- "$checksum_temporary_path" "$checksum_path"
checksum_created=1
rm -f "$checksum_temporary_path"
checksum_temporary_path=

trap - EXIT HUP INT TERM

printf 'Encrypted backup created: %s\n' "$final_path"
printf 'Ciphertext checksum created: %s\n' "$checksum_path"
