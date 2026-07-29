#!/usr/bin/env bash
set -euo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source_backup=${1:-}
recipients_file=${BACKUP_AGE_RECIPIENTS_FILE:-}
age_bin=${AGE_BIN:-age}

if [[ -z "$source_backup" || ! -f "$source_backup" || "$source_backup" == *.age ]]; then
  printf 'Usage: %s /path/to/legacy-calograph.dump\n' "$0" >&2
  exit 1
fi
if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to encrypt existing backups.\n' >&2
  exit 1
fi
if [[ -z "$recipients_file" || ! -r "$recipients_file" ]]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable age recipients file.\n' >&2
  exit 1
fi

source_dir=$(CDPATH= cd -- "$(dirname -- "$source_backup")" && pwd)
source_name=$(basename "$source_backup")
absolute_source="$source_dir/$source_name"
final_path="$absolute_source.age"
if [[ -e "$final_path" || -e "$final_path.sha256" ]]; then
  printf 'Encrypted destination already exists; nothing was changed.\n' >&2
  exit 1
fi

cd "$project_root"
scripts/verify-backup.sh "$absolute_source"

temporary_path=$(mktemp "$source_dir/.$source_name.XXXXXX.partial")
trap 'rm -f "$temporary_path"' EXIT HUP INT TERM
"$age_bin" --encrypt --recipients-file "$recipients_file" \
  <"$absolute_source" >"$temporary_path"
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

printf 'Encrypted copy created: %s\n' "$final_path"
printf 'The original plaintext backup was not modified or deleted.\n'
