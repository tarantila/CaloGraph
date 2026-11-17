#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${POSTGRES_TEST_PROJECT:-calograph-postgres-tests}
case "$project_name" in
  ''|calograph|calograph-dev|calograph-analysis-v2-review)
    printf 'POSTGRES_TEST_PROJECT must name an isolated test project.\n' >&2
    exit 1 ;;
esac
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES COMPOSE_PROFILES

compose() {
  ENVIRONMENT=test CALOGRAPH_PUBLIC_URL=http://localhost:8180 COOKIE_SECURE=false ENABLE_HSTS=false \
    TRUSTED_HOSTS=localhost,127.0.0.1 TRUSTED_ORIGINS=http://localhost:8180,http://127.0.0.1:8180 \
    docker compose --env-file /dev/null -f "$project_root/docker-compose-dev.yml" \
      --project-name "$project_name" --profile test "$@"
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    compose logs --no-color --tail=300 postgres-ci >&2 || true
  fi
  # Test PostgreSQL uses tmpfs; never delete globally declared Review volumes.
  if ! compose down --remove-orphans; then
    status=1
  fi
  exit "$status"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$project_root"
compose run --rm --build backend-postgres-ci
