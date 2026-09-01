#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir=${BACKUP_DIR:?BACKUP_DIR is required}
retention_days=${BACKUP_RETENTION_DAYS:-30}
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || { printf 'Invalid backup retention.\n' >&2; exit 1; }
[[ -d "$backup_dir" && ! -L "$backup_dir" ]] || { printf 'Backup directory is invalid.\n' >&2; exit 1; }
now=$(date -u +%s)
cutoff=$((now - retention_days * 86400))

# Only exact, agent-managed names are considered. Never use broad find-delete:
# unrelated operator files and symlinks remain untouched.
for artifact in "$backup_dir"/calograph-*.dump.age "$backup_dir"/calograph-secrets-*.tar.age; do
  [[ -e "$artifact" && ! -L "$artifact" && -f "$artifact" ]] || continue
  [[ "$(stat -c '%h' -- "$artifact" 2>/dev/null || printf 2)" == 1 ]] || continue
  name=$(basename -- "$artifact")
  if [[ ! "$name" =~ ^calograph-(secrets-)?[0-9]{8}T[0-9]{6}Z\.(dump|tar)\.age$ ]]; then
    continue
  fi
  stamp=${BASH_REMATCH[0]}
  stamp=${stamp#calograph-}
  stamp=${stamp#secrets-}
  stamp=${stamp%%.*}
  epoch=$(date -u -d "${stamp:0:8} ${stamp:9:2}:${stamp:11:2}:${stamp:13:2}" +%s 2>/dev/null || printf 0)
  (( epoch > 0 && epoch < cutoff )) || continue
  checksum="$artifact.sha256"
  # Delete an artifact and its matching checksum as one managed pair. Do not
  # delete an unrelated checksum or any file with unsafe link semantics.
  [[ -f "$checksum" && ! -L "$checksum" ]] || continue
  [[ "$(stat -c '%h' -- "$checksum" 2>/dev/null || printf 2)" == 1 ]] || continue
  rm -f -- "$artifact" "$checksum"
done

# Randomized partials are safe to remove only when they are in our directory
# and have the exact managed prefix/suffix.
for partial in "$backup_dir"/.calograph-*.partial; do
  [[ -f "$partial" && ! -L "$partial" ]] || continue
  [[ "$(stat -c '%h' -- "$partial" 2>/dev/null || printf 2)" == 1 ]] || continue
  partial_mtime=$(stat -c '%Y' -- "$partial" 2>/dev/null || printf 0)
  [[ "$partial_mtime" =~ ^[0-9]+$ ]] || continue
  (( now - partial_mtime >= 86400 )) || continue
  rm -f -- "$partial"
done
