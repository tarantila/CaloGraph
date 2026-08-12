# Security monitoring and audit events

CaloGraph writes security-relevant application events as one JSON object per
line on the `backend` or `yazio-scheduler` standard output stream. These events
are separate from ordinary request and scheduler messages and can be selected
by their `event` field.

```json
{"actor_ref":"b0f6a7f319c80bbb","client_ref":"3f67e4f455445cbb","event":"auth.login.failed","outcome":"failure","reason":"invalid_credentials","request_id":"4c74058b4d224db4adff9a9b7ff8fdb4","timestamp":"2026-08-03T12:00:00.000Z"}
```

The event name and optional fields are allowlisted in the application. Values
that could contain free text are rejected. In particular, security events must
never contain:

- usernames, email addresses, passwords, or YAZIO credentials;
- session, API, invitation, MFA, recovery, or registration tokens;
- imported health values, raw payloads, filenames, or arbitrary exception text;
- database URLs or secret-file contents.

`actor_ref`, `client_ref`, and `target_ref` are 16-character HMAC references
derived with the rate-limit secret. They allow events from the same installation
to be correlated without writing the underlying identifier. Rotating that
secret intentionally breaks correlation with older events. Request IDs are
limited to 32 lowercase hexadecimal characters.

## Covered events

The event stream covers:

- successful and failed password, passkey, and MFA authentication;
- password, MFA, passkey, session, invitation, registration, and API-token changes;
- successful, rejected, and transaction-failed administrative user lifecycle
  actions (`admin.user.deactivated`, `admin.user.reactivated`,
  `admin.user.deleted`, `admin.user.recovery_issued`,
  `admin.authenticators.reset`, `admin.user.lifecycle_rejected`, and
  `admin.user.lifecycle_failed`);
- successful and rejected password recovery completion;
- rate-limit decisions for every HTTP endpoint using the shared limiter;
- CLI user creation, recovery issuance, and authenticator reset;
- YAZIO connection changes, historical synchronization scheduling, and manual
  or scheduled synchronization outcomes;
- import starts, completions, partial failures, validation outcomes, and HTTP rejection;
- otherwise unhandled application request failures.

Import events contain only the source type, batch reference, and aggregate
counters. Failure reasons are fixed identifiers such as `record_limit`,
`invalid_xml`, or `database_error`.

For a quick local view, ignore non-JSON lines and select security events:

```bash
docker compose logs --no-color --no-log-prefix backend yazio-scheduler \
  | jq -R 'fromjson? | select(.event != null)'
```

## Client addresses and request correlation

Application events deliberately contain only `client_ref`. The public reverse
proxy access log is the authoritative source for the normalized client address.
This avoids copying IP addresses into several application logs and lets the
operator apply a shorter retention period to access logs.

The public proxy must generate and overwrite `X-Request-ID`. The bundled Nginx
accepts only a bounded hexadecimal value, otherwise creates a new ID, and sends
the resulting value to the backend. Access and application events can therefore
be joined on `request_id`. Do not accept an Internet client's forwarding chain.

Fail2ban should read the public Nginx or HAProxy access log, where the validated
client address is available, rather than trying to ban an HMAC `client_ref`.
A concrete filter and jail matching CaloGraph's documented JSON Nginx format
are provided in [Reverse proxy and TLS](reverse-proxy.md#optional-fail2ban).
At minimum, alert on repeated `401` and `429` responses for authentication,
invitation exchange, MFA, passkey, and YAZIO connection routes. Keep application
rate limits enabled; Fail2ban is an additional host-level control, not a
replacement.

## Retention and alerting

Compose keeps at most three 10 MiB log files per service. This protects disk
space but is not an audit-retention guarantee. Internet-facing installations
should forward security events and public access logs to a host or remote log
collector that the application containers cannot modify.

Define and document a retention period that matches the installation's privacy
requirements. A practical starting point is 30 days for access logs and 90 days
for pseudonymous security events, with shorter periods where incident response
does not require them. Restrict log access, encrypt the storage, monitor collector
failure and disk usage, and test that the following conditions generate alerts:

- repeated login, MFA, invitation, token, import, or YAZIO rate limits;
- repeated authentication failures followed by a successful login;
- administrative MFA reset, invitation revocation, API-token changes, and any
  user deactivation, reactivation, deletion, or rejected lifecycle action;
- partial imports, rejected oversized uploads, and repeated YAZIO failures;
- unhandled request failures.

Do not attach production logs to public issues. When diagnostic sharing is
necessary, remove request IDs and pseudonymous references as well as ordinary
personal data.
