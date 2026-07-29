#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}
environment_file=${SECRETS_SOURCE_FILE:-"$project_root/.env"}
secrets_source_dir=${SECRETS_SOURCE_DIR:-"$project_root/secrets"}
recipients_file=${BACKUP_AGE_RECIPIENTS_FILE:-}
age_bin=${AGE_BIN:-age}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
final_path="$backup_dir/calograph-secrets-$timestamp.tar.age"

if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to create encrypted secret backups.\n' >&2
  exit 1
fi
if [[ -z "$recipients_file" || ! -r "$recipients_file" ]]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable age recipients file.\n' >&2
  exit 1
fi
if [[ ! -f "$environment_file" ]]; then
  printf 'SECRETS_SOURCE_FILE does not name a readable file.\n' >&2
  exit 1
fi
if [[ ! -d "$secrets_source_dir" ]]; then
  printf 'SECRETS_SOURCE_DIR does not name a readable directory.\n' >&2
  exit 1
fi

mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
temporary_path=$(mktemp "$backup_dir/.calograph-secrets-$timestamp.XXXXXX.partial")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM

environment_dir=$(CDPATH= cd -- "$(dirname -- "$environment_file")" && pwd)
environment_name=$(basename "$environment_file")
secrets_parent=$(CDPATH= cd -- "$(dirname -- "$secrets_source_dir")" && pwd)
secrets_name=$(basename "$secrets_source_dir")
tar --create --file - \
  --directory "$environment_dir" "$environment_name" \
  --directory "$secrets_parent" "$secrets_name" \
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
  if [[ ! -r "$BACKUP_AGE_IDENTITY_FILE" ]]; then
    printf 'BACKUP_AGE_IDENTITY_FILE must name a readable age identity.\n' >&2
    exit 1
  fi
  "$age_bin" --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" \
    "$final_path" | tar --list --file - >/dev/null
fi

printf 'Encrypted environment and secret-file backup created: %s\n' "$final_path"
printf 'Ciphertext checksum created: %s\n' "$final_path.sha256"
