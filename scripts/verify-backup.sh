#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${1:-}
age_bin=${AGE_BIN:-age}

if [[ -z "$backup_file" || ! -f "$backup_file" ]]; then
  printf 'Usage: %s /path/to/calograph.dump[.age]\n' "$0" >&2
  exit 1
fi

backup_dir=$(CDPATH= cd -- "$(dirname -- "$backup_file")" && pwd)
backup_name=$(basename "$backup_file")
absolute_backup="$backup_dir/$backup_name"
checksum_file="$absolute_backup.sha256"

if [[ -f "$checksum_file" ]]; then
  expected_checksum=$(awk 'NR == 1 { print $1 }' "$checksum_file")
  if [[ ! "$expected_checksum" =~ ^[[:xdigit:]]{64}$ ]]; then
    printf 'Backup checksum file has an invalid format.\n' >&2
    exit 1
  fi
  actual_checksum=$(sha256sum -- "$absolute_backup" | awk '{print $1}')
  if [[ "$actual_checksum" != "$expected_checksum" ]]; then
    printf 'Backup checksum verification failed.\n' >&2
    exit 1
  fi
  printf 'Ciphertext checksum verified.\n'
else
  printf 'Warning: no SHA-256 file found; only format/authentication will be checked.\n' >&2
fi

cd "$project_root"
if [[ "$absolute_backup" == *.age ]]; then
  identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
  if ! command -v "$age_bin" >/dev/null 2>&1; then
    printf 'age is required to verify encrypted backups.\n' >&2
    exit 1
  fi
  if [[ -z "$identity_file" || ! -r "$identity_file" ]]; then
    printf 'BACKUP_AGE_IDENTITY_FILE must name a readable age identity.\n' >&2
    exit 1
  fi
  "$age_bin" --decrypt --identity "$identity_file" "$absolute_backup" \
    | docker compose exec -T postgres pg_restore --list >/dev/null
  printf 'Backup is authenticated and structurally readable: %s\n' \
    "$absolute_backup"
else
  printf 'Warning: verifying a legacy unencrypted database dump.\n' >&2
  docker compose exec -T postgres pg_restore --list \
    <"$absolute_backup" >/dev/null
  printf 'Legacy backup is structurally readable but unencrypted: %s\n' \
    "$absolute_backup"
fi
