#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project_name=calograph-verification-e2e
port=${E2E_PORT:-8188}
verification_date=${E2E_VERIFICATION_DATE:-$(date -u +%F)}
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/calograph-verification-e2e.XXXXXX")
secrets_dir="$temporary_root/secrets"
env_file="$temporary_root/.env"
mkdir -p "$secrets_dir"
chmod 700 "$temporary_root" "$secrets_dir"
cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$temporary_root"
}
trap cleanup EXIT INT TERM

for secret in postgres_password session_secret rate_limit_secret; do
  openssl rand -hex 32 >"$secrets_dir/$secret"
  chmod 444 "$secrets_dir/$secret"
done
for secret in credential_encryption_key mfa_encryption_key; do
  openssl rand -base64 32 | tr '+/' '-_' >"$secrets_dir/$secret"
  chmod 444 "$secrets_dir/$secret"
done

cat >"$env_file" <<EOF
ENVIRONMENT=development
POSTGRES_DB=calograph_verification
POSTGRES_USER=calograph_verification
POSTGRES_PASSWORD_FILE=$secrets_dir/postgres_password
SESSION_SECRET_FILE=$secrets_dir/session_secret
RATE_LIMIT_SECRET_FILE=$secrets_dir/rate_limit_secret
CREDENTIAL_ENCRYPTION_KEY_FILE=$secrets_dir/credential_encryption_key
MFA_ENCRYPTION_KEY_FILE=$secrets_dir/mfa_encryption_key
CALOGRAPH_PUBLIC_URL=http://127.0.0.1:$port
CALOGRAPH_BIND_ADDRESS=127.0.0.1
CALOGRAPH_PORT=$port
CALOGRAPH_EDGE_SUBNET=10.251.0.0/24
CALOGRAPH_EDGE_GATEWAY_IP=10.251.0.1
CALOGRAPH_DATA_SUBNET=10.252.0.0/24
CALOGRAPH_FRONTEND_PROXY_IP=10.251.0.10
COOKIE_SECURE=false
ENABLE_HSTS=false
TRUSTED_HOSTS=localhost,127.0.0.1,frontend
TRUSTED_ORIGINS=http://localhost:$port,http://127.0.0.1:$port,http://frontend:8080
E2E_VERIFICATION_DATE=$verification_date
BACKUP_AGENT_ENABLED=false
RELEASE_STATUS_ENABLED=false
EOF

compose() {
  docker compose \
    --project-name "$project_name" \
    --env-file "$env_file" \
    --file "$project_root/docker-compose.yml" \
    --file "$project_root/docker-compose.test.yml" \
    --file "$project_root/docker-compose.verification.yml" \
    "$@"
}

compose up --detach --build --wait postgres backend frontend
compose exec --no-TTY backend alembic upgrade head
compose exec --no-TTY backend python tests/e2e_verification_seed.py
compose run --rm --build --no-deps e2e
