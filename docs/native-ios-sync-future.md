# Future native iOS synchronization

The native iOS app, which is not part of the current MVP, should use the
documented `calograph_sync_v1` format.

- Request HealthKit read permissions only for dietary energy and supported
  macro- and micronutrients. Activity, steps, exercise minutes, hydration,
  weight, and body fat remain outside CaloGraph's scope.
- Explain consent for every data type and never transfer data without explicit
  authorization.
- Implement incremental synchronization with `HKAnchoredObjectQuery` and a
  securely persisted anchor.
- Store the import token only in the iOS Keychain.
- Send the stable HealthKit UUID as the external sample ID. Deleted samples
  will require an explicit tombstone protocol.
- Implement background transfer with `URLSession`, exponential backoff,
  jitter, and bounded retries.
- Synchronize with at least seven overlapping days and display local sync
  status and the latest server response.
- Handle token revocation, device changes, time-zone changes, and partial
  failures clearly.

A native app must not claim that it can read HealthKit in the background at any
time. iOS controls execution timing and data access.
