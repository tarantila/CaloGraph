#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${1:-"$project_root/.env"}
secrets_dir=${SECRETS_DIR:-"$project_root/secrets"}
destination="$secrets_dir/mfa_encryption_key"

if [ "${CONFIRM_MFA_SECRET_MIGRATION:-}" != "calograph" ]; then
  printf 'Migration cancelled. Set CONFIRM_MFA_SECRET_MIGRATION=calograph explicitly.\n' >&2
  exit 1
fi
if [ ! -f "$env_file" ]; then
  printf 'The requested environment file does not exist.\n' >&2
  exit 1
fi
if ! command -v openssl >/dev/null 2>&1; then
  printf 'openssl is required to generate the MFA encryption key.\n' >&2
  exit 1
fi
if grep -Eq '^(MFA_ENCRYPTION_KEY|MFA_ENCRYPTION_KEY_FILE)=' "$env_file"; then
  printf 'The environment already contains an MFA encryption setting.\n' >&2
  exit 1
fi
if [ -e "$destination" ]; then
  printf 'The MFA encryption key file already exists.\n' >&2
  exit 1
fi

mkdir -p "$secrets_dir"
chmod 700 "$secrets_dir"
secret_tmp=$(mktemp "$secrets_dir/.mfa.XXXXXX")
env_dir=$(CDPATH= cd -- "$(dirname -- "$env_file")" && pwd)
env_tmp=$(mktemp "$env_dir/.calograph-env.XXXXXX")
trap 'rm -f "$secret_tmp" "$env_tmp"' EXIT HUP INT TERM

openssl rand -base64 32 | tr '+/' '-_' >"$secret_tmp"
awk -v mfa_path="$destination" '
  { print }
  END {
    print ""
    print "MFA_ENCRYPTION_KEY_FILE=" mfa_path
  }
' "$env_file" >"$env_tmp"

chmod 444 "$secret_tmp"
chmod 600 "$env_tmp"
mv "$secret_tmp" "$destination"
mv "$env_tmp" "$env_file"
chmod 600 "$env_file"
trap - EXIT HUP INT TERM

printf 'Created and configured a dedicated MFA encryption key.\n'
printf 'The key value was not printed.\n'
