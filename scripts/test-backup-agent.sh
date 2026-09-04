#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_root=$(mktemp -d)
cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  rm -rf -- "$test_root"
  exit "$status"
}
trap cleanup EXIT HUP INT TERM

fail() {
  printf 'Backup-agent test failed: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "$1 is required."
}

require_command docker
require_command jq
require_command age
require_command age-keygen

compose_env="$test_root/compose.env"
source_env="$test_root/environment.env"
source_dir="$test_root/secrets"
mkdir -p -- "$source_dir"
printf '%s\n' 'SYNTHETIC_SECRET_SOURCE=compose-backup-test' >"$source_env"
printf '%s\n' 'synthetic-secret-value' >"$source_dir/example_secret"
recipients_file="$test_root/backup-recipients.txt"
printf '%s\n' 'age1testrecipientplaceholder' >"$recipients_file"
for secret_name in postgres_password session_secret rate_limit_secret credential_encryption_key mfa_encryption_key; do
  printf 'synthetic-%s\n' "$secret_name" >"$test_root/$secret_name"
done
cat >"$compose_env" <<EOF
ENVIRONMENT=production
POSTGRES_DB=calograph
POSTGRES_USER=calograph
POSTGRES_PASSWORD_FILE=$test_root/postgres_password
SESSION_SECRET_FILE=$test_root/session_secret
RATE_LIMIT_SECRET_FILE=$test_root/rate_limit_secret
CREDENTIAL_ENCRYPTION_KEY_FILE=$test_root/credential_encryption_key
MFA_ENCRYPTION_KEY_FILE=$test_root/mfa_encryption_key
CALOGRAPH_VERSION=0.6.3
CALOGRAPH_PUBLIC_URL=https://backup-test.example
COOKIE_SECURE=true
TRUSTED_HOSTS=backup-test.example
TRUSTED_ORIGINS=https://backup-test.example
ENABLE_HSTS=true
HSTS_INCLUDE_SUBDOMAINS=false
CALOGRAPH_EDGE_SUBNET=172.30.240.0/24
CALOGRAPH_EDGE_GATEWAY_IP=172.30.240.1
CALOGRAPH_FRONTEND_PROXY_IP=172.30.240.10
BACKUP_AGENT_ENABLED=false
BACKUP_AGE_RECIPIENTS_FILE=$recipients_file
BACKUP_SECRETS_SOURCE_FILE=$source_env
BACKUP_SECRETS_SOURCE_DIR=$source_dir
EOF

compose_config() {
  local override=$1
  if [[ "$override" == true ]]; then
    docker compose \
      -f "$project_root/docker-compose.yml" \
      -f "$project_root/docker-compose.backup-secrets.yml" \
      --env-file "$compose_env" \
      --profile backup config --format json
  else
    docker compose \
      -f "$project_root/docker-compose.yml" \
      --env-file "$compose_env" \
      --profile backup config --format json
  fi
}

base_config=$(compose_config false) || fail 'base Compose configuration did not validate.'
override_config=$(compose_config true) || fail 'secrets override Compose configuration did not validate.'

assert_base_config() {
  jq -e '
    (.services | keys | map(select(. == "backup-agent")) | length == 1)
    and (.services | has("backup-agent-secrets") | not)
    and (.services["backup-agent"].environment.BACKUP_INCLUDE_SECRETS == "false")
    and ([.services["backup-agent"].volumes[]] as $mounts
      | all($mounts[];
          ((.source // "") | contains("/backup-input") | not)
          and ((.target // "") | contains("/backup-input") | not))
      and any($mounts[];
          .type == "volume"
          and .source == "backup-artifacts"
          and .target == "/var/lib/calograph-backups/artifacts")
      and any($mounts[];
          .type == "volume"
          and .source == "backup-status"
          and .target == "/var/lib/calograph-backups/status"))
    and (.services["backup-agent"].read_only == true)
    and ((.services["backup-agent"].cap_drop | index("ALL")) != null)
    and ((.services["backup-agent"].security_opt | index("no-new-privileges:true")) != null)
    and ([.services["backup-agent"].volumes[]] | all(.[];
        (((.source // "") | test("docker\\.sock|identity|private"; "i")) | not)
        and (((.target // "") | test("docker\\.sock|identity|private"; "i")) | not)))
  ' <<<"$base_config" >/dev/null || fail 'base Compose security/default boundary is incorrect.'
}

assert_override_config() {
  jq -e '
    (.services | keys | map(select(. == "backup-agent")) | length == 1)
    and (.services | has("backup-agent-secrets") | not)
    and (.services["backup-agent"].environment.BACKUP_INCLUDE_SECRETS == "true")
    and (.services["backup-agent"].environment.SECRETS_SOURCE_FILE == "/backup-input/environment.env")
    and (.services["backup-agent"].environment.SECRETS_SOURCE_DIR == "/backup-input/secrets")
    and ([.services["backup-agent"].volumes[]] as $mounts
      | ([ $mounts[]
          | select((.target // "") | startswith("/backup-input/")) ] as $input_mounts
        | ($input_mounts | length == 2)
          and all($input_mounts[];
              .type == "bind"
              and .read_only == true
              and (.source | type == "string" and length > 0))
          and any($input_mounts[];
              .target == "/backup-input/environment.env")
          and any($input_mounts[];
              .target == "/backup-input/secrets"))
      and all($mounts[]; 
          (((.source // "") | test("docker\\.sock|identity|private"; "i")) | not)
          and (((.target // "") | test("docker\\.sock|identity|private"; "i")) | not)))
    and (.services["backup-agent"].read_only == true)
    and ((.services["backup-agent"].cap_drop | index("ALL")) != null)
    and ((.services["backup-agent"].security_opt | index("no-new-privileges:true")) != null)
  ' <<<"$override_config" >/dev/null || fail 'secrets override boundary is incorrect.'
}


assert_base_config
assert_override_config

identity_file="$test_root/backup-identity.txt"
age-keygen -o "$identity_file" >/dev/null 2>&1
runtime_recipients="$test_root/runtime-recipients.txt"
age-keygen -y "$identity_file" >"$runtime_recipients"
runtime_dir="$test_root/runtime-artifacts"
runtime_status_dir="$test_root/runtime-status"
mkdir -p -- "$runtime_dir" "$runtime_status_dir"

stub_bin="$test_root/bin"
mkdir -p -- "$stub_bin"
cat >"$stub_bin/pg_dump" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '1\n' >>"$PG_DUMP_CALLS_FILE"
printf '%s' 'synthetic-postgresql-custom-archive'
EOF
chmod 700 "$stub_bin/pg_dump"
pg_dump_calls="$test_root/pg-dump-calls"

(
  cd "$project_root"
  PATH="$stub_bin:$PATH" \
  PG_DUMP_CALLS_FILE="$pg_dump_calls" \
  BACKUP_AGENT_ENABLED=true \
  BACKUP_AGENT_RUN_ONCE=true \
  BACKUP_INCLUDE_SECRETS=true \
  BACKUP_PGHOST=synthetic-postgres \
  POSTGRES_DB=calograph \
  POSTGRES_USER=calograph \
  BACKUP_AGE_RECIPIENTS_FILE="$runtime_recipients" \
  BACKUP_DIR="$runtime_dir" \
  BACKUP_STATUS_FILE="$runtime_status_dir/status.json" \
  SECRETS_SOURCE_FILE="$source_env" \
  SECRETS_SOURCE_DIR="$source_dir" \
  BACKUP_RETENTION_DAYS=30 \
  scripts/backup-agent.sh
) || fail 'single backup agent did not create database and secrets backups.'

[[ "$(wc -l <"$pg_dump_calls")" -eq 1 ]] || fail 'database backup was scheduled more than once.'
database_artifact=$(find "$runtime_dir" -maxdepth 1 -type f -name 'calograph-*.dump.age' -print -quit)
secrets_artifact=$(find "$runtime_dir" -maxdepth 1 -type f -name 'calograph-secrets-*.tar.age' -print -quit)
[[ -n "$database_artifact" && -n "$secrets_artifact" ]] || fail 'one agent did not create both backup artifact types.'
[[ "$(find "$runtime_dir" -maxdepth 1 -type f -name 'calograph-*.dump.age' | wc -l)" -eq 1 ]] || fail 'more than one database artifact was published.'
[[ "$(find "$runtime_dir" -maxdepth 1 -type f -name 'calograph-secrets-*.tar.age' | wc -l)" -eq 1 ]] || fail 'more than one secrets artifact was published.'

for artifact in "$database_artifact" "$secrets_artifact"; do
  (
    cd "$(dirname "$artifact")"
    sha256sum --check --status "$(basename "$artifact").sha256"
  ) || fail "checksum failed for $(basename "$artifact")."
done
age --decrypt --identity "$identity_file" "$database_artifact" >"$test_root/decrypted-database.dump" || fail 'database age pipeline could not be decrypted.'
cmp --silent "$test_root/decrypted-database.dump" <(printf '%s' 'synthetic-postgresql-custom-archive') || fail 'database plaintext pipeline changed.'
mkdir "$test_root/recovered-secrets"
age --decrypt --identity "$identity_file" "$secrets_artifact" \
  | tar --extract --directory "$test_root/recovered-secrets" \
  || fail 'secrets age pipeline could not be recovered.'
cmp --silent "$source_env" "$test_root/recovered-secrets/$(basename "$source_env")" \
  || fail 'environment source was changed in the secrets archive.'
cmp --silent "$source_dir/example_secret" \
  "$test_root/recovered-secrets/$(basename "$source_dir")/example_secret" \
  || fail 'secret source was changed in the secrets archive.'

status_file="$runtime_status_dir/status.json"
jq -e '
  .schema_version == 1
  and .automation.enabled == true
  and (.components.database.state == "healthy")
  and (.components.environment_secrets.state == "healthy")
  and (.components.database.encryption == "age")
  and (.components.environment_secrets.encryption == "age")
  and (. as $status | ($status | tostring | test("identity|docker\\.sock|synthetic-secret-value"; "i") | not))
' "$status_file" >/dev/null || fail 'status writer produced an invalid or unsafe report.'
[[ "$(wc -l <"$status_file")" -eq 1 ]] || fail 'status writer emitted more than one report.'
[[ "$(grep -o '"automation":' "$status_file" | wc -l)" -eq 1 ]] || fail 'status writer ran more than once.'

stale_database="$runtime_dir/calograph-20000101T000000Z.dump.age"
stale_secrets="$runtime_dir/calograph-secrets-20000101T000000Z.tar.age"
printf stale >"$stale_database"
printf stale >"$stale_secrets"
printf '%s  %s\n' "$(sha256sum -- "$stale_database" | cut -d ' ' -f1)" "$(basename "$stale_database")" >"$stale_database.sha256"
printf '%s  %s\n' "$(sha256sum -- "$stale_secrets" | cut -d ' ' -f1)" "$(basename "$stale_secrets")" >"$stale_secrets.sha256"
printf sentinel >"$runtime_dir/operator-sentinel"
printf partial >"$runtime_dir/operator.partial"
touch -d '2 days ago' "$runtime_dir/operator.partial"
(
  cd "$project_root"
  BACKUP_DIR="$runtime_dir" BACKUP_RETENTION_DAYS=30 scripts/backup-retention.sh
) || fail 'retention pipeline failed.'
[[ ! -e "$stale_database" && ! -e "$stale_database.sha256" ]] || fail 'retention left a stale database pair.'
[[ ! -e "$stale_secrets" && ! -e "$stale_secrets.sha256" ]] || fail 'retention left a stale secrets pair.'
[[ -e "$runtime_dir/operator-sentinel" && -e "$runtime_dir/operator.partial" ]] || fail 'retention removed an unrelated operator file.'

printf 'Backup-agent Compose and one-shot pipeline checks passed.\n'
