# Security Policy

CaloGraph processes sensitive health data. Do not report security issues in a
public issue with real payloads, tokens, passwords, logs, or screenshots. Use
the repository host's private security channel or contact the operator
directly.

## Supported versions

CaloGraph is a work in progress in the `0.x` phase. Security fixes are provided
for the current state of the `main` branch. Dependencies and container base
images must be updated regularly and fully tested afterward.

## Secure installation

- Do not expose the application publicly without TLS.
- Use independent random database, session, rate-limit, and credential
  encryption secrets.
- Set `COOKIE_SECURE=true` behind HTTPS.
- Create import tokens per device, rotate them periodically, and revoke them
  immediately if lost.
- Encrypt the database volume and backups and restrict access to the operator.
- Never attach logs or backups to public support channels.
- Configure reverse-proxy trust boundaries as described in
  `docs/reverse-proxy.md`.

The complete threat model is documented in
[docs/threat-model.md](docs/threat-model.md).
