# Changelog

Alle wesentlichen Änderungen an CaloGraph werden in dieser Datei dokumentiert.
Das Projekt verwendet [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.1] - 2026-07-23

### Hinzugefügt

- Persönliche Benutzerkonten mit Einladungen und strikt getrennten Ernährungsdaten.
- Manueller und automatischer YAZIO-Sync mit verschlüsselten Zugangsdaten,
  Sechs-Stunden-Intervall und zufälliger zeitlicher Streuung.
- Mikronährstoffanalyse für Vitamine und Mineralstoffe mit Datenabdeckung und
  neutralem EU-NRV-Vergleich.
- Historisierte Kalorien- und Makronährstoffbudgets sowie korrekte Tages- und
  Wochenberechnungen.
- Betriebs-, Backup-, Wiederherstellungs- und Update-Dokumentation samt
  Hilfsskripten.

### Geändert

- Ernährungsübersicht, Wochenansicht, Kalender, Trends und Datenqualität wurden
  visuell und inhaltlich überarbeitet.
- Datenstatus bewertet nur noch, ob Ernährungsdaten vorhanden sind; niedrige
  Werte werden nicht als unvollständig abgewertet.
- Aktivitäts-, Flüssigkeits- und Gewichtsdaten wurden aus Import, Auswertung und
  Oberfläche entfernt.
- Branding und Anwendungssymbole wurden durch die CaloGraph-Logos ersetzt.

### Sicherheit

- YAZIO-Zugangsdaten werden verschlüsselt gespeichert.
- Importdaten, Ziele und Analysen sind konsequent einem Benutzer zugeordnet.

[Unreleased]: https://github.com/tarantila/CaloGraph/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/tarantila/CaloGraph/compare/b4ca2cf...v0.1.1
