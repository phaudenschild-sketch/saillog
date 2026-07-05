# masarasi ⛵

Ein Segel-Logbuch für Windows 11 (und Linux/macOS), das die Daten aus
deinem **NMEA2000-Netzwerk** über ein **WLAN/LAN-Gateway** liest und
automatisch sowie manuell Logbuch-Einträge erstellt.

Das Programm nutzt **nur die Python-Standardbibliothek** — keine externen
Pakete, keine Installations-Hürden. Auf Windows 11 reicht ein normales
Python.

## Funktionen

- 📡 **Live-Anbindung** ans Gateway per TCP oder UDP (NMEA0183-Stream)
- 📊 **Live-Dashboard**: Position, SOG/COG, Fahrt durchs Wasser, Wind
  (scheinbar & wahr), Kurs, Tiefe, Wassertemperatur
- ⏱️ **Automatisches Logging** in einstellbarem Intervall
- ✍️ **Manuelle Einträge** mit Auto-Fill der aktuellen Messwerte
  (Ort/Hafen, Crew, Notizen)
- 💾 **Speicherung** in einer lokalen SQLite-Datenbank
- 📤 **Export** als **CSV** (Tabellenkalkulation) und **GPX** (Track für
  OpenCPN, Google Earth, …)
- 🧪 **Simulator** zum Testen ohne Boot

## Voraussetzungen

- Python 3.9 oder neuer (mit tkinter — bei den offiziellen Windows-Installern
  standardmäßig dabei)

## Starten

Ohne Installation, direkt aus dem Projektordner:

```bash
python main.py
```

Oder als installiertes Paket:

```bash
pip install -e .
masarasi          # startet die GUI
```

## Gateway einrichten

Deine Datenquelle muss die NMEA2000-Daten als **NMEA0183-Sätze über TCP
oder UDP** ausgeben. Das können praktisch alle WLAN/LAN-Gateways und
Plotter.

### Orca Core

Der Orca Core ist selbst ein NMEA2000-Gateway. Im eigenen WLAN-Modus ist
er meist unter `192.168.4.1` erreichbar und liefert NMEA0183 per **TCP,
Port 2000** — das sind die Standardwerte in masarasi. Hängt der Orca Core
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

### Quelle automatisch finden (empfohlen am Boot)

Wenn du IP/Port nicht kennst, finde sie mit dem Discovery-Scanner:

```bash
# TCP-Ports einer bekannten IP scannen (z.B. Orca Core)
python discover.py 192.168.4.1

# Mehr Ports probieren
python discover.py 192.168.4.1 --full

# Auf UDP-Broadcasts lauschen (z.B. B&G)
python discover.py --udp
```

`discover.py` startet — wie `main.py` — direkt aus dem `masarasi`-Ordner,
ohne Installation. (Gleichwertig: aus `src/` heraus
`python -m masarasi.discover …`.)

Der Scanner meldet, auf welchem Port NMEA-Sätze ankommen, und gibt dir die
exakte Zeile zum Eintragen in masarasi aus.

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
python -m masarasi.simulator --port 2000

# Terminal 2 – GUI starten und mit host=127.0.0.1, port=2000, tcp verbinden
python main.py
```

## Unterstützte NMEA0183-Sätze

RMC, GGA, GLL (Position/Zeit) · VTG, VHW (SOG/COG, Fahrt d. Wasser) ·
MWV, MWD (scheinbarer & wahrer Wind) · DPT, DBT (Tiefe) · MTW
(Wassertemperatur) · HDG, HDT, HDM (Steuerkurs).

## Altes TripCon-Logbuch importieren

Eine TripCon-Sicherung (`.tcdb`) ist eine SQLite-Datenbank. masarasi kann
sie **lokal** auslesen und wieder zugänglich machen — die Datei muss
nirgends hochgeladen werden.

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

**Zusätzlich in die masarasi-App importieren** (erscheint im Logbuch):
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

## Tests

```bash
python -m unittest discover -s tests -v
```

## Projektstruktur

```
masarasi/
├── main.py                     ← Bequemer Start ohne Installation
├── pyproject.toml
├── src/masarasi/
│   ├── app.py                  ← Einstiegspunkt (baut GUI)
│   ├── gui.py                  ← tkinter-Oberfläche
│   ├── config.py               ← Einstellungen (~/.masarasi/config.json)
│   ├── nmea.py                 ← NMEA0183-Parser
│   ├── source.py               ← TCP/UDP-Netzwerk-Client (Thread)
│   ├── livedata.py             ← Thread-sicherer Messwert-Speicher
│   ├── logbook.py              ← Auto-/Manuell-Logging-Dienst
│   ├── storage.py              ← SQLite + CSV/GPX-Export
│   ├── discover.py             ← Quellen-Scanner (Orca Core, B&G, …)
│   ├── legacy.py               ← Analyse alter Sicherungen + Bildextraktion
│   ├── tripcon.py              ← Import alter TripCon-Logbücher (.tcdb)
│   └── simulator.py            ← NMEA0183-Testsimulator
└── tests/
```

## Speicherorte

- Konfiguration: `~/.masarasi/config.json`
- Datenbank: `~/.masarasi/logbook.sqlite3`

(unter Windows: `C:\Users\<Name>\.masarasi\`)

## Hinweis zu NMEA2000 vs. NMEA0183

Die Daten stammen aus deinem NMEA2000-Bus. Das Gateway übersetzt sie in
NMEA0183 — das gängige, offene Format, das masarasi liest. Falls dein
Gateway ausschließlich das rohe NMEA2000-Format (PGN/RAW) senden kann,
sag Bescheid, dann ergänze ich einen entsprechenden Decoder.
