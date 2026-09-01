#!/usr/bin/env bash
set -euo pipefail
umask 077

# Isolated agent: only a public age recipients file is available.
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-/var/lib/calograph-backups/artifacts}
status_file=${BACKUP_STATUS_FILE:-/var/lib/calograph-backups/status.json}
schedule_state_file=${BACKUP_SCHEDULE_STATE_FILE:-$(dirname -- "$status_file")/schedule-state}
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
cd "$project_root"
export BACKUP_DIR="$backup_dir"

write_status() {
  local state=$1 reason=$2 attempt=$3 success=$4 next=$5 db_state=$6 secrets_state=$7
  local status_dir status_tmp success_json reason_json secrets_success_json db_success_json threshold
  status_dir=$(dirname -- "$status_file")
  status_tmp=$(mktemp "$status_dir/.backup-status.XXXXXX.partial")
  chmod 600 "$status_tmp"
  threshold=${BACKUP_FRESHNESS_THRESHOLD_SECONDS:-172800}
  if [[ -n "$success" ]]; then success_json="\"$success\""; else success_json=null; fi
  if [[ "$db_state" == "healthy" ]]; then db_success_json="\"$attempt\""; else db_success_json=null; fi
  if [[ "$secrets_state" == "healthy" ]]; then secrets_success_json=$success_json; else secrets_success_json=null; fi
  if [[ -n "$reason" ]]; then reason_json="\"$reason\""; else reason_json=null; fi
  printf '{"schema_version":1,"reported_at":"%s","target":"calograph","freshness_threshold_seconds":%s,"automation":{"enabled":true,"last_attempt_at":"%s","last_success_at":%s,"next_run_at":"%s","last_error_code":%s,"schedule_timezone":"%s","schedule_time":"%s","retention_days":%s},"components":{"database":{"state":"%s","last_attempt_at":"%s","last_success_at":%s,"artifact_created_at":%s,"matching_backup":true,"verification":"not_verified","encryption":"age"},"environment_secrets":{"state":"%s","last_attempt_at":"%s","last_success_at":%s,"artifact_created_at":%s,"matching_backup":true,"verification":"not_verified","encryption":"age"}}}\n' \
    "$(iso_now)" "$threshold" "$attempt" "$success_json" "$next" "$reason_json" "$timezone" "$schedule_time" "$retention_days" \
    "$db_state" "$attempt" "$db_success_json" "$db_success_json" "$secrets_state" "$attempt" "$secrets_success_json" "$secrets_success_json" >"$status_tmp"
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
