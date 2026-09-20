# Task 7 report

## Scope

Expanded the German Google Health integrations UI and source-priority behavior without changing backend contracts or Apple Health/YAZIO behavior.

- Google Health description now names nutrition, activity energy and weight.
- Reauthorization and missing-scope states expose a German message and accessible reauthorization action.
- Google synchronization consumes the backend nested per-domain response and displays only aggregate status/counts and safe status labels.
- Activity Energy and Weight source rows remain availability-driven; Google appears only when the backend availability response advertises it.
- English locale keys remain in parity with the German locale.

## RED before implementation

Ran the repository's current focused Vitest equivalent (`npm run test:unit -- --run tests/account-integrations.test.ts`) after adding the review regression assertions and before production changes.

Result: **RED**, including the new reauthorization persistence assertion: the result panel disappeared after the post-sync status refresh changed the connection to `reauth_required`.

The brief's `npm test` command is not defined in `frontend/package.json`; `npm run test:unit` is the current equivalent.

## GREEN and typecheck

- `npm run test:unit -- --run tests/account-integrations.test.ts tests/account-data-sources.test.ts`
  - **PASS: 2 files, 27 tests**
- `npm run typecheck`
  - **PASS: vue-tsc --noEmit**
- Google sync fixtures and rendering use backend vocabulary exactly: aggregate `success`, `partial_failure`, `no_data`, `reauth_required`, `failed`; domain `success`, `truncated`, `no_data`, `failed`, `reauth_required`.
- English and German loading/status locale keys remain in parity.
- Forbidden terminology check over the changed Google UI/locales (`Health Connect`, `Android Bridge`, `Google Fit`)
  - **PASS: no matches**

## Visual verification

Opened the actual routes with browser tooling:

- `/konto/integrationen`
- `/konto/datenquellen`

The Vite app rendered only the skip-link. The authenticated UI could not be reached because the frontend proxy request to `/api/v1/auth/me` failed with `getaddrinfo ENOTFOUND backend`. No authenticated Google, reauthorization, no-data, or paused-YAZIO visual state is claimed.
