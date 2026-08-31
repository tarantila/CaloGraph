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

## Onboarding

New accounts created through an invitation or `create-user` start with a
four-step setup: optional personal details, required calorie and protein
targets, optional security factors, and completion. The authenticated status is
available at `GET /api/v1/settings/onboarding`; step transitions use the
CSRF-protected `POST /api/v1/settings/onboarding/advance` endpoint and require
the expected current step.

Accounts created before this state was introduced remain legacy accounts. They
are only asked for a target when none exists and are otherwise treated as
complete; the migration does not backfill onboarding rows. Non-interactive
service-account setup may explicitly use `create-user --skip-onboarding`; this
creates the account with onboarding already completed.

## Account lifecycle

Administrators can manage another account through the lifecycle API:

- `POST /api/v1/users/{user_id}/deactivate` changes an active account to
  inactive. The operation removes every session, revokes every still-valid API
  token and open invitation created by that account, removes outstanding
  WebAuthn challenges, and pauses its YAZIO schedule. Nutrition data, import
  history, targets, profile settings, password, TOTP credentials, recovery
  codes, passkeys, and encrypted YAZIO credentials remain stored.
- `POST /api/v1/users/{user_id}/reactivate` restores login with the existing
  password and authentication factors. It does not restore sessions, API
  tokens, invitations, or challenges, and it deliberately leaves YAZIO
  synchronization paused until the user verifies and saves the connection
  again.
- `DELETE /api/v1/users/{user_id}` permanently deletes an already inactive
  account and all user-owned authentication, nutrition, import, target,
  tracking, and YAZIO rows. It requires the active administrator's current
  password, every enabled MFA factor, and the target username as an exact
  confirmation. This action is irreversible.

An administrator cannot deactivate or delete their own account. CaloGraph also
serializes administrative lifecycle changes and ordinary account mutations
across backend workers and the scheduler, so a deactivation or deletion cannot
race with a new import, token, settings, authentication-factor, or YAZIO write.
At least one active administrator is preserved.

The `is_active` and `deactivated_at` fields form one state: active accounts have
no deactivation timestamp; inactive accounts always have one. Repeating a
deactivation or reactivation is safe and retains the same end state.

## Administrator interface

Under **Administration** administrators see the overview, user management,
invitations, persistent sign-in audit log, and safe system status. The account
page contains only the current user's profile, authentication factors, API
tokens, YAZIO connection, and personal data tools.

The user page reuses the lifecycle controls described below. The audit log is
paginated, filterable, retains pseudonymous client references, and never stores
authentication secrets or health values.

Deactivation and reactivation use explicit confirmation dialogs that describe
which credentials and data remain valid. Recovery issuance, authenticator
reset, and hard deletion require the current administrator password plus an MFA
or recovery code when MFA is enabled. These credentials exist only in the open
dialog and are cleared when it closes. Hard deletion additionally keeps its
submit action disabled until the target username has been entered exactly.

The recovery link is shown only after successful issuance and copied only by an
explicit user action. Its secret is carried in `/recovery#token=…`, never in a
query parameter. The public recovery page removes the fragment from browser
history immediately, supports manual token entry, applies the backend password
policy, creates no session, and confirms that the account remains inactive
after password replacement.

## Administrative account recovery

CaloGraph does not send recovery email. An active administrator can create a
one-time recovery link for another account with
`POST /api/v1/users/{user_id}/recovery-links`. Issuing a link requires the
administrator's current password and every enabled MFA factor. It atomically
deactivates the target, revokes its sessions and API tokens, pauses YAZIO, and
revokes older open recovery links. The raw link token is returned only in the
creation response, expires after 30 minutes, and is stored only as an HMAC
digest.

The unauthenticated `POST /api/v1/auth/recovery/complete` endpoint accepts that
one-time token and a policy-compliant new password. A successful completion
invalidates the token and all remaining sessions and API tokens, but deliberately
keeps the account inactive and YAZIO paused. The administrator must verify the
recovery out of band and explicitly reactivate the account before login is
possible. Invalid, expired, revoked, used, or concurrently consumed tokens have
the same public failure response. PostgreSQL-backed limits apply independently
to the normalized client address and token digest.

For a lost authenticator, an active administrator can call
`POST /api/v1/users/{user_id}/authenticators/reset`. The target must already be
inactive, and the administrator must freshly reauthenticate. The operation
removes the target's TOTP credential, recovery codes, passkeys, WebAuthn handle
and challenges, and revokes sessions and API tokens. It does not change the
password, nutrition data, imports, profile, or encrypted YAZIO credentials, and
it does not reactivate the account.

The corresponding operator commands are:

```bash
docker compose exec backend python -m app.cli issue-account-recovery \
  --username TARGET --admin-username ADMIN
docker compose exec backend python -m app.cli reset-authenticators \
  --username TARGET --admin-username ADMIN --confirm TARGET
```

Both commands prompt without echo for the administrator password and, when
configured, an MFA code. Recovery tokens are printed exactly once. Administrative
recovery issuance, completion, authenticator reset, and hard deletion all use
the same cross-worker lifecycle locks as deactivation and reactivation.

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
15 characters and signs in normally. New passwords are checked server-side
against a bundled common and breached-password digest list and for obvious
repetition or sequence patterns; no password or hash prefix is sent to a third
party. The same policy applies to registration, password changes, account
recovery, and CLI user creation.

Registration and CLI user creation deliberately create no nutrition target.
After the first successful login, the frontend routes the user to a dedicated
setup page and requires their own calorie budget and protein target before the
normal application becomes available. Both values can later be changed under
**Budgets & Ziele**. Optional maintenance, carbohydrate, fat, and fiber values
remain empty unless the user sets them. The existing user-scoped target API
persists only those submitted values; no cross-account or synthetic fallback
target is used. Technical tracking-quality defaults remain independent of this
onboarding step.
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

If a user loses every second factor, an active administrator can deactivate
the account and perform the authenticator reset described under
**Administrative account recovery**. The explicit confirmation must match the
target username. Reset removes TOTP, recovery codes, passkeys, WebAuthn state,
sessions, and API tokens; the account remains inactive until the administrator
separately reactivates it.

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

## Personal profile

Authenticated users can read and fully replace their own optional personal
profile through `GET` and `PUT /api/v1/settings/personal-profile`. A read does
not create a database row. Every omitted field in a `PUT` is cleared; trimmed,
empty text is stored as `null`. The profile supports display name, the defined
gender and diet values, non-future birth dates, height from more than 0 through
300 centimetres, voluntary health notes, and intolerances. No administrator or
user-ID variant exists, and general user responses do not include these fields.

The existing `/api/v1/settings/profile` preference endpoint exposes and updates
the separate preferred weight unit with the values `kg` and `lb`.

## Interface language

The public authentication pages are always English. This includes sign-in,
MFA, passkey sign-in, invitation registration, and account recovery. The
authenticated dashboard supports German and English; newly registered
accounts use German until an account language is selected.

After login, MFA completion, passkey sign-in, or session restoration, the
authenticated account preference becomes active. The language selector in the
**Konto** profile form persists a changed preference to the account and updates
the current page immediately. There is no public-page language selector and no
language selector in the global navigation. Logout and expired sessions return
to the English public authentication locale.

The frontend uses the selected authenticated locale for interface text, dates,
numbers, validation messages, chart labels, and accessibility labels. API
responses use stable problem-type identifiers; known authorization, lifecycle,
rate-limit, passkey, and password-policy failures are mapped to localized
messages. Context-specific legacy responses retain only their safe
server-provided detail when no stable problem type exists.
