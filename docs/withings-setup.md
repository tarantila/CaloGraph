# Withings setup

CaloGraph connects each user's Withings account through OAuth. It imports
supported body measurements and daily active energy from the Withings cloud.
The integration is optional and disabled by default; available measurements
depend on the user's devices and provider account.

## Enable the integration

The operator configures:

```dotenv
WITHINGS_ENABLED=true
WITHINGS_REDIRECT_URI=https://calograph.example.com/api/v1/withings/oauth/callback
```

Replace the example origin with the CaloGraph address. Unlike Google Health,
Withings requires an explicit `WITHINGS_REDIRECT_URI`; CaloGraph does not derive
it from `CALOGRAPH_PUBLIC_URL`. Use HTTPS; HTTP is accepted only for loopback
development addresses. The backend needs its normal credential-encryption key.
See [production configuration](production.md) and [backup guidance](backup-restore.md).

Each user supplies their own Client ID and Client Secret through CaloGraph.
There are no shared `WITHINGS_CLIENT_ID` or `WITHINGS_CLIENT_SECRET` environment
settings.

## Register and connect your application

1. Use the [Withings developer portal](https://developer.withings.com/) to
   register an application for access to your Withings account. Follow its
   current application and API-access requirements.
2. Register the exact callback configured as `WITHINGS_REDIRECT_URI`, including
   scheme, hostname, port if present, and `/api/v1/withings/oauth/callback`.
3. Sign in to CaloGraph and open **Konto → Integrationen → Withings**
   (`/konto/integrationen`). Save the application's Client ID and Client Secret
   together. Keep the secret private.
4. Start the connection flow, sign in at Withings, and grant both `user.metrics`
   and `user.activity`. These are the integration's required
   [OAuth scopes](https://developer.withings.com/developer-guide/v3/integration-guide/public-health-data-api/get-access/oauth-authorization-url/).
5. Return to CaloGraph in the same signed-in browser. Use the connection test
   for a bounded provider check, then **Synchronisieren** to import data.

The browser synchronizes the last 30 calendar days. The API accepts `days`
from 1 through 366, ending on the current day in the user's timezone.
Synchronization is manual: CaloGraph does not currently subscribe to Withings
webhooks or run scheduled Withings imports. Connecting alone does not backfill
all history.

## Imported data

| Withings measurement | CaloGraph metric | Unit |
| --- | --- | --- |
| Weight (type 1) | `weight_kg` | kg |
| Fat-free mass (type 5) | `fat_free_mass_kg` | kg |
| Body fat ratio (type 6) | `fat_ratio_pct` | % |
| Fat mass (type 8) | `fat_mass_kg` | kg |
| Muscle mass (type 76) | `muscle_mass_kg` | kg |
| Hydration mass (type 77) | `hydration_kg` | kg |
| Bone mass (type 88) | `bone_mass_kg` | kg |
| Daily activity `calories` | `active_energy_kcal` | kcal |

Body measurements retain their provider source and measurement identity.
Import support does not imply a separate analysis card for every body metric;
the current weight reader uses `weight_kg`. Hydration mass is body composition,
not water intake. Active energy uses the provider's daily `calories` field,
not `totalcalories` or an invented difference. Blood pressure, heart rate,
sleep, steps, ECG, and nutrition are not imported by this integration.

Weight/body measurements and activity energy are synchronized as separate
domains. One domain can succeed while the other fails; the integration card
reports their statuses and counts separately. Missing values remain missing,
and reimporting a stable provider identity updates it idempotently.

Choose Withings separately for **Gewicht** and **Aktivitätsenergie** under
**Konto → Datenquellen** when evidence is available. Source priority selects
provider values and never sums overlapping Withings, Apple Health, Google
Health, or YAZIO data. Active energy affects calorie budgets only when the
applicable target version enables activity credit. See
[analytics definitions](analytics-definitions.md).

## Credentials and troubleshooting

The client secret, access token, and refresh token are encrypted per account.
Saving replacement client credentials clears the old local grant and requires
reconnection. **Trennen** clears local authorization while retaining the client
pair; deleting credentials removes both. Imported data remains. Revoke the
application in your Withings account to remove provider-side authorization.

- **Integration unavailable:** check `WITHINGS_ENABLED` with the operator.
- **Not configured:** save both client credentials and check the callback
  setting. The callback displayed in CaloGraph must match the developer portal.
- **Reauthorization required:** reconnect and grant both required scopes.
- **No data:** confirm that the device has uploaded supported measurements to
  Withings and that their dates fall in the requested period.
- **Partial failure:** check each domain result. A successful connection test
  does not prove that activity and body measurements both imported successfully.
- **Provider or rate-limit failure:** retain existing imports and retry later;
  do not expose raw provider responses or tokens in diagnostics.

For API clients, the authenticated `/api/v1/withings` routes provide status,
credential management, OAuth start/callback, connection test, disconnect, and
manual synchronization. State-changing browser requests require CSRF protection.
