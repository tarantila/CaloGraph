# YAZIO-Import und Direktabruf

## Einordnung

YAZIO stellt derzeit keine dokumentierte öffentliche Export-API für diesen
Anwendungsfall bereit. CaloGraph kapselt die Integration daher in einem eigenen
Adapter. Analytics und Datenbank hängen nicht vom konkreten Exporter ab; wenn
sich dessen Format oder die Schnittstelle ändert, muss nur dieser Adapter
angepasst werden.

Der stabile Standardweg bleibt:

```text
YAZIO → Apple Health → Health Auto Export → CaloGraph
```

Der experimentelle Weg ist:

```text
YAZIO → yazio-exporter → CaloGraph-YAZIO-Adapter
```

## Vorhandene Varianten

### `days.json` oder `nutrients.json` hochladen

1. Mit
   [`yazio-exporter`](https://github.com/aleksandr-bogdanov/yazio-exporter)
   eine `days.json` und optional eine `nutrients.json` erzeugen.
2. In CaloGraph **Importe** öffnen.
3. Die Dateien nacheinander auswählen und importieren.

Unterstützt werden das originale Datumsobjekt des Exporters, eine Hülle der
Form `{ "days": { ... } }` und die originale `nutrients.json`. Zusätzlich
akzeptiert der Adapter einfache Tagesobjekte mit `energy`, `protein`, `carb`
und `fat`.

### Direkt aus dem Backend abrufen

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com
```

Ohne Datumsangaben werden die letzten 60 Tage einschließlich heute abgerufen.
Für einen anderen Zeitraum:

```bash
docker compose exec backend python -m app.cli sync-yazio \
  --username admin \
  --email name@example.com \
  --from-date 2026-01-01 \
  --end-date 2026-03-31
```

Das YAZIO-Passwort wird interaktiv und verdeckt abgefragt. Bei diesem manuellen
Abruf speichert CaloGraph weder Passwort noch Zugriffstoken. Zur Trennung mehrerer Konten wird aus der
normalisierten E-Mail-Adresse nur eine gekürzte SHA-256-Kennung gebildet; die
E-Mail-Adresse selbst landet nicht in den Gesundheitsdatensätzen.

### Automatisch synchronisieren

Für den unbeaufsichtigten Betrieb wird jede YAZIO-Verbindung einem
CaloGraph-Benutzer zugeordnet. E-Mail-Adresse und Passwort liegen ausschließlich
verschlüsselt in PostgreSQL; der separate Schlüssel bleibt in `.env`.

Einmalig einen Schlüssel erzeugen:

```bash
docker compose run --rm --no-deps backend \
  python -m app.cli generate-credential-key
```

Den ausgegebenen Wert als `CREDENTIAL_ENCRYPTION_KEY` in `.env` eintragen und
Backend sowie Scheduler neu erstellen:

```bash
docker compose up -d --build backend yazio-scheduler
```

Danach die persönliche Verbindung einrichten:

```bash
docker compose exec backend python -m app.cli configure-yazio \
  --username admin \
  --email name@example.com
```

Das Passwort wird verdeckt abgefragt und vor dem Speichern durch einen
echten YAZIO-Abruf geprüft. Standardmäßig synchronisiert der Scheduler alle
sechs Stunden erneut die letzten sieben Tage. Auf jeden automatischen
Folgetermin wird ein zufälliger Aufschlag von 1 bis 30 Minuten gerechnet, damit
Abrufe nicht dauerhaft zu einer festen Uhrzeit stattfinden. Der Wert lässt sich
über `YAZIO_SCHEDULER_JITTER_MINUTES` konfigurieren; `0` deaktiviert ihn. Dadurch
werden nachträgliche Änderungen aktualisiert, unveränderte Werte übersprungen
und neue Tage ergänzt. Die 26 zusätzlichen Mikronährstoffendpunkte werden wegen
der höheren Abrufzahl höchstens einmal innerhalb von 24 Stunden abgefragt;
Kalorien und Makronährstoffe bleiben im normalen Sechs-Stunden-Takt. Manuell
gestartete Importe beginnen weiterhin sofort und schließen Mikronährstoffe ein.

Angemeldete Benutzer können denselben persönlichen Abruf jederzeit über
**Jetzt synchronisieren** im Datenstatus des Ernährungsüberblicks auslösen. Der
Button verwendet ausschließlich die YAZIO-Verbindung des angemeldeten Kontos,
ist CSRF-geschützt und auf zwei Starts pro Minute begrenzt.

Status anzeigen oder die Automatik deaktivieren:

```bash
docker compose exec backend python -m app.cli yazio-status --username admin
docker compose exec backend python -m app.cli disable-yazio --username admin
```

Jeder Benutzer besitzt höchstens eine eigene YAZIO-Verbindung. Importierte
Samples, Importläufe und Synchronisationsstatus bleiben über `user_id` strikt
dem jeweiligen CaloGraph-Konto zugeordnet.

## Mapping

| YAZIO-Feld | CaloGraph-Metrik | Einheit |
|---|---|---|
| Summe `energy.energy` über alle Mahlzeiten | `dietary_energy_kcal` | kcal |
| Summe `nutrient.protein` | `protein_g` | g |
| Summe `nutrient.carb` | `carbohydrates_g` | g |
| Summe `nutrient.fat` | `fat_g` | g |
| `vitamin.a` bis `vitamin.k` | 13 kanonische Vitaminmetriken | mg oder µg |
| `mineral.calcium` bis `mineral.choline` | 13 kanonische Mineralstoffmetriken | mg oder µg |

Aktivität, Schritte und Wasser werden weder beim Direktabruf angefordert noch
vom YAZIO-Adapter übernommen. Mahlzeiten, Produkte, Rezepte, Profilfelder und
YAZIO-Ziele werden derzeit nicht persistiert. Auch bei aktivierter allgemeiner
Rohpayload-Aufbewahrung speichert der YAZIO-Adapter nicht die vollständige
Exportdatei. Ein erneuter Abruf desselben Tages aktualisiert die stabilen
Tageswerte idempotent.

Die Mikronährstoffe stammen aus den 26 separaten Tagesendpunkten von
`yazio-exporter==0.2.0`. Fehlende Angaben eines Produkts können deshalb wie eine
geringe Aufnahme aussehen. Die Analyse zeigt zusätzlich die Datenabdeckung und
wertet Werte erst ab 70 Prozent Abdeckung als belastbaren Orientierungshinweis.
EU-NRV-Werte aus Anhang XIII der Verordnung (EU) Nr. 1169/2011 dienen nur als
neutrale Erwachsenen-Orientierung und nicht als medizinische Diagnose.

## Sicherheits- und Betriebsgrenzen

- Der direkte Abruf verwendet die nicht dokumentierte YAZIO-API über die
  fest gepinnte Abhängigkeit `yazio-exporter==0.2.0`.
- Beim manuellen Abruf werden Zugangsdaten nur an den YAZIO-Endpunkt übertragen
  und nicht persistiert. Für die automatische Synchronisierung werden sie mit
  Fernet authentifiziert verschlüsselt; der Schlüssel wird getrennt in `.env`
  gehalten.
- Geht `CREDENTIAL_ENCRYPTION_KEY` verloren, können gespeicherte Verbindungen
  nicht wiederhergestellt werden und müssen neu eingerichtet werden.
- Wer sowohl Datenbank als auch `.env` lesen kann, kann auch die Zugangsdaten
  entschlüsseln. Dateirechte und Backups müssen deshalb beide schützen.
- Pro Direktabruf sind höchstens 366 Tage erlaubt.
- Nicht dieselben Tage parallel aus Apple Health und YAZIO importieren. Die
  Quellen bleiben für Nachvollziehbarkeit getrennt und werden daher nicht
  quellenübergreifend dedupliziert.
- Wenn die Schnittstelle nicht mehr funktioniert, bleibt der Apple-Health-Weg
  unverändert nutzbar.
