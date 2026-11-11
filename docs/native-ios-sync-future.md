# Future native iOS synchronization

The native iOS app, which is not part of the current MVP, should use the
documented `calograph_sync_v1` format.

- Request HealthKit read permissions only for the data types supported by the
  current [import contract](import-api.md): dietary energy, supported macro-
  and micronutrients, active energy, and body mass. Active energy is normalized
  to `active_energy_kcal`; it affects the budget only when the applicable
  target version enables activity credit. Body mass is normalized to
  `weight_kg`. Steps, exercise minutes, hydration, and body fat remain outside
  the supported import scope.
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
