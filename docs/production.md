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
  installations use the explicit
  `scripts/migrate-env-secrets.sh` procedure instead so database and YAZIO keys
  are not rotated accidentally.
- Compose mounts the four source files as service-scoped secrets under
  `/run/secrets`; it does not place their values or a password-bearing database
  URL in container environments. PostgreSQL receives only its password,
  `backend` receives all four, `yazio-scheduler` receives only the database
  password and credential key, and `frontend` receives none.
- PostgreSQL remains on the internal Docker network without a host port.
- Port `8180` remains bound to `127.0.0.1`; external access is provided only
  through a TLS reverse proxy.
- `CALOGRAPH_PUBLIC_URL` contains the final externally reachable HTTPS origin.
  CaloGraph uses it for invitation links.
- Set `COOKIE_SECURE=true`, list the exact public hostname in `TRUSTED_HOSTS`,
  and list the exact HTTPS origin in `TRUSTED_ORIGINS`. Wildcard hosts and HTTP
  production origins are rejected.
- `TRUSTED_PROXY_NETWORKS` contains only the Docker network from which the
  frontend reaches the backend; wildcard trust is rejected.
- Set `ENABLE_HSTS=true` after the domain is permanently available exclusively
  over HTTPS. Production mode will not start while it is false.
- Backups are encrypted with a dedicated `age` recipient, stored outside the
  Docker host with an immutable or offline copy, and a restore has been tested
  in practice. Keep the private `age` identity off the Docker host.
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
does not replace free-space monitoring. Pay particular attention to the
PostgreSQL volume, encrypted backup storage, and failed YAZIO runs shown in the
data-status view. Full-disk or volume encryption is still required for live
PostgreSQL data. A `partial_failed` historical import is safe to retry with the
same file; completed checkpoints are retained and deduplicated.

## Updates

The recommended process is documented in
[backup-restore.md](backup-restore.md) and reproduced by
`scripts/update-containers.sh`. Every update begins with a validated database
dump. Source updates and container updates remain separate, auditable steps.
