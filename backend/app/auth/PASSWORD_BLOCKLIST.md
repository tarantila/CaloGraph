# Password blocklist data

`common_passwords.sha256` contains sorted SHA-256 digests, not plaintext
passwords. It contains 10,898 unique normalized entries with at least 15
characters and has SHA-256
`d6dc2f6b88c6a353199c7fbf8a2c8ec045303c047eabee76657a94879f33fc65`.
It is generated from:

- SecLists release `2026.1`
- `Passwords/Common-Credentials/xato-net-10-million-passwords-1000000.txt`
- source SHA-256:
  `424a3e03a17df0a2bc2b3ca749d81b04e79d59cb7aeec8876a5a3f308d0caf51`

SecLists is distributed under the MIT License:
<https://github.com/danielmiessler/SecLists/blob/2026.1/LICENSE>

Regenerate the binary index with:

```bash
python scripts/build-password-blocklist.py \
  /path/to/xato-net-10-million-passwords-1000000.txt \
  backend/app/auth/common_passwords.sha256
```
