#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${1:-"$project_root/.env"}
secrets_dir=${SECRETS_DIR:-"$project_root/secrets"}

if [ "${CONFIRM_SECRET_MIGRATION:-}" != "calograph" ]; then
  printf 'Migration cancelled. Set CONFIRM_SECRET_MIGRATION=calograph explicitly.\n' >&2
  exit 1
fi
if [ ! -f "$env_file" ]; then
  printf 'The requested environment file does not exist.\n' >&2
  exit 1
fi

for existing in \
  "$secrets_dir/postgres_password" \
  "$secrets_dir/session_secret" \
  "$secrets_dir/rate_limit_secret" \
  "$secrets_dir/credential_encryption_key"
do
  if [ -e "$existing" ]; then
    printf 'A destination secret already exists; nothing was changed.\n' >&2
    exit 1
  fi
done

mkdir -p "$secrets_dir"
chmod 700 "$secrets_dir"
postgres_tmp=$(mktemp "$secrets_dir/.postgres.XXXXXX")
session_tmp=$(mktemp "$secrets_dir/.session.XXXXXX")
rate_tmp=$(mktemp "$secrets_dir/.rate.XXXXXX")
credential_tmp=$(mktemp "$secrets_dir/.credential.XXXXXX")
env_dir=$(CDPATH= cd -- "$(dirname -- "$env_file")" && pwd)
env_tmp=$(mktemp "$env_dir/.calograph-env.XXXXXX")
trap 'rm -f "$postgres_tmp" "$session_tmp" "$rate_tmp" "$credential_tmp" "$env_tmp"' \
  EXIT HUP INT TERM

extract_value() {
  key=$1
  destination=$2
  awk -v requested_key="$key" '
    index($0, requested_key "=") == 1 {
      value = substr($0, length(requested_key) + 2)
      sub(/\r$/, "", value)
      quote = substr(value, 1, 1)
      if (length(value) >= 2 && (quote == "\"" || quote == "\047") &&
          substr(value, length(value), 1) == quote) {
        value = substr(value, 2, length(value) - 2)
      }
      print value
      matches += 1
    }
    END {
      if (matches != 1) {
        exit 1
      }
    }
  ' "$env_file" >"$destination"
}

if ! extract_value POSTGRES_PASSWORD "$postgres_tmp" \
  || ! extract_value SESSION_SECRET "$session_tmp" \
  || ! extract_value RATE_LIMIT_SECRET "$rate_tmp" \
  || ! extract_value CREDENTIAL_ENCRYPTION_KEY "$credential_tmp"; then
  printf 'Expected each legacy secret exactly once; nothing was changed.\n' >&2
  exit 1
fi

if [ "$(wc -c <"$postgres_tmp")" -lt 17 ] \
  || [ "$(wc -c <"$session_tmp")" -lt 33 ] \
  || [ "$(wc -c <"$rate_tmp")" -lt 33 ]; then
  printf 'One or more legacy secrets are too short; nothing was changed.\n' >&2
  exit 1
fi
credential_size=$(wc -c <"$credential_tmp")
if [ "$credential_size" -ne 1 ] && [ "$credential_size" -ne 45 ]; then
  printf 'The legacy credential key is neither empty nor Fernet-sized.\n' >&2
  exit 1
fi

awk \
  -v postgres_path="$secrets_dir/postgres_password" \
  -v session_path="$secrets_dir/session_secret" \
  -v rate_path="$secrets_dir/rate_limit_secret" \
  -v credential_path="$secrets_dir/credential_encryption_key" '
    BEGIN {
      removed["POSTGRES_PASSWORD"] = 1
      removed["DATABASE_URL"] = 1
      removed["SESSION_SECRET"] = 1
      removed["RATE_LIMIT_SECRET"] = 1
      removed["CREDENTIAL_ENCRYPTION_KEY"] = 1
      removed["POSTGRES_PASSWORD_FILE"] = 1
      removed["SESSION_SECRET_FILE"] = 1
      removed["RATE_LIMIT_SECRET_FILE"] = 1
      removed["CREDENTIAL_ENCRYPTION_KEY_FILE"] = 1
    }
    {
      separator = index($0, "=")
      key = separator ? substr($0, 1, separator - 1) : ""
      if (!removed[key]) {
        print
      }
    }
    END {
      print "POSTGRES_PASSWORD_FILE=" postgres_path
      print "SESSION_SECRET_FILE=" session_path
      print "RATE_LIMIT_SECRET_FILE=" rate_path
      print "CREDENTIAL_ENCRYPTION_KEY_FILE=" credential_path
    }
  ' "$env_file" >"$env_tmp"

chmod 444 "$postgres_tmp" "$session_tmp" "$rate_tmp" "$credential_tmp"
chmod 600 "$env_tmp"
mv "$postgres_tmp" "$secrets_dir/postgres_password"
mv "$session_tmp" "$secrets_dir/session_secret"
mv "$rate_tmp" "$secrets_dir/rate_limit_secret"
mv "$credential_tmp" "$secrets_dir/credential_encryption_key"
mv "$env_tmp" "$env_file"
chmod 600 "$env_file"
trap - EXIT HUP INT TERM

printf 'Migrated legacy values into service-scoped secret files.\n'
printf 'The environment file now contains paths only; secret values were not printed.\n'
