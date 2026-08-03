# Backup, restore, and updates

## Protection model

A complete CaloGraph backup consists of two encrypted parts:

1. a PostgreSQL dump containing health data, accounts, and encrypted YAZIO
   credentials;
2. a matching archive containing `.env` plus the database, session, rate-limit,
   credential-encryption, and MFA-encryption secret files.

The scripts encrypt both parts with
[`age`](https://github.com/FiloSottile/age) before writing them to their final
location. A PostgreSQL dump is streamed directly from the container into
`age`; no plaintext temporary dump is created.

Stored YAZIO credentials cannot be decrypted without the original
credential-encryption key file. Anyone who obtains both the database and that
file can decrypt them, so both artifacts must use the same operational
protection.

Stored TOTP seeds likewise require the matching MFA-encryption key. Losing that
key requires an administrative MFA reset for every enrolled user; exposing it
together with the database compromises those TOTP seeds.

## Create a backup identity

Install `age` on a trusted administration system and create a dedicated
identity:

```bash
install -d -m 700 /secure/calograph-keys
age-keygen -o /secure/calograph-keys/backup-identity.txt
age-keygen -y /secure/calograph-keys/backup-identity.txt \
  > /etc/calograph/backup-recipients.txt
chmod 600 /secure/calograph-keys/backup-identity.txt
chmod 644 /etc/calograph/backup-recipients.txt
```

`backup-recipients.txt` contains only the public recipient and may remain on
the Docker host. Keep `backup-identity.txt` outside the Docker host, ideally
offline except during a controlled verification or restore. Losing the
identity makes the encrypted backups unrecoverable.

## Create an encrypted database backup

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
BACKUP_DIR=/srv/calograph-backups scripts/backup-postgres.sh
```

The script:

- performs a plaintext-free `pg_dump | pg_restore --list` preflight;
- creates a fresh custom-format dump and streams it directly into `age`;
- writes atomically through a randomized encrypted temporary file;
- applies directory mode `0700` and file mode `0600`;
- creates a SHA-256 checksum for the encrypted file.

The output is named `calograph-TIMESTAMP.dump.age`. The SHA-256 file detects
accidental transfer corruption. Authenticity and tamper detection come from
successful `age` decryption, not from the separately modifiable checksum.

Verify the backup on a trusted system that has the private identity and access
to a running CaloGraph PostgreSQL container:

```bash
export BACKUP_AGE_IDENTITY_FILE=/secure/calograph-keys/backup-identity.txt
scripts/verify-backup.sh \
  /srv/calograph-backups/calograph-TIMESTAMP.dump.age
```

Verification decrypts into a pipe and passes the plaintext directly to
`pg_restore --list`; it does not write a decrypted dump.

## Back up secrets separately

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
BACKUP_DIR=/srv/calograph-backups scripts/backup-secrets.sh
```

The result is `calograph-secrets-TIMESTAMP.tar.age`. The script streams a tar
archive containing `.env` and `secrets/` directly into `age`. No plaintext
archive is written. For tests or deliberately different locations, set
`SECRETS_SOURCE_FILE` and `SECRETS_SOURCE_DIR` explicitly.

To verify and recover it on a trusted system:

```bash
install -d -m 700 /secure/recovery/calograph
age --decrypt \
  --identity /secure/calograph-keys/backup-identity.txt \
  /srv/calograph-backups/calograph-secrets-TIMESTAMP.tar.age \
  | tar --extract --directory /secure/recovery/calograph
chmod 600 /secure/recovery/calograph/.env
chmod 700 /secure/recovery/calograph/secrets
chmod 444 /secure/recovery/calograph/secrets/*
```

Do not leave recovered files on a shared system.

## Migrate existing plaintext dumps

Existing dumps are never changed automatically. To create an encrypted copy:

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
scripts/encrypt-existing-backup.sh \
  /srv/calograph-backups/calograph-LEGACY.dump
```

The helper first checks that PostgreSQL can read the legacy dump, refuses to
overwrite an existing encrypted destination, and leaves the plaintext source
untouched. After a successful independent restore test, remove or archive the
legacy plaintext according to the operator's retention policy.

Legacy installations that still contain direct secrets in `.env` should move
them into source files before their next backup:

```bash
CONFIRM_SECRET_MIGRATION=calograph scripts/migrate-env-secrets.sh
```

The migration preserves the database password and credential-encryption key,
removes the password-bearing `DATABASE_URL`, atomically replaces `.env` with
path-only settings, never prints secret values, and refuses to overwrite an
existing destination. It does not migrate or delete old backup files.

An installation that already completed this migration but does not yet have a
dedicated MFA key uses:

```bash
CONFIRM_MFA_SECRET_MIGRATION=calograph scripts/migrate-mfa-secret.sh
```

This helper generates an independent Fernet key, adds only its path to `.env`,
and refuses to overwrite an existing MFA setting or key file.

## Restore

Restore replaces the current CaloGraph database, so the script requires an
explicit confirmation:

```bash
export BACKUP_AGE_IDENTITY_FILE=/secure/calograph-keys/backup-identity.txt
CONFIRM_RESTORE=calograph \
  scripts/restore-postgres.sh \
  /srv/calograph-backups/calograph-TIMESTAMP.dump.age
```

The script verifies the checksum, authenticated decryption, and dump
structure; stops the frontend, backend, and YAZIO scheduler; streams the
decrypted dump directly into `pg_restore --clean --if-exists --no-owner`;
applies pending Alembic migrations; and starts all services. It deliberately
does not overwrite `.env` or the secret source directory.

Before restoring to a new host:

1. Install the repository, Docker, and `age`.
2. Recover the matching environment/secret archive in the repository root and
   verify modes `0600` for files and `0700` for `secrets/`.
3. Start the same PostgreSQL major version.
4. Run the restore script with the private identity.
5. Verify login, data status, the last YAZIO sync, and one manual sync.

PostgreSQL 18 stores its version-specific cluster below the mounted
`/var/lib/postgresql` volume. Changing the PostgreSQL major version requires
dump/restore or `pg_upgrade`; changing only the image tag is insufficient.

## Test restoration and retention

Restore a current backup to an isolated test system at least quarterly. A
successful backup command alone does not prove that the database, `.env`,
secret files, age identity, and application can be recovered together.

Keep at least one encrypted copy outside the Docker host and one immutable or
offline copy. Define retention and deletion periods appropriate for health
data. Full-disk or volume encryption remains required for live PostgreSQL
storage and any temporary recovery system; `age` protects backup artifacts,
not a running database.

## Update containers

After selecting a tested release through `CALOGRAPH_VERSION` in `.env`:

```bash
export BACKUP_AGE_RECIPIENTS_FILE=/etc/calograph/backup-recipients.txt
BACKUP_DIR=/srv/calograph-backups \
  BACKUP_SECRETS=1 \
  scripts/update-containers.sh
```

The script validates Compose, creates encrypted backups first, pulls
PostgreSQL plus the selected backend and frontend release images, and starts
them with `--no-build` before waiting for healthy services. It intentionally
does not run `git pull` or build source; source updates and image selection
remain separate, controlled actions.

After every update:

```bash
docker compose ps
docker compose logs --no-color --tail=100 backend yazio-scheduler
docker compose exec backend python -m app.cli yazio-status --username YOUR_USER
```

An Alembic downgrade is not a substitute for a tested backup.
