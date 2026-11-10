#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${VERIFICATION_E2E_PROJECT:-calograph-verification-e2e}
case "$project_name" in
  ''|calograph|calograph-dev|calograph-analysis-v2-review)
    printf 'VERIFICATION_E2E_PROJECT must name an isolated test project.\n' >&2
    exit 1 ;;
esac
unset COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_ENV_FILES COMPOSE_PROFILES
verification_date=${E2E_VERIFICATION_DATE:-$(TZ=Europe/Berlin date +%F)}
cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$status" -ne 0 ]; then
    compose logs --no-color --tail=300 verification-postgres verification-backend verification-frontend >&2 || true
  fi
  # PostgreSQL is tmpfs; do not remove unrelated globally declared volumes.
  if ! compose down --remove-orphans; then
    printf 'Verification E2E Compose cleanup failed.\n' >&2
    status=1
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

export E2E_VERIFICATION_DATE="$verification_date"

compose() {
  ENVIRONMENT=test CALOGRAPH_PUBLIC_URL=http://localhost:8188 COOKIE_SECURE=false ENABLE_HSTS=false \
    TRUSTED_HOSTS=localhost,127.0.0.1 TRUSTED_ORIGINS=http://localhost:8188,http://127.0.0.1:8188 \
    docker compose \
      --project-name "$project_name" \
      --env-file /dev/null \
      --file "$project_root/docker-compose-dev.yml" \
      --profile verification \
      "$@"
}

compose config --quiet
if [ "${E2E_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  compose up --detach --no-build --wait verification-postgres verification-backend verification-frontend
else
  compose up --detach --build --wait verification-postgres verification-backend verification-frontend
fi
compose exec --no-TTY verification-backend alembic upgrade head
compose exec --no-TTY verification-backend python - \
  < "$project_root/backend/tests/e2e_verification_seed.py"
compose run --rm --build --no-deps verification
