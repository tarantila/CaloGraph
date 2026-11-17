# Google Health setup

CaloGraph connects each user's Google Health account through the official
Google Health API v4. The integration reads available nutrition, active energy,
and body weight. It is optional and disabled by default.

## Enable the integration

The operator sets `GOOGLE_HEALTH_ENABLED=true` in the deployment environment.
`CALOGRAPH_PUBLIC_URL` must match the address used to open CaloGraph. The backend
also needs its normal credential-encryption key; follow the existing
[production configuration](production.md) and [backup guidance](backup-restore.md).
Client IDs and client secrets belong to individual users and are entered in
CaloGraph, not in shared environment variables.

## Create your OAuth client

1. Follow Google's [Cloud and OAuth setup](https://developers.google.com/health/setup)
   to create or select a Google Cloud project and enable the Google Health API.
2. Configure the OAuth consent screen. If the app is in Testing, add the Google
   account you will connect to its test users. Google's verification and access
   requirements depend on the app's audience and publishing status.
3. Create an OAuth client of type **Web application** (Web Server).
4. Register this exact authorized redirect URI, replacing the example origin
   with the configured CaloGraph origin:

   ```text
   https://calograph.example.com/api/v1/google-health/oauth/callback
   ```

   The callback is derived from `CALOGRAPH_PUBLIC_URL`; there is no separate
   Google Health redirect-URI setting. Scheme, hostname, port, and path must
   match. Use the callback shown in **Konto → Integrationen → Google Health**.
5. Configure these three read-only scopes in the Google project's Data Access
   settings. CaloGraph requests all three together:

   ```text
   https://www.googleapis.com/auth/googlehealth.nutrition.readonly
   https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly
   https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly
   ```

A Google Cloud client is required; credentials from the legacy Fitbit developer
portal are not a substitute. Keep the client secret private.

## Connect and synchronize

First sign in to the Google Health mobile app with the Google account you
intend to authorize, so that it has a linked Google Health profile.

1. Sign in to your CaloGraph account and open **Konto → Integrationen**
   (`/konto/integrationen`).
2. Save the Google Health Client ID and Client Secret together.
3. Start the Google authorization flow and grant all requested permissions.
   Remain signed in to CaloGraph in the same browser until the callback returns.
4. Use the connection test to check access without importing health history.
5. Select **Synchronisieren** to retrieve the last 30 calendar days. The API
   supports a bounded `days` value from 1 through 366; the browser uses 30.

Nutrition, activity energy, and weight have separate result statuses and
counts. A successful domain can remain imported when another domain fails;
inspect the domain results before treating a synchronization as complete.
These are manual imports. CaloGraph does not run an automatic Google Health
scheduler or automatically import the entire account history after connecting.

## Data and source selection

- Nutrition Log evidence and supported daily rollups feed canonical nutrition
  reads. Only values actually supplied by Google are retained; missing food
  detail or nutrients are not invented.
- Active energy is normalized to `active_energy_kcal` in kcal.
- Body weight is normalized to `weight_kg` in kg.
- Other Google Health data types, including body fat, sleep, heart rate, and
  steps, are outside this integration's current import contract.

Under **Konto → Datenquellen**, configure provider priority separately for
nutrition, activity energy, and weight. Availability depends on evidence for
the signed-in user and domain; an authorized connection alone does not prove
that data exists. Providers are never added together. Activity energy changes
a calorie budget only when the applicable historical target version enables
activity credit. See [analytics definitions](analytics-definitions.md).

## Credentials and troubleshooting

CaloGraph stores the client secret and refresh token encrypted per user.
OAuth state and PKCE bind authorization to the initiating account. Saving a
replacement credential pair requires a new authorization. **Trennen** removes
the local grant while retaining the client pair; deleting credentials removes
both. Existing imported data remains. To revoke provider-side access as well,
remove the app's permission in your Google account.

- **Integration unavailable:** ask the operator to check
  `GOOGLE_HEALTH_ENABLED`.
- **Redirect mismatch:** compare the callback shown by CaloGraph with the
  Google Cloud client's authorized redirect URI and `CALOGRAPH_PUBLIC_URL`.
- **Access blocked or missing scope:** check the API enablement, configured
  scopes, test-user list, and consent; authorize again after a scope change.
- **Reauthorization required:** reconnect through CaloGraph. Google documents
  [seven-day refresh-token expiry for Testing-mode clients](https://developers.google.com/health/setup#token-behavior-during-testing);
  this can require periodic consent even when CaloGraph is configured correctly.
- **No data:** check whether Google actually has supported records for the
  requested period. A successful empty response is not a recorded zero.
- **Transient failure:** synchronization retries temporary provider and
  rate-limit failures up to three attempts. Credential, scope, malformed-data,
  and persistence errors are not retried automatically.

Do not publish OAuth callback URLs containing codes/state, credentials, tokens,
or provider payloads when requesting help. The [import API](import-api.md)
describes the account-scoped connection and bounded synchronization contract.
