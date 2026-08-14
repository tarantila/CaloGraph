#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 4 ]]; then
  printf 'Verwendung: %s RELEASE_COMMIT RELEASE_TAG BACKEND_DIGEST FRONTEND_DIGEST\n' "$0" >&2
  exit 2
fi

release_commit=$1
release_tag=$2
expected_backend_digest=$3
expected_frontend_digest=$4
repository=${GITHUB_REPOSITORY:?GITHUB_REPOSITORY fehlt}
owner=${repository%%/*}
owner=${owner,,}

fail() {
  printf 'Image-Promotion fehlgeschlagen: %s\n' "$1" >&2
  exit 1
}

[[ "$release_commit" =~ ^[0-9a-f]{40}$ ]] || fail 'Ungültige Release-Commit-SHA.'
[[ "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Ungültiges Release-Tag.'

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
    case "$output" in
      *"not found"*|*"Not Found"*|*"no such manifest"*|*"No such manifest"*|*"MANIFEST_UNKNOWN"*|*"manifest unknown"*) return 2 ;;
      *) printf '%s\n' "$output" >&2; return 1 ;;
    esac
  fi
}
images=(calograph-backend calograph-frontend)
declare -A source_digests

for image in "${images[@]}"; do
  case "$image" in
    calograph-backend) expected_digest=$expected_backend_digest ;;
    calograph-frontend) expected_digest=$expected_frontend_digest ;;
    *) fail "Unbekanntes Image: $image" ;;
  esac
  valid_digest "$expected_digest" || fail "Ungültiger validierter Digest: $image"
  image_ref="ghcr.io/$owner/$image"
  source_ref="$image_ref:sha-$release_commit"
  source_digest=$(inspect_digest "$source_ref" || true)
  valid_digest "$source_digest" || fail "Quell-Image fehlt: $source_ref"
  [[ "$source_digest" == "$expected_digest" ]] || \
    fail "Quell-Digest weicht vom validierten Digest ab: $source_ref"

  docker pull "$source_ref" >/dev/null
  pulled_digest=$(inspect_digest "$source_ref" || true)
  [[ "$pulled_digest" == "$expected_digest" ]] || \
    fail "Digest änderte sich beim Laden: $source_ref"
  [[ "$pulled_digest" == "$source_digest" ]] || \
    fail "Digest änderte sich beim Laden: $source_ref"
  source_digests["$image"]=$source_digest
done

for image in "${images[@]}"; do
  image_ref="ghcr.io/$owner/$image"
  source_ref="$image_ref:sha-$release_commit"
  source_digest=${source_digests["$image"]}
  release_ref="$image_ref:$release_tag"
  if existing_release_digest=$(inspect_digest "$release_ref"); then
    valid_digest "$existing_release_digest" || \
      fail "Ungültiger bestehender Release-Digest: $release_ref"
    [[ "$existing_release_digest" == "$source_digest" ]] || \
      fail "Release-Tag zeigt bereits auf einen anderen Digest: $release_ref"
    printf '%s bleibt unverändert auf %s.\n' "$release_ref" "$source_digest"
  else
    inspect_status=$?
    [[ "$inspect_status" -eq 2 ]] || \
      fail "Release-Tag konnte nicht sicher gelesen werden: $release_ref"
    docker tag "$source_ref" "$release_ref"
    docker push "$release_ref" >/dev/null
  fi

  latest_ref="$image_ref:latest"
  docker tag "$source_ref" "$latest_ref"
  docker push "$latest_ref" >/dev/null

  release_digest=$(inspect_digest "$release_ref" || true)
  latest_digest=$(inspect_digest "$latest_ref" || true)
  valid_digest "$release_digest" || fail "Release-Digest konnte nicht verifiziert werden: $release_ref"
  valid_digest "$latest_digest" || fail "Latest-Digest konnte nicht verifiziert werden: $latest_ref"
  [[ "$source_digest" == "$release_digest" ]] || \
    fail "Digest-Invariante verletzt: $source_ref != $release_ref"
  [[ "$source_digest" == "$latest_digest" ]] || \
    fail "Digest-Invariante verletzt: $source_ref != $latest_ref"

  printf '%s: sha=%s release=%s latest=%s\n' \
    "$image" "$source_digest" "$release_digest" "$latest_digest"
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    output_name=${image#calograph-}
    printf '%s_digest=%s\n' "$output_name" "$source_digest" >>"$GITHUB_OUTPUT"
  fi
done
