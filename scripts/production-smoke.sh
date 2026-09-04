#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=${PRODUCTION_SMOKE_PROJECT:-calograph-production-smoke}
smoke_edge_subnet=${PRODUCTION_SMOKE_EDGE_SUBNET:-172.31.250.0/24}
smoke_edge_gateway_ip=${PRODUCTION_SMOKE_EDGE_GATEWAY_IP:-172.31.250.1}
smoke_frontend_proxy_ip=${PRODUCTION_SMOKE_FRONTEND_PROXY_IP:-172.31.250.10}
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
mfa_key="$smoke_root/mfa-key"
legacy_env="$smoke_root/legacy.env"
legacy_secrets="$smoke_root/legacy-secrets"

compose() {
  docker compose \
    --project-name "$project_name" \
    --env-file "$smoke_env" \
    "$@"
}

backup_compose() {
  docker compose \
    -f docker-compose.yml \
    -f docker-compose.backup-secrets.yml \
    --project-name "$project_name" \
    --env-file "$smoke_env" \
    "$@"
}

cleanup() {
  compose --profile backup down --volumes --remove-orphans >/dev/null 2>&1 || true
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
if ! CONFIRM_MFA_SECRET_MIGRATION=calograph \
  SECRETS_DIR="$legacy_secrets" \
  scripts/migrate-mfa-secret.sh "$legacy_env"; then
  fail "MFA secret migration failed."
fi
if ! grep -q '^MFA_ENCRYPTION_KEY_FILE=' "$legacy_env" \
  || grep -q '^MFA_ENCRYPTION_KEY=' "$legacy_env" \
  || [ ! -s "$legacy_secrets/mfa_encryption_key" ]; then
  fail "MFA secret migration did not create a path-only configuration."
fi
chmod 755 "$legacy_secrets"
chmod 444 "$legacy_secrets"/*

cp "$project_root/.env.production.example" "$smoke_env"
printf '%s\n' 'ci-production-database-password' >"$postgres_password"
printf '%s\n' 'ci-production-session-secret-0123456789abcdef' >"$session_secret"
printf '%s\n' 'ci-production-rate-secret-fedcba9876543210' >"$rate_limit_secret"
: >"$credential_key"
openssl rand -base64 32 | tr '+/' '-_' >"$mfa_key"
chmod 444 \
  "$postgres_password" \
  "$session_secret" \
  "$rate_limit_secret" \
  "$credential_key" \
  "$mfa_key"
sed -i \
  -e 's/calograph\.example\.com/calograph-ci.internal/g' \
  -e 's/CALOGRAPH_PORT=8180/CALOGRAPH_PORT=18180/g' \
  -e "s|CALOGRAPH_EDGE_SUBNET=172\\.30\\.0\\.0/24|CALOGRAPH_EDGE_SUBNET=$smoke_edge_subnet|g" \
  -e "s/CALOGRAPH_EDGE_GATEWAY_IP=172\\.30\\.0\\.1/CALOGRAPH_EDGE_GATEWAY_IP=$smoke_edge_gateway_ip/g" \
  -e "s/CALOGRAPH_FRONTEND_PROXY_IP=172\\.30\\.0\\.10/CALOGRAPH_FRONTEND_PROXY_IP=$smoke_frontend_proxy_ip/g" \
  -e 's/RECOVERY_RATE_LIMIT=10/RECOVERY_RATE_LIMIT=7/g' \
  -e 's/RECOVERY_IP_RATE_LIMIT=30/RECOVERY_IP_RATE_LIMIT=11/g' \
  -e 's/RECOVERY_RATE_LIMIT_WINDOW_SECONDS=900/RECOVERY_RATE_LIMIT_WINDOW_SECONDS=777/g' \
  -e "s|POSTGRES_PASSWORD_FILE=.*|POSTGRES_PASSWORD_FILE=$postgres_password|" \
  -e "s|SESSION_SECRET_FILE=.*|SESSION_SECRET_FILE=$session_secret|" \
  -e "s|RATE_LIMIT_SECRET_FILE=.*|RATE_LIMIT_SECRET_FILE=$rate_limit_secret|" \
  -e "s|CREDENTIAL_ENCRYPTION_KEY_FILE=.*|CREDENTIAL_ENCRYPTION_KEY_FILE=$credential_key|" \
  -e "s|MFA_ENCRYPTION_KEY_FILE=.*|MFA_ENCRYPTION_KEY_FILE=$mfa_key|" \
  "$smoke_env"
chmod 644 "$smoke_env"

if ! command -v age >/dev/null 2>&1 || ! command -v age-keygen >/dev/null 2>&1; then
  printf 'age and age-keygen are required for the production smoke test.\n' >&2
  exit 1
fi
age-keygen -o "$age_identity" >/dev/null 2>&1
age-keygen -y "$age_identity" >"$age_recipients"
chmod 444 "$age_recipients"

cd "$project_root"
if [ "${PRODUCTION_SMOKE_USE_PREBUILT_IMAGES:-false}" = "true" ]; then
  smoke_build_option=--no-build
else
  smoke_build_option=--build
fi
if ! compose up -d "$smoke_build_option" --wait --wait-timeout 240 \
  postgres backend frontend; then
  fail "Production Compose stack did not become healthy."
fi

backend_environment=$(compose exec -T backend env)
for forbidden_name in \
  DATABASE_URL \
  DATABASE_PASSWORD \
  POSTGRES_PASSWORD \
  SESSION_SECRET \
  RATE_LIMIT_SECRET \
  CREDENTIAL_ENCRYPTION_KEY \
  MFA_ENCRYPTION_KEY
do
  if printf '%s\n' "$backend_environment" | grep -q "^${forbidden_name}="; then
    fail "Backend exposes ${forbidden_name} as an environment variable."
  fi
done
if ! printf '%s\n' "$backend_environment" \
  | grep -Fxq "TRUSTED_PROXY_NETWORKS=$smoke_frontend_proxy_ip/32"; then
  fail "Backend does not trust only the fixed frontend proxy address."
fi
for expected_setting in \
  RECOVERY_RATE_LIMIT=7 \
  RECOVERY_IP_RATE_LIMIT=11 \
  RECOVERY_RATE_LIMIT_WINDOW_SECONDS=777
do
  if ! printf '%s\n' "$backend_environment" | grep -q "^${expected_setting}$"; then
    fail "Backend did not receive ${expected_setting} from the Compose environment."
  fi
done
for required_file_name in \
  DATABASE_PASSWORD_FILE \
  SESSION_SECRET_FILE \
  RATE_LIMIT_SECRET_FILE \
  CREDENTIAL_ENCRYPTION_KEY_FILE \
  MFA_ENCRYPTION_KEY_FILE
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
   test -e /run/secrets/credential_encryption_key &&
   test -s /run/secrets/mfa_encryption_key'; then
  fail "Backend secret files are missing."
fi
if compose exec -T postgres sh -c \
  'test -e /run/secrets/session_secret ||
   test -e /run/secrets/rate_limit_secret ||
   test -e /run/secrets/credential_encryption_key ||
   test -e /run/secrets/mfa_encryption_key'; then
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
  CREDENTIAL_ENCRYPTION_KEY \
  MFA_ENCRYPTION_KEY \
  MFA_ENCRYPTION_KEY_FILE
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
for forbidden_mount in \
  /run/secrets/session_secret \
  /run/secrets/mfa_encryption_key
do
  if printf '%s\n' "$scheduler_mounts" | grep -qx "$forbidden_mount"; then
    fail "YAZIO scheduler received ${forbidden_mount}."
  fi
done

if ! curl --fail --silent --show-error \
  --header 'Host: calograph-ci.internal' \
  http://127.0.0.1:18180/health >/dev/null; then
  fail "Production frontend health endpoint failed."
fi

forwarded_https_status=$(curl --silent --show-error \
  --dump-header "$response_headers" \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'X-Forwarded-Proto: https' \
  --header 'X-Forwarded-For: 198.51.100.7, 203.0.113.9' \
  http://127.0.0.1:18180/)
if [ "$forwarded_https_status" != "200" ]; then
  fail "Trusted proxy HTTPS request returned HTTP $forwarded_https_status instead of 200."
fi
if ! tr -d '\r' <"$response_headers" \
  | grep -Eiq '^strict-transport-security: max-age=31536000$'; then
  fail "Trusted proxy HTTPS request did not receive HSTS."
fi

docs_status=$(curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  http://127.0.0.1:18180/api/docs)
if [ "$docs_status" != "404" ]; then
  fail "Production API documentation returned HTTP $docs_status instead of 404."
fi

attempt=1
while [ "$attempt" -le 5 ]; do
  invitation_status=$(curl --silent --show-error \
    --output /dev/null \
    --write-out '%{http_code}' \
    --header 'Host: calograph-ci.internal' \
    --header 'X-Forwarded-Proto: https' \
    --header "X-Forwarded-For: 198.51.100.$attempt, 203.0.113.9" \
    --header 'Content-Type: application/json' \
    --data '{"token":"invalid-invitation-token-for-proxy-test"}' \
    http://127.0.0.1:18180/api/v1/auth/invitation/exchange)
  if [ "$invitation_status" != "400" ]; then
    fail "Invitation proxy test returned HTTP $invitation_status before its limit."
  fi
  attempt=$((attempt + 1))
done
invitation_status=$(curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'X-Forwarded-Proto: https' \
  --header 'X-Forwarded-For: 198.51.100.99, 203.0.113.9' \
  --header 'Content-Type: application/json' \
  --data '{"token":"invalid-invitation-token-for-proxy-test"}' \
  http://127.0.0.1:18180/api/v1/auth/invitation/exchange)
if [ "$invitation_status" != "429" ]; then
  fail "Untrusted forwarded-for prefixes bypassed the shared client-IP rate limit."
fi

if ! compose exec -T backend python -m app.cli create-user \
  --username smoke-admin \
  --password smoke-password-is-long-and-unique \
  --skip-onboarding \
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

ipv4_proxy_login_status=$(curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'X-Forwarded-For: 203.0.113.42' \
  --header 'Content-Type: application/json' \
  --data '{"username":"proxy-ipv4-probe","password":"synthetic-invalid-password"}' \
  http://127.0.0.1:18180/api/v1/auth/login)
if [ "$ipv4_proxy_login_status" != "401" ]; then
  fail "Trusted proxy IPv4 audit probe returned HTTP $ipv4_proxy_login_status instead of 401."
fi

ipv6_proxy_login_status=$(curl --silent --show-error \
  --output /dev/null \
  --write-out '%{http_code}' \
  --header 'Host: calograph-ci.internal' \
  --header 'X-Forwarded-For: 2001:db8::42' \
  --header 'Content-Type: application/json' \
  --data '{"username":"proxy-ipv6-probe","password":"synthetic-invalid-password"}' \
  http://127.0.0.1:18180/api/v1/auth/login)
if [ "$ipv6_proxy_login_status" != "401" ]; then
  fail "Trusted proxy IPv6 audit probe returned HTTP $ipv6_proxy_login_status instead of 401."
fi

proxy_audit_ips=$(compose exec -T postgres psql \
  --username calograph \
  --dbname calograph \
  --tuples-only \
  --no-align \
  --command \
    "SELECT client_ip
     FROM security_audit_events
     WHERE event = 'auth.login.failed'
     ORDER BY occurred_at DESC
     LIMIT 2;")
expected_proxy_audit_ips=$(printf '%s\n' '2001:db8::42' '203.0.113.42')
if [ "$proxy_audit_ips" != "$expected_proxy_audit_ips" ]; then
  fail "Trusted proxy IPv4/IPv6 audit addresses were not preserved exactly."
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

if ! compose exec -T postgres psql \
  --username calograph \
  --dbname calograph \
  --set ON_ERROR_STOP=1 \
  --command \
    "CREATE TABLE backup_stream_probe (
       id integer PRIMARY KEY,
       payload text NOT NULL
     );
     INSERT INTO backup_stream_probe
     SELECT item_id, string_agg(md5((item_id * 1000 + part_id)::text), '')
     FROM generate_series(1, 4096) AS item_id
     CROSS JOIN generate_series(1, 64) AS part_id
     GROUP BY item_id;" >/dev/null; then
  fail "Large synthetic backup-stream probe could not be created."
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
if [ "$(stat -c '%a' "$backup_dir")" != "700" ] \
  || [ "$(stat -c '%a' "$database_backup")" != "600" ] \
  || [ "$(stat -c '%a' "$database_backup.sha256")" != "600" ]; then
  fail "Encrypted database backup permissions are too broad."
fi
if ! (
  cd "$backup_dir"
  sha256sum --check --status "$(basename "$database_backup.sha256")"
); then
  fail "Encrypted database backup checksum could not be verified."
fi
if find "$backup_dir" -maxdepth 1 -type f \
  ! -name '*.age' ! -name '*.sha256' -print -quit | grep -q .; then
  fail "Database backup left a plaintext or partial artifact."
fi
restore_test_status="$smoke_root/restore-test.json"
if ! BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  BACKUP_RESTORE_TEST_STATUS_FILE="$restore_test_status" \
  BACKUP_RESTORE_TEST_HOST_MODE=true \
  BACKUP_RESTORE_POSTGRES_IMAGE=postgres:18.4-alpine \
  BACKUP_RESTORE_POSTGRES_MAJOR=18 \
  scripts/isolated-restore-test.sh "$database_backup"; then
  fail "Selected encrypted backup failed the isolated restore test."
fi
if ! grep -q '"result":"RESTORE_TESTED"' "$restore_test_status"; then
  fail "Isolated restore test did not publish successful sanitized evidence."
fi


docker_bin=$(command -v docker)
docker_wrapper_dir="$smoke_root/docker-wrapper"
mkdir "$docker_wrapper_dir"
cat >"$docker_wrapper_dir/docker" <<EOF
#!/usr/bin/env sh
if [ "\${CALOGRAPH_TEST_FAIL_PG_DUMP:-0}" = "1" ]; then
  case " \$* " in
    *" compose exec -T postgres pg_dump "*) exit 73 ;;
  esac
fi
exec "$docker_bin" "\$@"
EOF
chmod 700 "$docker_wrapper_dir/docker"

pg_dump_failure_dir="$smoke_root/pg-dump-failure"
mkdir "$pg_dump_failure_dir"
if PATH="$docker_wrapper_dir:$PATH" \
  CALOGRAPH_TEST_FAIL_PG_DUMP=1 \
  COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_DIR="$pg_dump_failure_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/backup-postgres.sh; then
  fail "Database backup masked a pg_dump failure."
fi
if find "$pg_dump_failure_dir" -mindepth 1 -print -quit | grep -q .; then
  fail "Failed pg_dump left a database backup artifact."
fi

age_failure="$smoke_root/fail-age"
printf '%s\n' '#!/usr/bin/env sh' 'exit 74' >"$age_failure"
chmod 700 "$age_failure"
age_failure_dir="$smoke_root/age-failure"
mkdir "$age_failure_dir"
if AGE_BIN="$age_failure" \
  COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_DIR="$age_failure_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/backup-postgres.sh; then
  fail "Database backup masked an age encryption failure."
fi
if find "$age_failure_dir" -mindepth 1 -print -quit | grep -q .; then
  fail "Failed age encryption left a database backup artifact."
fi
publish_wrapper_dir="$smoke_root/publish-wrapper"
mkdir "$publish_wrapper_dir"
ln_bin=$(command -v ln)
date_bin=$(command -v date)
cat >"$publish_wrapper_dir/ln" <<EOF
#!/usr/bin/env sh
case " \$* " in
  *".sha256 ")
    if [ "\${CALOGRAPH_TEST_FAIL_CHECKSUM_LINK:-0}" = "1" ]; then
      exit 75
    fi
    ;;
esac
exec "$ln_bin" "\$@"
EOF
cat >"$publish_wrapper_dir/date" <<EOF
#!/usr/bin/env sh
if [ "\${CALOGRAPH_TEST_FIXED_DATE:-0}" = "1" ]; then
  printf '%s\n' '20000101T000000Z'
  exit 0
fi
exec "$date_bin" "\$@"
EOF
chmod 700 "$publish_wrapper_dir/ln" "$publish_wrapper_dir/date"

checksum_failure_dir="$smoke_root/checksum-publication-failure"
mkdir "$checksum_failure_dir"
if PATH="$publish_wrapper_dir:$PATH" \
  CALOGRAPH_TEST_FAIL_CHECKSUM_LINK=1 \
  COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_DIR="$checksum_failure_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/backup-postgres.sh >/dev/null 2>&1; then
  fail "Database backup masked a checksum publication failure."
fi
if find "$checksum_failure_dir" -mindepth 1 -print -quit | grep -q .; then
  fail "Failed checksum publication left a database backup artifact."
fi

collision_dir="$smoke_root/final-name-collision"
mkdir "$collision_dir"
collision_backup="$collision_dir/calograph-20000101T000000Z.dump.age"
printf 'preexisting-synthetic-sentinel' >"$collision_backup"
if PATH="$publish_wrapper_dir:$PATH" \
  CALOGRAPH_TEST_FIXED_DATE=1 \
  COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_DIR="$collision_dir" \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/backup-postgres.sh >/dev/null 2>&1; then
  fail "Database backup overwrote a colliding final name."
fi
if [ "$(cat "$collision_backup")" != "preexisting-synthetic-sentinel" ] \
  || find "$collision_dir" -mindepth 1 \
    ! -path "$collision_backup" -print -quit | grep -q .; then
  fail "Final-name collision damaged the existing file or left an artifact."
fi


truncated_backup="$smoke_root/truncated.dump.age"
backup_size=$(stat -c '%s' "$database_backup")
dd if="$database_backup" of="$truncated_backup" \
  bs=1 count=$((backup_size / 2)) status=none
truncated_checksum=$(sha256sum -- "$truncated_backup" | awk '{print $1}')
printf '%s  %s\n' "$truncated_checksum" "$(basename "$truncated_backup")" \
  >"$truncated_backup.sha256"
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$truncated_backup" >/dev/null 2>&1; then
  fail "Truncated encrypted database backup was accepted."
fi

checksum_mismatch_backup="$smoke_root/checksum-mismatch.dump.age"
cp "$database_backup" "$checksum_mismatch_backup"
cp "$database_backup.sha256" "$checksum_mismatch_backup.sha256"
printf 'transfer-corruption' >>"$checksum_mismatch_backup"
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$checksum_mismatch_backup" >/dev/null 2>&1; then
  fail "Database backup with a mismatched checksum was accepted."
fi

tampered_backup="$smoke_root/tampered.dump.age"
cp "$database_backup" "$tampered_backup"
printf 'tampered' >>"$tampered_backup"
tampered_checksum=$(sha256sum -- "$tampered_backup" | awk '{print $1}')
printf '%s  %s\n' "$tampered_checksum" "$(basename "$tampered_backup")" \
  >"$tampered_backup.sha256"
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$tampered_backup" >/dev/null 2>&1; then
  fail "Tampered encrypted database backup was accepted."
fi

wrong_identity="$smoke_root/wrong-backup-identity.txt"
age-keygen -o "$wrong_identity" >/dev/null 2>&1
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$wrong_identity" \
  scripts/verify-backup.sh "$database_backup" >/dev/null 2>&1; then
  fail "Database backup was accepted with the wrong age identity."
fi

invalid_archive="$smoke_root/invalid.dump.age"
printf 'not-a-postgresql-custom-archive' \
  | age --encrypt --recipients-file "$age_recipients" >"$invalid_archive"
invalid_checksum=$(sha256sum -- "$invalid_archive" | awk '{print $1}')
printf '%s  %s\n' "$invalid_checksum" "$(basename "$invalid_archive")" \
  >"$invalid_archive.sha256"
if COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$invalid_archive" >/dev/null 2>&1; then
  fail "Authenticated non-PostgreSQL archive was accepted."
fi

if ! grep -q '^BACKUP_AGENT_ENABLED=false$' "$smoke_env" \
  || ! sed -i \
    's/^BACKUP_AGENT_ENABLED=false$/BACKUP_AGENT_ENABLED=true/' \
    "$smoke_env" \
  || ! grep -q '^BACKUP_AGENT_ENABLED=true$' "$smoke_env"; then
  fail "Synthetic backup-agent override could not be enabled."
fi
printf '%s\n' \
  "BACKUP_AGE_RECIPIENTS_FILE=$age_recipients" \
  "BACKUP_SECRETS_SOURCE_FILE=$smoke_env" \
  "BACKUP_SECRETS_SOURCE_DIR=$legacy_secrets" \
  >>"$smoke_env"

agent_container="${project_name}-backup-agent-check"
agent_backup_dir="$smoke_root/agent-backups"
agent_status_dir="$smoke_root/agent-status"
mkdir -p "$agent_backup_dir" "$agent_status_dir"
if ! (
  export \
    BACKUP_AGENT_ENABLED=true \
    BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
    BACKUP_SECRETS_SOURCE_FILE="$smoke_env" \
    BACKUP_SECRETS_SOURCE_DIR="$legacy_secrets"
  backup_compose --profile backup run \
    --name "$agent_container" \
    --no-deps \
    -e BACKUP_AGENT_RUN_ONCE=true \
    backup-agent
); then
  fail "Single backup-agent secrets-mode run failed."
fi
if ! docker cp \
  "$agent_container:/var/lib/calograph-backups/artifacts/." \
  "$agent_backup_dir/"; then
  fail "Single backup-agent artifacts could not be collected."
fi
if ! docker cp \
  "$agent_container:/var/lib/calograph-backups/status/." \
  "$agent_status_dir/"; then
  fail "Single backup-agent status could not be collected."
fi
agent_environment=$(docker inspect \
  --format '{{range .Config.Env}}{{println .}}{{end}}' "$agent_container")
agent_mounts=$(docker inspect \
  --format '{{range .Mounts}}{{println .Destination "|" .RW}}{{end}}' "$agent_container")
agent_cap_drop=$(docker inspect \
  --format '{{json .HostConfig.CapDrop}}' "$agent_container")
agent_security_options=$(docker inspect \
  --format '{{json .HostConfig.SecurityOpt}}' "$agent_container")
if printf '%s\n' "$agent_environment" \
  | grep -Eiq 'BACKUP_AGE_IDENTITY_FILE|private.*key|docker\.sock'; then
  fail "Backup agent received a private identity or Docker socket."
fi
if printf '%s\n' "$agent_mounts" \
  | grep -Eiq 'docker\.sock|identity|private'; then
  fail "Backup agent has an unsafe private or Docker socket mount."
fi
if [ "$(docker inspect --format '{{.HostConfig.ReadonlyRootfs}}' "$agent_container")" != true ] \
  || ! printf '%s' "$agent_cap_drop" | grep -q '"ALL"' \
  || ! printf '%s' "$agent_security_options" \
    | grep -q 'no-new-privileges:true'; then
  fail "Backup agent lost its read-only or capability security boundary."
fi
if ! printf '%s\n' "$agent_mounts" \
  | grep -Eq '^/var/lib/calograph-backups/artifacts \| true$' \
  || ! printf '%s\n' "$agent_mounts" \
    | grep -Eq '^/var/lib/calograph-backups/status \| true$'; then
  fail "Backup agent artifact/status mounts are not writable."
fi
if ! docker rm "$agent_container" >/dev/null; then
  fail "Backup-agent test container could not be removed."
fi
agent_database_backup=$(find "$agent_backup_dir" -maxdepth 1 -name '*.dump.age' -print -quit)
agent_secrets_backup=$(find "$agent_backup_dir" -maxdepth 1 -name '*.tar.age' -print -quit)
if [ -z "$agent_database_backup" ] \
  || [ -z "$agent_secrets_backup" ] \
  || [ "$(find "$agent_backup_dir" -maxdepth 1 -name '*.dump.age' | wc -l)" -ne 1 ] \
  || [ "$(find "$agent_backup_dir" -maxdepth 1 -name '*.tar.age' | wc -l)" -ne 1 ]; then
  fail "Single backup-agent run did not create exactly one of each encrypted artifact type."
fi
for agent_artifact in "$agent_database_backup" "$agent_secrets_backup"; do
  if ! (
    cd "$agent_backup_dir"
    sha256sum --check --status "$(basename "$agent_artifact").sha256"
  ); then
    fail "Single backup-agent artifact checksum failed."
  fi
done
if ! COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  scripts/verify-backup.sh "$agent_database_backup" >/dev/null 2>&1; then
  fail "Single backup-agent database artifact failed external age verification."
fi
agent_recovered_secrets="$smoke_root/agent-recovered-secrets"
mkdir "$agent_recovered_secrets"
if ! age --decrypt --identity "$age_identity" "$agent_secrets_backup" \
  | tar --extract --directory "$agent_recovered_secrets"; then
  fail "Single backup-agent secrets artifact could not be recovered."
fi
if ! cmp --silent "$smoke_env" \
  "$agent_recovered_secrets/environment.env" \
  || ! cmp --silent \
    "$legacy_secrets/postgres_password" \
    "$agent_recovered_secrets/secrets/postgres_password"; then
  fail "Single backup-agent secrets artifact changed its synthetic sources."
fi
agent_status_file="$agent_status_dir/status.json"
if ! grep -q '"database":{"state":"healthy"' "$agent_status_file" \
  || ! grep -q '"environment_secrets":{"state":"healthy"' "$agent_status_file" \
  || [ "$(grep -o '"automation":' "$agent_status_file" | wc -l)" -ne 1 ]; then
  fail "Single backup-agent run wrote incomplete or duplicate status."
fi
if ! (
  BACKUP_AGENT_ENABLED=true \
  BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
  BACKUP_SECRETS_SOURCE_FILE="$smoke_env" \
  BACKUP_SECRETS_SOURCE_DIR="$legacy_secrets" \
  backup_compose --profile backup up -d --wait backup-agent
); then
  fail "Long-running backup-agent secrets-mode service failed to start."
fi

if ! COMPOSE_PROJECT_NAME="$project_name" \
  COMPOSE_ENV_FILES="$smoke_env" \
  BACKUP_AGE_IDENTITY_FILE="$age_identity" \
  CONFIRM_RESTORE=calograph \
  scripts/restore-postgres.sh "$database_backup"; then
  fail "Encrypted database restore failed."
fi
if ! backup_agent_running_services=$( \
  export \
    BACKUP_AGENT_ENABLED=true \
    BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
    BACKUP_SECRETS_SOURCE_FILE="$smoke_env" \
    BACKUP_SECRETS_SOURCE_DIR="$legacy_secrets"
  backup_compose --profile backup ps --status running --services
); then
  fail "Long-running backup-agent service status could not be inspected."
fi
if [ "$(printf '%s\n' "$backup_agent_running_services" \
  | grep -Fx 'backup-agent' | wc -l)" -ne 1 ]; then
  fail "Long-running backup-agent service is not running exactly once."
fi
if ! backup_agent_container=$( \
  export \
    BACKUP_AGENT_ENABLED=true \
    BACKUP_AGE_RECIPIENTS_FILE="$age_recipients" \
    BACKUP_SECRETS_SOURCE_FILE="$smoke_env" \
    BACKUP_SECRETS_SOURCE_DIR="$legacy_secrets"
  backup_compose --profile backup ps -q backup-agent
); then
  fail "Long-running backup-agent container could not be inspected."
fi
backup_agent_environment=$(docker inspect \
  --format '{{range .Config.Env}}{{println .}}{{end}}' "$backup_agent_container")
if ! printf '%s\n' "$backup_agent_environment" \
  | grep -Fqx 'BACKUP_INCLUDE_SECRETS=true'; then
  fail "Restore did not preserve the backup-agent secrets override."
fi

restored_probe_count=$(
  compose exec -T postgres psql \
    --username calograph \
    --dbname calograph \
    --tuples-only \
    --no-align \
    --command 'SELECT count(*) FROM backup_stream_probe;'
)
if [ "$restored_probe_count" != "4096" ]; then
  fail "The complete large backup stream was not restored."
fi
