# Benutzerverwaltung

## Grundprinzip

Jedes CaloGraph-Konto besitzt eigene Gesundheitswerte, Importläufe,
Zielhistorien, Import-Tokens und höchstens eine persönliche YAZIO-Verbindung.
Alle Lese- und Schreibabfragen werden über `user_id` auf das angemeldete Konto
eingeschränkt. Ein Administrator sieht in der Benutzerverwaltung nur
Kontodaten, nicht die Ernährungswerte anderer Benutzer.

## Erster Administrator

Der erste über die CLI angelegte Benutzer wird automatisch Administrator:

```bash
docker compose exec backend python -m app.cli create-user
```

Weitere CLI-Benutzer erhalten nur mit `--admin` Administratorrechte. Bei einem
Upgrade einer bestehenden Einzelbenutzerinstallation wird der älteste
vorhandene Benutzer einmalig zum Administrator.

## Freund einladen

Unter **Konto → Benutzerverwaltung** erzeugt der Administrator einen
Einladungslink. Der Link:

- ist standardmäßig sieben Tage gültig;
- kann genau einmal verwendet werden;
- kann vor Verwendung widerrufen werden;
- wird nur direkt nach der Erstellung vollständig angezeigt.

Der Empfänger öffnet den Link, wählt Benutzername und ein mindestens zwölf
Zeichen langes Passwort und meldet sich anschließend regulär an. Es werden
keine E-Mail-Nachrichten durch CaloGraph versendet.

## Persönliches YAZIO

Jeder Benutzer richtet unter **Konto → Persönliche YAZIO-Verbindung** die
eigenen Zugangsdaten ein. CaloGraph prüft die Verbindung einmal gegen YAZIO und
speichert E-Mail und Passwort anschließend mit dem
`CREDENTIAL_ENCRYPTION_KEY` verschlüsselt. Automatische und manuelle Importe
schreiben ausschließlich in dieses Benutzerkonto.

Da der YAZIO-Direktabruf eine nicht dokumentierte Schnittstelle verwendet,
bleibt Apple Health der unabhängige Rückfallweg.
