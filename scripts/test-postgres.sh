#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${POSTGRES_TEST_PROJECT:-calograph-postgres-tests}

compose() {
  docker compose -f docker-compose.yml -f docker-compose.test.yml --project-name "$project_name" "$@"
}

cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
}

trap cleanup EXIT HUP INT TERM

cd "$project_root"
compose run --rm --build backend-postgres-ci
