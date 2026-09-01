#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${1:-}
age_bin=${AGE_BIN:-age}

if [[ -z "$backup_file" || ! -f "$backup_file" || -L "$backup_file" ]]; then
  printf 'Usage: %s /path/to/encrypted-backup.age\n' "$0" >&2
  exit 64
fi
if [[ "$(stat -c '%h' -- "$backup_file" 2>/dev/null || printf 2)" != 1 ]]; then
  printf 'Backup file must not be a hard link.\n' >&2
  exit 1
fi
backup_dir=$(CDPATH= cd -- "$(dirname -- "$backup_file")" && pwd)
backup_name=$(basename -- "$backup_file")
absolute_backup="$backup_dir/$backup_name"
checksum_file="$absolute_backup.sha256"

if [[ -f "$checksum_file" && ! -L "$checksum_file" ]]; then
  expected_checksum=$(cut -d ' ' -f1 <"$checksum_file")
  if [[ ! "$expected_checksum" =~ ^[[:xdigit:]]{64}$ ]]; then
    printf 'Backup checksum file has an invalid format.\n' >&2
    exit 1
  fi
  actual_checksum=$(sha256sum -- "$absolute_backup" | cut -d ' ' -f1)
  if [[ "$actual_checksum" != "$expected_checksum" ]]; then
    printf 'Backup checksum verification failed.\n' >&2
    exit 1
  fi
  printf 'Ciphertext checksum verified.\n'
else
  printf 'Backup checksum file is missing.\n' >&2
  exit 1
fi

identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
identity_mode=
if [[ -n "$identity_file" && -r "$identity_file" && ! -L "$identity_file" ]]; then
  identity_mode=$(stat -c '%a' -- "$identity_file" 2>/dev/null || true)
fi
if [[ -z "$identity_file" || ! -r "$identity_file" || -L "$identity_file" || ! "$identity_mode" =~ ^[0-7]00$ ]]; then
  printf 'BACKUP_AGE_IDENTITY_FILE must be readable only by its owner (no group/other permissions).\n' >&2
  exit 1
fi
if [[ ! -f "$absolute_backup" || "${absolute_backup##*.}" != age ]]; then
  printf 'Only encrypted .age backups can be verified.\n' >&2
  exit 1
fi
if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to verify encrypted backups.\n' >&2
  exit 1
fi
cd "$project_root"
# Authentication and complete archive processing happen only in this
# externally-keyed workflow; the backup agent has no identity key.
if ! "$age_bin" --decrypt --identity "$identity_file" "$absolute_backup" 2>/dev/null \
  | docker compose exec -T postgres pg_restore --file=/dev/null --no-owner 2>/dev/null; then
  printf 'Encrypted backup authentication or PostgreSQL archive processing failed.\n' >&2
  exit 1
fi
if [[ -n "${BACKUP_DATABASE_VERIFICATION_STATUS_FILE:-}" ]]; then
  verification_dir=$(dirname -- "$BACKUP_DATABASE_VERIFICATION_STATUS_FILE")
  mkdir -p -- "$verification_dir"
  verification_tmp=$(mktemp "$verification_dir/.restore-verification.XXXXXX.partial")
  chmod 600 "$verification_tmp"
  printf '{"schema_version":1,"target":"calograph","result":"RESTORE_VERIFIED","component":"database","artifact":"%s","sha256":"%s","verified_at":"%s"}\n' \
    "$backup_name" "$expected_checksum" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$verification_tmp"
  mv -f -- "$verification_tmp" "$BACKUP_DATABASE_VERIFICATION_STATUS_FILE"
fi
printf 'Encrypted backup authenticated and fully processed.\n'
