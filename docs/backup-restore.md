# Backup, restore, and updates

## What must be backed up

A complete CaloGraph backup consists of two separately protected parts:

1. the PostgreSQL dump containing health data, accounts, and encrypted YAZIO
   credentials;
2. the `.env` file containing the database, session, rate-limit, and
   `CREDENTIAL_ENCRYPTION_KEY` secrets.

Stored YAZIO credentials cannot be decrypted without the original
`CREDENTIAL_ENCRYPTION_KEY`. Conversely, anyone who obtains both the database
and `.env` can decrypt them. Store both backups on encrypted storage with
restrictive permissions, ideally with an additional copy outside the Docker
host.

## Create and verify a database backup

```bash
BACKUP_DIR=/secure/encrypted/path scripts/backup-postgres.sh
scripts/verify-backup.sh /secure/encrypted/path/calograph-TIMESTAMP.dump
```

The backup script:

- uses `pg_dump --format=custom`;
- writes atomically through a randomized temporary file;
- sets directory permissions to `0700` and file permissions to `0600`;
- runs `pg_restore --list` before releasing the backup;
- creates a SHA-256 file for detecting later transfer corruption.

The checksum detects accidental damage. It is not cryptographic authentication
against an attacker who can modify both files.

## Back up secrets separately

Only when `BACKUP_DIR` is actually encrypted and protected:

```bash
BACKUP_DIR=/secure/encrypted/path scripts/backup-secrets.sh
```

The resulting file is a copy of `.env` and must be handled like a password
vault. Copy both the database dump and the secrets backup to a second,
independent medium after creation.

## Restore

Restore replaces the current CaloGraph database, so the script requires an
explicit confirmation:

```bash
CONFIRM_RESTORE=calograph \
  scripts/restore-postgres.sh /secure/path/calograph-TIMESTAMP.dump
```

The script validates the dump and checksum, stops the frontend, backend, and
YAZIO scheduler, restores the database with
`--clean --if-exists --no-owner`, applies pending Alembic migrations, and
starts all services. It deliberately does not overwrite `.env`.

Before restoring to a new host:

1. Install the repository and Docker.
2. Deliberately restore the matching secrets backup as `.env` and run
   `chmod 600 .env`.
3. Start the same PostgreSQL major version.
4. Run the restore script.
5. Verify login, data status, the last YAZIO sync, and one manual sync.

PostgreSQL 18 stores its version-specific cluster below the mounted
`/var/lib/postgresql` volume. Changing the PostgreSQL major version requires
dump/restore or `pg_upgrade`; changing only the image tag is insufficient.

## Test restoration regularly

Restore a current dump to an isolated test system at least quarterly. A
successful `pg_dump` alone does not prove that application access, `.env`,
encryption keys, and the database can be restored together.

## Update containers

After selecting the desired source revision locally:

```bash
BACKUP_DIR=/secure/encrypted/path \
  BACKUP_SECRETS=1 \
  scripts/update-containers.sh
```

Use `BACKUP_SECRETS=1` only with encrypted storage. The script validates the
Compose configuration, creates and verifies the backup first, pulls the pinned
PostgreSQL image, rebuilds backend and frontend with current base images, and
waits for healthy services. It intentionally does not run `git pull`; selecting
the commit to install remains a separate, controlled action.

After every update:

```bash
docker compose ps
docker compose logs --no-color --tail=100 backend yazio-scheduler
docker compose exec backend python -m app.cli yazio-status --username YOUR_USER
```

An Alembic downgrade is not a substitute for a backup.
