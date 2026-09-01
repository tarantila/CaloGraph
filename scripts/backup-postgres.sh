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
  printf 'age is required to create encrypted backups.\n' >&2
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
temporary_path=$(mktemp "$backup_dir/.calograph-$timestamp.XXXXXX.partial")
chmod 600 "$temporary_path"

cd "$project_root"
# In the isolated agent, connect directly over the PostgreSQL data network.
# Host-side invocation retains the existing docker compose convenience.
if [[ -n "${BACKUP_PGHOST:-}" ]]; then
  if [[ -n "${PGPASSWORD_FILE:-}" ]]; then
    [[ -r "$PGPASSWORD_FILE" && ! -L "$PGPASSWORD_FILE" ]] || { printf 'Database password source is unavailable.\\n' >&2; exit 1; }
    PGPASSWORD=$(cat -- "$PGPASSWORD_FILE")
    export PGPASSWORD
  fi
  dump_command=(pg_dump --host "$BACKUP_PGHOST" --port "${PGPORT:-5432}")
else
  dump_command=(docker compose exec -T postgres pg_dump)
fi
# Create custom format and stream it directly into age. No plaintext dump is
# ever written to disk; pipefail prevents publication if either process fails.
"${dump_command[@]}" \
  --username "$database_user" \
  --dbname "$database_name" \
  --format custom \
  --no-owner \
  | "$age_bin" --encrypt --recipients-file "$recipients_file" >"$temporary_path"
if ! dd if="$temporary_path" bs=1 count=64 2>/dev/null | LC_ALL=C grep -q 'age-encryption.org/v1'; then
  printf 'Encrypted backup did not have a valid age header.\n' >&2
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
checksum_temporary_path=$(mktemp "$backup_dir/.calograph-$timestamp.sha256.XXXXXX.partial")
printf '%s  %s\n' "$checksum" "$(basename "$final_path")" >"$checksum_temporary_path"
chmod 600 "$checksum_temporary_path"
if ! ln -- "$checksum_temporary_path" "$checksum_path"; then
  printf 'A checksum already exists for this timestamp.\n' >&2
  exit 1
fi
rm -f -- "$checksum_temporary_path"
checksum_temporary_path=
final_published=false
printf 'Encrypted backup created.\n'
printf 'Ciphertext checksum created.\n'
