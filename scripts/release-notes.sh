#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  printf 'Verwendung: %s VERSION\n' "$0" >&2
  exit 2
fi

version=$1
changelog=${CHANGELOG_FILE:-CHANGELOG.md}
case "$version" in
  ''|*[!0-9.]*)
    printf 'Ungültige Release-Version: %s\n' "$version" >&2
    exit 2
    ;;
esac

notes=$(
  awk -v section_header="## [$version]" '
    index($0, section_header) == 1 &&
      (length($0) == length(section_header) ||
       substr($0, length(section_header) + 1, 1) ~ /[[:space:]]/) {
        in_section = 1
        found = 1
        next
      }
    in_section && /^## \[/ { exit }
    in_section { print; if ($0 !~ /^[[:space:]]*$/) content = 1 }
    END {
      if (!found || !content) exit 1
    }
  ' "$changelog"
)

if [ -z "$notes" ]; then
  printf 'Kein Inhalt für Release %s in %s gefunden.\n' "$version" "$changelog" >&2
  exit 1
fi

printf '%s\n' "$notes"
