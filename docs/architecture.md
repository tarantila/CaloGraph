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

Migrations run before the backend process and abort container startup on
failure. The backend uses multiple workers, so rate limits are stored in
PostgreSQL. Redis and a general-purpose background queue are deliberately
omitted. The single YAZIO scheduler checks for due connections at a
configurable interval. Credentials are encrypted per user with Fernet; the
separate key comes from `.env` and must be included in the backup and
permissions strategy together with the database. Historical uploads are
streamed during the request and expose upload progress in the browser.
