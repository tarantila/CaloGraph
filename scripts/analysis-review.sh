#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
compose_file="$project_root/docker-compose.analysis-review.yml"
project_name=calograph-analysis-v2-review
database_name=calograph_analysis_v2_test
volume_name=calograph-analysis-v2-review-postgres-data
demo_username=calograph-analysis-demo

state_home=${XDG_DATA_HOME:-${HOME:?HOME must be set}/.local/share}
secrets_dir=$(realpath -m -- "$state_home/calograph/analysis-review/secrets")

unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES COMPOSE_PROFILES

fail() {
  printf 'Analysis-Review: %s\n' "$1" >&2
  exit 1
}

compose() {
  ANALYSIS_REVIEW_SECRETS_DIR="$secrets_dir" docker compose \
    --project-directory "$project_root" \
    --env-file /dev/null \
    --project-name "$project_name" \
    --file "$compose_file" \
    "$@"
}

check_docker() {
  command -v docker >/dev/null 2>&1 || fail 'Docker ist nicht installiert.'
  docker info >/dev/null 2>&1 || fail 'Docker ist nicht erreichbar.'
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 ist erforderlich.'
}

check_secret_files() {
  for secret in postgres_password session_secret rate_limit_secret credential_encryption_key mfa_encryption_key; do
    if [ ! -f "$secrets_dir/$secret" ] || [ ! -s "$secrets_dir/$secret" ] || [ ! -r "$secrets_dir/$secret" ]; then
      fail "Review-Secret fehlt oder ist nicht lesbar: $secrets_dir/$secret"
    fi
  done
}

check_secret_path() {
  case "$secrets_dir" in
    /tmp|/tmp/*)
      fail 'Review-Secrets dürfen nicht unter /tmp liegen.'
      ;;
    "$project_root"|"$project_root"/*)
      fail 'Review-Secrets dürfen nicht im Repository liegen.'
      ;;
  esac
}

check_volume_owner() {
  if docker volume inspect "$volume_name" >/dev/null 2>&1; then
    volume_project=$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$volume_name")
    volume_key=$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' "$volume_name")
    if [ "$volume_project" != "$project_name" ] || [ "$volume_key" != 'postgres-data' ]; then
      fail "Das Volume $volume_name gehört nicht nachweislich zu diesem Review-Projekt."
    fi
    check_secret_files
    volume_exists=yes
  else
    volume_exists=no
  fi
}

verify_target_database() {
  if ! actual_database=$(compose exec -T postgres psql -X -U calograph -d "$database_name" -Atq -c 'SELECT current_database()'); then
    fail "Zieldatenbank $database_name ist nicht erreichbar."
  fi
  if [ "$actual_database" != "$database_name" ]; then
    fail "Falsches Datenbankziel: erwartet $database_name."
  fi
}

query_demo_identity() {
  if ! demo_id=$(compose run --rm --no-deps -T backend python -c 'from app.services.analysis_demo import demo_user_id; print(demo_user_id())'); then
    fail 'Deterministische Demo-ID konnte nicht ermittelt werden.'
  fi
  if ! demo_identity=$(compose exec -T postgres psql -X -U calograph -d "$database_name" -Atq -F ':' -c "SELECT id::text || ':' || username FROM users WHERE id = '$demo_id'::uuid OR username = '$demo_username'"); then
    fail 'Demo-Account konnte nicht geprüft werden.'
  fi
  case "$demo_identity" in
    '') printf 'absent\n' ;;
    "$demo_id:$demo_username") printf 'present\n' ;;
    *) fail 'Reservierte Demo-ID oder Demo-Benutzername ist belegt.' ;;
  esac
}

run_up() {
  check_docker
  check_secret_path
  check_volume_owner

  if [ "$volume_exists" = no ]; then
    SECRETS_DIR="$secrets_dir" sh "$project_root/scripts/init-secrets.sh"
    check_secret_files
  fi

  compose config --quiet
  compose build backend frontend
  compose up --detach --wait --wait-timeout 120 postgres
  verify_target_database
  compose run --rm --no-deps -T backend alembic upgrade head
  verify_target_database

  if [ "$(query_demo_identity)" = absent ]; then
    user_count=$(compose exec -T postgres psql -X -U calograph -d "$database_name" -Atq -c 'SELECT count(*) FROM users')
    if [ "$user_count" = 0 ]; then
      [ -t 0 ] || fail 'Die Erstinitialisierung benötigt ein Terminal für die private Passwortabfrage.'
      verify_target_database
      compose run --rm --no-deps backend python -m app.cli create-user \
        --username calograph-analysis-review-admin --admin --skip-onboarding
      verify_target_database
    else
      other_user_count=$(compose exec -T postgres psql -X -U calograph -d "$database_name" -Atq -c "SELECT count(*) FROM users WHERE username <> 'calograph-analysis-review-admin'")
      [ "$other_user_count" = 0 ] || fail 'Review-DB enthält weitere Benutzer ohne Demo-Account; Seed wird nicht gestartet.'
    fi
    [ -t 0 ] || fail 'Der Demo-Seed benötigt ein Terminal für die private Passwortabfrage.'
    verify_target_database
    compose run --rm --no-deps backend python -m app.cli seed-analysis-demo
    verify_target_database
    [ "$(query_demo_identity)" = present ] || fail 'Der Demo-Seed hat keinen Demo-Account hergestellt.'
  fi

  compose up --detach --wait --wait-timeout 180 backend frontend
  compose exec -T backend python -c 'import json, urllib.request; request = urllib.request.Request("http://127.0.0.1:8000/health/ready", headers={"Host": "localhost"}); response = urllib.request.urlopen(request, timeout=5); result = json.load(response); assert response.status == 200 and result.get("status") == "ready"'
  curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8289/health >/dev/null
  printf 'Analysis-Review bereit: http://localhost:8289\n'
}

run_down() {
  check_docker
  compose down
}

case "${1:-}" in
  up) run_up ;;
  down) run_down ;;
  *)
    printf 'Aufruf: %s {up|down}\n' "$0" >&2
    exit 2
    ;;
esac
