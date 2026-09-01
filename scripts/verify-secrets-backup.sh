#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_file=${1:-}
age_bin=${AGE_BIN:-age}
identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
identity_mode=
if [[ -n "$identity_file" && -r "$identity_file" && ! -L "$identity_file" ]]; then
  identity_mode=$(stat -c '%a' -- "$identity_file" 2>/dev/null || true)
fi
if [[ -z "$identity_file" || ! -r "$identity_file" || -L "$identity_file" || ! "$identity_mode" =~ ^[0-7]00$ ]]; then
  printf 'BACKUP_AGE_IDENTITY_FILE must be readable only by its owner (no group/other permissions).\n' >&2
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
