# Zukünftige native iOS-Synchronisierung

Die nicht im MVP enthaltene iOS-App soll das dokumentierte `calograph_sync_v1`-Format verwenden.

- HealthKit-Leseberechtigungen nur für Energieaufnahme, unterstützte Nährstoffe, Wasser, Gewicht, Körperfett, aktive Energie, Schritte und optional Trainingsminuten anfordern.
- Einverständnis je Datentyp erklären; niemals Daten ohne ausdrückliche Freigabe übertragen.
- Inkrementelle Synchronisation mit `HKAnchoredObjectQuery` und sicher persistiertem Anchor umsetzen.
- Import-Token ausschließlich im iOS Keychain speichern.
- stabile HealthKit-UUID als externe Sample-ID übertragen; gelöschte Samples benötigen später ein explizites Tombstone-Protokoll.
- Hintergrundübertragung mit `URLSession`, exponentiellem Backoff, Jitter und begrenzten Wiederholungen implementieren.
- mindestens sieben Tage überlappend synchronisieren, lokalen Sync-Status und letzte Serverantwort anzeigen.
- Tokenwiderruf, Gerätewechsel, Zeitzonenwechsel und Teilfehler verständlich behandeln.

Eine native App darf nicht behaupten, HealthKit im Hintergrund jederzeit lesen zu können; iOS entscheidet über Ausführungszeit und Datenzugriff.

