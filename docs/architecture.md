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

Migrations abort container startup on failure. The backend uses multiple
workers, so rate limits use atomic
PostgreSQL upserts. Login protection has independent buckets for all attempts
from an IP address (IPv6 is grouped by `/64`) and failed attempts for a
pseudonymized account identifier. Expired buckets are removed by the scheduler.
Redis and a general-purpose background queue are deliberately omitted. The
single YAZIO scheduler checks for due connections at a configurable interval.
Credentials are encrypted per user with Fernet; the separate key comes from
`.env` and must be included in the backup and permissions strategy together
with the database. YAZIO network work runs in a short-lived isolated child
process with explicit request timeouts and a parent-enforced absolute deadline.
PostgreSQL advisory locks enforce one operation per user and a small
deployment-wide concurrency budget across HTTP workers and the scheduler. The
same database also stores temporary rate-limit and circuit-breaker buckets, so
these protections need no additional service.

Historical uploads are streamed during the request and expose upload progress
in the browser. Nginx forwards the Apple Health request without buffering it a
second time; its multipart ceiling and Starlette's ephemeral `/tmp` spool are
driven by validated byte settings. The XML adapter yields one record at a time, services
perform bulk lookups and commit accepted samples in 500-record batches, and a
PostgreSQL advisory lock permits only one import per user across backend
workers. A failed import after a checkpoint is retained as `partial_failed` so
a retry can continue idempotently.
