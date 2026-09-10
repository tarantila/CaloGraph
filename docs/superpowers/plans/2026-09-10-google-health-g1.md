# Google Health G1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Google Health OAuth foundation and a fixed-host read-only Nutrition Log client while preserving C0 and leaving the Nutrition domain untouched.

**Architecture:** Add a focused `app.google_health` package for OAuth primitives, connection orchestration, safe contracts, and fixed-host transport. Add one user-owned encrypted connection plus one-time OAuth-flow persistence, expose only authenticated `/api/v1/google-health/*` routes, and keep Google payloads in validated memory-only DTOs. Reuse the existing session/CSRF, credential encryption, user lock, rate-limit, security-event, Alembic, and settings patterns.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Alembic, Pydantic, requests, `google-auth`, `google-auth-oauthlib`, Fernet credential encryption, pytest, Ruff, MyPy, SQLite test harness, isolated PostgreSQL migration tests.

**Spec:** `docs/superpowers/specs/2026-09-10-google-health-g1.md`

## Global Constraints

- Base branch is C0 commit `80ef3d4b22094bc58640516439ef657aea958c65`; do not amend or alter C0.
- Official endpoints are exactly `https://health.googleapis.com/v4`, `https://accounts.google.com/o/oauth2/v2/auth`, and `https://oauth2.googleapis.com/token`.
- Request exactly `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`; never request write or unrelated scopes.
- API routes are `/api/v1/google-health/status`, `/api/v1/google-health/oauth/start`, and `/api/v1/google-health/oauth/callback`.
- Redirect URI is only `<CALOGRAPH_PUBLIC_URL>/api/v1/google-health/oauth/callback`.
- OAuth state is random, user-bound, hashed at rest, short-lived, one-time, and paired with encrypted PKCE S256 verifier.
- Refresh tokens and PKCE verifiers use existing `app.services.credential_crypto`; no access tokens, codes, client secrets, raw state, or raw payloads are persisted/logged/returned.
- Google Health is disabled by default and has secret-safe value/file settings using existing configuration conventions.
- Start is authenticated, CSRF-protected, user/IP rate-limited; callback fails closed without a matching active session and user-bound flow.
- Connection reauthorization preserves UUID and atomically replaces token/scopes/expiry/state/error fields.
- Client is fixed-host, GET-only, bounded, timeout-controlled, redirect-free, explicitly typed, and has no arbitrary request method or URL API.
- G1 makes no Nutrition-domain, provider-registry, source-priority, B6, frontend, hydration, or C0 changes.
- Tests are written and observed failing before production implementation; each task runs only its focused tests during implementation and skips project-wide lint/build suites until final verification.

---

### Task 1: Google dependency, settings, redirect, and OAuth primitives

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock`
- Modify: `backend/app/config.py`
- Create: `backend/app/google_health/__init__.py`
- Create: `backend/app/google_health/constants.py`
- Create: `backend/app/google_health/oauth.py`
- Test: `backend/tests/test_google_health_config.py`
- Test: `backend/tests/test_google_health_oauth.py`
- Modify: `.env.example`, `.env.production.example` only if existing deployment contracts require declarations

**Interfaces:**
- Produces `GOOGLE_HEALTH_SCOPE`, `GOOGLE_HEALTH_AUTH_URI`, `GOOGLE_HEALTH_TOKEN_URI`, `GOOGLE_HEALTH_API_BASE_URL`, and `GOOGLE_HEALTH_CALLBACK_PATH` constants.
- Produces `google_health_redirect_uri(public_url: str) -> str`.
- Produces `create_oauth_state() -> str`, `hash_oauth_state(raw_state: str) -> str`, `create_pkce_verifier() -> str`, `pkce_challenge(verifier: str) -> str`, and deterministic `normalize_granted_scopes(scopes: object) -> tuple[str, ...]`.
- Produces an authorization-request builder that accepts a state, verifier, redirect URI, and `reauthorize: bool`, emits only the exact scope, and conditionally emits consent prompting.
- Settings expose disabled-by-default `google_health_enabled`, `google_health_client_id`, `google_health_client_secret`, and `google_health_client_secret_file`; sensitive values are excluded from repr/model dump and use existing file conflict/validation behavior.

- [ ] **Step 1: Write failing tests for exact constants, callback derivation, secret-safe settings, and PKCE/state invariants.**

The tests must assert exact hosts/scope/path, reject a public URL containing a path/query/fragment that would create an unsafe callback, verify random state/verifier and S256 base64url output, verify state hashing never equals raw state, verify sorted deduplicated granted scopes, verify disabled defaults, direct/file conflict rejection, secret-safe repr/model dump, and conditional `prompt=consent`.

- [ ] **Step 2: Run focused tests to verify the expected missing-symbol failures.**

Run from `backend/`:

```bash
pytest -q tests/test_google_health_config.py tests/test_google_health_oauth.py
```

Expected: collection or assertion failures because the Google Health constants/settings/helpers do not yet exist.

- [ ] **Step 3: Resolve official Google dependencies with uv and implement the minimal constants/settings/OAuth helpers.**

Run:

```bash
uv add google-auth google-auth-oauthlib
```

Use the resolved versions in `uv.lock`; do not hand-edit or guess versions. Reuse `_read_secret_file`, `load_secret_files`, Fernet validation, and `Field(exclude=True, repr=False)` patterns. Use only fixed callback path and exact scope.

- [ ] **Step 4: Run focused tests and commit the task.**

```bash
pytest -q tests/test_google_health_config.py tests/test_google_health_oauth.py
```

Commit with a message describing Google Health OAuth primitives and dependency resolution.

---

### Task 2: Encrypted connection/flow models and Alembic migration

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/20260910_0029_google_health_oauth.py`
- Test: `backend/tests/test_google_health_models.py`
- Test: `backend/tests/test_google_health_migration.py`

**Interfaces:**
- Produces `GoogleHealthConnection` with UUID ID, unique user ID, encrypted refresh token, deterministic JSON scopes, state/error/timestamp fields, expiry, and user relationship.
- Produces `GoogleHealthOAuthFlow` with UUID ID, user ID, state hash, encrypted verifier, expiry, consumed timestamp, and creation timestamp.
- Migration upgrades from `20260910_0028` and downgrades cleanly on SQLite/PostgreSQL without touching Nutrition tables.

- [ ] **Step 1: Write failing model and migration tests.**

Test user isolation, one connection per user, cascade metadata, encrypted blobs as bytes, flow expiry/consumption fields, named constraints/indexes, migration head linkage, upgrade/downgrade, and absence of Nutrition-table changes.

- [ ] **Step 2: Run the focused tests and observe failure.**

```bash
pytest -q tests/test_google_health_models.py tests/test_google_health_migration.py
```

Expected: missing model/table/revision failures.

- [ ] **Step 3: Implement the ORM models and explicit additive migration.**

Use existing UUID/FK/index/constraint conventions. Store only Fernet ciphertext in encrypted columns. Keep the migration linear after `20260910_0028`; use SQLite batch operations only where required by the existing migration style.

- [ ] **Step 4: Run focused model tests and commit.**

```bash
pytest -q tests/test_google_health_models.py tests/test_google_health_migration.py
```

Commit the models and migration together.

---

### Task 3: OAuth lifecycle service and API routes

**Files:**
- Create: `backend/app/google_health/service.py`
- Create: `backend/app/google_health/errors.py`
- Create: `backend/app/api/google_health.py`
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/security_events.py`
- Modify: `backend/app/services/security_audit.py` if Google events are intended to persist
- Modify: `backend/app/config.py` for Google-specific rate-limit/timeout settings if needed
- Create: `backend/app/schemas_google_health.py` or extend existing schema module according to local convention
- Test: `backend/tests/test_google_health_api.py`
- Test: `backend/tests/test_google_health_service.py`
- Test: `backend/tests/test_google_health_security.py`

**Interfaces:**
- Produces `start_google_health_oauth(db, user, request) -> authorization_url`.
- Produces `complete_google_health_oauth(db, user, callback_params) -> safe status result`.
- Produces `google_health_status(db, user) -> safe status DTO`.
- Service accepts injectable OAuth exchange/clock/transport seams so tests never contact Google.
- API exposes the three fixed routes and uses existing `require_csrf`, `current_user`, `get_db`, shared user lock, rate limiter, and problem response conventions.

- [ ] **Step 1: Write failing API/service/security tests.**

Cover authenticated and unauthenticated status/start/callback, CSRF and Origin failure, user/IP rate limiting, exact authorization query parameters, no forced consent on ordinary reconnect, flow persistence with hashed state/encrypted verifier, missing session fail-closed, unknown/expired/replayed/cross-user state, token exchange failure mapping, exact granted-scope enforcement, missing refresh token, connection UUID preservation on reauth, encryption failure, invalid_grant transition, safe status fields, safe security events, and no secrets in logs/API responses.

- [ ] **Step 2: Run the focused tests and observe failure.**

```bash
pytest -q tests/test_google_health_api.py tests/test_google_health_service.py tests/test_google_health_security.py
```

Expected: missing route/service/model orchestration failures.

- [ ] **Step 3: Implement the minimal service and routes.**

Use the official Google OAuth library through a narrow adapter. Atomically claim flows under user/row locking, encrypt secrets before commit, preserve connection ID on successful reauthorization, and classify only fixed safe error codes. Never include raw provider errors in exceptions, event details, database fields, or response bodies. Register every event in the catalog and persistent audit allowlist when applicable.

- [ ] **Step 4: Run focused tests and commit.**

```bash
pytest -q tests/test_google_health_api.py tests/test_google_health_service.py tests/test_google_health_security.py
```

Commit the OAuth lifecycle and route integration.

---

### Task 4: Fixed-host read-only Google Health client

**Files:**
- Create: `backend/app/google_health/client.py`
- Create: `backend/app/google_health/transport.py` only if a separate bounded transport is needed
- Test: `backend/tests/test_google_health_client.py`

**Interfaces:**
- Produces `GoogleHealthClient(transport, credentials)` with explicit `get_nutrition_log_page(...)` only.
- Produces typed errors for auth, scope, rate limit, timeout/network, provider unavailable, and invalid response.
- Transport contract has a fixed GET operation and cannot accept arbitrary method/host input.

- [ ] **Step 1: Write failing client tests.**

Assert fixed `https://health.googleapis.com/v4` host, GET-only behavior, no redirects, explicit timeout, bounded page size, safe query encoding, no arbitrary URL/method surface, status/error mapping, invalid JSON rejection without raw-body leakage, and no persistence/logging of raw response bodies.

- [ ] **Step 2: Run focused tests and observe failure.**

```bash
pytest -q tests/test_google_health_client.py
```

Expected: missing client and DTO failures.

- [ ] **Step 3: Implement the minimal bounded client.**

Use an injected `requests`-compatible transport seam. Construct paths only from fixed constants and validated pagination/filter arguments. Keep access tokens in memory through official Google credentials refresh behavior; never write them to the database. Map all external failures to fixed internal error types without retaining response bodies.

- [ ] **Step 4: Run focused client tests and commit.**

```bash
pytest -q tests/test_google_health_client.py
```

Commit the read-only client.

---

### Task 5: Bounded Nutrition Log DTO and page operation

**Files:**
- Modify: `backend/app/google_health/client.py` or create `backend/app/google_health/dtos.py` if the DTO boundary is clearer
- Test: `backend/tests/test_google_health_nutrition_log.py`
- Test: `backend/tests/test_google_health_no_persistence.py`

**Interfaces:**
- Produces validated in-memory DTOs for one Nutrition Log page, including bounded data points, validated `next_page_token`, and civil-time filter handling from `nutrition_log.interval.civil_start_time`.
- Does not create or modify Nutrition-domain ORM rows.

- [ ] **Step 1: Write failing page/DTO tests.**

Cover valid page parsing, absent/invalid next-page token, page-size limits, civil-time boundaries, malformed interval values, omitted hydration access, raw-payload non-persistence, and aggregate-only smoke result semantics.

- [ ] **Step 2: Run focused tests and observe failure.**

```bash
pytest -q tests/test_google_health_nutrition_log.py tests/test_google_health_no_persistence.py
```

Expected: missing DTO/page-operation failures.

- [ ] **Step 3: Implement validation and in-memory return types.**

Reject malformed provider shapes. Return only validated DTOs and pagination metadata. Do not call any Nutrition-domain ingestion service or persist response content.

- [ ] **Step 4: Run focused tests and commit.**

```bash
pytest -q tests/test_google_health_nutrition_log.py tests/test_google_health_no_persistence.py
```

Commit the Nutrition Log transport DTO boundary.

---

### Task 6: Full verification, live-gate decision, and branch review

**Files:**
- Modify only files required by failing verification or review findings
- Test: existing C0 and YAZIO regression suites as selected by repository CI

- [ ] **Step 1: Run the focused complete G1 suite.**

```bash
pytest -q tests/test_google_health_*.py
```

Also verify the C0 watermark and no Google provider registry/source-priority changes with targeted repository checks.

- [ ] **Step 2: Run repository verification gates.**

Run the current commands from CI/configuration, including backend tests, Ruff, MyPy, Alembic head/migration checks, and the isolated PostgreSQL migration path. Do not report unavailable or failed checks as passed.

- [ ] **Step 3: Run independent static change review.**

Dispatch the repository-required `change-review` agent for this security-sensitive OAuth/credential/migration change. Resolve all material findings before completion.

- [ ] **Step 4: Evaluate live OAuth prerequisites.**

If no configured test client exists with the exact callback registered, do not open a browser or attempt OAuth. Report the exact required redirect URI and mark live auth/read `OPEN`. If configured, run only the bounded auth/read smoke and report aggregate counts without health values.

- [ ] **Step 5: Inspect the complete diff and report exact final status lines.**

Verify no secrets, raw payloads, debug output, TODO placeholders, Nutrition-domain modifications, C0 registry changes, or unrelated user changes are present. Report:

```text
G1 GOOGLE HEALTH OAUTH FOUNDATION: PASSED/FAILED
GOOGLE HEALTH LIVE AUTH: PASSED/FAILED/OPEN
GOOGLE HEALTH LIVE READ: PASSED/FAILED/OPEN
NUTRITION DOMAIN MODIFIED: NO
READY FOR GOOGLE HEALTH G2: YES/NO
```
