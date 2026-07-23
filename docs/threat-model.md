# Threat Model

## Schutzgüter

Gesundheitswerte, Zugangsdaten, Import-Tokens, Session-Cookies, Rohimporte, Backups und die Verfügbarkeit der privaten Anwendung.

## Risiken und Maßnahmen

- **Gestohlenes Import-Token:** gerätespezifische Tokens, HMAC-Hash, einmalige Anzeige, Ablauf/Widerruf, Rate-Limit und TLS.
- **Öffentlicher Importendpunkt:** Bearer-Authentifizierung, Größenlimit, Schema- und Einheitenprüfung, redigierte Antworten und Reverse-Proxy-Schutz.
- **Kompromittierter Browser:** HttpOnly-Session, kurze Angriffsfläche ohne Dritt-Skripte; ein kompromittiertes Endgerät kann dennoch sichtbare Daten lesen.
- **Unsichere Backups:** Betreiber muss Backups verschlüsseln, Berechtigungen beschränken und Restores testen.
- **Sensible Logs:** keine Payloads oder Gesundheitswerte; Request-IDs statt Nutzdaten.
- **YAZIO-Zugangsdaten:** der manuelle Abruf persistiert sie nicht. Für den
  automatischen Sync werden E-Mail und Passwort pro Benutzer mit Fernet
  authentifiziert verschlüsselt. Der Schlüssel liegt ausschließlich in `.env`,
  erscheint nicht in Prozessargumenten und muss gemeinsam mit Datenbank-Backups
  geschützt sowie gesichert werden.
- **Inoffizielle Drittanbieter-Schnittstelle:** gekapselter Adapter, fest gepinnte Exporter-Version, begrenzter Abrufzeitraum und Apple Health als unabhängiger Rückfallweg.
- **Doppelte Quellen:** Apple Health und YAZIO nicht für denselben Zeitraum parallel verwenden; quellenübergreifende Deduplizierung würde die Herkunft verschleiern.
- **Manipuliertes JSON:** Pydantic-/Adaptervalidierung, endliche nichtnegative Dezimalwerte, erlaubte Metriken und Datenbank-Constraints.
- **Bösartige ZIP/XML-Datei:** Gesamt-/Einzelgrößen, Eintragslimit, Kompressionsverhältnis, Pfadprüfung, genau eine `export.xml`, keine DTD/Entities/Netzwerkzugriffe, streamende Verarbeitung.
- **XSS:** Vue-Escaping, keine Darstellung beliebigen HTMLs, lokale Assets und strenge CSP.
- **CSRF:** SameSite-Cookie, separater CSRF-Header und Origin-Prüfung für schreibende Browserschnittstellen.
- **Brute Force:** PostgreSQL-gestützte Limits pro Minute; Reverse Proxy kann zusätzliche Limits setzen.
- **Weitergegebener Einladungslink:** kryptografisch zufällige, nur gehashte
  Einladungstokens, einmalige Nutzung, siebentägiges Ablaufdatum und
  administrativer Widerruf. Der Klartext wird nur direkt nach dem Erstellen
  angezeigt.
- **Unbefugte Benutzerverwaltung:** Benutzer- und Einladungsschnittstellen sind
  ausschließlich für Administratoren freigegeben. Der erste per CLI angelegte
  Benutzer wird automatisch Administrator; weitere Konten erhalten keine
  administrativen Rechte.
- **Datenzugriff zwischen Konten:** Abfragen, Importe, Ziele, Tokens und
  YAZIO-Verbindungen werden immer über die authentifizierte Benutzer-ID
  eingeschränkt. Dieser Isolationspfad wird mit einem separaten
  Einladungs-/Login-Szenario getestet.
- **Datenverlust:** persistentes Volume, Alembic, regelmäßige verschlüsselte Backups und dokumentierter Restore.

## Restrisiken

Wer Host, Datenbank oder Browserprofil vollständig kontrolliert, kann Gesundheitsdaten lesen. CaloGraph ersetzt keine Festplattenverschlüsselung, Host-Härtung, Netzwerksegmentierung oder sichere Backup-Aufbewahrung.
