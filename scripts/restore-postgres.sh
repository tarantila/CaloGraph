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

backup_agent_container=$(docker compose -f "$project_root/docker-compose.yml" --profile backup ps -aq backup-agent)
backup_agent_running=false
backup_agent_secrets=false
if [[ -n "$backup_agent_container" ]]; then
  backup_agent_inspection=$(docker inspect "$backup_agent_container")
  if ! backup_agent_state=$(BACKUP_AGENT_INSPECTION="$backup_agent_inspection" python3 - <<'PY'
import json
import os
import sys

try:
    containers = json.loads(os.environ['BACKUP_AGENT_INSPECTION'])
    if len(containers) != 1:
        raise ValueError('ambiguous backup agent')
    container = containers[0]
    environment = dict(item.split('=', 1) for item in container['Config']['Env'])
    secret_mode = environment.get('BACKUP_INCLUDE_SECRETS') == 'true'
    print('true' if container['State']['Running'] else 'false')
    print('true' if secret_mode else 'false')
    if secret_mode:
        for key, target in [
            ('SECRETS_SOURCE_FILE', '/backup-input/environment.env'),
            ('SECRETS_SOURCE_DIR', '/backup-input/secrets'),
        ]:
            if environment.get(key) != target:
                raise ValueError('unexpected backup source configuration')
            mounts = [mount for mount in container['Mounts'] if mount['Destination'] == target]
            if len(mounts) != 1:
                raise ValueError('missing or ambiguous backup source')
            mount = mounts[0]
            source = mount['Source']
            if (mount['Type'] != 'bind' or mount['RW'] is not False
                    or not source.startswith('/') or '\\n' in source or '\\r' in source):
                raise ValueError('unsafe backup source')
            print(source)
        if any(mount['Destination'].startswith('/backup-input/')
               and mount['Destination'] not in {'/backup-input/environment.env', '/backup-input/secrets'}
               for mount in container['Mounts']):
            raise ValueError('unexpected backup source mount')
except (KeyError, TypeError, ValueError):
    print('Restore refused: backup-agent secret source mounts cannot be verified.', file=sys.stderr)
    sys.exit(1)
PY
  ); then
    exit 1
  fi
  mapfile -t backup_agent_values <<<"$backup_agent_state"
  backup_agent_running=${backup_agent_values[0]}
  backup_agent_secrets=${backup_agent_values[1]}
  if [[ "$backup_agent_secrets" == true ]]; then
    # Container inspection, not today's dotenv defaults, is authoritative.
    export BACKUP_SECRETS_SOURCE_FILE=${backup_agent_values[2]}
    export BACKUP_SECRETS_SOURCE_DIR=${backup_agent_values[3]}
  fi
fi
compose() {
  if [[ "$backup_agent_secrets" == true ]]; then
    "$project_root/scripts/backup-compose.sh" "$@"
  else
    docker compose -f "$project_root/docker-compose.yml" "$@"
  fi
}
# Validate retained source paths before stopping services or changing data.
compose --profile backup config --quiet

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
