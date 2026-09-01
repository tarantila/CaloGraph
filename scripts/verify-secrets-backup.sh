#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_file=${1:-}
age_bin=${AGE_BIN:-age}
identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
if [[ -z "$backup_file" || ! -f "$backup_file" || -L "$backup_file" ]]; then
  printf 'Usage: %s /path/to/calograph-secrets-TIMESTAMP.tar.age\n' "$0" >&2
  exit 64
fi
if [[ -z "$identity_file" || ! -r "$identity_file" || -L "$identity_file" ]]; then
  printf 'BACKUP_AGE_IDENTITY_FILE must name a readable external age identity.\n' >&2
  exit 1
fi
checksum_file="$backup_file.sha256"
if [[ ! -f "$checksum_file" || -L "$checksum_file" ]]; then
  printf 'Backup checksum file is missing.\n' >&2
  exit 1
fi
expected_checksum=$(cut -d ' ' -f1 <"$checksum_file")
if [[ ! "$expected_checksum" =~ ^[[:xdigit:]]{64}$ ]] \
  || [[ "$(sha256sum -- "$backup_file" | cut -d ' ' -f1)" != "$expected_checksum" ]]; then
  printf 'Backup checksum verification failed.\n' >&2
  exit 1
fi
if ! command -v "$age_bin" >/dev/null 2>&1 \
  || ! "$age_bin" --decrypt --identity "$identity_file" "$backup_file" 2>/dev/null \
    | tar --list --file - >/dev/null 2>/dev/null; then
  printf 'Encrypted secrets archive authentication or processing failed.\n' >&2
  exit 1
fi
printf 'Encrypted secrets archive authenticated and fully processed.\n'
