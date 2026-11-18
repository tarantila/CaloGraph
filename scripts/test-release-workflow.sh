#!/usr/bin/env bash
set -Eeuo pipefail

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
ref_validator="$project_root/scripts/release/validate-release-ref.sh"
ci_workflow="$project_root/.github/workflows/ci.yml"
release_workflow="$project_root/.github/workflows/release.yml"
release_validator="$project_root/scripts/release/validate-release.sh"
repository=tarantila/CaloGraph

expect_ref() {
  local expected=$1
  shift
  local actual
  actual=$("$ref_validator" "$@" "$repository")
  [[ "$actual" == "$expected" ]]
}

expect_rejected_ref() {
  if "$ref_validator" "$@" "$repository" >/dev/null 2>&1; then
    printf 'Unexpectedly accepted release ref: %s\n' "$1" >&2
    exit 1
  fi
}

expect_ref main refs/heads/main \
  "$repository/.github/workflows/release.yml@refs/heads/main"
expect_ref release/0.7.x refs/heads/release/0.7.x \
  "$repository/.github/workflows/release.yml@refs/heads/release/0.7.x"
expect_rejected_ref refs/heads/feature/release \
  "$repository/.github/workflows/release.yml@refs/heads/feature/release"
expect_rejected_ref refs/heads/release/nested/branch \
  "$repository/.github/workflows/release.yml@refs/heads/release/nested/branch"
expect_rejected_ref refs/heads/release/0.7.x \
  "$repository/.github/workflows/release.yml@refs/heads/main"

test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT
mkdir -p "$test_root/bin"
release_commit=0000000000000000000000000000000000000001
changelog="$test_root/CHANGELOG.md"
run_json="$test_root/runs.json"
output_file="$test_root/github-output"
cat >"$changelog" <<'CHANGELOG'
## [0.7.1] - 2026-11-17

### Maintenance

Synthetic release validation fixture.
CHANGELOG
touch "$output_file"

cat >"$test_root/bin/git" <<'GIT'
#!/usr/bin/env bash
set -Eeuo pipefail
case "$1" in
  cat-file) exit 0 ;;
  rev-parse) printf '%s\n' "$TEST_RELEASE_COMMIT" ;;
  show)
    case "$2" in
      *:backend/pyproject.toml) printf 'version = "0.7.1"\n' ;;
      *:frontend/package.json) printf '{"version":"0.7.1"}\n' ;;
      *:CHANGELOG.md) cat "$TEST_CHANGELOG" ;;
      *) exit 2 ;;
    esac
    ;;
  *) exit 2 ;;
esac
GIT
cat >"$test_root/bin/gh" <<'GH'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$1" == run && "$2" == list ]]; then
  cat "$TEST_RUN_JSON"
  exit 0
fi
if [[ "$1" == attestation && "$2" == verify ]]; then
  shift 2
  source_ref=
  while (($#)); do
    if [[ "$1" == --source-ref ]]; then
      source_ref=$2
      break
    fi
    shift
  done
  [[ "$source_ref" == "$TEST_EXPECTED_ATTESTATION_REF" ]]
  exit
fi
exit 2
GH
cat >"$test_root/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$1 $2" == 'buildx imagetools' && "$3" == inspect ]]
printf 'Digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n'
DOCKER
chmod +x "$test_root/bin/"*

write_run() {
  local branch=$1
  printf \
    '[{"databaseId":7,"event":"push","headBranch":"%s","headSha":"%s","status":"completed","conclusion":"success","url":"https://example.invalid/run/7"}]\n' \
    "$branch" "$release_commit" >"$run_json"
}

run_release_validation() {
  local branch=$1
  local attested_ref=$2
  env \
    PATH="$test_root/bin:$PATH" \
    GITHUB_REPOSITORY="$repository" \
    GITHUB_OUTPUT="$output_file" \
    GH_TOKEN=synthetic \
    REPOSITORY_PRIVATE=false \
    TEST_RELEASE_COMMIT="$release_commit" \
    TEST_CHANGELOG="$changelog" \
    TEST_RUN_JSON="$run_json" \
    TEST_EXPECTED_ATTESTATION_REF="$attested_ref" \
    "$release_validator" v0.7.1 "$release_commit" "$branch"
}

write_run release/0.7.x
run_release_validation release/0.7.x refs/heads/release/0.7.x >/dev/null

write_run release/0.7.0
if run_release_validation release/0.7.x refs/heads/release/0.7.x >/dev/null 2>&1; then
  printf 'Release validation accepted CI from a different branch.\n' >&2
  exit 1
fi

write_run release/0.7.x
if run_release_validation release/0.7.x refs/heads/main >/dev/null 2>&1; then
  printf 'Release validation accepted attestations from a different branch.\n' >&2
  exit 1
fi

write_run main
run_release_validation main refs/heads/main >/dev/null

# Keep GitHub Actions branch filters and publication gates aligned with the
# executable ref policy.
awk '
  $0 == "  publish-images:" { job = 1; found = 1; next }
  /^  [[:alnum:]_-]+:/ { job = 0 }
  job && $0 == "    needs: [containers-and-e2e]" { needs = 1 }
  END { exit !(found && needs) }
' "$ci_workflow"
grep -Fq '      - release/*' "$ci_workflow"
grep -Fq "if: github.event_name == 'push' && (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/heads/release/'))" "$ci_workflow"
grep -Fq '[[ "$GITHUB_REF" =~ ^refs/heads/release/[^/]+$ ]]' "$ci_workflow"
grep -Fq "if: github.ref == 'refs/heads/main'" "$ci_workflow"
grep -Fq 'headBranch == $branch' "$release_validator"
grep -Fq '"$EVENT_REF" "$WORKFLOW_REF" "$REPOSITORY"' "$release_workflow"
grep -Fq 'ref: ${{ github.ref }}' "$release_workflow"
awk '
  /scripts\/release\/validate-release\.sh/ {
    count++
    if ($0 != "        run: scripts/release/validate-release.sh \"$RELEASE_VERSION\" \"$RELEASE_COMMIT\" \"$RELEASE_BRANCH\"") invalid = 1
  }
  END {
    if (count != 1 || invalid) {
      print "Release validation must use the exact single-line invocation." > "/dev/stderr"
      exit 1
    }
  }
' "$release_workflow"
grep -Fq 'source_ref="refs/heads/$source_branch"' "$release_validator"
[[ $(grep -Fc -- '--source-ref "$source_ref"' "$release_validator") -eq 2 ]]

echo 'Release branch policy contracts passed.'
