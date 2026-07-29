# Software supply chain

CaloGraph treats dependency resolution, container construction, publication,
and retention as one auditable pipeline. The workflow is intentionally split
between untrusted change validation and trusted publication.

## Dependency and source controls

- Every third-party GitHub Action is pinned to a full commit SHA.
- Every Dockerfile and Compose base image retains a readable version tag and is
  pinned to an immutable manifest digest.
- `npm ci` is followed by both production-only and complete dependency audits.
- `pip-audit` checks an exported production lock set and the complete
  development environment.
- Gitleaks scans every pushed or proposed Git change; the complete existing
  history was separately baseline-scanned when F-10 was introduced.
- Dependabot checks GitHub Actions, Docker digests, npm, and Python dependencies
  every week. Its pull requests still have to pass the complete CI pipeline.

`.gitleaksignore` contains only exact historical fingerprints for inert
test/documentation placeholders. It does not suppress a rule, file, path, or
future occurrence.

The `brace-expansion` override in `frontend/package.json` is deliberate. It
forces all development-only callers onto the patched 5.0.8 release for
CVE-2026-14257 without accepting the unrelated breaking changes proposed by a
forced general audit rewrite.

The backend uses the official Python 3.14 Alpine 3.23 image. During F-10
validation, the contemporaneous Debian slim image produced 23 high or critical
OS findings with no available fixed package version. The Alpine variant passed
the same strict Trivy gate with no suppressions and also passed the complete
backend, migration, browser, backup, and restore checks.

## Image validation and publication

Pull requests and ordinary branch pushes receive only read access. GitHub
builds both runtime targets locally, scans the OS and application packages with
Trivy, emits SPDX JSON SBOMs, and then runs browser and production-smoke tests
against those exact prebuilt images.

Only a successful push to `main` or a SemVer-style `vX.Y.Z` tag starts the
separate publication job with package and attestation permissions. That job
rebuilds candidates from the trusted commit, then applies the same exact-image
tests and high/critical Trivy gate before it signs in to GHCR.

Published names and tags are:

- `ghcr.io/tarantila/calograph-backend:sha-COMMIT`
- `ghcr.io/tarantila/calograph-frontend:sha-COMMIT`
- `edge` for the current successful `main` image
- the exact `vX.Y.Z` tag plus `latest` for a successful release tag

GitHub's Sigstore-backed artifact attestation service signs build provenance
and the SPDX SBOM for each published image digest. The SBOM files are also
retained as workflow artifacts.

Verify a published image with the GitHub CLI:

```bash
gh attestation verify \
  oci://ghcr.io/tarantila/calograph-backend:vX.Y.Z \
  --repo tarantila/CaloGraph
```

## Running release images

Set the image repositories and an existing release tag in `.env`:

```dotenv
CALOGRAPH_BACKEND_IMAGE=ghcr.io/tarantila/calograph-backend
CALOGRAPH_FRONTEND_IMAGE=ghcr.io/tarantila/calograph-frontend
CALOGRAPH_VERSION=vX.Y.Z
```

Then pull and start without allowing a local source rebuild:

```bash
docker compose pull backend frontend yazio-scheduler
docker compose up -d --no-build --wait backend frontend yazio-scheduler
```

The backend and scheduler deliberately use the same backend image.

## GHCR retention

The weekly cleanup workflow:

- never deletes an image version carrying a SemVer-style release tag,
- keeps the ten newest non-release image versions,
- deletes older tagged non-release versions only after 30 days,
- leaves tagless OCI records untouched because they can be provenance or SBOM
  referrers rather than runnable images, and
- defaults to a dry run when started manually.

Scheduled runs perform the deletion. GitHub retains deleted package versions
for a limited restoration window, but release-tag protection is the primary
safety control. The package must inherit repository Actions access so its
`GITHUB_TOKEN` has package administration permission.
