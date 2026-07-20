#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
e2e_username=${E2E_USERNAME:-e2e-admin}
e2e_password=${E2E_PASSWORD:-e2e-password-change-me}
TRUSTED_HOSTS=${TRUSTED_HOSTS:-localhost,127.0.0.1,frontend}
export TRUSTED_HOSTS

cd "$project_root"
docker compose up -d --build postgres backend frontend
docker compose exec -T backend python -m app.cli create-user \
  --username "$e2e_username" \
  --password "$e2e_password" \
  --if-not-exists
token_output=$(docker compose exec -T backend python -m app.cli create-import-token \
  --username "$e2e_username" \
  --label playwright)
e2e_token=$(printf '%s\n' "$token_output" | tail -n 1)

docker compose --profile test run --rm \
  -e E2E_USERNAME="$e2e_username" \
  -e E2E_PASSWORD="$e2e_password" \
  -e E2E_IMPORT_TOKEN="$e2e_token" \
  e2e
