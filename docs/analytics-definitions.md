# Analytics-Definitionen

## Tage und Wochen

Ein Sample wird dem lokalen Datum seines Startzeitpunkts in der Benutzerzeitzone zugeordnet. Wochen beginnen standardmäßig am Montag. Das Wochenziel ist die Summe der je Tag gültigen Zielversion; die Abweichung ist Aufnahme minus Ziel.

## Fehlende Werte

Tage ohne Messung bleiben `null`. Sie werden weder als null Kalorien noch automatisch als vollständiger Fastentag interpretiert. Gleitende 7-, 14- und 28-Kalendertagesmittel nutzen standardmäßig nur vorhandene Tage mit Status `complete` oder `probably_complete`.

## Vollständigkeit

Die Heuristik vergibt jeweils bis zu zwei Punkte für Kalorien relativ zum Ziel, vorhandene Protein-/Kohlenhydrat-/Fettwerte, zeitlich getrennte Einträge und Kalorien relativ zum persönlichen 28-Tage-Median. Standardklassen: 7–8 vollständig, 5–6 wahrscheinlich vollständig, 3–4 wahrscheinlich unvollständig, 0–2 unvollständig. Ohne Ernährungssample gilt `no_data`. Gründe werden in der API und Oberfläche ausgegeben; eine manuelle Markierung hat Vorrang.

## Kalender

Abweichungen relativ zum gültigen Ziel: unter −15 %, −15 bis −5 %, −5 bis +5 %, +5 bis +15 % und über +15 %. Unvollständige und fehlende Tage erhalten eigene Klassen. Text und Symbole ergänzen die Farbe.

