#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s /path/to/calograph-backup.dump.age\n' "$0"
  printf '\nOperator-only isolated restore test. The dump is decrypted only into pg_restore.\n'
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then usage; exit 0; fi
backup_file=${1:-}
if [[ -z "$backup_file" || "$backup_file" == -* ]]; then usage >&2; exit 64; fi

backup_name=$(basename -- "$backup_file")
if [[ "$backup_file" != "$backup_name" && "$backup_file" != */"$backup_name" ]]; then
  printf 'Backup path is invalid.\n' >&2
  exit 64
fi
if [[ ! "$backup_name" =~ ^calograph-[A-Za-z0-9._-]+\.dump\.age$ ]]; then
  printf 'Only a safe CaloGraph .dump.age filename is accepted.\n' >&2
  exit 1
fi
if [[ ! -f "$backup_file" || -L "$backup_file" ]]; then
  printf 'Backup must be a regular file and not a symlink.\n' >&2
  exit 1
fi
if [[ "$(stat -c '%h' -- "$backup_file" 2>/dev/null || printf 2)" != 1 ]]; then
  printf 'Backup file must not be a hard link.\n' >&2
  exit 1
fi
backup_dir=$(CDPATH= cd -- "$(dirname -- "$backup_file")" && pwd)
absolute_backup="$backup_dir/$backup_name"
checksum_file="$absolute_backup.sha256"
if [[ ! -f "$checksum_file" || -L "$checksum_file" ]]; then
  printf 'Adjacent backup checksum is required.\n' >&2
  exit 1
fi
if [[ "$(stat -c '%h' -- "$checksum_file" 2>/dev/null || printf 2)" != 1 ]]; then
  printf 'Checksum file must not be a hard link.\n' >&2
  exit 1
fi
if [[ "$(stat -c '%s' -- "$checksum_file" 2>/dev/null || printf 4097)" -gt 4096 ]]; then
  printf 'Checksum file is too large.\n' >&2
  exit 1
fi
checksum_line=$(tr -d '\r' <"$checksum_file")
if [[ "$checksum_line" =~ ^([[:xdigit:]]{64})[[:space:]]+\*?([^[:space:]]+)[[:space:]]*$ ]]; then
  expected_checksum=${BASH_REMATCH[1],,}
  [[ "${BASH_REMATCH[2]}" == "$backup_name" ]] || { printf 'Checksum filename does not match the backup.\n' >&2; exit 1; }
elif [[ "$checksum_line" =~ ^[[:xdigit:]]{64}[[:space:]]*$ ]]; then
  expected_checksum=${checksum_line:0:64}
  expected_checksum=${expected_checksum,,}
else
  printf 'Backup checksum file has an invalid format.\n' >&2
  exit 1
fi
# Check the ciphertext before invoking age or any PostgreSQL process.
actual_checksum=$(sha256sum -- "$absolute_backup" | cut -d ' ' -f1)
[[ "$actual_checksum" == "$expected_checksum" ]] || { printf 'Backup checksum verification failed.\n' >&2; exit 1; }
printf 'Ciphertext checksum verified.\n'

identity_file=${BACKUP_AGE_IDENTITY_FILE:-}
identity_mode=
if [[ -n "$identity_file" && -r "$identity_file" && ! -L "$identity_file" ]]; then
  identity_mode=$(stat -c '%a' -- "$identity_file" 2>/dev/null || true)
fi
if [[ -z "$identity_file" || ! -f "$identity_file" || -L "$identity_file" || ! "$identity_mode" =~ ^[0-7]00$ ]]; then
  printf 'BACKUP_AGE_IDENTITY_FILE must be a readable owner-only regular file.\n' >&2
  exit 1
fi
if [[ "$(stat -c '%h' -- "$identity_file" 2>/dev/null || printf 2)" != 1 ]]; then
  printf 'Age identity must not be a hard link.\n' >&2
  exit 1
fi
age_bin=${AGE_BIN:-age}
docker_bin=${DOCKER_BIN:-docker}
command -v "$age_bin" >/dev/null 2>&1 || { printf 'age is required.\n' >&2; exit 1; }
command -v "$docker_bin" >/dev/null 2>&1 || { printf 'docker is required.\n' >&2; exit 1; }

image=${BACKUP_RESTORE_POSTGRES_IMAGE:-postgres:18.4-alpine}
[[ "$image" =~ ^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$ ]] || { printf 'PostgreSQL image name is invalid.\n' >&2; exit 64; }
postgres_major=${BACKUP_RESTORE_POSTGRES_MAJOR:-18}
[[ "$postgres_major" =~ ^[0-9]{2}$ && "$postgres_major" -ge 10 && "$postgres_major" -le 99 ]] || { printf 'PostgreSQL major is invalid.\n' >&2; exit 64; }
status_file=${BACKUP_RESTORE_TEST_STATUS_FILE:-/var/lib/calograph-backups/status/restore-test.json}
[[ "$status_file" == /* && "$status_file" != *..* && "$status_file" =~ ^/[A-Za-z0-9._/-]+$ ]] || { printf 'Restore-test status path is invalid.\n' >&2; exit 64; }

suffix=$(od -An -N8 -tu8 /dev/urandom | tr -d ' ')
project="calograph-restore-test-${suffix:-$$}"
container_name="${project}-postgres"
network_name="${project}-network"
volume_name="${project}-data"
password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
result=RESTORE_TEST_FAILED
failure_code=archive_restore_failed
cleanup_done=false

cleanup() {
  local rc=0
  "$docker_bin" rm -f "$container_name" >/dev/null 2>&1 || rc=1
  "$docker_bin" network rm "$network_name" >/dev/null 2>&1 || rc=1
  "$docker_bin" volume rm "$volume_name" >/dev/null 2>&1 || rc=1
  cleanup_done=true
  return "$rc"
}
trap 'if [[ "$cleanup_done" != true ]]; then cleanup >/dev/null 2>&1 || true; fi; if [[ -n "${status_tmp:-}" ]]; then rm -f -- "$status_tmp" >/dev/null 2>&1 || true; fi' EXIT HUP INT TERM

# This workflow intentionally uses docker run, never Compose and never the
# normal postgres-data volume.  The temporary network/volume are disposable.
if ! "$docker_bin" volume create "$volume_name" >/dev/null 2>&1; then
  failure_code=cleanup_failed
else
  if ! "$docker_bin" network create "$network_name" >/dev/null 2>&1; then
    failure_code=cleanup_failed
  elif ! "$docker_bin" run -d --name "$container_name" --network "$network_name" \
      -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=calograph \
      -v "${volume_name}:/var/lib/postgresql" "$image" >/dev/null 2>&1; then
    failure_code=cleanup_failed
  else
    ready=false
    stable_checks=0
    for _ in {1..60}; do
      if "$docker_bin" exec "$container_name" pg_isready -U postgres -d calograph >/dev/null 2>&1 \
        && "$docker_bin" exec "$container_name" psql --username=postgres -X -v ON_ERROR_STOP=1 --dbname=calograph -qAt -c "SELECT 1;" >/dev/null 2>&1; then
        stable_checks=$((stable_checks + 1))
      else
        stable_checks=0
      fi
      if (( stable_checks >= 3 )); then ready=true; break; fi
      sleep 1
    done
    if [[ "$ready" != true ]]; then
      failure_code=database_unreachable
    elif ! "$age_bin" --decrypt --identity "$identity_file" "$absolute_backup" 2>/dev/null \
        | "$docker_bin" exec -i -e PGPASSWORD="$password" "$container_name" pg_restore --username=postgres --dbname=calograph --no-owner --no-acl --exit-on-error >/dev/null 2>&1; then
      failure_code=archive_restore_failed
    elif ! "$docker_bin" exec -e PGPASSWORD="$password" "$container_name" psql --username=postgres -X -v ON_ERROR_STOP=1 --dbname=calograph \
        -qAt -c "SELECT 1 WHERE to_regclass('public.alembic_version') IS NOT NULL AND to_regclass('public.users') IS NOT NULL;" >/dev/null 2>&1; then
      failure_code=schema_check_failed
    elif ! "$docker_bin" exec -e PGPASSWORD="$password" "$container_name" psql --username=postgres -X -v ON_ERROR_STOP=1 --dbname=calograph \
        -qAt -c "SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM public.users WHERE id IS NULL);" >/dev/null 2>&1; then
      failure_code=consistency_check_failed
    else
      result=RESTORE_TESTED
    fi
  fi
fi

# Cleanup must complete before either success or failure evidence is handed off.
if ! cleanup; then
  result=RESTORE_TEST_FAILED
  failure_code=cleanup_failed
fi

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
status_dir=$(dirname -- "$status_file")
status_tmp=
write_record() {
  local payload=$1
  if [[ -e "$status_file" && -L "$status_file" ]]; then
    return 1
  fi
  if [[ "$status_file" != /var/lib/calograph-backups/status/restore-test.json || "${BACKUP_RESTORE_TEST_HOST_MODE:-false}" == true ]] \
      && mkdir -p -- "$status_dir" 2>/dev/null && [[ -w "$status_dir" || -w "$status_file" ]]; then
    status_tmp=$(mktemp "$status_dir/.restore-test.XXXXXX.partial")
    chmod 600 "$status_tmp"
    printf '%s\n' "$payload" >"$status_tmp"
    mv -f -- "$status_tmp" "$status_file"
    return 0
  fi
  # Named Compose status volumes are handed off to backup-agent.  No private
  # identity is mounted into that service; only this sanitized JSON is sent.
  local quoted_status
  printf -v quoted_status '%q' "$status_file"
  printf '%s\n' "$payload" | "$docker_bin" compose exec -T --user calograph-backup backup-agent \
    sh -c "status=$quoted_status; dir=\$(dirname -- \"\$status\"); tmp=\$(mktemp \"\$dir/.restore-test.XXXXXX.partial\") || exit 1; chmod 600 \"\$tmp\"; cat >\"\$tmp\"; mv -f -- \"\$tmp\" \"\$status\"" >/dev/null 2>&1
}
read_previous_status() {
  local quoted_status
  if [[ "$status_file" == /var/lib/calograph-backups/status/restore-test.json && "${BACKUP_RESTORE_TEST_HOST_MODE:-false}" != true ]]; then
    printf -v quoted_status '%q' "$status_file"
    "$docker_bin" compose exec -T --user calograph-backup backup-agent \
      sh -c "cat -- $quoted_status" 2>/dev/null || true
  elif [[ -f "$status_file" && ! -L "$status_file" ]]; then
    cat -- "$status_file" 2>/dev/null || true
  fi
}

previous_record=$(read_previous_status)


if [[ "$result" == RESTORE_TESTED ]]; then
  payload=$(printf '{"schema_version":1,"result":"RESTORE_TESTED","tested_at":"%s","artifact":"%s","sha256":"%s","postgres_major":%s}' \
    "$now" "$backup_name" "$expected_checksum" "$postgres_major")
else
  # Preserve a previously successful sanitized record when one is available.
  previous_at= previous_artifact= previous_sha=
  if [[ -n "$previous_record" ]]; then
    previous_result=$(printf '%s' "$previous_record" | sed -n 's/.*"result":"\([^"]*\)".*/\1/p' | tr -d '\r\n' || true)
    if [[ "$previous_result" == RESTORE_TESTED ]]; then
      previous_at=$(printf '%s' "$previous_record" | sed -n 's/.*"tested_at":"\([^"]*\)".*/\1/p' | tr -d '\r\n' || true)
      previous_artifact=$(printf '%s' "$previous_record" | sed -n 's/.*"artifact":"\([^"]*\)".*/\1/p' | tr -d '\r\n' || true)
      previous_sha=$(printf '%s' "$previous_record" | sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p' | tr -d '\r\n' || true)
      [[ "$previous_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ && "$previous_artifact" =~ ^calograph-[A-Za-z0-9._-]+\.dump\.age$ && "$previous_sha" =~ ^[[:xdigit:]]{64}$ ]] || previous_at=
    fi
  fi
  payload=$(printf '{"schema_version":1,"result":"RESTORE_TEST_FAILED","tested_at":"%s","artifact":"%s","sha256":"%s","postgres_major":%s,"failure_code":"%s"' \
    "$now" "$backup_name" "$expected_checksum" "$postgres_major" "$failure_code")
  if [[ -n "$previous_at" ]]; then
    payload+=$(printf ',"last_success_at":"%s","last_success_artifact":"%s","last_success_sha256":"%s"' "$previous_at" "$previous_artifact" "$previous_sha")
  fi
  payload+='}'
fi
if ! write_record "$payload"; then
  printf 'Restore-test status handoff failed.\n' >&2
  exit 1
fi
if [[ "$result" == RESTORE_TESTED ]]; then
  printf 'Isolated restore test passed; sanitized status recorded. No live database was used.\n'
  exit 0
fi
printf 'Isolated restore test failed (%s); sanitized status recorded.\n' "$failure_code" >&2
exit 1
