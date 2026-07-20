# Apple Health einrichten

## Automatische Übertragung

1. Health Auto Export auf dem iPhone installieren und HealthKit-Zugriff nur für gewünschte Kategorien erlauben.
2. In CaloGraph ein gerätespezifisches Import-Token erzeugen.
3. Eine REST-API-Automation mit JSON, Export Version 2 und dem Importendpunkt anlegen.
4. `Authorization` auf `Bearer <Token>` setzen.
5. Zunächst den Validierungsendpunkt testen, danach den normalen Importendpunkt verwenden.
6. Einen überlappenden Zeitraum von sieben Tagen übertragen, um verspätete Daten zu erfassen.

iOS kann Exporte verzögern, wenn das Gerät gesperrt, der Stromsparmodus aktiv oder Hintergrundaktualisierung deaktiviert ist. CaloGraph kann diese Plattformbeschränkungen nicht umgehen.

## Historische Daten

Apple Health öffnen, das Profilbild wählen und **Alle Gesundheitsdaten exportieren** ausführen. Das ZIP unverändert in der CaloGraph-Importansicht auswählen. Keine Exportdatei an Dritte senden.

Apple Health enthält Messwerte und Quellen, aber nicht zuverlässig Lebensmittel-, Rezept- oder Mahlzeitennamen.

