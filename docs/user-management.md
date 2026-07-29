# User management

## Core model

Every CaloGraph account owns its health values, import batches, target history,
import tokens, and at most one personal YAZIO connection. All reads and writes
are restricted to the authenticated account through `user_id`. In user
management, an administrator sees account metadata but never another user's
nutrition values.

## First administrator

The first user created through the CLI becomes an administrator automatically:

```bash
docker compose exec backend python -m app.cli create-user
```

Later CLI users receive administrator rights only with `--admin`. When an
existing single-user installation is upgraded, the oldest existing user is
promoted once.

## Invite another user

Under **Konto → Benutzerverwaltung**, an administrator creates an invitation
link. The link:

- is valid for seven days by default;
- can be used exactly once;
- can be revoked before use;
- is shown in full only immediately after creation.

The link always starts with the canonical `CALOGRAPH_PUBLIC_URL` configured by
the operator. Set this value to the final HTTPS domain before creating links
for other users.

The secret is carried in a URL fragment such as
`/einladung#token=invite_…`. Browsers remove fragments before making an HTTP
request, so the secret does not reach reverse proxies or access logs. The
frontend removes the fragment from the visible URL immediately and exchanges
the token for a signed, ten-minute registration state in an `HttpOnly`,
`SameSite=Strict` cookie. That cookie contains only the invitation identifier
and expiry, not the original token. The raw token is invalidated by the
exchange, and the cookie is deleted after registration.

The recipient then chooses a username and a password containing at least
15 characters and signs in normally. New passwords are checked locally against
a bundled common and breached-password digest list; no password or hash prefix
is sent to a third party. CaloGraph does not send email.
Path-based links from older versions must be revoked and regenerated because
supporting them would expose their token during the first HTTP request.

## Two-factor authentication

Each user can enable TOTP under **Konto → Zwei-Faktor-Authentifizierung** after
confirming their current CaloGraph password. The setup QR code and manual
secret are displayed only for the pending setup. Activation requires a valid
Authenticator code and returns ten one-time recovery codes, which the user
must store offline.

After activation, a password login creates only a five-minute, `HttpOnly`,
`SameSite=Strict` MFA challenge. A regular session is issued only after a
valid, previously unused TOTP time step or recovery code. Replacing recovery
codes or disabling TOTP requires both the current password and an active
second factor. These changes revoke every other session for that account.

If a user loses both the Authenticator and all recovery codes, an operator can
perform an emergency reset:

```bash
docker compose exec backend python -m app.cli reset-mfa \
  --username USERNAME --confirm USERNAME
```

The explicit confirmation must match the username. The reset removes the
TOTP credential, recovery codes, and all passkeys and revokes every session;
the user must sign in with their password and enroll again.

## Passkeys

Each user can register multiple discoverable passkeys under **Konto →
Passkeys**. Enrollment requires the current password and, when TOTP is enabled,
an active TOTP or recovery code. Adding or removing a passkey revokes every
other session for that account.

Passkey sign-in is passwordless and requires local user verification, such as
Windows Hello, Touch ID, Face ID, Android biometrics, or the device PIN.
CaloGraph binds WebAuthn to the exact host and origin configured through
`CALOGRAPH_PUBLIC_URL`; production therefore requires HTTPS. Registration
challenges are tied to the current user session. Authentication and
registration challenges expire after five minutes, are consumed once, and are
cleaned up by the scheduler. Stored credential public keys are not secrets.

## Personal YAZIO connection

Each user configures their own credentials under
**Konto → Persönliche YAZIO-Verbindung**. CaloGraph verifies the connection
against YAZIO once and then stores the email and password encrypted with
`CREDENTIAL_ENCRYPTION_KEY`. Manual and scheduled imports write only to that
user's account. If YAZIO later rejects the stored credentials, CaloGraph pauses
automatic synchronization for that account. Saving and successfully verifying
the credentials again resumes the personal schedule.

Because direct YAZIO retrieval uses an undocumented interface, Apple Health
remains the independent fallback path.

## Interface language

The dashboard currently uses German for every account. The data model already
stores a language preference, but the application must not expose an English
option until all navigation, forms, validation messages, charts, and
accessibility labels are translated consistently. A per-account German/English
selector is planned as a later internationalization phase.
