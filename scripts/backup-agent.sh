#!/usr/bin/env bash
set -euo pipefail
umask 077

# Isolated agent: only a public age recipients file is available.
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-/var/lib/calograph-backups/artifacts}
status_file=${BACKUP_STATUS_FILE:-/var/lib/calograph-backups/status.json}
schedule_state_file=${BACKUP_SCHEDULE_STATE_FILE:-$(dirname -- "$status_file")/schedule-state}
database_verification_status_file=${BACKUP_DATABASE_VERIFICATION_STATUS_FILE:-$(dirname -- "$status_file")/database-verification.json}
secrets_verification_status_file=${BACKUP_SECRETS_VERIFICATION_STATUS_FILE:-$(dirname -- "$status_file")/secrets-verification.json}
include_secrets=${BACKUP_INCLUDE_SECRETS:-false}
retention_days=${BACKUP_RETENTION_DAYS:-30}
schedule_time=${BACKUP_SCHEDULE_TIME:-02:30}
timezone=${CALOGRAPH_TIMEZONE:-Europe/Berlin}
run_once=${BACKUP_AGENT_RUN_ONCE:-false}

iso_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
next_run() {
  local now candidate
  now=$(date +%s)
  candidate=$(TZ="$timezone" date -d "today $schedule_time" +%s 2>/dev/null || printf 0)
  if (( candidate <= now )); then
    candidate=$(TZ="$timezone" date -d "tomorrow $schedule_time" +%s 2>/dev/null || printf '%s' "$((now + 86400))")
  fi
  date -u -d "@$candidate" +%Y-%m-%dT%H:%M:%SZ
}
if [[ "${BACKUP_AGENT_ENABLED:-false}" != "true" ]]; then
  printf 'Backup agent is disabled.\n'
  exit 0
fi
case "$schedule_time" in [0-2][0-9]:[0-5][0-9]) : ;; *) printf 'Invalid backup schedule time.\n' >&2; exit 64 ;; esac
[[ "$timezone" =~ ^[A-Za-z0-9_+./-]+$ ]] || { printf 'Invalid backup timezone.\n' >&2; exit 64; }
mkdir -p -- "$backup_dir" "$(dirname -- "$status_file")"
chmod 700 -- "$backup_dir" "$(dirname -- "$status_file")"
export BACKUP_DIR="$backup_dir"
cd "$project_root"
latest_artifact() {
  local pattern=$1 candidate latest=
  for candidate in "$backup_dir"/$pattern; do
    [[ -f "$candidate" && ! -L "$candidate" && "${candidate##*.}" == age ]] || continue
    if [[ -z "$latest" || "$candidate" > "$latest" ]]; then latest=$candidate; fi
  done
  [[ -n "$latest" ]] || return 1
  basename -- "$latest"
}

verification_is_current() {
  local verification_file=$1 reference=$2 expected_artifact=$3 expected_checksum=$4
  local artifact checksum verified_at verified_epoch reference_epoch
  [[ -r "$verification_file" && ! -L "$verification_file" ]] || return 1
  artifact=$(sed -n 's/.*"artifact":"\([^"]*\)".*/\1/p' "$verification_file")
  checksum=$(sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p' "$verification_file")
  verified_at=$(sed -n 's/.*"verified_at":"\([^"]*\)".*/\1/p' "$verification_file")
  [[ "$artifact" == "$expected_artifact" && "$checksum" == "$expected_checksum" ]] || return 1
  [[ "$verified_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] || return 1
  verified_epoch=$(date -u -d "$verified_at" +%s 2>/dev/null) || return 1
  reference_epoch=$(date -u -d "$reference" +%s 2>/dev/null) || return 1
  (( verified_epoch >= reference_epoch )) || return 1
  verification_verified_at=$verified_at
}


write_status() {
  local state=$1 reason=$2 attempt=$3 success=$4 next=$5 db_state=$6 secrets_state=$7
  local status_dir status_tmp success_json reason_json secrets_success_json db_success_json threshold
  local db_artifact db_checksum secrets_artifact secrets_checksum
  local db_artifact_json=null db_checksum_json=null secrets_artifact_json=null secrets_checksum_json=null
  local db_verified_json=null secrets_verified_json=null verification_verified_at=
  local db_verification=not_verified secrets_verification=not_verified
  local db_matching=false secrets_matching=false db_encryption=not_reported secrets_encryption=not_reported
  status_dir=$(dirname -- "$status_file")
  status_tmp=$(mktemp "$status_dir/.backup-status.XXXXXX.partial")
  chmod 600 "$status_tmp"
  threshold=${BACKUP_FRESHNESS_THRESHOLD_SECONDS:-172800}
  if [[ -n "$success" ]]; then success_json="\"$success\""; else success_json=null; fi
  if [[ "$db_state" == "healthy" ]]; then
    db_success_json="\"$attempt\""
    db_artifact=$(latest_artifact 'calograph-*.dump.age' || true)
    if [[ -n "$db_artifact" ]]; then
      db_checksum=$(sha256sum -- "$backup_dir/$db_artifact" | cut -d ' ' -f1)
      db_artifact_json="\"$db_artifact\""
      db_checksum_json="\"$db_checksum\""
      verification_verified_at=
      if verification_is_current "$database_verification_status_file" "$attempt" "$db_artifact" "$db_checksum"; then
        db_verification=full
        db_verified_json="\"$verification_verified_at\""
      fi
    fi
    db_matching=true
    db_encryption=age
  else
    db_success_json=null
  fi
  if [[ "$secrets_state" == "healthy" ]]; then
    secrets_success_json=$success_json
    secrets_artifact=$(latest_artifact 'calograph-secrets-*.tar.age' || true)
    if [[ -n "$secrets_artifact" ]]; then
      secrets_checksum=$(sha256sum -- "$backup_dir/$secrets_artifact" | cut -d ' ' -f1)
      secrets_artifact_json="\"$secrets_artifact\""
      secrets_checksum_json="\"$secrets_checksum\""
      verification_verified_at=
      if verification_is_current "$secrets_verification_status_file" "$success" "$secrets_artifact" "$secrets_checksum"; then
        secrets_verification=full
        secrets_verified_json="\"$verification_verified_at\""
      fi
    fi
    secrets_matching=true
    secrets_encryption=age
  else
    secrets_success_json=null
  fi
  if [[ -n "$reason" ]]; then reason_json="\"$reason\""; else reason_json=null; fi
  printf '{"schema_version":1,"reported_at":"%s","target":"calograph","freshness_threshold_seconds":%s,"automation":{"enabled":true,"last_attempt_at":"%s","last_success_at":%s,"next_run_at":"%s","last_error_code":%s,"schedule_timezone":"%s","schedule_time":"%s","retention_days":%s},"components":{"database":{"state":"%s","last_attempt_at":"%s","last_success_at":%s,"artifact_created_at":%s,"artifact":%s,"sha256":%s,"matching_backup":%s,"verification":"%s","last_verified_at":%s,"encryption":"%s"},"environment_secrets":{"state":"%s","last_attempt_at":"%s","last_success_at":%s,"artifact_created_at":%s,"artifact":%s,"sha256":%s,"matching_backup":%s,"verification":"%s","last_verified_at":%s,"encryption":"%s"}}}\n' \
    "$(iso_now)" "$threshold" "$attempt" "$success_json" "$next" "$reason_json" "$timezone" "$schedule_time" "$retention_days" \
    "$db_state" "$attempt" "$db_success_json" "$db_success_json" "$db_artifact_json" "$db_checksum_json" "$db_matching" "$db_verification" "$db_verified_json" "$db_encryption" \
    "$secrets_state" "$attempt" "$secrets_success_json" "$secrets_success_json" "$secrets_artifact_json" "$secrets_checksum_json" "$secrets_matching" "$secrets_verification" "$secrets_verified_json" "$secrets_encryption" >"$status_tmp"
  mv -f -- "$status_tmp" "$status_file"
}

mark_scheduled_today() {
  local today state_tmp
  today=$(TZ="$timezone" date +%Y-%m-%d)
  state_tmp=$(mktemp "$(dirname -- "$schedule_state_file")/.schedule-state.XXXXXX.partial")
  printf '%s\n' "$today" >"$state_tmp"
  chmod 600 "$state_tmp"
  mv -f -- "$state_tmp" "$schedule_state_file"
}
run_once() {
  local attempt success next db_state secrets_state state reason
  attempt=$(iso_now)
  success=
  next=$(next_run)
  db_state=failed; secrets_state=disabled; state=failed; reason=latest_attempt_failed
  if scripts/backup-postgres.sh >/dev/null 2>&1; then
    db_state=healthy; success=$attempt; state=attention; reason=
    if [[ "$include_secrets" == "true" ]]; then
      if scripts/backup-secrets.sh >/dev/null 2>&1; then
        secrets_state=healthy
      else
        secrets_state=failed; success=; state=failed; reason=latest_attempt_failed
      fi
    fi
    scripts/backup-retention.sh >/dev/null 2>&1 || true
  fi
  write_status "$state" "$reason" "$attempt" "$success" "$next" "$db_state" "$secrets_state"
  [[ "$state" != failed ]]
}
if [[ "$run_once" == "true" ]]; then run_once; exit $?; fi
while :; do
  today=$(TZ="$timezone" date +%Y-%m-%d)
  now_epoch=$(date +%s)
  today_schedule_epoch=$(TZ="$timezone" date -d "today $schedule_time" +%s 2>/dev/null || printf '%s' "$((now_epoch + 86400))")
  scheduled=
  [[ -r "$schedule_state_file" ]] && scheduled=$(cat -- "$schedule_state_file" 2>/dev/null || true)
  if [[ "$scheduled" != "$today" && "$now_epoch" -ge "$today_schedule_epoch" ]]; then
    # Persist the day before work so supervisor restarts cannot duplicate it.
    mark_scheduled_today
    run_once || true
  fi
  if [[ "$scheduled" == "$today" || "$now_epoch" -ge "$today_schedule_epoch" ]]; then
    next_schedule_epoch=$(TZ="$timezone" date -d "tomorrow $schedule_time" +%s 2>/dev/null || printf '%s' "$((now_epoch + 86400))")
  else
    next_schedule_epoch=$today_schedule_epoch
  fi
  sleep_seconds=$((next_schedule_epoch - now_epoch)); (( sleep_seconds > 0 )) || sleep_seconds=60
  sleep "$sleep_seconds"
done
