#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
e2e_username=${E2E_USERNAME:-e2e-admin}
e2e_password=${E2E_PASSWORD:-e2e-password-change-me}
CALOGRAPH_PUBLIC_URL=http://frontend:8080
export CALOGRAPH_PUBLIC_URL
TRUSTED_HOSTS=${TRUSTED_HOSTS:-localhost,127.0.0.1,frontend}
export TRUSTED_HOSTS
TRUSTED_ORIGINS=${TRUSTED_ORIGINS:-http://localhost:8180,http://127.0.0.1:8180}
case ",$TRUSTED_ORIGINS," in
  *,http://frontend:8080,*) ;;
  *) TRUSTED_ORIGINS="$TRUSTED_ORIGINS,http://frontend:8080" ;;
esac
export TRUSTED_ORIGINS

cd "$project_root"
if [ "${E2E_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  docker compose up -d --no-build postgres backend frontend
else
  docker compose up -d --build postgres backend frontend
fi
docker compose exec -T backend python -m app.cli create-user \
  --username "$e2e_username" \
  --password "$e2e_password" \
  --admin \
  --if-not-exists
token_output=$(docker compose exec -T backend python -m app.cli create-import-token \
  --username "$e2e_username" \
  --label playwright)
e2e_token=$(printf '%s\n' "$token_output" | tail -n 1)

if [ "${E2E_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  docker compose --profile test build e2e
  docker compose --profile test run --rm \
    -e E2E_USERNAME="$e2e_username" \
    -e E2E_PASSWORD="$e2e_password" \
    -e E2E_IMPORT_TOKEN="$e2e_token" \
    e2e
else
  docker compose --profile test run --rm --build \
    -e E2E_USERNAME="$e2e_username" \
    -e E2E_PASSWORD="$e2e_password" \
    -e E2E_IMPORT_TOKEN="$e2e_token" \
    e2e
fi
