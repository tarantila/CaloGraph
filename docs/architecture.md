# Architektur

## Komponenten

`frontend` enthält die statisch gebaute Vue-3-Anwendung und Nginx. Nginx liefert die SPA aus und leitet `/api/` intern an `backend` weiter. `backend` ist ein FastAPI-Monolith mit modular getrennten HTTP-, Auth-, Import-, Service- und Analytics-Schichten. `yazio-scheduler` verwendet dasselbe Backend-Image und stößt fällige YAZIO-Importe ohne eigenen HTTP-Port an. `postgres` ist die einzige persistente Laufzeitabhängigkeit und nicht an den Host gebunden.

## Datenfluss

```text
iPhone-Exporter ── HTTPS/JSON ─────┐
Apple-Health-XML/ZIP ─ Browser ────┼─ FastAPI ─ Adapter ─ Normalisierung ─ PostgreSQL
YAZIO-JSON / manueller Abruf ──────┤
YAZIO ─ Scheduler ─ Benutzerkonto ─┤
Browser ─ Vue/Nginx ───────────────┘                                   ↓
Browser ← Vue/Nginx ← benutzerbezogene API-Antworten ← Analytics-Service ─┘
```

Adapter liefern kanonische Samples. Nur Services schreiben Daten, Analytics liest ausschließlich das interne Modell. Jeder Importlauf, jedes Sample und jede YAZIO-Verbindung trägt eine `user_id`; Datenbankabfragen werden immer auf den angemeldeten Benutzer eingeschränkt. Eine spätere native iOS-App verwendet das vorhandene `calograph_sync_v1`-Format.

## Betriebsentscheidungen

Migrationen laufen vor dem Backend-Prozess und brechen den Containerstart bei Fehlern ab. Das Backend verwendet mehrere Worker; Rate-Limits liegen deshalb in PostgreSQL. Es gibt bewusst kein Redis und keine allgemeine Hintergrundwarteschlange. Der einzelne YAZIO-Scheduler fragt in einem konfigurierbaren Intervall nach fälligen Verbindungen. Zugangsdaten werden pro Benutzer mit Fernet verschlüsselt; der separate Schlüssel kommt aus `.env` und gehört gemeinsam mit der Datenbank in das Backup- und Berechtigungskonzept. Historische Uploads werden während der Anfrage streamend verarbeitet und zeigen im Browser den Uploadfortschritt.
