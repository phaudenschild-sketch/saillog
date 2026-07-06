# Projektstatus & Weiterarbeit (Handover)

Stand-Notiz für die nächste Arbeitssitzung an **masarasi** (Segel-Logbuch).
Das Repo ist eigenständig: `github.com/phaudenschild-sketch/masarasi`
(Laptop-Arbeitskopie: `C:\claude\masarasi`).

## Schnellstart

```bash
cd C:\claude\masarasi
git pull
python main.py                 # GUI starten
python -m unittest discover -s tests   # 92 Tests
```

Optionale Zusatzpakete: `pip install pillow` (JPG-Screenshots),
`pip install pyserial` (serieller Anschluss / Maretron).

Konfiguration & Datenbank liegen unter `~/.masarasi/`
(`config.json`, `logbook.sqlite3`) — **nicht** im Repo.

## Bord-Hardware (dieses Boot)

| Gerät | Anbindung in masarasi | Liefert |
|---|---|---|
| **B&G Zeus** (Plotter) | TCP `192.168.9.224:10110` | Position, SOG/COG, Wind (MWV/MWD), Tiefe, Wassertemp, Kurs (HDG/VHW), **Log** (VLW, Grunddistanz-Fallback), Lufttemp/**Luftdruck**/Krängung/Trimm/Ruder (XDR), AIS. **Keine Motordaten.** |
| **Maretron USB100** | seriell `COM11 @ 115200` | NMEA2000→0183: **Drehzahl** (RPM), **Lichtmaschinenspannung** (→ Motor an/aus). Öldruck/Kühlwassertemp/Motorstunden kommen als **proprietäre `$P…`-Sätze** — **noch nicht dekodiert**. |
| **Orca Core** | `192.168.9.100` | N2K-Gateway; auf Standardports bisher nichts gefunden. `discover.py 192.168.9.100 --sweep` noch offen. |
| **PredictWind DataHub** | `192.168.9.113` | Multiplexer; aktuell nicht nötig. |

**Mehrquellen-Betrieb:** In der App unter „Quellen…" B&G (TCP) **und** Maretron
(serial) anlegen → „Verbinden" liest beide gleichzeitig in einen Datensatz.

**GoFree-Plotterbild:** Live-Mirroring ist ein lizenzierter Navico-Videokanal
(Tier 3) — ohne Lizenz/HDMI-Ausgang nicht zugänglich. Plotter-Screenshots
daher **manuell laden** (Feld „Kartenplotter").

## Umgesetzt

- Mehrquellen-Eingang **TCP / UDP / seriell**, zusammengeführt in `LiveData`
- NMEA0183-Parser: Navigation + Wind + Tiefe + **Log (VLW)** + Motor (RPM),
  **XDR** (Luft/Baro/Krängung/Trimm/Ruder, Tacho, Spannung, Öldruck, Stunden)
- **Motor an/aus** automatisch aus Lichtmaschinenspannung (≥13 V), sonst RPM
- Flaches „Console"-Layout: Messwerte | Bedingungen | Kartenplotter
- **Dauerhafte Bedingungsfelder** (Anlass, Motor, Segel, Wetter, Sicht,
  Seegang, Bemerkung) — bei **jedem** Log (auto + manuell) mitgeschrieben
- Auto-Logging (Intervall) + „✎ Eintrag speichern"
- **Törns** mit Start-/Endwerten (Log/Motorstunden aus NMEA vorbelegt)
- **Einträge bearbeiten & löschen**, ✎-Marker für Bearbeitetes
- **Zeitzone** (System oder fester UTC-Versatz); intern UTC gespeichert
- SQLite + Migration; **CSV/GPX-Export** (optional pro Törn)
- **Kartenplotter-Bild pro Eintrag** (manuell laden, ansehen, exportieren)
- Werkzeuge: `discover.py` (`--full/--udp/--gofree/--sweep`),
  `inspect_backup.py`, `import_tripcon.py`, NMEA-Simulator
- **TripCon-Import** (.tcdb): Törns, Messwerte, Tracks, Bilder, **Anlass**
  (LogEvent) + Wetter/Sicht aus den Übersetzungstabellen aufgelöst

## Offene Punkte / nächste Schritte

1. **Maretron `$P…`-Sätze dekodieren** → Öldruck, Kühlwassertemperatur,
   Motorstunden automatisch ins Logbuch. *Benötigt:* ein paar echte `$P…`-
   Rohzeilen aus dem Rohdaten-Fenster (Motor läuft). Parser: `src/masarasi/nmea.py`.
2. **Orca Core erkunden:** `python discover.py 192.168.9.100 --sweep --gofree --udp`
   → prüfen, ob dort zusätzliche/Motordaten erreichbar sind.
3. **TripCon-Anlass verifizieren:** nach Neu-Import prüfen, ob die Anlass-Spalte
   sinnvoll gefüllt ist. Falls nicht: je 2 Zeilen aus `B100_Log` (Spalte
   `LogEvent`), `S005_ParamValue`, `S000_Translation` → Mapping in
   `src/masarasi/tripcon.py` (`_resolve_code`) anpassen.
4. **Optional:** TripCon-Plotterbilder (`…/bilder/plotter/`) an die importierten
   Einträge hängen; CSV-Export wahlweise in Lokalzeit; weitere Zeitzonen.

## Architektur (Kurz)

```
src/masarasi/
  app.py         Einstieg (GUI)
  gui.py         tkinter-Oberfläche (Quellen, Dashboard, Bedingungen,
                 Törns, Tabelle, Bearbeiten/Löschen, Plotter, Zeitzone)
  source.py      Quelle: TCP/UDP/seriell (Thread, Reconnect)
  nmea.py        NMEA0183-Parser + FIELD_LABELS + engine_running()
  livedata.py    thread-sicherer Messwert-Speicher
  logbook.py     Auto-/Manuell-Logging, Bedingungen, Törns
  storage.py     SQLite: LogEntry/Trip, Migration, CSV/GPX, Bilder
  fields.py      Auswahllisten (Segel/Wetter/Sicht)
  timeutil.py    Zeitzonen-Umrechnung (UTC ↔ Anzeige)
  config.py      Einstellungen (~/.masarasi/config.json)
  discover.py    Quellen-/Port-/GoFree-Scanner
  plotter_capture.py  Bild laden/als-PNG (Pillow optional)
  legacy.py / tripcon.py  Analyse & Import alter TripCon-Sicherungen
  simulator.py   NMEA0183-Testsimulator
tests/           unittest (ohne Boot lauffähig)
```

Reine Python-Standardbibliothek (Pillow/pyserial nur optional). Tests laufen
ohne Hardware; GUI wird bei Bedarf unter Xvfb rauchgetestet.
