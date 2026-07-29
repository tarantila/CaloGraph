#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
secrets_dir=${SECRETS_DIR:-"$project_root/secrets"}

if ! command -v openssl >/dev/null 2>&1; then
  printf 'openssl is required to generate CaloGraph secrets.\n' >&2
  exit 1
fi

mkdir -p "$secrets_dir"
chmod 700 "$secrets_dir"

create_hex_secret() {
  destination=$1
  if [ -e "$destination" ]; then
    printf 'Preserved existing secret file: %s\n' "$destination"
    return
  fi
  temporary=$(mktemp "$secrets_dir/.secret.XXXXXX")
  openssl rand -hex 32 >"$temporary"
  chmod 444 "$temporary"
  mv "$temporary" "$destination"
  printf 'Created secret file: %s\n' "$destination"
}

create_fernet_secret() {
  destination=$1
  if [ -e "$destination" ]; then
    printf 'Preserved existing secret file: %s\n' "$destination"
    return
  fi
  temporary=$(mktemp "$secrets_dir/.secret.XXXXXX")
  openssl rand -base64 32 | tr '+/' '-_' >"$temporary"
  chmod 444 "$temporary"
  mv "$temporary" "$destination"
  printf 'Created secret file: %s\n' "$destination"
}

create_hex_secret "$secrets_dir/postgres_password"
create_hex_secret "$secrets_dir/session_secret"
create_hex_secret "$secrets_dir/rate_limit_secret"
create_fernet_secret "$secrets_dir/credential_encryption_key"
create_fernet_secret "$secrets_dir/mfa_encryption_key"

printf 'Secret initialization complete. Values were not printed.\n'
