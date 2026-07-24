# Production Docker operation

## Minimum checklist

- `.env` is owned by the operator and has file mode `0600`.
- `POSTGRES_PASSWORD`, `SESSION_SECRET`, `RATE_LIMIT_SECRET`, and
  `CREDENTIAL_ENCRYPTION_KEY` are independent random values.
- PostgreSQL remains on the internal Docker network without a host port.
- Port `8180` remains bound to `127.0.0.1`; external access is provided only
  through a TLS reverse proxy.
- `CALOGRAPH_PUBLIC_URL` contains the final externally reachable HTTPS origin.
  CaloGraph uses it for invitation links and automatically adds it to the host
  and CSRF-origin allowlists.
- With HTTPS, set `COOKIE_SECURE=true`, an exact HTTPS origin in
  `TRUSTED_ORIGINS`, and the public hostname in `TRUSTED_HOSTS` only when
  additional origins or host aliases are required.
- `TRUSTED_PROXY_NETWORKS` contains only the Docker network from which the
  frontend reaches the backend; wildcard trust is rejected.
- Enable `ENABLE_HSTS=true` only after the domain is permanently available
  exclusively over HTTPS.
- Backups are encrypted, stored outside the Docker host, and a restore has been
  tested in practice.
- Docker and host security updates are installed regularly.

## Runtime

The Compose services use `restart: unless-stopped`, internal health checks,
`no-new-privileges`, read-only filesystems for the frontend and Python
services, and bounded Docker log rotation. The YAZIO scheduler writes a
heartbeat to its ephemeral `/tmp`; its health check detects a stuck scheduler
independently of the backend.

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
data-status view.

## Updates

The recommended process is documented in
[backup-restore.md](backup-restore.md) and reproduced by
`scripts/update-containers.sh`. Every update begins with a validated database
dump. Source updates and container updates remain separate, auditable steps.
