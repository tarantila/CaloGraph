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

compose_files=(-f docker-compose.yml)
backup_agent_container=$(docker compose --profile backup ps -aq backup-agent 2>/dev/null || true)
backup_agent_environment=
if [[ -n "$backup_agent_container" ]]; then
  backup_agent_environment=$(docker inspect \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "$backup_agent_container" 2>/dev/null || true)
fi
if [[ -n "$backup_agent_container" ]] \
  && grep -Fqx 'BACKUP_INCLUDE_SECRETS=true' <<<"$backup_agent_environment"; then
  compose_files+=(-f docker-compose.backup-secrets.yml)
fi
compose() {
  docker compose "${compose_files[@]}" "$@"
}

running_services=$(compose --profile backup ps --status running --services 2>/dev/null || true)
backup_agent_running=false
case $'\n'"$running_services"$'\n' in *$'\nbackup-agent\n'*) backup_agent_running=true ;; esac

scripts/verify-backup.sh "$backup_file"
# Remove legacy orphans before the destructive restore so they cannot run during it.
compose up -d --no-recreate --remove-orphans postgres
compose --profile backup stop frontend backend yazio-scheduler backup-agent

if [[ "$backup_file" == *.age ]]; then
  "$age_bin" --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" "$backup_file" \
    | compose exec -T postgres pg_restore \
      --clean \
      --if-exists \
      --no-owner \
      --exit-on-error \
      --username="$database_user" \
      --dbname="$database_name"
else
  compose exec -T postgres pg_restore \
    --clean \
    --if-exists \
    --no-owner \
    --exit-on-error \
    --username="$database_user" \
    --dbname="$database_name" <"$backup_file"
fi

compose run --rm --no-deps backend alembic upgrade head
compose up -d --wait --remove-orphans
if [[ "$backup_agent_running" == true ]]; then
  compose --profile backup up -d --wait backup-agent
fi
compose ps
printf 'Restore completed. Verify login, data status, and the YAZIO connection.\n'
