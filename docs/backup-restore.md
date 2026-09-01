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

## 2. Health reporting and status interpretation

The agent atomically publishes a versioned `status.json` containing only state
and reason codes, timestamps, schedule/retention metadata, component status, the
logical target, and the configured freshness threshold. It never contains
recipient strings, paths, filenames, command output, or secret values. The
read-only admin endpoint `/api/v1/admin/backup-status` exposes that contract to
administrators only.

`Healthy` means the database and required environment/secrets components are
reported successful, fresh, matching, and fully verified. `Attention required`
means a valid report says stale, incomplete, mismatched, or not fully verified.
`Failed` means the latest attempt or verification explicitly failed and outranks
other states. `Unknown` means no report, a malformed report, or a report too old
to trust; an API transport failure is also unavailable, not a failed backup.
`Disabled` means the agent is deactivated. A `CREATED` artifact is never called
`RESTORE_VERIFIED`: only an external identity and a complete `pg_restore`
processing or isolated restore test can establish verification.

## 3. Create recipients and schedule automation

Install `age` on a trusted administration system and create a dedicated key:

```bash
install -d -m 700 /secure/calograph-keys
age-keygen -o /secure/calograph-keys/backup-identity.txt
age-keygen -y /secure/calograph-keys/backup-identity.txt \
  > /etc/calograph/backup-recipients.txt
chmod 600 /secure/calograph-keys/backup-identity.txt
chmod 644 /etc/calograph/backup-recipients.txt
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
`BACKUP_RETENTION_DAYS`. Start the dedicated Compose profile deliberately:

```bash
BACKUP_AGENT_ENABLED=true docker compose --profile backup up -d backup-agent
```

Scheduling is internal to the restart-tolerant agent; no host cron is needed.
`BACKUP_INCLUDE_SECRETS=false` is the secure default. To include the optional
environment archive, set both source paths and start the separate secrets
profile; it is not enabled by the generic backup profile:

```bash
BACKUP_INCLUDE_SECRETS=true \
BACKUP_SECRETS_SOURCE_FILE=/secure/calograph/.env \
BACKUP_SECRETS_SOURCE_DIR=/secure/calograph/secrets \
docker compose --profile backup-secrets up -d backup-agent-secrets
```

Secret-source mounts are absent from the generic agent and fail closed unless
the secrets profile is explicitly selected. Values are never logged or written
as plaintext. The agent performs managed-only retention: it removes only its
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
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
BACKUP_DIR=/srv/calograph-backups scripts/backup-postgres.sh
```

`pg_dump --format custom` streams directly to `age`; only a randomized
ciphertext partial exists before atomic publication. Modes are `0700` for the
directory and `0600` for artifacts/checksums. A SHA-256 checksum detects
accidental transfer corruption, while authenticated age decryption detects
ciphertext tampering. A separately modifiable checksum is not authenticity.

The optional environment archive is separate and explicitly paired by its
backup timestamp:

```bash
BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt \
BACKUP_DIR=/srv/calograph-backups scripts/backup-secrets.sh
```

## 5. Verify and restore with an external private identity

Private-key operations are deliberately separate from automated creation. On a
trusted administration system:

```bash
export BACKUP_AGE_IDENTITY_FILE=/secure/calograph-keys/backup-identity.txt
scripts/verify-backup.sh /srv/calograph-backups/calograph-TIMESTAMP.dump.age
```

Verification checks the checksum, decrypts the ciphertext, and makes
`pg_restore` process the complete custom archive while discarding generated SQL.
It writes no decrypted dump. Verify the matching secrets archive with `age
--decrypt ... | tar --list --file -`; do not extract it onto a shared system.
A checksum-only check is not a full verification, and a successful backup
command is not proof of recoverability.

Restore is destructive and requires explicit confirmation:

```bash
export BACKUP_AGE_IDENTITY_FILE=/secure/calograph-keys/backup-identity.txt
CONFIRM_RESTORE=calograph scripts/restore-postgres.sh \
  /srv/calograph-backups/calograph-TIMESTAMP.dump.age
```

The workflow verifies before stopping services, streams decryption directly to
`pg_restore --clean --if-exists --no-owner`, applies migrations, and starts
services. It does not overwrite `.env` or secret sources. Keep `CREATED`,
external `RESTORE_VERIFIED`, and isolated restore-test evidence as separate
operational records.

## 6. New-host recovery, versions, and restore tests

Before a new-host recovery:

1. Install Docker, the repository, and `age`; recover the matching environment
   archive using the private identity and set files to `0600` and the secrets
   directory to `0700`.
2. Use the same PostgreSQL major version and test the complete pair in an
   isolated environment.
3. Run the restore workflow, then verify login, data status, MFA, encrypted
   YAZIO credentials, and one manual synchronization.

PostgreSQL major upgrades require dump/restore or `pg_upgrade`; changing only an
image tag is insufficient. Restore a current set to an isolated test system at
least quarterly. Keep at least one off-host copy and one immutable or offline
copy. Define retention/deletion according to health-data obligations and ensure
full-disk encryption for every live or temporary recovery system.

## 7. Legacy migration and updates

Existing plaintext dumps are not changed automatically. Encrypt one explicitly,
verify it independently, and then remove the plaintext according to policy:

```bash
BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt \
 scripts/encrypt-existing-backup.sh /srv/calograph-backups/calograph-LEGACY.dump
```

Legacy direct secrets can be moved into source files with
`CONFIRM_SECRET_MIGRATION=calograph scripts/migrate-env-secrets.sh`; this never
prints values. An independent MFA key can be created with
`CONFIRM_MFA_SECRET_MIGRATION=calograph scripts/migrate-mfa-secret.sh`.

After selecting a tested application version through `CALOGRAPH_VERSION`, run
the existing controlled update workflow. Back up and verify first, update
PostgreSQL/backend/frontend together, wait for health, and inspect logs. Never
place a private identity in production Compose or the backup-agent container.
