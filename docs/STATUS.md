# Projektstatus & Weiterarbeit (Handover)

Stand-Notiz für die nächste Arbeitssitzung an **masarasi** (Segel-Logbuch).
Das Repo ist eigenständig: `github.com/phaudenschild-sketch/masarasi`
(Laptop-Arbeitskopie: `C:\claude\masarasi`).

## Schnellstart

```bash
cd C:\claude\masarasi
git pull
python main.py                 # GUI starten
python -m unittest discover -s tests   # 116 Tests
```

Optionale Zusatzpakete: `pip install pillow` (JPG-Screenshots),
`pip install pyserial` (serieller Anschluss / Maretron).

Konfiguration & Datenbank liegen unter `~/.masarasi/`
(`config.json`, `logbook.sqlite3`) — **nicht** im Repo.

## Bord-Hardware (dieses Boot)

| Gerät | Anbindung in masarasi | Liefert |
|---|---|---|
| **B&G Zeus** (Plotter) | TCP `192.168.9.224:10110` | Position, SOG/COG, Wind (MWV/MWD), Tiefe, Wassertemp, Kurs (HDG/VHW), **Log** (VLW, Grunddistanz-Fallback), Lufttemp/**Luftdruck**/Krängung/Trimm/Ruder (XDR), AIS. **Keine Motordaten.** |
| **Maretron USB100** | seriell `COM11 @ 115200` | NMEA2000→0183: **Drehzahl** (`IIRPM`), **Kühlwassertemperatur**, **Lichtmaschinenspannung**, **Motorstunden** (aus `$PMAREPD`), **Log** (`IIVLW`). Öldruck-Feld leer (kein Sensor). |
| **Orca Core** | `192.168.9.100` | **Geparkt.** Proprietär: kein NMEA0183, sondern HTTP-Status (8080/8085/8090) + WebSocket 9000 (`Server: Python/websockets`). WS sendet nach Connect nur `{"event":"imuBegin"}`, streamt Daten erst nach einem unbekannten App-`subscribe`. Nicht erschlossen (Aufwand ≫ Nutzen, Daten bereits über B&G/Maretron vorhanden). Diagnose: `orca_probe.py`. |
| **PredictWind DataHub** | `192.168.9.113` | Multiplexer; aktuell nicht nötig. |

**Mehrquellen-Betrieb:** In der App unter „Quellen…" B&G (TCP) **und** Maretron
(serial) anlegen → „Verbinden" liest beide gleichzeitig in einen Datensatz.
AIS-Sätze (`!AIVDM`/`!AIVDO`) werden dabei je Quelle separat dekodiert.

**AIS-Karte:** Knopf „🗺 AIS-Karte" startet einen lokalen Webserver
(nur `127.0.0.1`) und öffnet eine Leaflet-Karte mit OpenFreeMap. Sie zeigt das
eigene Schiff, alle AIS-Ziele mit **echter Richtung** (COG/Heading) und den
Track des **ausgewählten Törns**. Kartenhintergrund/Leaflet werden vom CDN
geladen (an Bord über Starlink); ohne Netz bleibt nur der Hintergrund leer.

**Kartenplotter (GoFree):** entfernt — Live-Mirroring ist ein lizenzierter
Navico-Videokanal (Tier 3), ohne Lizenz/HDMI nicht zugänglich; funktionierte
nicht. `plotter_capture.py` bleibt als Bild-Hilfsmodul erhalten, ist aber
nicht mehr an die Oberfläche gebunden.

## Umgesetzt

- Mehrquellen-Eingang **TCP / UDP / seriell**, zusammengeführt in `LiveData`
- NMEA0183-Parser: Navigation + Wind + Tiefe + **Log (VLW)** + Motor (RPM),
  **XDR** (Luft/Baro/Krängung/Trimm/Ruder, Tacho, Spannung, Öldruck, Stunden)
- **Motor an/aus** automatisch aus Lichtmaschinenspannung (≥13 V), sonst RPM
- Flaches „Console"-Layout: Messwerte | Bedingungen nebeneinander
- **Dauerhafte Bedingungsfelder** (Anlass, Motor, Segel, Wetter, Sicht,
  Seegang, Bemerkung) — bei **jedem** Log (auto + manuell) mitgeschrieben
- **AutoLog-Auslöser** (wie TripCon, `autolog.py`): Intervall, SOG-/STW-Schwelle,
  Kurswechsel (geglättet), Flachwasser, abrupte Verzögerung, Strecke seit letztem
  Eintrag — der Auslösegrund wird als Anlass gespeichert. Knopf „AutoLog…"
- **Törns** mit Start-/Endwerten (Log/Motorstunden aus NMEA vorbelegt)
- **Einträge bearbeiten & löschen**, ✎-Marker für Bearbeitetes
- **Zeitzone** (System oder fester UTC-Versatz); intern UTC gespeichert
- SQLite + Migration; **CSV/GPX-Export** (optional pro Törn)
- **Tanken & Verbrauch** (Knopf „⛽ Tanken…"): Tankungen mit Zeit, Liter, Ort,
  „voll getankt" und Motorstunden (aus NMEA vorbelegt); Verbrauch in l/h wird
  „voll-zu-voll" berechnet — unabhängig von der schwankenden Tankanzeige.
  **Restfüllstand + Reichweite** (Rest-Motorstunden) aus Tankgröße (Standard
  160 L, einstellbar) und aktuellen Motorstunden (`fuel.py`)
- **Crewliste** (Ein-/Ausklarieren): Bootsangaben + Ort/Datum (gespeichert)
  + Crew je Törn; **Personen-Speicher** (einmal erfasste Personen sind über
  ein Auswahlmenü wiederverwendbar); druckbare, zweisprachige HTML-Liste
  (DE/EN) im Browser (`crewlist.py`, Knopf „Crewliste…" in der Törn-Leiste)
- **AIS-Decoder** (`!AIVDM`/`!AIVDO`, Typen 1/2/3/5/18/19/24, Mehrteiler) +
  **AIS-Karte** (Leaflet + OpenFreeMap) mit eigenem Schiff, Zielen, Törn-Track
  und **anklickbaren Logbuch-Einträgen** (Popup mit Details); Ebenen-Umschalter
  - Mehrteiler-Zusammensetzung je Funkkanal (Wetherdock vergibt Sequenz-ID neu)
  - **Automatische COG-Korrektur:** erkennt Feeds, die COG fälschlich in ganzen
    Grad statt Zehntelgrad liefern (B&G-Multiplexer an Bord), und rechnet um
  - `python -m masarasi.ais "<!AIVDM-Zeile>"` — Sätze am Boot einzeln prüfen
- Werkzeuge: `discover.py` (`--full/--udp/--gofree/--sweep`),
  `inspect_backup.py`, `import_tripcon.py`, NMEA-Simulator
- **TripCon-Import** (.tcdb): Törns, Messwerte, Tracks, Bilder, **Anlass**
  (LogEvent) + Wetter/Sicht aus den Übersetzungstabellen aufgelöst

## Offene Punkte / nächste Schritte

1. **TripCon-Anlass verifizieren:** nach Neu-Import prüfen, ob die Anlass-Spalte
   sinnvoll gefüllt ist. Falls nicht: je 2 Zeilen aus `B100_Log` (Spalte
   `LogEvent`), `S005_ParamValue`, `S000_Translation` → Mapping in
   `src/masarasi/tripcon.py` (`_resolve_code`) anpassen.
2. **Optional:** TripCon-Plotterbilder (`…/bilder/plotter/`) an die importierten
   Einträge hängen; CSV-Export wahlweise in Lokalzeit; weitere Zeitzonen;
   Rate-of-Turn (`ROT`) / Ruderlage-Anzeige.

*(Orca Core: untersucht und geparkt — proprietäres WebSocket-Protokoll,
kein Nutzen; siehe Hardware-Tabelle.)*

### Erledigt (Motordaten, Juli 2026)
Maretron `$PMAREPD` dekodiert → Kühlwassertemperatur, Lichtmaschinenspannung,
Motorstunden; `IIRPM` → Drehzahl; `ENV_ATMOS_P`/`ENV_OUTAIR_T`-XDR ergänzt.
Motor-an/aus nutzt jetzt vorrangig die Drehzahl.

## Architektur (Kurz)

```
src/masarasi/
  app.py         Einstieg (GUI)
  gui.py         tkinter-Oberfläche (Quellen, Dashboard, Bedingungen,
                 Törns, Tabelle, Bearbeiten/Löschen, AIS-Karte, Zeitzone)
  source.py      Quelle: TCP/UDP/seriell (Thread, Reconnect, AIS-Routing)
  nmea.py        NMEA0183-Parser + FIELD_LABELS + engine_running()
  ais.py         AIS-Decoder (!AIVDM/!AIVDO) + Zielliste
  webmap.py      lokaler Kartenserver (Leaflet + OpenFreeMap)
  crewlist.py    druckbare Crewliste (HTML, DE/EN)
  geo.py         Distanzen (Haversine) — Strecke im Törn aus der GPS-Spur
  fuel.py        Verbrauchsberechnung (l/h) aus den Tank-Einträgen
  livedata.py    thread-sicherer Messwert-Speicher
  logbook.py     Auto-/Manuell-Logging, Bedingungen, Törns
  autolog.py     AutoLog-Auslöser (Intervall/SOG/Kurs/Tiefe/…)
  storage.py     SQLite: LogEntry/Trip, Migration, CSV/GPX, Bilder
  fields.py      Auswahllisten (Segel/Wetter/Sicht)
  timeutil.py    Zeitzonen-Umrechnung (UTC ↔ Anzeige)
  config.py      Einstellungen (~/.masarasi/config.json)
  discover.py    Quellen-/Port-/GoFree-Scanner
  plotter_capture.py  Bild laden/als-PNG (Pillow optional, ungenutzt)
  legacy.py / tripcon.py  Analyse & Import alter TripCon-Sicherungen
  simulator.py   NMEA0183-Testsimulator
tests/           unittest (ohne Boot lauffähig)
```

Reine Python-Standardbibliothek (Pillow/pyserial nur optional). Tests laufen
ohne Hardware; GUI wird bei Bedarf unter Xvfb rauchgetestet.
