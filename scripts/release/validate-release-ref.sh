#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 3 ]]; then
  printf 'Usage: %s <event-ref> <workflow-ref> <repository>\n' "$0" >&2
  exit 2
fi

event_ref=$1
workflow_ref=$2
repository=$3

if [[ "$event_ref" == refs/heads/main ]]; then
  release_branch=main
elif [[ "$event_ref" =~ ^refs/heads/release/([A-Za-z0-9][A-Za-z0-9._-]*)$ ]]; then
  release_branch="release/${BASH_REMATCH[1]}"
else
  printf 'Release source must be main or a single-level release/* branch.\n' >&2
  exit 1
fi

expected_workflow_ref="$repository/.github/workflows/release.yml@$event_ref"
if [[ "$workflow_ref" != "$expected_workflow_ref" ]]; then
  printf 'Release workflow ref must match the selected source ref.\n' >&2
  exit 1
fi

printf '%s\n' "$release_branch"
