#!/usr/bin/env sh
set -eu
umask 077

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${BACKUP_DIR:-"$project_root/backups"}

cd "$project_root"
docker compose config --quiet
recipients_file=${BACKUP_AGE_RECIPIENTS_FILE:-}
if [ -z "$recipients_file" ] && [ -f "$project_root/.env" ] && [ -r "$project_root/.env" ]; then
  recipients_file=$(
    awk '
      function trim_left(value) {
        sub(/^[[:space:]]+/, "", value)
        return value
      }
      function trim(value) {
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        return value
      }
      function closing_quote(text, delimiter, position, character, escaped) {
        escaped = 0
        for (position = 1; position <= length(text); position++) {
          character = substr(text, position, 1)
          if (escaped) {
            escaped = 0
            continue
          }
          if (character == "\\") {
            escaped = 1
            continue
          }
          if (character == delimiter) return position
        }
        return 0
      }
      function decode_quoted(text, delimiter, position, character, next_character, result) {
        result = ""
        for (position = 1; position <= length(text); position++) {
          character = substr(text, position, 1)
          if (character == "\\" && position < length(text)) {
            next_character = substr(text, position + 1, 1)
            if (next_character == delimiter ||
                (delimiter == "\"" &&
                  (next_character == "\\" || next_character == "$"))) {
              result = result next_character
              position++
              continue
            }
          }
          result = result character
        }
        return result
      }
      function is_assignment(text, candidate) {
        candidate = trim_left(text)
        if (candidate ~ /^export[[:space:]]+/)
          sub(/^export[[:space:]]+/, "", candidate)
        return candidate ~ /^[^[:space:]=:]+[[:space:]]*[:=]/
      }
      function parse_assignments(text, line, candidate, key, after_key, separator,
                                 remainder, delimiter, closing, quoted_value,
                                 suffix, exported, target) {
        line = trim(text)
        while (line != "") {
          if (line ~ /^#/) return
          exported = 0
          if (line ~ /^export[[:space:]]+/) {
            sub(/^export[[:space:]]+/, "", line)
            exported = 1
          }
          if (line !~ /^[^[:space:]=:]+[[:space:]]*[:=]/) return

          key = line
          sub(/[[:space:]]*[:=].*$/, "", key)
          after_key = line
          sub(/^[^[:space:]=:]+[[:space:]]*/, "", after_key)
          separator = substr(after_key, 1, 1)
          remainder = line
          sub(/^[^[:space:]=:]+[[:space:]]*[:=]/, "", remainder)
          remainder = trim_left(remainder)
          target = !exported && key == "BACKUP_AGE_RECIPIENTS_FILE" &&
            separator == "="
          delimiter = substr(remainder, 1, 1)

          if (delimiter == "\"" || delimiter == single_quote) {
            closing = closing_quote(substr(remainder, 2), delimiter)
            if (closing == 0) {
              if (target) {
                value = ""
                found = 1
              }
              multiline_quote = delimiter
              return
            }
            quoted_value = substr(remainder, 2, closing - 1)
            suffix = trim_left(substr(remainder, closing + 2))
            if (target) {
              if (suffix == "" || substr(suffix, 1, 1) == "#" ||
                  is_assignment(suffix))
                value = decode_quoted(quoted_value, delimiter)
              else
                value = ""
              found = 1
            }
            if (suffix == "" || substr(suffix, 1, 1) == "#") return
            line = suffix
            continue
          }

          if (target) {
            sub(/[[:space:]]+#.*$/, "", remainder)
            value = trim(remainder)
            found = 1
          }
          return
        }
      }
      BEGIN { single_quote = sprintf("%c", 39) }
      {
        line = $0
        if (multiline_quote != "") {
          closing = closing_quote(line, multiline_quote)
          if (closing == 0) next
          line = substr(line, closing + 1)
          multiline_quote = ""
        }
        line = trim_left(line)
        if (line == "" || line ~ /^#/) next
        parse_assignments(line)
      }
      END {
        if (found) print value
        else exit 1
      }
    ' "$project_root/.env"
  ) || recipients_file=
fi

if [ -z "$recipients_file" ] || [ ! -f "$recipients_file" ] \
  || [ ! -r "$recipients_file" ] || [ -L "$recipients_file" ]; then
  printf 'BACKUP_AGE_RECIPIENTS_FILE must name a readable regular non-symlink public age recipients file.\n' >&2
  exit 1
fi
BACKUP_DIR="$backup_dir" BACKUP_AGE_RECIPIENTS_FILE="$recipients_file" scripts/backup-postgres.sh
if [ "${BACKUP_SECRETS:-0}" = "1" ]; then
  BACKUP_DIR="$backup_dir" BACKUP_AGE_RECIPIENTS_FILE="$recipients_file" scripts/backup-secrets.sh
else
  printf 'Secret backup skipped. Set BACKUP_SECRETS=1 to create an age-encrypted copy.\n'
fi

docker compose pull postgres backend frontend yazio-scheduler
docker compose up -d --no-build --wait --remove-orphans
docker compose ps
printf 'Container-Update abgeschlossen. Anwendung und YAZIO-Status jetzt prüfen.\n'
