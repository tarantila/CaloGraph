# Production Docker operation

## Minimum checklist

- Start from `.env.production.example`, set `ENVIRONMENT=production`, and
  replace every example value. The application reports all rejected variable
  names together without printing their values.
- `.env` is owned by the operator and has file mode `0600`.
- Run `scripts/init-secrets.sh` for a new installation. The ignored
  `secrets/` directory is owned by the operator with mode `0700`; its files use
  mode `0444`. The restrictive parent directory prevents other host users from
  traversing to those files, while the read-only file mode lets non-root
  container UIDs read only the individual file Compose mounts. Existing
  installations use the explicit `scripts/migrate-env-secrets.sh` procedure
  instead so database and YAZIO keys are not rotated accidentally, followed by
  `scripts/migrate-mfa-secret.sh` when adding MFA to an installation that
  already uses file-backed secrets.
- Compose mounts the five source files as service-scoped secrets under
  `/run/secrets`; it does not place their values or a password-bearing database
  URL in container environments. PostgreSQL receives only its password,
  `backend` receives all five, `yazio-scheduler` receives only the database
  password, YAZIO credential key, and shared rate-limit key required by the
  deployment-wide provider circuit breaker, and `frontend` receives none. The
  scheduler never receives the session or MFA-encryption key.
- PostgreSQL remains on the internal Docker network without a host port.
- Port `8180` remains bound to `127.0.0.1`; external access is provided only
  through a TLS reverse proxy.
- `CALOGRAPH_PUBLIC_URL` contains the final externally reachable HTTPS origin.
  CaloGraph uses it for invitation links.
- Set `COOKIE_SECURE=true`, list the exact public hostname in `TRUSTED_HOSTS`,
  and list the exact HTTPS origin in `TRUSTED_ORIGINS`. Wildcard hosts and HTTP
  production origins are rejected.
- `CALOGRAPH_EDGE_SUBNET`, `CALOGRAPH_EDGE_GATEWAY_IP`, and
  `CALOGRAPH_FRONTEND_PROXY_IP` describe a dedicated, non-overlapping edge
  network. Compose derives backend proxy trust from the exact frontend `/32`;
  production rejects subnet-wide trust.
- Set `ENABLE_HSTS=true` after the domain is permanently available exclusively
  over HTTPS. Production mode will not start while it is false.
- Keep `HSTS_INCLUDE_SUBDOMAINS=false` unless every descendant of the exact
  CaloGraph hostname permanently supports HTTPS. It does not affect sibling
  hosts, but an accidental policy for a plain-HTTP descendant can make that
  host unreachable in browsers until the cached policy expires.
- Keep `ENABLE_API_DOCS=false` for an Internet-facing installation unless the
  generated schema is required and the public proxy restricts both
  `/api/docs` and `/api/openapi.json` to an operator network.
- Backups are encrypted with a dedicated `age` recipient, stored outside the
  Docker host with an immutable or offline copy, and a restore has been tested
  in practice. Keep the long-lived private identity off the Docker host; make
  only a read-only working copy available during a controlled backup,
  verification, or restore, then remove that copy immediately.
- Docker and host security updates are installed regularly.

## Runtime

The Compose services use `restart: unless-stopped`, internal health checks,
`no-new-privileges`, read-only filesystems for the frontend and Python
services, and bounded Docker log rotation. The YAZIO scheduler writes a
heartbeat to its ephemeral `/tmp`; its health check detects a stuck scheduler
independently of the backend.

Runtime traffic is split across three Docker networks. `frontend` and
`backend` share the `edge` network, `backend`, `yazio-scheduler`, and
`postgres` share the externally isolated `data` network, and only the
scheduler joins the additional `egress` network. PostgreSQL therefore has no
external gateway and is not reachable from the frontend. The frontend,
backend, and scheduler drop every Linux capability. PostgreSQL also drops all
capabilities and adds back only `CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`,
and `SETUID`, which its official entrypoint needs for fresh initialization and
the switch to its unprivileged runtime user.

The root `Dockerfile` provides separate `backend-runtime` and
`frontend-runtime` targets. Both production targets switch to an unprivileged
user; the frontend uses the purpose-built NGINX unprivileged runtime with all
writable state kept in an ephemeral `/tmp`. `WEB_CONCURRENCY`, `UVICORN_LOG_LEVEL`,
`UVICORN_TIMEOUT_KEEP_ALIVE`, `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN`, and
`UVICORN_ACCESS_LOG` are runtime settings and can be changed through `.env`
without rebuilding. Keep `RUN_MIGRATIONS=true` unless migrations are managed
explicitly as a separate deployment step.

Login throttling defaults to 30 attempts per IP network in five minutes and ten
failed attempts per normalized account identifier in 15 minutes. Password
changes allow five failed current-password checks in 15 minutes. These limits
and windows can be adjusted with the documented `LOGIN_*` and
`PASSWORD_CHANGE_*` values in `.env`; keep the temporary windows bounded to
avoid permanent account lockouts.

New single-factor passwords require at least 15 characters and are checked
locally against a bundled digest blocklist derived from SecLists; CaloGraph
does not send password material to an external breach service. Production
sessions use an `__Host-` cookie with `Secure`, `HttpOnly`, `Path=/`, no
`Domain`, and `SameSite=Lax`. The server enforces a 24-hour idle timeout and a
30-day absolute timeout by default. `SESSION_IDLE_TIMEOUT_HOURS` can be lowered
or raised to at most seven days; `SESSION_ABSOLUTE_TIMEOUT_DAYS` can be lowered
but not raised above 30 days. The scheduler deletes expired, idle, and revoked
session rows hourly. Active session and import-token timestamps are written at
most once every five minutes so read-heavy dashboard traffic does not turn into
an equal number of database commits.

Users can enable TOTP in their account settings. The pending and enabled TOTP
secret is encrypted with a dedicated key that is mounted only into the
backend. Ten one-time recovery codes are generated during activation and
stored only as HMAC digests. Accepted TOTP time steps are persisted to prevent
replay, while login and MFA-management attempts use temporary database-backed
limits. If a user loses all factors, an active
administrator can first deactivate the account and reset its authenticators
under **Konto → Benutzerverwaltung**. The equivalent operator command is
`python -m app.cli reset-authenticators --username USER --admin-username ADMIN
--confirm USER`. Both paths require the administrator's current password and
enabled MFA, remove every target authenticator, and revoke sessions and API
tokens. The target stays inactive until separately reactivated.

Users can enroll discoverable passkeys after confirming their password and,
when enabled, TOTP. Passkey authentication requires user verification and is
bound to the exact `CALOGRAPH_PUBLIC_URL` host and origin. Production passkeys
therefore depend on a valid HTTPS deployment; changing the public hostname
requires users to enroll new passkeys for the new relying-party ID. Temporary
WebAuthn challenges are stored in PostgreSQL, consumed exactly once, and
deleted hourly after expiry.

Historical Apple Health uploads default to 500 MiB. The bundled Nginx permits
512 MiB through `NGINX_MAX_UPLOAD_BYTES` on that one endpoint for multipart
overhead, forwards the body without proxy buffering, and admits only one
concurrent large upload. The backend uses a 600 MiB
`BACKEND_TMPFS_BYTES`-controlled ephemeral `/tmp` tmpfs for Starlette's upload
spool; no health export is written to a host bind mount or persistent Docker
volume. An outer reverse proxy must allow slightly more than 500 MiB and use a
request timeout suitable for the connection, or the upload will be rejected
before reaching CaloGraph.

File-import rate limits, XML record/sample caps, the 2-GiB expanded ZIP limit,
and the 500-record database batch size are configurable through the
`MAX_IMPORT_*`, `MAX_ZIP_*`, `IMPORT_BATCH_SIZE`, and `FILE_IMPORT_*` settings
in `.env`. Measure representative exports before raising them.
Production validation requires the Nginx limit to include multipart overhead,
the backend tmpfs to include additional reserve space, JSON not to exceed the
general upload limit, and the expanded ZIP ceiling not to be smaller than the
upload ceiling. The expanded XML is streamed and does not need to fit into
tmpfs.

Direct YAZIO access is enabled by `YAZIO_ENABLED=true`. Before public
operation, keep the default explicit 3.05-second connect timeout, 15-second
read timeout, 25-second login deadline, and five-minute full-operation
deadline unless measured provider behavior requires a change. Saving
credentials and starting a manual sync share independent per-user and per-IP
limits of two attempts per ten minutes. PostgreSQL advisory locks cap active
YAZIO operations at two across all backend workers and the scheduler, while a
database-backed circuit breaker pauses provider calls after five provider or
deadline failures in ten minutes. No Redis service or general-purpose job
queue is required.

Authentication failures are not retried and do not affect the shared circuit
breaker. They disable scheduled sync only for the affected account; saving and
successfully verifying new credentials enables it again. Set
`YAZIO_ENABLED=false` and recreate the backend and scheduler to use the
operational kill switch:

```bash
docker compose up -d --force-recreate backend yazio-scheduler
```

Compose exposes optional CPU, memory, and PID ceilings for every runtime
container. They remain disabled by default because legitimate Apple Health
histories and self-hosted machines vary widely in size; Docker-host or parent
cgroup constraints still apply. For an internet-facing installation, measure
the workload and uncomment the concrete `FRONTEND_*`, `BACKEND_*`,
`YAZIO_SCHEDULER_*`, and `POSTGRES_*` starting values in the environment
template. A 2-GiB backend ceiling is the initial recommendation for the
default 500-MiB upload configuration. Keep it comfortably above
`BACKEND_TMPFS_BYTES` because tmpfs pages count toward container memory usage.
Leaving the variables unset is an explicit operational acceptance of the
host-level resource-exhaustion risk.

The backend currently performs migrations before starting its single
deployment. Before running multiple backend replicas, move migrations into a
separate one-shot deployment job so concurrent replicas cannot attempt the
same migration.

## Fail-closed production validation

The backend checks production safety before running migrations. The YAZIO
scheduler performs a role-specific check before entering its loop and therefore
does not need access to web-session or reverse-proxy secrets. Backend startup
is rejected when the public origin is not HTTPS, secure cookies or HSTS are
disabled, known development secrets or database passwords are present, secrets
are reused, effective proxy trust remains loopback-only or uses the generic
Docker address pool, direct YAZIO access has no valid Fernet key, or upload
capacities contradict each other. IPv4 proxy networks broader than `/16` and
IPv6 networks broader than `/64` are rejected. Development and test
environments remain able to use localhost HTTP.

Changing only `ENVIRONMENT=production` is therefore intentionally insufficient.
The real domain, exact Docker subnet, independent secrets, and TLS policy must
be configured first.

Check status:

```bash
docker compose config --quiet
docker compose ps
docker compose logs --no-color --tail=100 backend yazio-scheduler frontend
curl --fail http://127.0.0.1:8180/health
```

## Network and TLS

An example configuration is available in
[reverse-proxy.md](reverse-proxy.md). Do not expose CaloGraph directly through
a public HTTP port. The firewall and reverse proxy should open only the ports
that are actually required.

## Storage and monitoring

Docker logs rotate per service across at most three files of 10 MB each. This
does not provide durable audit retention and does not replace free-space
monitoring. The backend and scheduler emit allowlisted, pseudonymous JSON
security events; Internet-facing installations should forward them to a
protected host or remote collector. Client IP addresses remain in the public
proxy access log so host controls such as Fail2ban can act on them. Event fields,
privacy rules, retention guidance, and alerting recommendations are documented
in [security-monitoring.md](security-monitoring.md). Pay particular attention to the
PostgreSQL volume, encrypted backup storage, and failed YAZIO runs shown in the
data-status view. Full-disk or volume encryption is still required for live
PostgreSQL data. A `partial_failed` historical import is safe to retry with the
same file; completed checkpoints are retained and deduplicated.

## Updates

The recommended process is documented in
[backup-restore.md](backup-restore.md) and reproduced by
`scripts/update-containers.sh`. Every update begins with a validated database
dump. Source updates and container updates remain separate, auditable steps.

Release images are available from GHCR after the complete CI, exact-image
browser/production-smoke tests, and high/critical vulnerability gate succeed.
Compose defaults to the public GHCR image repositories. Set a release
`CALOGRAPH_VERSION`, then start with `--no-build` so deployment cannot silently
replace the tested image with a local build. Image tags, signed attestations,
SPDX SBOMs, dependency automation, and cleanup rules are described in
[supply-chain.md](supply-chain.md).
