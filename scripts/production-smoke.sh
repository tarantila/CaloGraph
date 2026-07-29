#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${PRODUCTION_SMOKE_PROJECT:-calograph-production-smoke}
smoke_env=$(mktemp)
response_headers=$(mktemp)

compose() {
  docker compose \
    --project-name "$project_name" \
    --env-file "$smoke_env" \
    "$@"
}

cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -f "$smoke_env" "$response_headers"
}

fail() {
  compose logs --no-color --tail=300 || true
  printf '%s\n' "$1" >&2
  exit 1
}

trap cleanup EXIT HUP INT TERM

cp "$project_root/.env.production.example" "$smoke_env"
sed -i \
  -e 's/CHANGE_ME_DATABASE_PASSWORD/ci-production-database-password/g' \
  -e 's/CHANGE_ME_AT_LEAST_32_RANDOM_CHARACTERS/ci-production-session-secret-0123456789abcdef/g' \
  -e 's/CHANGE_ME_ANOTHER_RANDOM_SECRET/ci-production-rate-secret-fedcba9876543210/g' \
  -e 's/calograph\.example\.com/calograph-ci.internal/g' \
  -e 's/CALOGRAPH_PORT=8180/CALOGRAPH_PORT=18180/g' \
  "$smoke_env"

cd "$project_root"
if ! compose up -d --build --wait --wait-timeout 240 postgres backend frontend; then
  fail "Production Compose stack did not become healthy."
fi

if ! curl --fail --silent --show-error \
  --header 'Host: calograph-ci.internal' \
  http://127.0.0.1:18180/health >/dev/null; then
  fail "Production frontend health endpoint failed."
fi

auth_status=$(curl --silent --show-error \
  --dump-header "$response_headers" \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'X-Request-ID: attacker-controlled' \
  http://127.0.0.1:18180/api/v1/auth/me)
if [ "$auth_status" != "401" ]; then
  fail "Production API proxy returned HTTP $auth_status instead of 401."
fi
if ! tr -d '\r' <"$response_headers" \
  | grep -Eiq '^x-request-id: [a-f0-9]{32}$'; then
  fail "Production API proxy did not return a generated bounded request ID."
fi
