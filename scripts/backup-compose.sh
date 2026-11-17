#!/usr/bin/env bash
# Explicit opt-in to the production backup agent's environment/secrets archive.
set -Eeuo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
source_root=$project_root
compose_options=()
arguments=("$@")
fail() { printf 'Backup Compose: %s\n' "$1" >&2; exit 1; }
[[ $# -gt 0 ]] || fail 'Pass Docker Compose options and a command.'
command -v python3 >/dev/null 2>&1 || fail 'python3 is required.'

# Only global options are used to resolve Compose's interpolation environment.
# The caller cannot inject an additional definition or an include/override file.
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file|--file=*|-f?*) fail 'Additional Compose definitions are not allowed.' ;;
    --env-file|--profile|--project-name|-p|--parallel|--ansi|--progress|--project-directory)
      [[ $# -ge 2 ]] || fail 'A Compose option is missing its value.'
      [[ "$1" != --project-directory ]] || source_root=$2
      compose_options+=("$1" "$2"); shift 2 ;;
    --project-directory=*) source_root=${1#*=}; compose_options+=("$1"); shift ;;
    --env-file=*|--profile=*|--project-name=*|--parallel=*|--ansi=*|--progress=*|-p?*|--compatibility|--all-resources)
      compose_options+=("$1"); shift ;;
    -*) fail 'Unsupported global Compose option.' ;;
    *) break ;;
  esac
done
[[ $# -gt 0 ]] || fail 'A Docker Compose command is required.'
unset COMPOSE_FILE

temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/calograph-backup-compose.XXXXXX")
override="$temporary_root/secrets.json"
child=
cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ -n "$child" ]]; then
    kill "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  rm -rf -- "$temporary_root"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Never shell-source dotenv files. Compose itself applies shell/--env-file/.env
# precedence; capture privately and retain only the two path values we need.
source_file=${BACKUP_SECRETS_SOURCE_FILE-}
source_dir=${BACKUP_SECRETS_SOURCE_DIR-}
if [[ ! ${BACKUP_SECRETS_SOURCE_FILE+x} || ! ${BACKUP_SECRETS_SOURCE_DIR+x} ]]; then
  docker compose -f "$project_root/docker-compose.yml" "${compose_options[@]}" \
    config --environment >"$temporary_root/environment"
  while IFS='=' read -r name value; do
    case "$name" in
      BACKUP_SECRETS_SOURCE_FILE) [[ ${BACKUP_SECRETS_SOURCE_FILE+x} ]] || source_file=$value ;;
      BACKUP_SECRETS_SOURCE_DIR) [[ ${BACKUP_SECRETS_SOURCE_DIR+x} ]] || source_dir=$value ;;
    esac
  done <"$temporary_root/environment"
  rm -f -- "$temporary_root/environment"
fi
source_file=${source_file:-./.env}
source_dir=${source_dir:-./secrets}
python3 - "$source_root" "$source_file" "$source_dir" "$override" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys


def checked_path(value, directory):
    if '\n' in value or '\r' in value:
        raise ValueError('Source paths must not contain newlines.')
    # Check every traversed component before normalizing '..': even a symlink
    # followed by '..' must not bypass the no-symlinks boundary.
    path = Path(value)
    if not path.is_absolute():
        path = Path(sys.argv[1]).absolute() / path
    cursor = Path(path.anchor)
    for component in path.parts[1:]:
        cursor = cursor / component
        if stat.S_ISLNK(cursor.lstat().st_mode):
            raise ValueError('Source paths must not traverse symlinks.')
    mode = cursor.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError('Source file/directory has the wrong type.')
    if not os.access(cursor, os.R_OK):
        raise ValueError('Source file/directory is not readable.')
    # Compose uses $$ to preserve literal dollar signs in bind-source paths.
    return str(cursor.resolve(strict=True)).replace('$', '$$')


try:
    mounts = []
    for value, target, directory in [
        (sys.argv[2], '/backup-input/environment.env', False),
        (sys.argv[3], '/backup-input/secrets', True),
    ]:
        mounts.append({'type': 'bind', 'source': checked_path(value, directory),
                       'target': target, 'read_only': True,
                       'bind': {'create_host_path': False}})
    document = {'services': {'backup-agent': {
        'environment': {'BACKUP_INCLUDE_SECRETS': 'true',
                        'SECRETS_SOURCE_FILE': '/backup-input/environment.env',
                        'SECRETS_SOURCE_DIR': '/backup-input/secrets'},
        'volumes': mounts,
    }}}
    with open(sys.argv[4], 'x', encoding='utf-8') as stream:
        json.dump(document, stream)
except (OSError, ValueError):
    print('Backup Compose: expected readable non-symlink source file and directory.', file=sys.stderr)
    sys.exit(1)
PY

# Run in the background so signals interrupt wait and trigger private cleanup.
# Explicit stdin forwarding preserves streams used by exec/run and restore.
docker compose -f "$project_root/docker-compose.yml" -f "$override" "${arguments[@]}" <&0 &
child=$!
status=0
wait "$child" || status=$?
child=
exit "$status"
