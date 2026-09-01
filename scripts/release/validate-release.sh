#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 2 ]]; then
  printf 'Verwendung: %s VERSION RELEASE_COMMIT\n' "$0" >&2
  exit 2
fi

release_version_input=$1
release_commit=$2
repository=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}
repository_private=${REPOSITORY_PRIVATE:-false}
owner=${repository%%/*}
owner=${owner,,}

fail() {
  printf 'Release-Gate fehlgeschlagen: %s\n' "$1" >&2
  exit 1
}

[[ "$repository_private" != true ]] || fail 'Das Repository ist nicht öffentlich.'
[[ "$release_version_input" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || \
  fail "Nicht unterstützte Release-Version: $release_version_input"
[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]] || \
  fail 'Release-Commit ist keine vollständige Commit-SHA.'
git cat-file -e "$release_commit^{commit}" || \
  fail "Release-Commit ist lokal nicht verfügbar: $release_commit"
current_commit=$(git rev-parse HEAD)
[[ "$current_commit" == "$release_commit" ]] || \
  fail "Checkout stimmt nicht mit RELEASE_COMMIT überein: $current_commit"

version=${release_version_input#v}
backend_version=$(git show "$release_commit:backend/pyproject.toml" \
  | awk -F'"' '$1 ~ /^version = / {print $2; exit}')
frontend_version=$(git show "$release_commit:frontend/package.json" \
  | awk -F'"' '$2 == "version" {print $4; exit}')
[[ -n "$backend_version" && -n "$frontend_version" ]] || \
  fail 'Backend- oder Frontend-Version konnte nicht aus RELEASE_COMMIT ermittelt werden.'
[[ "$backend_version" == "$version" && "$frontend_version" == "$version" ]] || \
  fail "Versionsabweichung: Input=$version Backend=$backend_version Frontend=$frontend_version"

changelog_file=$(mktemp)
trap 'rm -f "$changelog_file"' EXIT
git show "$release_commit:CHANGELOG.md" >"$changelog_file" || \
  fail 'CHANGELOG.md fehlt im RELEASE_COMMIT.'
CHANGELOG_FILE="$changelog_file" scripts/release-notes.sh "$version" >/dev/null || \
  fail "CHANGELOG enthält keine vollständige Version $version."
printf 'Release-Version: %s\n' "$release_version_input"
printf 'Release-Commit: %s\n' "$release_commit"

run_json=$(gh run list \
  --repo "$repository" \
  --workflow ci.yml \
  --commit "$release_commit" \
  --limit 20 \
  --json databaseId,event,headBranch,headSha,status,conclusion,url)
main_run=$(jq -c --arg sha "$release_commit" '
  [ .[]
    | select(.event == "push")
    | select(.headBranch == "main")
    | select(.headSha == $sha)
    | select(.status == "completed" and .conclusion == "success")
  ]
  | if length > 0 then .[0] else empty end
' <<<"$run_json")
[[ -n "$main_run" ]] || \
  fail "Kein vollständig erfolgreicher Main-CI-Lauf für $release_commit gefunden."
main_run_id=$(jq -r '.databaseId' <<<"$main_run")
printf 'Erfolgreicher Main-CI-Lauf: %s\n' "$main_run_id"
printf 'Main-CI-URL: %s\n' "$(jq -r '.url' <<<"$main_run")"

valid_digest() {
  [[ "$1" =~ ^sha256:[0-9a-f]{64}$ ]]
}

inspect_digest() {
  local image_ref=$1
  local output
  local digest
  if output=$(docker buildx imagetools inspect "$image_ref" 2>&1); then
    digest=$(printf '%s\n' "$output" | awk '$1 == "Digest:" {print $2; exit}')
    [[ -n "$digest" ]] || return 1
    printf '%s\n' "$digest"
  else
    printf '%s\n' "$output" >&2
    return 1
  fi
}

for image in calograph-backend calograph-frontend calograph-backup-agent; do
  image_ref="ghcr.io/$owner/$image:sha-$release_commit"
  digest=$(inspect_digest "$image_ref" || true)
  valid_digest "$digest" || fail "Immutable Image fehlt oder hat ungültigen Digest: $image_ref"
  printf '%s SHA-Digest: %s\n' "$image" "$digest"

  gh attestation verify "oci://$image_ref@$digest" \
    --repo "$repository" \
    --signer-workflow "$repository/.github/workflows/ci.yml" \
    --source-ref refs/heads/main \
    --source-digest "$release_commit" >/dev/null || \
    fail "Keine passende Main-CI-Provenance für $image_ref@$digest."
  gh attestation verify "oci://$image_ref@$digest" \
    --repo "$repository" \
    --predicate-type https://spdx.dev/Document/v2.3 \
    --signer-workflow "$repository/.github/workflows/ci.yml" \
    --source-ref refs/heads/main \
    --source-digest "$release_commit" >/dev/null || \
    fail "Keine passende Main-CI-SPDX-Attestation für $image_ref@$digest."

  case "$image" in
    calograph-backend) output_name=backend_digest ;;
    calograph-frontend) output_name=frontend_digest ;;
    calograph-backup-agent) output_name=backup_agent_digest ;;
    *) fail "Unbekanntes Image: $image" ;;
  esac
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$output_name" "$digest" >>"$GITHUB_OUTPUT"
  fi
done

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'release_commit=%s\n' "$release_commit" >>"$GITHUB_OUTPUT"
  printf 'main_run_id=%s\n' "$main_run_id" >>"$GITHUB_OUTPUT"
fi
