# Backup, restore, and updates

## 1. Protection model and trust boundary

A complete CaloGraph backup set has two matching encrypted artifacts:

1. a PostgreSQL custom-format dump containing health data, accounts, and encrypted
   YAZIO credentials; and
2. an optional environment-and-secrets archive containing `.env` and the
   database, session, rate-limit, credential-encryption, and MFA key files.

Both are encrypted with [`age`](https://github.com/FiloSottile/age) for one or
more public recipients. The dedicated backup agent is opt-in and disabled by
default. It has no age identity/private key, Docker socket, application runtime
access, frontend access, or backend secrets. It connects only to PostgreSQL on
the internal data network and writes only encrypted artifacts and a sanitized
status report to dedicated mounts. `read_only`, `cap_drop: ALL`,
`no-new-privileges`, `init`, and resource limits are enabled in Compose.

The host must still provide full-disk/volume encryption for live PostgreSQL and
recovery systems. `age` protects exported artifacts, not a running database or
an exposed host.

<a id="2"></a>
## 2. Health reporting and status interpretation

The agent atomically publishes a versioned `status.json` with state and reason
codes, timestamps, schedule/retention metadata, component status, logical
target, freshness threshold, and private matching metadata needed to correlate
external proofs. The backend sanitizes that report before the read-only admin
endpoint `/api/v1/admin/backup-status` exposes it to administrators only:
recipient strings, paths, filenames, command output, and secret values are
never exposed.

`Healthy` operation means the database and every required component reported a
successful, fresh, matching backup. It deliberately does **not** require
external archive verification: archive processing is an independent recovery
check. `Attention required` means operation freshness, completeness, matching,
or required-component data needs action. `Failed` means the latest backup
attempt explicitly failed and outranks other states. `Unknown` means no report,
a malformed report, or a report too old to trust; an API transport failure is
also unavailable, not a failed backup. `Disabled` means the agent is
deactivated.

The recovery section reports two independent checks:

* **External archive verification** authenticates and fully processes one
  selected encrypted archive with a private age identity. It records only
  sanitized state and time in the API; no database is restored and this is not
  a production restore. The latest artifact must exactly match its checksum
  and the verification timestamp must not be in the future.
* **Isolated restore test** is an operator-run test of one selected artifact in
  a disposable PostgreSQL container and volume. It never uses the live
  database, is not part of daily backup automation, and is recommended after
  setup and every 90 days. Its sanitized status is read-only through the
  admin API and contains state, safe result metadata, and timestamps; artifact
  names, SHA-256 values, paths, command output, and secrets are excluded.

<a id="3"></a>
## 3. Create recipients and schedule automation

Install `age` on a trusted administration system and create a dedicated key:

```bash
install -d -m 700 /example/private
age-keygen -o /example/private/backup-identity.txt
age-keygen -y /example/private/backup-identity.txt \
  > /example/config/backup-recipients.txt
chmod 600 /example/private/backup-identity.txt
chmod 644 /example/config/backup-recipients.txt
```

The recipients file contains public data and may be mounted read-only by the
agent. Keep every private identity outside the Docker host, ideally offline
except during controlled verification or restore. Add multiple recipients for
independent recovery custodians; each recipient can decrypt the same artifact.
Losing every private identity makes the artifacts unrecoverable. A compromised
identity permits decryption, so revoke/rotate recipients by creating a new
recipient set and a fresh backup set; old artifacts remain recoverable only by
old authorized identities.

Set `BACKUP_AGE_RECIPIENTS_FILE` to the public file, choose
`BACKUP_SCHEDULE_TIME` (local `HH:MM`), `CALOGRAPH_TIMEZONE`, and
`BACKUP_RETENTION_DAYS`. Start the single dedicated Compose agent deliberately:

```bash
BACKUP_AGENT_ENABLED=true docker compose --profile backup up -d backup-agent
```

The backend reads the status volume read-only. The restore-test status path
defaults to `/var/lib/calograph-backups/status/restore-test.json`, and the
restore-test interval defaults to 90 days (bounded to 1–365 days). The
backup-agent receives these values for the shared-volume boundary but never
writes restore-test success or receives a private identity.
Scheduling is internal to the restart-tolerant agent; no host cron is needed.
The base Compose service has `BACKUP_INCLUDE_SECRETS=false` and no mounts for
secret sources. To include the optional environment archive, use the explicit
override with the same `backup-agent` service and configure both source paths:

```bash
BACKUP_AGENT_ENABLED=true \
BACKUP_SECRETS_SOURCE_FILE=/example/config/calograph.env \
BACKUP_SECRETS_SOURCE_DIR=/example/config/secrets \
docker compose \
  -f docker-compose.yml \
  -f docker-compose.backup-secrets.yml \
  --profile backup up -d backup-agent
```

The override sets `BACKUP_INCLUDE_SECRETS=true`,
`SECRETS_SOURCE_FILE=/backup-input/environment.env`, and
`SECRETS_SOURCE_DIR=/backup-input/secrets`, and adds only read-only source
mounts. It does not create a second service or scheduler/status writer.
Secret-source mounts are absent from the generic agent and fail closed unless
the override is explicitly selected. Values are never logged or written as
plaintext. The agent performs managed-only retention: it removes only its
exact artifact/checksum pairs and randomized partials, with symlink, hardlink,
and path checks. Unrelated files are never selected by broad deletion.

The agent runs as the configured non-root `CALOGRAPH_UID`/`CALOGRAPH_GID`
(default `10001:10001`). Ensure the public recipients file and, when enabled,
the read-only `.env`/`secrets/` sources are readable by that account; never
solve permission errors by running the agent as root or mounting the private
identity.

## 4. Manual database and secrets creation

For a host-side database backup, only the public recipients file is needed:

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/example/config/backup-recipients.txt
BACKUP_DIR=/example/backups scripts/backup-postgres.sh
```

<a id="5"></a>
## 5. External archive verification

Private-key operations are deliberately separate from automated creation. On a
trusted administration system, select one encrypted database archive:

```bash
export BACKUP_AGE_IDENTITY_FILE=/example/private/backup-identity.txt
scripts/verify-backup.sh /example/backups/calograph-TIMESTAMP.dump.age
```

The verifier checks the adjacent SHA-256 checksum first, decrypts the
ciphertext, and makes `pg_restore` process the complete custom archive while
discarding generated SQL. It writes no decrypted dump. This is **External
archive verification** (`ARCHIVE_VERIFIED`), not a restore: no database is
changed and no production data is replaced. Verify a matching secrets archive
with `age --decrypt ... | tar --list --file -`; do not extract it onto a
shared system. A checksum-only check is not full archive verification.

When the agent should reflect a successful archive verification, configure
`BACKUP_DATABASE_VERIFICATION_STATUS_FILE` and, when the optional secrets
archive is enabled, `BACKUP_SECRETS_VERIFICATION_STATUS_FILE` to files in its
writable status volume. The verifier writes a record only after checksum,
age authentication, and archive processing succeed. The record is valid only
for that exact artifact and digest. The backend exposes only state and time;
it never exposes the artifact name, digest, path, command output, or secrets.
With a Compose named volume, hand off the sanitized record through the agent:

```bash
docker compose exec -T --user calograph-backup backup-agent \
  sh -c 'cat > /var/lib/calograph-backups/status/database-verification.json' \
  < /example/tmp/database-verification.json
```

Never mount the private identity into CaloGraph runtime services or
`backup-agent`. A later backup is operation-healthy without this check and
returns the archive summary to `not_verified` until the selected newest
artifact is checked again.

<a id="6"></a>
## 6. Operator-run isolated restore test

Run the isolated test for one explicit encrypted `.dump.age` artifact:

```bash
export BACKUP_AGE_IDENTITY_FILE=/example/private/backup-identity.txt
BACKUP_RESTORE_TEST_STATUS_FILE=/example/status/restore-test.json \
  scripts/isolated-restore-test.sh \
  /example/backups/calograph-TIMESTAMP.dump.age
```

The script requires an owner-only private identity, a regular non-linked
artifact, a safe basename, and a valid adjacent checksum. It verifies the
checksum before decryption, then streams `age --decrypt` directly into
`pg_restore` in a disposable isolated PostgreSQL container. The image defaults
to `postgres:18.4-alpine` and can be selected with the safe
`BACKUP_RESTORE_POSTGRES_IMAGE` variable. A temporary network and volume are
used; the normal `postgres-data` volume, live database, and CaloGraph Compose
network are never mounted or contacted. Silent schema and consistency
assertions check `alembic_version`, `users`, and basic invariants without
printing health or user values. The generated database password is ephemeral
and never printed.

Temporary containers, networks, volumes, and files are removed on both
success and failure. Only after restore checks and cleanup preparation succeed
does the script atomically publish a sanitized, versioned restore-test record.
It records `RESTORE_TESTED` on success or an allow-listed failure code on
failure, retaining a prior successful timestamp when possible. The normal
Compose named status volume can be handed off with:

```bash
docker compose exec -T --user calograph-backup backup-agent \
  sh -c 'cat > /var/lib/calograph-backups/status/restore-test.json' \
  < /example/tmp/restore-test.json
```

The agent has no private age identity and never writes restore-test success.
Restore-test evidence is read-only through `/api/v1/admin/backup-status`; there
is no web write endpoint. The UI states `never_tested`, `current`, `due`,
`unknown`, or `failed`. Missing, malformed, or future evidence is `unknown`,
not `never_tested`; after a successful test it is due after the documented
90-day interval.

## 7. Destructive restore and new-host recovery

Restore to a live installation is a separate, destructive operator action and
requires explicit confirmation:

```bash
export BACKUP_AGE_IDENTITY_FILE=/example/private/backup-identity.txt
CONFIRM_RESTORE=calograph scripts/restore-postgres.sh \
  /example/backups/calograph-TIMESTAMP.dump.age
```

The existing workflow verifies before stopping services, streams decryption
directly to `pg_restore --clean --if-exists --no-owner`, applies migrations,
and starts services. It does not overwrite `.env` or secret sources. Keep
`CREATED`, external `ARCHIVE_VERIFIED`, and isolated restore-test evidence as
separate operational records. The production smoke test remains a separate
regression test and does not replace checking a selected user artifact.

If the running backup agent uses the explicit secrets override, the restore
workflow detects that same service configuration and preserves it when
restarting the single agent.

Before a new-host recovery:

1. Install Docker, the repository, and `age`; recover the matching environment
   archive using the private identity and set files to owner-only permissions.
2. Use the same PostgreSQL major version and run the isolated test on the
   complete pair before any destructive restore.
3. Run the restore workflow, then verify login, data status, MFA, encrypted
   YAZIO credentials, and one manual synchronization.

PostgreSQL major upgrades require dump/restore or `pg_upgrade`; changing only
an image tag is insufficient. Keep at least one off-host copy and one
immutable or offline copy. Define retention/deletion according to health-data
obligations and encrypt every live or temporary recovery system.

## 8. Legacy migration and updates

Existing plaintext dumps are not changed automatically. Encrypt one explicitly,
verify it independently, and then remove the plaintext according to policy:

```bash
BACKUP_AGE_RECIPIENTS_FILE=/example/config/backup-recipients.txt \
 scripts/encrypt-existing-backup.sh /example/backups/calograph-LEGACY.dump
```

Legacy direct secrets can be moved into source files with
`CONFIRM_SECRET_MIGRATION=calograph scripts/migrate-env-secrets.sh`; this never
prints values. An independent MFA key can be created with
`CONFIRM_MFA_SECRET_MIGRATION=calograph scripts/migrate-mfa-secret.sh`.

After selecting a tested application version through `CALOGRAPH_VERSION`, run
the existing controlled update workflow. Back up and verify first, update
PostgreSQL/backend/frontend together, wait for health, and inspect logs. Never
place a private identity in production Compose or the backup-agent container.
