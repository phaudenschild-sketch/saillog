# masarasi weitergeben & kommerziell denken

Zwei Themen: (1) Ist der Stand so, dass du ihn an andere (ehemalige
TripCon-)Nutzer weitergeben kannst? (2) Was könnte eine kommerzielle
Version sinnvoll machen?

---

## 1. Weitergabe an andere Nutzer — Check

### Was schon gut passt

- **Keine externen Abhängigkeiten** (nur Python-Standardbibliothek). Auf
  Windows 11 reicht ein normales Python — keine Installations-Hürden.
  Pillow (Foto-Verkleinerung/Vorschau) und pyserial (COM-Port) sind
  **optional**; fehlen sie, degradiert die App sauber statt abzustürzen.
- **Keine persönlichen Daten im Code.** Alle Stammdaten-Felder (Name, MMSI,
  E-Mail, Häfen …) sind leer vorbelegt; Datenbank und Konfiguration liegen
  unter `~/.masarasi/` — **nicht** im Programmordner/Repo.
- **`.gitignore` sauber**: keine `*.pyc`, keine `logbook.sqlite3`, keine
  `config.json`, keine `.env`, keine `.tcdb` im Repo.
- **Alle 229 Tests grün**; Kernpfade (Import, Karte, Berichte, Track) unter
  einem headless-Display geprüft.
- **LICENSE** ergänzt (MIT — passend zur bereits in `pyproject.toml`
  deklarierten Lizenz). Damit dürfen andere die Software frei nutzen und
  anpassen; du als Urheber kannst trotzdem jederzeit eine separate
  kommerzielle Version verkaufen (Dual-Licensing steht dir als Rechteinhaber
  frei).
- **TripCon-Umsteiger im Blick**: `.tcdb`-Import direkt im Menü
  **Extras → „TripCon-Backup importieren…"** (mit Vorschau der Kennzahlen und
  Rückfrage vor dem Import) — das ist für die Zielgruppe der stärkste
  Startpunkt.
- **Zum Ausprobieren ohne Boot** gibt es den Simulator
  (`python -m masarasi.simulator` bzw. `masarasi-sim`), der einen
  NMEA-Stream liefert — ideal, damit Interessierte die App am Küchentisch
  testen können.

### Für die Weitergabe angepasst

- Die Quellen-**Vorlagen-Knöpfe** trugen bisher deine konkreten Werte
  (Plotter-IP `192.168.9.224`, `COM11`). Neutralisiert: B&G-Vorlage setzt nur
  noch den NMEA-Standardport `10110` und verweist auf „🔍 GoFree suchen“ für
  die IP; Maretron-Vorlage nutzt `COM3` als üblichen Beispielwert.

### Vor der Weitergabe noch erledigen (Empfehlungen)

1. **`docs/STATUS.md` enthält deine Bordkonfiguration** (feste IPs, MMSI-Kontext,
   Netzstruktur deines Boots). Das ist als *Handover für dich* nützlich, gehört
   aber nicht in ein öffentliches Release. Vorschlag: STATUS.md aus dem
   Weitergabe-Paket herausnehmen (oder die IP-Tabelle durch generische
   Beispiele ersetzen).
2. **Kurz-Anleitung „Von TripCon zu masarasi“**: eine Seite, die genau den Weg
   Backup (`.tcdb`) → Extras-Import → erste Schritte zeigt. Das senkt die
   Einstiegshürde für genau deine Zielgruppe am meisten. (Kann ich erstellen.)
3. **Fertiges Windows-Paket**: viele Segler haben kein Python. Eine
   `.exe`/Installer (PyInstaller) macht die Weitergabe drastisch einfacher.
   Aktuell ist „Python installieren + `python main.py`“ die Hürde. (Siehe
   kommerzielle Ideen — das ist auch für Gratis-Weitergabe Gold wert.)
4. **Haftungshinweis**: kurzer Satz „Navigation immer mit amtlichen Mitteln;
   masarasi ist ein Logbuch, kein Navigationsgerät.“ — schützt dich und ist bei
   der Zielgruppe selbstverständlich.
5. **Support-Kanal** festlegen (E-Mail / GitHub Issues), damit Rückmeldungen
   nicht privat verpuffen.

### Risiken/Grenzen ehrlich benennen

- **Getestet auf deiner Hardware** (B&G Zeus, Maretron). Andere Gateways/Plotter
  liefern evtl. leicht andere NMEA-Sätze. Der Parser ist robust, aber Feldberichte
  anderer Nutzer sind Gold — daher Punkt 5.
- **Kartenkacheln kommen vom Internet** (OpenFreeMap/OSM CDN). Ohne Netz bleibt der
  Kartenhintergrund leer (Spur/Marker werden trotzdem gezeichnet). Für reine
  Offline-Nutzung wären lokale Kacheln nötig (kommerzielle Idee).

---

## 2. Was für eine kommerzielle Version Sinn macht

Die Zielgruppe (Fahrtensegler, TripCon-Umsteiger) zahlt erfahrungsgemäß für
**Bequemlichkeit, Verlässlichkeit und Auswertung** — nicht für Features, die
sie selbst zusammenstückeln können. Priorisiert nach Aufwand/Nutzen:

### Sofort wertvoll (kleiner Aufwand, hoher Nutzen)

1. **Ein-Klick-Installer (Windows `.exe`, später macOS).** Der größte einzelne
   Hebel. „Herunterladen, Doppelklick, läuft“ statt Python-Setup. Rechtfertigt
   allein schon einen kleinen Kaufpreis.
2. **Auto-Erkennung der Datenquelle** ausbauen (GoFree-/mDNS-Discovery ist da) —
   „Verbinden“ soll ohne IP-Wissen funktionieren. Für Einsteiger entscheidend.
3. **Berichte als echtes PDF** (statt „im Browser drucken“) inkl. eigenem
   Deckblatt/Logo. Törnbericht/Fahrtenbuch als schön gesetztes PDF ist ein
   klassisches Bezahl-Feature.

### Mittelfristig (klare Zahlungsbereitschaft)

4. **Cloud-Backup & Geräte-Sync** (Logbuch auf mehreren Geräten, automatische
   Sicherung). Klassisches Abo-Feature; technisch: die SQLite-DB + Bilder
   verschlüsselt in einen Objektspeicher.
5. **Offline-Karten** (lokale Kachel-Pakete pro Revier). Für Blauwasser/keine
   Starlink-Reichweite relevant.
6. **Elektronisches Bordbuch/„Meilennachweis“**: exportierbarer Nachweis
   gefahrener Seemeilen/Zeiten für Scheine (SKS/SSS-Meilennachweis), mit
   Unterschriftszeile. Sehr konkreter Nutzen für Ausbildungscrews.
7. **Wetter-/Track-Overlay**: GRIB/Wind über die Karte, Track-Export zu
   Diensten. Baut auf der bestehenden dichten Track-Aufzeichnung auf.

### Strategisch (größerer Aufwand, Differenzierung)

8. **Orca-Core-/B&G-Live-Integration vertiefen** (steht in `docs/ORCA_CORE.md`
   angerissen): mehr Bordsensorik automatisch ins Logbuch. Differenziert gegen
   reine Hand-Logbücher.
9. **Companion-App fürs Handy** (nur Ansehen + Foto/Notiz an Bord aufnehmen,
   Sync mit dem Laptop). Senkt die Schwelle, unterwegs Einträge zu machen.
10. **Mehrsprachigkeit** (EN/FR/NL) — vergrößert den Markt deutlich; die
    Crewliste ist schon zweisprachig, die Basis ist also da.

### Geschäftsmodell-Optionen

- **Freemium**: Kern-Logbuch gratis (baut Nutzerbasis/Vertrauen bei
  TripCon-Umsteigern auf), Bezahl-Features = Installer-Komfort, PDF-Berichte,
  Cloud-Sync, Offline-Karten.
- **Einmalkauf** für den Installer + Berichte (niedrige Hemmschwelle, passt zur
  Segler-Mentalität „kein Abo“), **Abo nur** für laufende Kosten verursachende
  Dienste (Cloud-Sync, Kartenpakete).
- **Lizenz-Hinweis**: Bei MIT darf jeder auch kommerziell weiterverwenden. Wenn
  du eine bezahlte Version schützen willst, halte deren Zusatzmodule
  (Installer-Signierung, Cloud, PDF-Engine) **getrennt** und stelle nur den
  Kern unter MIT. Als Urheber kannst du beliebig dual-lizenzieren.

### Realistische Einschätzung

Der Markt ist eine Nische (Fahrtensegler mit NMEA-Netz), aber zahlungskräftig
und unterversorgt, seit TripCon nicht weiterentwickelt wird. Der **schnellste
Weg zu Umsatz** ist Punkt 1–3 (Installer, Auto-Connect, PDF-Berichte) — das
verwandelt das jetzige „Bastler-taugliche Tool“ in ein „für jeden Segler
benutzbares Produkt“, ohne die solide, abhängigkeitsfreie Basis aufzugeben.
