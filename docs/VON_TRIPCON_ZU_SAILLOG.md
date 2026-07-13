# Von TripCon zu SailLog

Diese Kurz-Anleitung führt TripCon-Umsteiger in wenigen Schritten zu SailLog:
altes Logbuch sichern → importieren → weiterführen. **Deine TripCon-Daten
bleiben unangetastet** – SailLog liest die Sicherung nur lokal aus, es wird
nichts hochgeladen und nichts an der TripCon-Datei geändert.

---

## In 5 Minuten umgezogen

1. **TripCon-Sicherung erstellen** – in TripCon ein Backup schreiben. Es
   entsteht eine Datei mit der Endung **`.tcdb`** (eine SQLite-Datenbank),
   z. B. `TripCon_20240713.tcdb`. Merke dir, wohin du sie gespeichert hast.
2. **SailLog starten** (`SailLog.exe` bzw. `python main.py`).
3. Menü **Extras → „TripCon-Backup importieren…"** öffnen und die `.tcdb`
   auswählen.
4. SailLog zeigt zuerst eine **Übersicht** (Anzahl Törns, Einträge, Bilder,
   Zeitraum) und fragt **vor** dem Import nach. Bestätigen.
5. Fertig – deine Törns erscheinen im Logbuch.

Das war's für den Grundfall. Die Abschnitte unten erklären, was genau
übernommen wird und wie es danach weitergeht.

---

## Was wird übernommen?

| Aus TripCon | In SailLog |
|---|---|
| Törns (von/nach, Start-/Endzeit, Motorstunden, Log-Stand) | Törns im Logbuch (Status „abgeschlossen") |
| Logbuch-Einträge inkl. Messwerten | Einträge vom Typ `tripcon` |
| Position, SOG, COG, Wind, Tiefe, Wassertemperatur … | die jeweiligen Felder je Eintrag |
| Anlass, Bewölkung, Niederschlag, Sicht (codiert) | in Klartext aufgelöst (deutsche Sprache) |
| Kommentare, Luftdruck/-temperatur | in die Notiz des Eintrags |
| Positionen der Einträge | Kartenspur des Törns (Logbuch-Karte) |
| Plotter-Screenshots, Wetterbilder | an die Einträge angehängte Fotos |
| Schiffe (+ Foto, Maße, Rufzeichen, MMSI …) | Stammdaten unter **Stammdaten → Schiffe verwalten** |
| Personen/Crew (+ Foto, Adresse, Ausweis …) | Stammdaten unter **Stammdaten → Personen verwalten** |

**Schiff je Törn:** SailLog trägt das gefahrene Schiff automatisch am Törn
ein. Enthält die TripCon-Datei eine Schiff-Zuordnung pro Törn, wird sie
übernommen; andernfalls – und das ist der Normalfall, weil eine TripCon-
Sicherung meist **ein** Boot betrifft – wird das eine importierte Schiff allen
Törns zugeordnet. So zeigen später alle Berichte das richtige Schiff.

> **Mehrere Boote?** Importiere je Boot **eine** eigene TripCon-Sicherung.
> Dann bekommt jeder Törn automatisch sein passendes Schiff. Törns, die vor
> dieser Automatik importiert wurden, werden beim erneuten Import nachgezogen.

---

## Erneuter Import ist gefahrlos

Die importierten Einträge tragen den Typ `tripcon`. Ein **erneuter** Import
derselben oder einer neueren Sicherung **ersetzt** diese Einträge – es
entstehen keine Dubletten. **Von Hand erfasste** Einträge (Typ `manual`) und
alle deine eigenen Änderungen bleiben dabei unberührt. Schiffe und Personen
werden über den Namen erkannt; leere Felder werden nachgefüllt, ohne deine
Eingaben zu überschreiben.

---

## Nach dem Import: erste Schritte

1. **Schiff prüfen/aktiv setzen** – **Stammdaten → Schiffe verwalten**. Das
   aktive Schiff steuert Neu-Einträge; die Berichte nehmen ohnehin das je
   Törn eingetragene Schiff.
2. **Törn ansehen** – oben den Törn auswählen; die Einträge stehen im
   Logbuch, die Spur auf der **🗺 Logbuch-Karte…**.
3. **Törn-Angaben korrigieren** – **✎ Törn bearbeiten…** (Name, Orte, Zeiten,
   Schiff, und bei lückenhaften Alt-Spuren auch **Seemeilen manuell**).
4. **Bericht erzeugen** – **📄 Bericht…**: Törnbericht, Etappenbericht (mit
   Fotos) oder Fahrtenbuch, wahlweise mit Karte, als **PDF** oder im Browser.
5. **Seemeilen-Nachweis** – **Extras → 🎓 Seemeilen-Nachweis** für Segelscheine
   im ganzen deutschsprachigen Raum (SKS/SSS/SHS, FB3/FB4, Hochseeschein),
   inkl. automatisch berechneter Nachtmeilen.

---

## Logbuch weiterführen

Ab jetzt führst du das Logbuch in SailLog weiter – je nach Ausstattung:

- **Mit Bordnetz (NMEA):** oben **Verbinden** (Quelle einmalig einrichten,
  siehe README – B&G/Navico, Maretron, Orca Core). Einträge und Track laufen
  dann automatisch.
- **Nur mit GPS-Maus (USB):** einfacher USB-GPS-Empfänger genügt – Position,
  SOG und COG werden automatisch übernommen. Einrichtung: **Quellen… → Vorlage
  „GPS-Maus (USB)"** (Baud automatisch). Details im README, Abschnitt
  „GPS-Maus (USB)".
- **Ganz von Hand:** **➕ Neuer Eintrag…** – auch nachträglich mit frei
  wählbarer Zeit. Vorhandene Live-Werte werden vorbelegt, alles bleibt
  editierbar.

---

## Gut zu wissen

- **Keine Cloud, kein Konto.** Alles bleibt auf deinem Rechner. Deine
  SailLog-Daten liegen unter `~/.saillog/` (Windows:
  `C:\Users\<Name>\.saillog\`), getrennt vom Programmordner.
- **Ausprobieren ohne Boot:** Der eingebaute Simulator liefert einen
  NMEA-Stream (`python -m saillog.simulator`) – ideal, um SailLog am
  Küchentisch zu testen.
- **Kommandozeile (optional):** Wer mag, kann eine Sicherung auch per Skript
  auslesen (`logbuch.csv`, GPX-Tracks, alle Bilder) – siehe README,
  Abschnitt „Altes TripCon-Logbuch importieren".
- **Navigation:** SailLog ist ein **Logbuch**, kein Navigationsgerät – zur
  Navigation immer amtliche Mittel verwenden.

Fragen oder ein TripCon-Backup, das nicht sauber importiert? Melde dich –
Feldberichte von echten TripCon-Dateien helfen, den Import weiter zu
verbessern.
