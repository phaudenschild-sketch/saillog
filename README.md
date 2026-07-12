# TripLog ⛵

Ein Segel-Logbuch für Windows 11 (und Linux/macOS), das die Daten aus
deinem **NMEA2000-Netzwerk** über ein **WLAN/LAN-Gateway** liest und
automatisch sowie manuell Logbuch-Einträge erstellt.

Das Programm nutzt **nur die Python-Standardbibliothek** — keine externen
Pakete, keine Installations-Hürden. Auf Windows 11 reicht ein normales
Python.

> **Weiterarbeit / aktueller Stand:** siehe [`docs/STATUS.md`](docs/STATUS.md)
> — Hardware-Karte des Boots, erledigte Features und offene nächste Schritte.

## Funktionen

- 📡 **Live-Anbindung** ans Gateway per TCP oder UDP (NMEA0183-Stream)
- 📊 **Live-Dashboard**: Position, SOG/COG, Fahrt durchs Wasser, Wind
  (scheinbar & wahr), Kurs, Tiefe, Wassertemperatur, Motordrehzahl/Öldruck
- ⚙️ **Motor ein/aus** — automatisch aus NMEA erkannt (Drehzahl `RPM`/`XDR`
  oder Öldruck > 0), manuell übersteuerbar
- ⛵ **Dauerhafte Bedingungs-Maske** (wie TripCon): Anlass, Motor, Großsegel
  (Voll/Reff 1/Reff 2/geborgen), Genua 0–100 %, Spinnaker, Bewölkung,
  Niederschlag, Sicht, Seegang/Wellenhöhe, Bemerkung — stehen fest in der
  Hauptmaske und werden bei **jedem** Log (automatisch **und** manuell)
  mitgeschrieben; einfach ändern, wenn sich die Bedingungen ändern
- 📏 **Log-Stand** (`VLW`) und **Motorstunden** aus dem NMEA-Netz gelesen und
  bei Törnbeginn/-abschluss automatisch vorbelegt
- 🗺️ **Törns** — Einträge gruppieren; Törn mit Startort, Wasser-/Diesel­menge,
  Motorenstunden und Log-Stand beginnen und am Ende abschließen
- ⛽ **Tanken & Verbrauch** (Knopf „⛽ Tanken…"): festhalten, **wann/wieviel/wo**
  getankt wurde, mit Schalter **„voll getankt"** und Motorstunden (aus dem
  NMEA-Netz vorbelegt). TripLog berechnet den **Verbrauch in l/h** zwischen
  zwei Voll-Tankungen — verlässlich trotz schwankender Tankanzeige — und zeigt
  aus der **Tankgröße** (Standard 160 L, einstellbar) den geschätzten
  **Restfüllstand** und die **Reichweite** (Rest-Motorstunden)
- 📄 **Berichte** (Knopf „📄 Bericht…"): Etappen-/Törn-Bericht und Fahrtenbuch
  mit Schiffsdaten, Crew, Einträgen, Meilen-Zusammenfassung, optionaler **Karte**
  und **Logo/Copyright** — wahlweise **direkt als echtes PDF gespeichert**
  (nutzt den installierten Edge/Chrome) oder im Browser geöffnet
- 🧾 **Crewliste** (Knopf „Crewliste…"): Bootsangaben (einmal gespeichert) und
  Crew je Törn erfassen und eine **zweisprachige, druckbare Crewliste (DE/EN)**
  fürs Ein-/Ausklarieren erzeugen — öffnet im Browser, dort drucken oder als
  PDF speichern
- 🛰️ **AIS-Karte** (Knopf „🗺 AIS-Karte"): dekodiert `!AIVDM`/`!AIVDO`-Sätze
  (Typen 1/2/3/5/18/19/24 inkl. Mehrteiler) und öffnet eine **Leaflet-Karte
  mit OpenFreeMap**. Sie zeigt das **eigene Schiff**, alle **AIS-Ziele mit
  echter Richtung** (COG/Heading), den **Track des ausgewählten Törns** und die
  **Logbuch-Einträge als anklickbare Punkte** (Popup mit Zeit, Position, SOG,
  Wind, Motor, Segel, Anlass, Notiz). Ein Ebenen-Umschalter blendet
  Logbuch/Track/AIS ein und aus. Der Kartenserver läuft nur lokal
  (`127.0.0.1`); Karten-Kacheln kommen vom CDN (an Bord über Starlink).
- ⏱️ **AutoLog mit Auslösern** (wie TripCon, Knopf „AutoLog…"): Intervall,
  Fahrt über Grund/durchs Wasser ≥ Schwelle, Kurswechsel (geglättet),
  Wassertiefe ≤ Schwelle, abrupte Fahrtreduzierung, Strecke seit letztem
  Eintrag — der Auslösegrund wird als **Anlass** mitgeschrieben (dem aktiven
  Törn zugeordnet, inkl. der Bedingungswerte)
- ✏️ **Einträge bearbeiten & löschen** — Doppelklick öffnet den Eintrag;
  geänderte Einträge werden mit einem **✎-Marker** gekennzeichnet
- 🕐 **Zeitzone** einstellbar (Systemzeit oder fester UTC-Versatz); intern
  wird UTC gespeichert, angezeigt in deiner Zone — Spalte **Anlass** in der
  Tabelle
- 💾 **Speicherung** in einer lokalen SQLite-Datenbank
- 📤 **Export** als **CSV** und **GPX** (optional pro Törn)
- 💾 **Backup** (Knopf „💾 Backup…"): Logbuch-Datenbank (inkl. Fotos) und
  Einstellungen als **zeitgestempelte ZIP** — manuell oder automatisch beim
  Beenden (die letzten N behalten); eine Datei zum Kopieren auf einen USB-Stick
- 📥 **Import** alter **TripCon**-Logbücher (`.tcdb`)
- 🧪 **Simulator** zum Testen ohne Boot

## Voraussetzungen

- Python 3.9 oder neuer (mit tkinter — bei den offiziellen Windows-Installern
  standardmäßig dabei)
- **Optional** `pyserial` (nur für serielle Quellen wie den Maretron USB100):
  `pip install pyserial`. Ohne läuft alles andere unverändert.
- **Optional** `pillow` (nur für den **Foto-Import**, verkleinert die Bilder):
  `pip install pillow`. Ohne Pillow ist der Foto-Import deaktiviert.
- Für die **AIS-Karte** genügt ein Browser; die Kartenkacheln werden online
  von OpenFreeMap geladen (an Bord über Starlink).

## Starten

Ohne Installation, direkt aus dem Projektordner:

```bash
python main.py
```

Oder als installiertes Paket:

```bash
pip install -e .
triplog          # startet die GUI
```

## Gateway einrichten

Deine Datenquelle muss die NMEA2000-Daten als **NMEA0183-Sätze über TCP
oder UDP** ausgeben. Das können praktisch alle WLAN/LAN-Gateways und
Plotter.

### Orca Core

Der Orca Core ist selbst ein NMEA2000-Gateway. Im eigenen WLAN-Modus ist
er meist unter `192.168.4.1` erreichbar und liefert NMEA0183 per **TCP,
Port 2000** — das sind die Standardwerte in TripLog. Hängt der Orca Core
am Boots-Router, hat er eine per DHCP zugewiesene IP (dann Discovery
nutzen, siehe unten).

| Feld | Wert |
|---|---|
| Host | `192.168.4.1` (bzw. IP im Router-Netz) |
| Port | `2000` |
| Protokoll | `tcp` |

### B&G / Navico (Zeus, …)

B&G-Plotter geben NMEA0183 übers WLAN meist per **TCP (~Port 2053)** oder
als **UDP-Broadcast** aus. Aktiviere am Plotter unter *Einstellungen →
Netzwerk/WLAN* die NMEA-über-IP-Ausgabe. Host = IP des Plotters.

### Maretron USB100 (NMEA2000 → NMEA0183 über USB)

Ein Maretron USB100 hängt am NMEA2000-Bus und wandelt die PGNs in
NMEA0183 um — **inklusive Motordaten** (Drehzahl per `RPM`, Öldruck,
Kühlwassertemperatur, Lichtmaschinen­spannung und Betriebsstunden als
proprietäre Sätze). Er erscheint am PC als **COM-Port**.

1. `pip install pyserial`
2. COM-Port im Windows-Geräte-Manager ablesen (z.B. `COM5`)
3. In TripLog: **Protokoll = serial**, **Host = COM-Port** (`COM5`),
   **Port = Baudrate** (Standard `115200`) → **Verbinden**
4. **Rohdaten…** öffnen und prüfen, welche Sätze ankommen

### Quelle automatisch finden (empfohlen am Boot)

Wenn du IP/Port nicht kennst, finde sie mit dem Discovery-Scanner:

```bash
# TCP-Ports einer bekannten IP scannen (z.B. Orca Core)
python discover.py 192.168.4.1

# Mehr Ports probieren
python discover.py 192.168.4.1 --full

# Auf UDP-Broadcasts lauschen (z.B. B&G)
python discover.py --udp

# GoFree-Dienste des B&G-Plotters anzeigen (Multicast 239.2.1.1:2052)
python discover.py --gofree
```

`discover.py` startet — wie `main.py` — direkt aus dem `triplog`-Ordner,
ohne Installation. (Gleichwertig: aus `src/` heraus
`python -m triplog.discover …`.)

Der Scanner meldet, auf welchem Port NMEA-Sätze ankommen, und gibt dir die
exakte Zeile zum Eintragen in TripLog aus.

Danach oben im Programm Host/Port/Protokoll eintragen und **Verbinden**
klicken — das Dashboard füllt sich mit Live-Werten.

### Rohdaten prüfen

Über den Knopf **Rohdaten…** öffnest du ein Fenster, das die eingehenden
NMEA-Sätze live anzeigt — ideal, um am Boot zu sehen, ob die Verbindung
steht und welche Sätze Orca Core / B&G tatsächlich senden.

## Vorher am Schreibtisch testen

Ein mitgelieferter Simulator sendet realistische Segeldaten, damit du
alles ausprobieren kannst, bevor du an Bord gehst:

```bash
# Terminal 1 – Simulator starten
python -m triplog.simulator --port 2000

# Terminal 2 – GUI starten und mit host=127.0.0.1, port=2000, tcp verbinden
python main.py
```

## AIS-Karte

Über den Knopf **„🗺 AIS-Karte"** startet TripLog einen kleinen lokalen
Webserver (nur `127.0.0.1`) und öffnet im Browser eine **Leaflet-Karte mit
OpenFreeMap**. Eingehende `!AIVDM`/`!AIVDO`-Sätze (aller angeschlossenen
Quellen) werden dekodiert und darauf angezeigt:

- **eigenes Schiff** (Position, Richtung aus Heading bzw. COG),
- **AIS-Ziele** mit **echter Richtung** (Pfeil = COG/Heading), Name, MMSI,
  SOG und COG im Popup,
- **Track des ausgewählten Törns** als Linie.

Die Kartenkacheln lädt die Seite online von OpenFreeMap (an Bord über
Starlink). Ohne Internet bleiben nur die Kacheln leer — Schiffe und Track
werden trotzdem gezeichnet.

> **Kartenplotter-Spiegelung (GoFree):** Der Live-Bildschirm des B&G-Plotters
> läuft über einen lizenzierten Navico-Videokanal (Tier 3) und ist ohne
> Lizenz/HDMI nicht zugänglich — diese (nicht funktionierende) Anzeige wurde
> aus der Oberfläche entfernt. Die GoFree-**Daten**schnittstelle bleibt über
> `python discover.py --gofree` (Multicast `239.2.1.1:2052`) einsehbar; die
> Messdaten holt TripLog ohnehin über NMEA0183 (z.B. B&G TCP-Port 10110).

## Unterstützte NMEA0183-Sätze

RMC, GGA, GLL (Position/Zeit) · VTG, VHW (SOG/COG, Fahrt d. Wasser) ·
MWV, MWD (scheinbarer & wahrer Wind) · DPT, DBT (Tiefe) · MTW
(Wassertemperatur) · HDG, HDT, HDM (Steuerkurs) · **AIS** `!AIVDM`/`!AIVDO`
(Typen 1/2/3/5/18/19/24).

## Altes TripCon-Logbuch importieren

Eine TripCon-Sicherung (`.tcdb`) ist eine SQLite-Datenbank. TripLog kann
sie **lokal** auslesen und wieder zugänglich machen — die Datei muss
nirgends hochgeladen werden.

**Am einfachsten direkt in der App:** Menü **Extras → „TripCon-Backup
importieren…"**, `.tcdb` auswählen. TripLog zeigt zuerst eine Übersicht
(Törns, Einträge, Bilder, Zeitraum) und fragt vor dem Import nach. Die alten
Einträge bekommen den Typ `tripcon`; ein erneuter Import ersetzt sie (keine
Dubletten), eigene Einträge bleiben unberührt.

Für Skript-Nutzer geht es auch über die Kommandozeile:

**Struktur ansehen** (Formate, Tabellen, Bilder):
```bash
python inspect_backup.py "C:\Pfad\TripCon_JJJJMMTT.tcdb"
```

**Törns exportieren + Bilder extrahieren** in einen Ordner:
```bash
python import_tripcon.py "C:\Pfad\TripCon_JJJJMMTT.tcdb" --out "C:\claude\tripcon-export"
```
Erzeugt:
- `logbuch.csv` — alle Einträge mit Messwerten
- `tracks/…gpx` — ein GPS-Track pro Törn (für OpenCPN/Google Earth)
- `bilder/plotter/`, `bilder/wetter/`, `bilder/schiffe/`, `bilder/crew/`
  — alle eingebetteten Bilder (u.a. die Kartenplotter-Screenshots)

**Zusätzlich in die TripLog-App importieren** (erscheint im Logbuch):
```bash
python import_tripcon.py "C:\Pfad\TripCon_JJJJMMTT.tcdb" --out "C:\claude\tripcon-export" --into-app
```
Die alten Einträge bekommen den Typ `tripcon`; ein erneuter Import ersetzt
sie (keine Dubletten).

**Hinweise zum TripCon-Format** (DB-Version 366): Koordinaten sind in
Dezimal-Bogenminuten gespeichert (Grad = Wert / 60); Törns in
`B105_Trips`, Einträge in `B100_Log`, Messwerte in den `V…`-Tabellen
(je über `LogID`), Track in `B111_TrackInfo`, Bilder als BLOB in
`B104_BinDat`.

## Als Windows-Programm weitergeben (EXE + Installer)

Damit andere Segler TripLog **ohne Python** nutzen können, lässt sich eine
eigenständige `TripLog.exe` (mit Symbol) und ein Installer bauen — auf Windows
einfach **`build_windows.bat`** doppelklicken. Details, Optionen (Pillow/pyserial
mitpacken) und der Inno-Setup-Installer: siehe **[`docs/BUILD.md`](docs/BUILD.md)**.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Lizenz

MIT — siehe [`LICENSE`](LICENSE). Frei nutz- und anpassbar; Weitergabe an
andere Segler ausdrücklich erwünscht. **Navigation immer mit den amtlichen
Mitteln** — TripLog ist ein Logbuch, kein Navigationsgerät.

## Projektstruktur

```
triplog/
├── main.py                     ← Bequemer Start ohne Installation
├── pyproject.toml
├── src/triplog/
│   ├── app.py                  ← Einstiegspunkt (baut GUI)
│   ├── gui.py                  ← tkinter-Oberfläche
│   ├── config.py               ← Einstellungen (~/.triplog/config.json)
│   ├── fields.py               ← Auswahllisten (Segel, Wetter, Sicht)
│   ├── nmea.py                 ← NMEA0183-Parser (inkl. Motor RPM/XDR)
│   ├── ais.py                  ← AIS-Decoder (!AIVDM/!AIVDO) + Zielliste
│   ├── webmap.py               ← lokaler Kartenserver (Leaflet + OpenFreeMap)
│   ├── crewlist.py             ← druckbare Crewliste (HTML, DE/EN)
│   ├── fuel.py                 ← Verbrauchsberechnung (l/h) aus Tankungen
│   ├── source.py               ← TCP/UDP/seriell-Client (Thread, AIS-Routing)
│   ├── livedata.py             ← Thread-sicherer Messwert-Speicher
│   ├── logbook.py              ← Auto-/Manuell-Logging-Dienst
│   ├── autolog.py              ← AutoLog-Auslöser (Intervall/SOG/Kurs/Tiefe/…)
│   ├── photos.py               ← Foto-Import (Ordner-Watcher + Verkleinern)
│   ├── backup.py               ← Datensicherung (ZIP: DB + Einstellungen)
│   ├── storage.py              ← SQLite + CSV/GPX-Export
│   ├── discover.py             ← Quellen-Scanner (Orca Core, B&G, …)
│   ├── plotter_capture.py      ← Bild laden/als-PNG (Pillow optional, ungenutzt)
│   ├── legacy.py               ← Analyse alter Sicherungen + Bildextraktion
│   ├── tripcon.py              ← Import alter TripCon-Logbücher (.tcdb)
│   └── simulator.py            ← NMEA0183-Testsimulator
└── tests/
```

## Speicherorte

- Konfiguration: `~/.triplog/config.json`
- Datenbank: `~/.triplog/logbook.sqlite3`

(unter Windows: `C:\Users\<Name>\.triplog\`)

## Hinweis zu NMEA2000 vs. NMEA0183

Die Daten stammen aus deinem NMEA2000-Bus. Das Gateway übersetzt sie in
NMEA0183 — das gängige, offene Format, das TripLog liest. Falls dein
Gateway ausschließlich das rohe NMEA2000-Format (PGN/RAW) senden kann,
sag Bescheid, dann ergänze ich einen entsprechenden Decoder.
