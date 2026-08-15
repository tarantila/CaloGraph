# Architecture

## Components

`frontend` contains the statically built Vue 3 application and Nginx. Nginx
serves the SPA and proxies `/api/` internally to `backend`. `backend` is a
FastAPI monolith with modular HTTP, authentication, import, service, and
analytics layers. `yazio-scheduler` uses the same backend image and starts due
YAZIO imports without exposing another HTTP port. `postgres` is the only
persistent runtime dependency and is not bound to the host.

## Data flow

```text
iPhone exporter ── HTTPS/JSON ─────┐
Apple Health XML/ZIP ─ browser ─────┼─ FastAPI ─ adapter ─ normalization ─ PostgreSQL
YAZIO JSON / manual retrieval ──────┤
YAZIO ─ scheduler ─ user account ───┤
Browser ─ Vue/Nginx ────────────────┘                                   ↓
Browser ← Vue/Nginx ← user-scoped API responses ← analytics service ─────┘
```

Adapters produce canonical samples. Only services write data, and analytics
reads only the internal model. Every import batch, sample, and YAZIO connection
has a `user_id`; database queries are always restricted to the authenticated
user. A future native iOS app will use the existing `calograph_sync_v1` format.

## Operational decisions

`ENVIRONMENT` is explicit. Before production migrations or the scheduler loop,
CaloGraph validates the HTTPS origin, cookie and HSTS policy, independent
secrets, database credentials, request allowlists, YAZIO encryption, and
upload-capacity relationships. Unsafe production configuration aborts startup
with variable names but never secret values. Development keeps localhost HTTP
available.

Compose separates browser-facing proxy traffic, internal data traffic, and
scheduler egress. PostgreSQL is attached only to the externally isolated data
network; the frontend never shares a network with it. Application containers
drop all Linux capabilities, while PostgreSQL receives only the small set its
official entrypoint needs to initialize ownership and switch users. Optional
per-service memory, CPU, and PID ceilings can be enabled through the deployment
environment after measuring representative imports.

Migrations abort container startup on failure. The backend uses multiple
workers, so rate limits use atomic
PostgreSQL upserts. Login protection has independent buckets for all attempts
from an IP address (IPv6 is grouped by `/64`) and failed attempts for a
pseudonymized account identifier. Expired buckets are removed by the scheduler.
Redis and a general-purpose background queue are deliberately omitted. The
single YAZIO scheduler checks for due connections at a configurable interval.
The same hourly maintenance cycle removes revoked sessions and sessions beyond
their server-side idle or absolute lifetime. Internet-facing sessions use a
host-only secure cookie; development retains a separate non-prefixed localhost
cookie so HTTP development remains usable.
Credentials are encrypted per user with Fernet. The host stores the key as an
operator-controlled file outside the image; Compose mounts it only into the
backend and scheduler. Database, session, and rate-limit secrets use the same
file-based mechanism with narrower service grants. The scheduler receives the
rate-limit secret because its provider failures must address the same
deployment-wide circuit-breaker buckets as HTTP workers; it never receives the
session secret. `.env` contains paths, not secret values. The files must be
included in the encrypted backup and permissions strategy together with the
database. YAZIO network work runs in a short-lived isolated child process with
explicit request timeouts and a parent-enforced absolute deadline.

TOTP uses a separate Fernet key mounted only into the backend. Password login
for an enrolled user yields a signed five-minute challenge instead of a
session; a session is issued after the second factor succeeds. The database
stores encrypted TOTP seeds, the last accepted time step for replay
protection, and HMAC-only one-time recovery codes. MFA-management actions
require the current password plus a factor and preserve only the current
session.

Passkeys use WebAuthn discoverable credentials for passwordless sign-in.
CaloGraph stores only the credential public key, user handle, metadata, and
signature counter. Five-minute registration and authentication challenges are
persisted in PostgreSQL and claimed under a row lock, making each challenge
single-use across backend workers. Registration is additionally bound to the
current user session. Verification requires a matching
`CALOGRAPH_PUBLIC_URL` relying-party host and origin plus authenticator user
verification.
PostgreSQL advisory locks coordinate user operations across backend workers
and the scheduler. Ordinary user mutations hold a shared per-user lock;
deactivation, reactivation, and hard deletion hold the exclusive form plus a
deployment-wide administrator-invariant lock. The lifecycle service re-reads
the account only after acquiring those locks and commits status changes,
credential/session invalidation, YAZIO pausing, and deletion atomically.
Separate YAZIO advisory locks still enforce one YAZIO operation per user and a
small deployment-wide provider concurrency budget. PostgreSQL also stores
temporary rate-limit and circuit-breaker buckets, so these protections need no
additional service.

Historical uploads are streamed during the request and expose upload progress
in the browser. Nginx forwards the Apple Health request without buffering it a
second time; its multipart ceiling and Starlette's ephemeral `/tmp` spool are
driven by validated byte settings. The XML adapter yields one record at a time,
services perform bulk lookups and commit accepted samples in 500-record batches,
and a PostgreSQL advisory lock permits only one import per user across backend
workers. Non-ZIP failures after a checkpoint are retained as `partial_failed`
so a retry can continue idempotently; ZIP integrity, XML, and limit failures
roll back their import completely.
