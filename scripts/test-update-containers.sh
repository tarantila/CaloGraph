#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
cleanup() {
  local status=$?
  trap - EXIT
  rm -rf -- "$test_root"
  exit "$status"
}
trap cleanup EXIT

fail() {
  printf 'Update-containers test failed: %s\n' "$1" >&2
  exit 1
}

setup_case() {
  CASE_ROOT="$test_root/$1"
  CASE_PROJECT="$CASE_ROOT/project"
  CASE_BIN="$CASE_ROOT/bin"
  CASE_TRACE="$CASE_ROOT/trace"
  CASE_EXPECTED=
  mkdir -p -- "$CASE_PROJECT/scripts" "$CASE_BIN"
  cp -- "$repo_root/scripts/update-containers.sh" "$CASE_PROJECT/scripts/update-containers.sh"
  : >"$CASE_TRACE"

  cat >"$CASE_BIN/docker" <<'EOF'
#!/bin/sh
set -eu
printf 'docker:%s\n' "$*" >>"$TRACE_FILE"
EOF
  cat >"$CASE_PROJECT/scripts/backup-postgres.sh" <<'EOF'
#!/bin/sh
set -eu
recipients=${BACKUP_AGE_RECIPIENTS_FILE:-}
printf 'backup-postgres:%s\n' "$recipients" >>"$TRACE_FILE"
if [ -z "$recipients" ] || [ ! -f "$recipients" ] || [ ! -r "$recipients" ] || [ -L "$recipients" ]; then
  exit 1
fi
[ "$recipients" = "$EXPECTED_RECIPIENTS" ] || exit 1
[ "${FAKE_BACKUP_FAIL:-0}" != 1 ]
EOF
  cat >"$CASE_PROJECT/scripts/backup-secrets.sh" <<'EOF'
#!/bin/sh
set -eu
recipients=${BACKUP_AGE_RECIPIENTS_FILE:-}
printf 'backup-secrets:%s\n' "$recipients" >>"$TRACE_FILE"
[ "$recipients" = "$EXPECTED_RECIPIENTS" ]
EOF
  chmod 700 "$CASE_BIN/docker" "$CASE_PROJECT/scripts/backup-postgres.sh" \
    "$CASE_PROJECT/scripts/backup-secrets.sh"
  chmod 700 "$CASE_PROJECT/scripts/update-containers.sh"
}

run_update() {
  (
    cd "$CASE_PROJECT"
    unset BACKUP_AGE_RECIPIENTS_FILE BACKUP_SECRETS BACKUP_DIR FAKE_BACKUP_FAIL
    export PATH="$CASE_BIN:$PATH"
    export TRACE_FILE="$CASE_TRACE"
    export EXPECTED_RECIPIENTS="$CASE_EXPECTED"
    env "$@" ./scripts/update-containers.sh
  )
}

assert_trace() {
  local expected_file="$CASE_ROOT/expected-trace"
  printf '%s\n' "$@" >"$expected_file"
  diff -u "$expected_file" "$CASE_TRACE" || fail 'operation order or recipient path was incorrect.'
}

expect_failure_before_backup() {
  if run_update "$@" >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"; then
    fail 'invalid recipient configuration unexpectedly succeeded.'
  fi
  assert_trace 'docker:compose config --quiet'
}

# Explicit process environment must override the project .env value.
setup_case environment_precedence
environment_recipients="$CASE_ROOT/environment recipients.txt"
dotenv_recipients="$CASE_ROOT/dotenv-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$environment_recipients"
printf '%s\n' 'synthetic public recipient' >"$dotenv_recipients"
printf 'BACKUP_AGE_RECIPIENTS_FILE=%s\n' "$dotenv_recipients" >"$CASE_PROJECT/.env"
CASE_EXPECTED=$environment_recipients
run_update "BACKUP_AGE_RECIPIENTS_FILE=$environment_recipients"
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$environment_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# The parser accepts whitespace and matching quotes, and never executes other dotenv fields.
setup_case dotenv_fallback
fallback_recipients="$CASE_ROOT/recipients with spaces.txt"
marker="$CASE_ROOT/should-not-execute"
printf '%s\n' 'synthetic public recipient' >"$fallback_recipients"
printf 'UNRELATED_VALUE=$(touch %s)\n  BACKUP_AGE_RECIPIENTS_FILE = "%s"  \n' \
  "$marker" "$fallback_recipients" >"$CASE_PROJECT/.env"
CASE_EXPECTED=$fallback_recipients
run_update BACKUP_SECRETS=1
[ ! -e "$marker" ] || fail 'shell-looking dotenv content was executed.'
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$fallback_recipients" \
  "backup-secrets:$fallback_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# Normal single-quoted dotenv values are resolved literally as well.
setup_case single_quoted_dotenv
single_quoted_recipients="$CASE_ROOT/single-quoted-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$single_quoted_recipients"
printf "  BACKUP_AGE_RECIPIENTS_FILE = '%s'  \n" \
  "$single_quoted_recipients" >"$CASE_PROJECT/.env"
CASE_EXPECTED=$single_quoted_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$single_quoted_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# A recipient-looking line inside a multiline unrelated value is not an assignment.
setup_case multiline_unrelated_value
configured_recipients="$CASE_ROOT/configured-recipients.txt"
embedded_recipients="$CASE_ROOT/embedded-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$configured_recipients"
printf '%s\n' 'synthetic public recipient' >"$embedded_recipients"
cat >"$CASE_PROJECT/.env" <<EOF
BACKUP_AGE_RECIPIENTS_FILE=$configured_recipients
export UNRELATED.VALUE-NAME='first line
BACKUP_AGE_RECIPIENTS_FILE=$embedded_recipients
last line'
EOF
CASE_EXPECTED=$configured_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$configured_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# Compose-supported leading punctuation and colon separators also delimit assignments.
setup_case dotenv_punctuation_headers
configured_recipients="$CASE_ROOT/configured-recipients.txt"
embedded_recipients="$CASE_ROOT/embedded-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$configured_recipients"
printf '%s\n' 'synthetic public recipient' >"$embedded_recipients"
cat >"$CASE_PROJECT/.env" <<EOF
BACKUP_AGE_RECIPIENTS_FILE=$configured_recipients
.UNRELATED='first line
BACKUP_AGE_RECIPIENTS_FILE=$embedded_recipients
last line'
-UNRELATED: 'first line
BACKUP_AGE_RECIPIENTS_FILE=$embedded_recipients
last line'
EOF
CASE_EXPECTED=$configured_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$configured_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# Escaped single quotes are decoded as literal path characters, not evaluated.
setup_case escaped_single_quote_path
escaped_recipients="$CASE_ROOT/operator's recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$escaped_recipients"
printf '%s\n' "BACKUP_AGE_RECIPIENTS_FILE='$CASE_ROOT/operator\\'s recipients.txt'" \
  >"$CASE_PROJECT/.env"
CASE_EXPECTED=$escaped_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$escaped_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# Assignments after a same-line quoted value must still protect later multiline values.
setup_case same_line_multiline_value
configured_recipients="$CASE_ROOT/configured-recipients.txt"
embedded_recipients="$CASE_ROOT/embedded-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$configured_recipients"
printf '%s\n' 'synthetic public recipient' >"$embedded_recipients"
cat >"$CASE_PROJECT/.env" <<EOF
BACKUP_AGE_RECIPIENTS_FILE=$configured_recipients
A='one' B='first line
BACKUP_AGE_RECIPIENTS_FILE=$embedded_recipients
last line'
EOF
CASE_EXPECTED=$configured_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$configured_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# A comment-looking line can close an unrelated multiline value.
setup_case multiline_comment_closing
configured_recipients="$CASE_ROOT/configured-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$configured_recipients"
cat >"$CASE_PROJECT/.env" <<EOF
export UNRELATED='first line
# closes the value'
BACKUP_AGE_RECIPIENTS_FILE=$configured_recipients
EOF
CASE_EXPECTED=$configured_recipients
run_update
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$configured_recipients" \
  'docker:compose pull postgres backend frontend yazio-scheduler' \
  'docker:compose up -d --no-build --wait --remove-orphans' \
  'docker:compose ps'

# Missing configuration fails before the encrypted backup and every container operation.
setup_case missing_configuration
expect_failure_before_backup

# Missing files, directories, and symlinks are rejected before backup starts.
for invalid_kind in missing directory symlink; do
  setup_case "invalid_$invalid_kind"
  valid_recipients="$CASE_ROOT/valid-recipients.txt"
  candidate="$CASE_ROOT/$invalid_kind-recipients"
  printf '%s\n' 'synthetic public recipient' >"$valid_recipients"
  case "$invalid_kind" in
    missing) ;;
    directory) mkdir -- "$candidate" ;;
    symlink) ln -s -- "$valid_recipients" "$candidate" ;;
  esac
  CASE_EXPECTED=$candidate
  expect_failure_before_backup "BACKUP_AGE_RECIPIENTS_FILE=$candidate"
done

# Exercise the unreadable-file branch when the test user can represent it.
setup_case invalid_unreadable
unreadable_recipients="$CASE_ROOT/unreadable-recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$unreadable_recipients"
chmod 000 "$unreadable_recipients"
if [ ! -r "$unreadable_recipients" ]; then
  CASE_EXPECTED=$unreadable_recipients
  expect_failure_before_backup "BACKUP_AGE_RECIPIENTS_FILE=$unreadable_recipients"
else
  printf 'Skipping unreadable-file check because this user can read mode-000 files.\n'
fi

# A command substitution in the target value stays literal and cannot execute.
setup_case executable_target_value
marker="$CASE_ROOT/target-command-ran"
printf 'BACKUP_AGE_RECIPIENTS_FILE=$(touch %s)\n' "$marker" >"$CASE_PROJECT/.env"
expect_failure_before_backup
[ ! -e "$marker" ] || fail 'command substitution in the target value was executed.'

# An encrypted database backup remains mandatory before any image pull or recreation.
setup_case backup_failure
backup_recipients="$CASE_ROOT/recipients.txt"
printf '%s\n' 'synthetic public recipient' >"$backup_recipients"
CASE_EXPECTED=$backup_recipients
if run_update "BACKUP_AGE_RECIPIENTS_FILE=$backup_recipients" FAKE_BACKUP_FAIL=1 \
  >"$CASE_ROOT/stdout" 2>"$CASE_ROOT/stderr"; then
  fail 'container update continued after backup failure.'
fi
assert_trace \
  'docker:compose config --quiet' \
  "backup-postgres:$backup_recipients"

printf 'Update-containers recipient tests passed.\n'
