#!/usr/bin/env bash
set -euo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_file=${BACKUP_FILE:-${1:-}}
database_name=${POSTGRES_DB:-calograph}
database_user=${POSTGRES_USER:-calograph}
age_bin=${AGE_BIN:-age}

if [[ -z "$backup_file" || ! -f "$backup_file" ]]; then
  printf 'Usage: CONFIRM_RESTORE=calograph %s /path/to/calograph.dump[.age]\n' "$0" >&2
  exit 1
fi
if [[ "${CONFIRM_RESTORE:-}" != "calograph" ]]; then
  printf 'Restore cancelled. Set CONFIRM_RESTORE=calograph for this destructive operation.\n' >&2
  exit 1
fi

cd "$project_root"
running_services=$(docker compose --profile backup --profile backup-secrets ps --status running --services 2>/dev/null || true)
backup_agent_running=false
backup_agent_secrets_running=false
case $'\n'"$running_services"$'\n' in *$'\nbackup-agent\n'*) backup_agent_running=true ;; esac
case $'\n'"$running_services"$'\n' in *$'\nbackup-agent-secrets\n'*) backup_agent_secrets_running=true ;; esac
scripts/verify-backup.sh "$backup_file"
docker compose --profile backup --profile backup-secrets stop frontend backend yazio-scheduler backup-agent backup-agent-secrets

if [[ "$backup_file" == *.age ]]; then
  "$age_bin" --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" "$backup_file" \
    | docker compose exec -T postgres pg_restore \
      --clean \
      --if-exists \
      --no-owner \
      --exit-on-error \
      --username="$database_user" \
      --dbname="$database_name"
else
  docker compose exec -T postgres pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --exit-on-error \
    --username="$database_user" \
    --dbname="$database_name" <"$backup_file"
fi

docker compose run --rm --no-deps backend alembic upgrade head
docker compose up -d --wait
if [[ "$backup_agent_running" == true ]]; then
  docker compose --profile backup up -d --wait backup-agent
fi
if [[ "$backup_agent_secrets_running" == true ]]; then
  docker compose --profile backup-secrets up -d --wait backup-agent-secrets
fi
docker compose ps
printf 'Restore completed. Verify login, data status, and the YAZIO connection.\n'
