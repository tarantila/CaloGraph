# Analytics-Definitionen

## Tage und Wochen

Ein Sample wird dem lokalen Datum seines Startzeitpunkts in der Benutzerzeitzone zugeordnet. Wochen beginnen standardmäßig am Montag. Das Wochenbudget ist die Summe der je Tag gültigen Budgetversion; die Abweichung ist Aufnahme minus Budget.

## Fehlende Werte

Tage ohne Messung bleiben `null`. Sie werden weder als null Kalorien noch automatisch als vollständiger Fastentag interpretiert. Gleitende 7-, 14- und 28-Kalendertagesmittel nutzen standardmäßig nur vorhandene Tage mit Status `complete` oder `probably_complete`.

## Datenverfügbarkeit

Ein Tag gilt als erfasst, sobald ein Kalorienwert importiert wurde. Die Höhe des
Werts, das Kalorienbudget, die Zahl der Mahlzeiten und vorhandene
Makronährstoffe beeinflussen diesen Status nicht. Ernährungsdaten ohne
Kalorienwert werden separat ausgewiesen; ohne Ernährungssample gilt `no_data`.
Eine manuelle Markierung hat Vorrang.

Die Standardansicht des Datenstatus beginnt mit dem ersten tatsächlich
vorhandenen Ernährungstag. Dadurch werden Kalendertage vor der ersten Nutzung
nicht fälschlich als Lücken gewertet. Tage nach diesem Start ohne
Ernährungsdaten bleiben sichtbar. Jeder importierte Kalorienwert fließt in
Trendmittelwerte ein, auch wenn er deutlich unter dem Budget oder den
persönlichen Durchschnittswerten liegt.

## Mikronährstoffe

Die Mikronährstoffanalyse berechnet je Nährstoff die Summe des gewählten
Zeitraums geteilt durch die Ernährungstage derselben Quelle. Fehlende
Nährstoffwerte eines Ernährungstags gehen als null in das Tagesmittel ein; der
Anteil der Tage mit einem tatsächlich gelieferten Wert wird deshalb separat als
Datenabdeckung angezeigt.

Ab mindestens 70 Prozent Datenabdeckung wird das Mittel mit dem
Nährstoffbezugswert für Erwachsene aus Anhang XIII der Verordnung (EU)
Nr. 1169/2011 verglichen. Unter 80 Prozent wird neutral als „unter
Orientierung“ markiert. Dieser Status ist kein Nachweis eines Mangels und keine
Empfehlung für Nahrungsergänzungsmittel. Cholin wird ohne Prozentvergleich
angezeigt, weil die verwendete EU-Tabelle dafür keinen NRV enthält.

## Kalender

Abweichungen relativ zum gültigen Ziel: unter −15 %, −15 bis −5 %, −5 bis +5 %, +5 bis +15 % und über +15 %. Unvollständige und fehlende Tage erhalten eigene Klassen. Text und Symbole ergänzen die Farbe.
