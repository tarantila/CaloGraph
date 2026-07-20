# Threat Model

## Schutzgüter

Gesundheitswerte, Zugangsdaten, Import-Tokens, Session-Cookies, Rohimporte, Backups und die Verfügbarkeit der privaten Anwendung.

## Risiken und Maßnahmen

- **Gestohlenes Import-Token:** gerätespezifische Tokens, HMAC-Hash, einmalige Anzeige, Ablauf/Widerruf, Rate-Limit und TLS.
- **Öffentlicher Importendpunkt:** Bearer-Authentifizierung, Größenlimit, Schema- und Einheitenprüfung, redigierte Antworten und Reverse-Proxy-Schutz.
- **Kompromittierter Browser:** HttpOnly-Session, kurze Angriffsfläche ohne Dritt-Skripte; ein kompromittiertes Endgerät kann dennoch sichtbare Daten lesen.
- **Unsichere Backups:** Betreiber muss Backups verschlüsseln, Berechtigungen beschränken und Restores testen.
- **Sensible Logs:** keine Payloads oder Gesundheitswerte; Request-IDs statt Nutzdaten.
- **Manipuliertes JSON:** Pydantic-/Adaptervalidierung, endliche nichtnegative Dezimalwerte, erlaubte Metriken und Datenbank-Constraints.
- **Bösartige ZIP/XML-Datei:** Gesamt-/Einzelgrößen, Eintragslimit, Kompressionsverhältnis, Pfadprüfung, genau eine `export.xml`, keine DTD/Entities/Netzwerkzugriffe, streamende Verarbeitung.
- **XSS:** Vue-Escaping, keine Darstellung beliebigen HTMLs, lokale Assets und strenge CSP.
- **CSRF:** SameSite-Cookie, separater CSRF-Header und Origin-Prüfung für schreibende Browserschnittstellen.
- **Brute Force:** PostgreSQL-gestützte Limits pro Minute; Reverse Proxy kann zusätzliche Limits setzen.
- **Datenverlust:** persistentes Volume, Alembic, regelmäßige verschlüsselte Backups und dokumentierter Restore.

## Restrisiken

Wer Host, Datenbank oder Browserprofil vollständig kontrolliert, kann Gesundheitsdaten lesen. CaloGraph ersetzt keine Festplattenverschlüsselung, Host-Härtung, Netzwerksegmentierung oder sichere Backup-Aufbewahrung.

