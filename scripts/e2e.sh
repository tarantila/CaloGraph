#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${E2E_PROJECT:-calograph-e2e}
case "$project_name" in
  ''|calograph|calograph-dev|calograph-analysis-v2-review)
    printf 'E2E_PROJECT must name an isolated test project.\n' >&2
    exit 1 ;;
esac
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES COMPOSE_PROFILES

compose() {
  ENVIRONMENT=test CALOGRAPH_PUBLIC_URL=http://localhost:8187 COOKIE_SECURE=false ENABLE_HSTS=false \
    TRUSTED_HOSTS=localhost,127.0.0.1 TRUSTED_ORIGINS=http://localhost:8187,http://127.0.0.1:8187 \
    docker compose --env-file /dev/null -f "$project_root/docker-compose-dev.yml" \
      --project-name "$project_name" --profile e2e "$@"
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    compose logs --no-color --tail=300 e2e-postgres e2e-backend e2e-frontend >&2 || true
  fi
  # All database state is tmpfs. No named volume removal is necessary or safe.
  if ! compose down --remove-orphans; then
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "${E2E_USERNAME:-}" ]; then
  e2e_username=$E2E_USERNAME
else
  e2e_username="e2e-admin-$(date +%s)-$$"
fi
e2e_password=${E2E_PASSWORD:-e2e-password-change-me}

cd "$project_root"
if [ "${E2E_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  compose up -d --no-build --wait e2e-postgres e2e-backend e2e-frontend
else
  compose up -d --build --wait e2e-postgres e2e-backend e2e-frontend
fi
compose exec -T e2e-backend python -m app.cli create-user \
  --username "$e2e_username" \
  --password "$e2e_password" \
  --admin \
  --skip-onboarding \
  --if-not-exists
token_output=$(compose exec -T e2e-backend python -m app.cli create-import-token \
  --username "$e2e_username" \
  --label playwright)
e2e_token=$(printf '%s\n' "$token_output" | tail -n 1)

if [ "${E2E_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  compose build e2e
  compose run --rm --no-deps \
    -e E2E_USERNAME="$e2e_username" \
    -e E2E_PASSWORD="$e2e_password" \
    -e E2E_IMPORT_TOKEN="$e2e_token" \
    e2e npx playwright test --workers=1 --grep-invert='verification-data\.spec\.ts'
else
  compose run --rm --build --no-deps \
    -e E2E_USERNAME="$e2e_username" \
    -e E2E_PASSWORD="$e2e_password" \
    -e E2E_IMPORT_TOKEN="$e2e_token" \
    e2e npx playwright test --workers=1 --grep-invert='verification-data\.spec\.ts'
fi
