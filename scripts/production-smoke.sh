#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${PRODUCTION_SMOKE_PROJECT:-calograph-production-smoke}
smoke_root=$(mktemp -d)
smoke_env="$smoke_root/production.env"
response_headers="$smoke_root/response-headers"
backup_dir="$smoke_root/backups"
age_identity="$smoke_root/backup-identity.txt"
age_recipients="$smoke_root/backup-recipients.txt"
postgres_password="$smoke_root/postgres-password"
session_secret="$smoke_root/session-secret"
rate_limit_secret="$smoke_root/rate-limit-secret"
credential_key="$smoke_root/credential-key"
legacy_env="$smoke_root/legacy.env"
legacy_secrets="$smoke_root/legacy-secrets"

compose() {
  docker compose \
    --project-name "$project_name" \
    --env-file "$smoke_env" \
    "$@"
}

cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$smoke_root"
}

fail() {
  if [ -f "$smoke_env" ]; then
    compose logs --no-color --tail=300 || true
  fi
  printf '%s\n' "$1" >&2
  exit 1
}

trap cleanup EXIT HUP INT TERM

{
  printf '%s\n' 'ENVIRONMENT=development'
  printf '%s\n' 'POSTGRES_PASSWORD=legacy-database-password'
  printf '%s\n' \
    'DATABASE_URL=postgresql+psycopg://calograph:legacy-database-password@postgres/calograph'
  printf '%s\n' 'SESSION_SECRET=legacy-session-secret-0123456789abcdef'
  printf '%s\n' 'RATE_LIMIT_SECRET=legacy-rate-secret-fedcba9876543210'
  printf '%s\n' 'CREDENTIAL_ENCRYPTION_KEY='
} >"$legacy_env"
if ! CONFIRM_SECRET_MIGRATION=calograph \
  SECRETS_DIR="$legacy_secrets" \
  scripts/migrate-env-secrets.sh "$legacy_env"; then
  fail "Legacy environment secret migration failed."
fi
if grep -Eq \
  '^(POSTGRES_PASSWORD|DATABASE_URL|SESSION_SECRET|RATE_LIMIT_SECRET|CREDENTIAL_ENCRYPTION_KEY)=' \
  "$legacy_env"; then
  fail "Legacy environment migration retained a direct secret."
fi
if [ "$(tr -d '\n' <"$legacy_secrets/postgres_password")" \
  != 'legacy-database-password' ] \
  || [ "$(tr -d '\n' <"$legacy_secrets/session_secret")" \
  != 'legacy-session-secret-0123456789abcdef' ] \
  || [ "$(tr -d '\n' <"$legacy_secrets/rate_limit_secret")" \
  != 'legacy-rate-secret-fedcba9876543210' ]; then
  fail "Legacy environment migration changed a secret value."
fi

cp "$project_root/.env.production.example" "$smoke_env"
printf '%s\n' 'ci-production-database-password' >"$postgres_password"
printf '%s\n' 'ci-production-session-secret-0123456789abcdef' >"$session_secret"
printf '%s\n' 'ci-production-rate-secret-fedcba9876543210' >"$rate_limit_secret"
: >"$credential_key"
chmod 444 \
  "$postgres_password" \
  "$session_secret" \
  "$rate_limit_secret" \
  "$credential_key"
sed -i \
  -e 's/calograph\.example\.com/calograph-ci.internal/g' \
  -e 's/CALOGRAPH_PORT=8180/CALOGRAPH_PORT=18180/g' \
  -e "s|POSTGRES_PASSWORD_FILE=.*|POSTGRES_PASSWORD_FILE=$postgres_password|" \
  -e "s|SESSION_SECRET_FILE=.*|SESSION_SECRET_FILE=$session_secret|" \
  -e "s|RATE_LIMIT_SECRET_FILE=.*|RATE_LIMIT_SECRET_FILE=$rate_limit_secret|" \
  -e "s|CREDENTIAL_ENCRYPTION_KEY_FILE=.*|CREDENTIAL_ENCRYPTION_KEY_FILE=$credential_key|" \
  "$smoke_env"

if ! command -v age >/dev/null 2>&1 || ! command -v age-keygen >/dev/null 2>&1; then
  printf 'age and age-keygen are required for the production smoke test.\n' >&2
  exit 1
fi
age-keygen -o "$age_identity" >/dev/null 2>&1
age-keygen -y "$age_identity" >"$age_recipients"

cd "$project_root"
if ! compose up -d --build --wait --wait-timeout 240 postgres backend frontend; then
  fail "Production Compose stack did not become healthy."
fi

backend_environment=$(compose exec -T backend env)
for forbidden_name in \
  DATABASE_URL \
  DATABASE_PASSWORD \
  POSTGRES_PASSWORD \
  SESSION_SECRET \
  RATE_LIMIT_SECRET \
  CREDENTIAL_ENCRYPTION_KEY
do
  if printf '%s\n' "$backend_environment" | grep -q "^${forbidden_name}="; then
    fail "Backend exposes ${forbidden_name} as an environment variable."
  fi
done
for required_file_name in \
  DATABASE_PASSWORD_FILE \
  SESSION_SECRET_FILE \
  RATE_LIMIT_SECRET_FILE \
  CREDENTIAL_ENCRYPTION_KEY_FILE
do
  if ! printf '%s\n' "$backend_environment" \
    | grep -q "^${required_file_name}=/run/secrets/"; then
    fail "Backend is missing ${required_file_name}."
  fi
done

if ! compose exec -T backend sh -c \
  'test -s /run/secrets/postgres_password &&
   test -s /run/secrets/session_secret &&
   test -s /run/secrets/rate_limit_secret &&
   test -e /run/secrets/credential_encryption_key'; then
  fail "Backend secret files are missing."
fi
if compose exec -T postgres sh -c \
  'test -e /run/secrets/session_secret ||
   test -e /run/secrets/rate_limit_secret ||
   test -e /run/secrets/credential_encryption_key'; then
  fail "PostgreSQL received an application secret."
fi
if compose exec -T frontend sh -c 'test -e /run/secrets'; then
  fail "Frontend received a secrets mount."
fi

if ! compose create yazio-scheduler >/dev/null; then
  fail "YAZIO scheduler container could not be created for secret-scope checks."
fi
scheduler_id=$(compose ps -aq yazio-scheduler)
scheduler_environment=$(docker inspect \
  --format '{{range .Config.Env}}{{println .}}{{end}}' "$scheduler_id")
for forbidden_name in \
  DATABASE_URL \
  DATABASE_PASSWORD \
  POSTGRES_PASSWORD \
  SESSION_SECRET \
  RATE_LIMIT_SECRET \
  CREDENTIAL_ENCRYPTION_KEY
do
  if printf '%s\n' "$scheduler_environment" | grep -q "^${forbidden_name}="; then
    fail "YAZIO scheduler exposes ${forbidden_name} as an environment variable."
  fi
done
if ! printf '%s\n' "$scheduler_environment" \
  | grep -q '^RATE_LIMIT_SECRET_FILE=/run/secrets/rate_limit_secret$'; then
  fail "YAZIO scheduler is missing its shared rate-limit secret file."
fi
scheduler_mounts=$(docker inspect \
  --format '{{range .Mounts}}{{println .Destination}}{{end}}' "$scheduler_id")
for required_mount in \
  /run/secrets/postgres_password \
  /run/secrets/rate_limit_secret \
  /run/secrets/credential_encryption_key
do
  if ! printf '%s\n' "$scheduler_mounts" | grep -qx "$required_mount"; then
    fail "YAZIO scheduler is missing ${required_mount}."
  fi
done
forbidden_mount=/run/secrets/session_secret
if printf '%s\n' "$scheduler_mounts" | grep -qx "$forbidden_mount"; then
  fail "YAZIO scheduler received ${forbidden_mount}."
fi

if ! curl --fail --silent --show-error \
  --header 'Host: calograph-ci.internal' \
  http://127.0.0.1:18180/health >/dev/null; then
  fail "Production frontend health endpoint failed."
fi

if ! compose exec -T backend python -m app.cli create-user \
  --username smoke-admin \
  --password smoke-password-is-long-and-unique \
  --if-not-exists >/dev/null; then
  fail "Production user creation with the password policy failed."
fi
login_status=$(curl --silent --show-error \
  --dump-header "$response_headers" \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'Content-Type: application/json' \
  --data '{"username":"smoke-admin","password":"smoke-password-is-long-and-unique"}' \
  http://127.0.0.1:18180/api/v1/auth/login)
if [ "$login_status" != "200" ]; then
  fail "Production login returned HTTP $login_status instead of 200."
fi
normalized_login_headers=$(tr -d '\r' <"$response_headers")
for required_cookie_pattern in \
  '^set-cookie: __Host-calograph_session=' \
  '^set-cookie: __Host-calograph_session=.*;.*Max-Age=2592000' \
  '^set-cookie: __Host-calograph_session=.*;.*HttpOnly' \
  '^set-cookie: __Host-calograph_session=.*;.*Path=/' \
  '^set-cookie: __Host-calograph_session=.*;.*SameSite=lax' \
  '^set-cookie: __Host-calograph_session=.*;.*Secure'
do
  if ! printf '%s\n' "$normalized_login_headers" \
    | grep -Eiq "$required_cookie_pattern"; then
    fail "Production session cookie is missing required host-only security attributes."
  fi
done
if printf '%s\n' "$normalized_login_headers" \
  | grep -Eiq '^set-cookie: __Host-calograph_session=.*;.*Domain='; then
  fail "Production session cookie unexpectedly contains a Domain attribute."
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

if ! COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_DIR="$backup_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/backup-postgres.sh; then
  fail "Encrypted database backup or verification failed."
fi
database_backup=$(find "$backup_dir" -maxdepth 1 -name '*.dump.age' -print -quit)
if [ -z "$database_backup" ]; then
  fail "Encrypted database backup was not created."
fi

tampered_backup="$smoke_root/tampered.dump.age"
cp "$database_backup" "$tampered_backup"
cp "$database_backup.sha256" "$tampered_backup.sha256"
printf 'tampered' >>"$tampered_backup"
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$tampered_backup" >/dev/null 2>&1; then
  fail "Tampered encrypted backup was accepted."
fi

if ! BACKUP_DIR="$backup_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  SECRETS_SOURCE_FILE="$smoke_env" \
  SECRETS_SOURCE_DIR="$legacy_secrets" \
  scripts/backup-secrets.sh; then
  fail "Encrypted secret backup failed."
fi
secret_backup=$(find "$backup_dir" -maxdepth 1 -name '*.tar.age' -print -quit)
if [ -z "$secret_backup" ]; then
  fail "Encrypted secret backup was not created."
fi
recovered_secrets="$smoke_root/recovered-secrets"
mkdir "$recovered_secrets"
if ! age --decrypt --identity "$age_identity" "$secret_backup" \
  | tar --extract --directory "$recovered_secrets"; then
  fail "Encrypted secret backup could not be recovered."
fi
if ! cmp --silent "$smoke_env" "$recovered_secrets/$(basename "$smoke_env")" \
  || ! cmp --silent \
    "$legacy_secrets/postgres_password" \
    "$recovered_secrets/$(basename "$legacy_secrets")/postgres_password"; then
  fail "Encrypted secret backup did not reproduce its synthetic sources."
fi

if ! COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  CONFIRM_RESTORE=calograph \
  scripts/restore-postgres.sh "$database_backup"; then
  fail "Encrypted database restore failed."
fi
