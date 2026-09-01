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
if [[ -n "${BACKUP_SECRETS_VERIFICATION_STATUS_FILE:-}" ]]; then
  verification_dir=$(dirname -- "$BACKUP_SECRETS_VERIFICATION_STATUS_FILE")
  mkdir -p -- "$verification_dir"
  verification_tmp=$(mktemp "$verification_dir/.restore-verification.XXXXXX.partial")
  chmod 600 "$verification_tmp"
  printf '{"schema_version":1,"target":"calograph","result":"RESTORE_VERIFIED","component":"environment_secrets","artifact":"%s","sha256":"%s","verified_at":"%s"}\n' \
    "$(basename -- "$backup_file")" "$expected_checksum" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$verification_tmp"
  mv -f -- "$verification_tmp" "$BACKUP_SECRETS_VERIFICATION_STATUS_FILE"
fi
printf 'Encrypted secrets archive authenticated and fully processed.\n'
