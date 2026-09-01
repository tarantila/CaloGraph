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
checksum_path="$final_path.sha256"
temporary_path=
checksum_temporary_path=
final_published=false

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  rm -f -- "${temporary_path:-}" "${checksum_temporary_path:-}"
  if [[ "$final_published" == true ]]; then
    rm -f -- "$final_path" "$checksum_path"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

if ! command -v "$age_bin" >/dev/null 2>&1; then
  printf 'age is required to create encrypted secret backups.\n' >&2
  exit 1
fi
if [[ -z "$recipients_file" || ! -r "$recipients_file" || -L "$recipients_file" ]]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable public age recipients file.\n' >&2
  exit 1
fi
if ! awk 'NF && $1 !~ /^#/ && $1 !~ /^age1[[:alnum:]]+$/ { bad=1 } END { exit bad }' "$recipients_file"; then
  printf 'Recipients file must contain only public age recipients.\n' >&2
  exit 1
fi
if [[ ! -f "$environment_file" || -L "$environment_file" ]]; then
  printf 'SECRETS_SOURCE_FILE does not name a readable file.\n' >&2
  exit 1
fi
if [[ ! -d "$secrets_source_dir" || -L "$secrets_source_dir" ]]; then
  printf 'SECRETS_SOURCE_DIR does not name a readable directory.\n' >&2
  exit 1
fi
mkdir -p -- "$backup_dir"
if [[ -L "$backup_dir" || ! -d "$backup_dir" ]]; then
  printf 'BACKUP_DIR must be a directory.\n' >&2
  exit 1
fi
chmod 700 "$backup_dir"
if [[ -e "$final_path" || -L "$final_path" || -e "$checksum_path" || -L "$checksum_path" ]]; then
  printf 'A backup already exists for this timestamp.\n' >&2
  exit 1
fi
temporary_path=$(mktemp "$backup_dir/.calograph-secrets-$timestamp.XXXXXX.partial")
chmod 600 "$temporary_path"

environment_dir=$(CDPATH= cd -- "$(dirname -- "$environment_file")" && pwd)
environment_name=$(basename -- "$environment_file")
secrets_parent=$(CDPATH= cd -- "$(dirname -- "$secrets_source_dir")" && pwd)
secrets_name=$(basename -- "$secrets_source_dir")
# The source mounts are read-only. tar data is piped straight to age; values
# never enter shell output or a plaintext temporary file.
tar --create --file - --directory "$environment_dir" "$environment_name" \
  --directory "$secrets_parent" "$secrets_name" \
  | "$age_bin" --encrypt --recipients-file "$recipients_file" >"$temporary_path"
test -s "$temporary_path"
if ! dd if="$temporary_path" bs=1 count=64 2>/dev/null | LC_ALL=C grep -q 'age-encryption.org/v1'; then
  printf 'Encrypted secrets backup did not have a valid age header.\n' >&2
  exit 1
fi
if ! ln -- "$temporary_path" "$final_path"; then
  printf 'A backup already exists for this timestamp.\n' >&2
  exit 1
fi
rm -f -- "$temporary_path"
temporary_path=
final_published=true

checksum=$(sha256sum -- "$final_path" | cut -d ' ' -f1)
checksum_temporary_path=$(mktemp "$backup_dir/.calograph-secrets-$timestamp.sha256.XXXXXX.partial")
printf '%s  %s\n' "$checksum" "$(basename "$final_path")" >"$checksum_temporary_path"
chmod 600 "$checksum_temporary_path"
if ! ln -- "$checksum_temporary_path" "$checksum_path"; then
  printf 'A checksum already exists for this timestamp.\n' >&2
  exit 1
fi
rm -f -- "$checksum_temporary_path"
checksum_temporary_path=
final_published=false
trap - EXIT HUP INT TERM
printf 'Encrypted environment and secret-file backup created.\n'
printf 'Ciphertext checksum created.\n'
