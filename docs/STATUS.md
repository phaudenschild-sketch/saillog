# Projektstatus & Weiterarbeit (Handover)

Stand-Notiz für die nächste Arbeitssitzung an **TripLog** (Segel-Logbuch).
Das Produkt heißt seit der Umbenennung **TripLog** (Python-Paket `triplog`);
„Masarasi" ist der Schiffsname und bleibt nur als solcher erhalten.
Das GitHub-Repo ist auf **`phaudenschild-sketch/triplog`** umbenannt
(GitHub leitet den alten Namen automatisch weiter). Auf dem Laptop den Remote
umstellen: `git remote set-url origin https://github.com/phaudenschild-sketch/triplog.git`
(die Arbeitskopie darf weiterhin `C:\claude\masarasi` heißen — der Ordnername
ist egal).

## Schnellstart

```bash
cd C:\claude\masarasi
git pull
python main.py                 # GUI starten
python -m unittest discover -s tests   # 235 Tests
```

Optionale Zusatzpakete: `pip install pillow` (JPG-Screenshots),
`pip install pyserial` (serieller Anschluss / Maretron).

Konfiguration & Datenbank liegen unter `~/.triplog/`
(`config.json`, `logbook.sqlite3`) — **nicht** im Repo.

## Bord-Hardware (dieses Boot)

| Gerät | Anbindung in triplog | Liefert |
|---|---|---|
| **B&G Zeus** (Plotter) | TCP `192.168.9.224:10110` | Position, SOG/COG, Wind (MWV/MWD), Tiefe, Wassertemp, Kurs (HDG/VHW), **Log** (VLW, Grunddistanz-Fallback), Lufttemp/**Luftdruck**/Krängung/Trimm/Ruder (XDR), AIS. **Keine Motordaten.** |
| **Maretron USB100** | seriell `COM11 @ 115200` | NMEA2000→0183: **Drehzahl** (`IIRPM`), **Kühlwassertemperatur**, **Lichtmaschinenspannung**, **Motorstunden** (aus `$PMAREPD`), **Log** (`IIVLW`). Öldruck-Feld leer (kein Sensor). |
| **Orca Core** | `192.168.9.100` | **Geparkt** (Details: `docs/ORCA_CORE.md`). REST-APIs (8080 JSON, 9001 Flask, 8085 Watchdog, 8090 Firmware-Upload) liefern nur Verwaltung/Kalibrierung — **keine Live-Daten** (87 Pfade geprüft). Live-Daten nur über **WebSocket 9000 binär** (`imuBegin` + sporadische Binärframes; Ping→Pong nötig). Kern-Mehrwert wäre IMU-Heading/Lage — Krängung/Trimm/Ruder liefert B&G aber schon (XDR). Nächster Schritt (später): App-Traffic mitschneiden. Diagnose: `orca_probe.py` (`--api`/`--deep`/`--listen`/`--fetch`). |
| **PredictWind DataHub** | `192.168.9.113` | Multiplexer; aktuell nicht nötig. |

**Mehrquellen-Betrieb:** In der App unter „Quellen…" B&G (TCP) **und** Maretron
(serial) anlegen → „Verbinden" liest beide gleichzeitig in einen Datensatz.
AIS-Sätze (`!AIVDM`/`!AIVDO`) werden dabei je Quelle separat dekodiert.

**AIS-Karte:** Knopf „🗺 AIS-Karte" startet einen lokalen Webserver
(nur `127.0.0.1`) und öffnet eine Leaflet-Karte mit OpenFreeMap. Sie zeigt das
eigene Schiff, alle AIS-Ziele mit **echter Richtung** (COG/Heading) und den
Track des **ausgewählten Törns**. Kartenhintergrund/Leaflet werden vom CDN
geladen (an Bord über Starlink); ohne Netz bleibt nur der Hintergrund leer.

**Kartenplotter (GoFree):** Live-*Bildschirm*-Mirroring ist ein lizenzierter
Navico-Videokanal (Tier 3), ohne Lizenz/HDMI nicht zugänglich; entfernt.
`plotter_capture.py` bleibt als Bild-Hilfsmodul erhalten, ist aber nicht mehr
an die Oberfläche gebunden.

**GoFree-*Daten*-Discovery** (kostenlos, Tier 1): B&G/Navico-MFDs kündigen ihre
Dienste per Multicast **239.2.1.1:2052** an (bestätigt über TripCons
Statuszeile „listen … to multicast '239.2.1.1:2052'"). `discover.py --gofree`
tritt dieser Gruppe bei, liest die Ankündigung (JSON) und schlägt die passende
triplog-Quelle vor (`TCP <ip>:<port>` des `nmea-0183`-Dienstes).
- `--iface 192.168.0.123` bindet an eine bestimmte lokale IP (wie TripCon) —
  nötig auf Rechnern mit mehreren Netzen (VM/WLAN+LAN), sonst wird evtl. auf
  dem falschen Netz gelauscht.
- `--raw` zeigt jedes empfangene Paket roh (Text + Hex) — falls die Firmware
  kein JSON, sondern ein Binärformat sendet, sieht man es so und kann es
  nachrüsten. `--seconds N` verlängert die Lauschzeit.

**Diensteverzeichnis Zeus3S 9** (aus der Ankündigung, IP 192.168.9.224):

| Port | Dienst | Nutzen |
|---|---|---|
| **10110** | NMEA0183 / nmea-0183 | Klartext-NMEA-0183 (TCP) — **das nutzt triplog bereits** |
| 80 | http | Web-/GoFree-HTTP-API |
| 21 | ftp | Dateizugriff |
| 554 | rtsp | **Plotter-Bildschirm als Video** (mit VLC testbar) |
| 6633 | navico-mfd-rp | MFD-Fernbedienung |
| **2053** | navico-nav-ws | **GoFree-WebSocket-Daten-API** (evtl. voller N2K-Satz inkl. Motor) |

Fazit NMEA: GoFree liefert über 0183 nichts Zusätzliches (= Port 10110, schon
genutzt). Neue Türen: `navico-nav-ws:2053` (mehr Daten, `gofree_probe.py`
erschließt das Protokoll) und `rtsp:554` (Plotterbild).

Im **Quellen-Dialog** gibt es den Knopf **„🔍 GoFree suchen"**: lauscht kurz,
findet MFDs und trägt deren NMEA-Quelle (`TCP <ip>:10110`) automatisch ein
(Duplikate werden vermieden).

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
  Eintrag — der Auslösegrund wird als Anlass gespeichert. Knopf „AutoLog…".
  Automatische Einträge (AutoLog **und** Foto-Import) gehen **immer in den
  offenen Törn** (`status='open'`), unabhängig davon, welcher Törn gerade zum
  Ansehen ausgewählt ist (`logbook.open_trip_id()`). Werkzeug `fix_trips.py`
  hängt versehentlich falsch zugeordnete Einträge nachträglich um.
- **Trackaufzeichnung (getrennte, dichte Kartenspur):** Neben den Log-Einträgen
  zeichnet der Auto-Thread reine **Track-Punkte** auf (`entry_type='track'`, nur
  Zeit + Position, dazu SOG/COG für Kartenpfeile — keine Bedingungen/Bilder):
  **bei jeder Kursänderung** (`track_course_threshold`, Standard 10°, mit
  Mindestfahrt) und sonst in einem kurzen Intervall (`track_interval_seconds`,
  Standard 60 s), mit Mindestbewegungs-Filter gegen Hafen-Spam
  (`autolog.evaluate_track`/`note_track`, `logbook.record_track`). Einstellbar
  im AutoLog-Dialog (Abschnitt „Trackaufzeichnung"). So bleibt die **Liste/
  Übersicht ruhig** (nur Log-Einträge), während die **Karte eine schöne, dichte
  Spur** bekommt. Track-Punkte sind in `store.all()` **standardmäßig
  ausgeblendet** (`include_track=False`); die Logbuch-Liste hat den Schalter
  **„Trackpunkte anzeigen"**, Karte/GPX ziehen sie über `include_track=True`.
  Distanz/Meilen und Berichte rechnen weiter nur mit den Log-Einträgen.
- **Foto-Import** (`photos.py`, Knopf „📷 Foto-Import…"): Ordner überwachen →
  Bild in „vernünftige" Größe (max. 1600 px, JPEG) verkleinern → Auto-Eintrag
  mit Bild + NMEA-Daten; Originale wandern nach `verarbeitet/` (braucht Pillow)
- **TripCon-Import im Menü** (**Extras → „TripCon-Backup importieren…"**):
  Dateiauswahl → read-only Analyse (Integrität/Törns/Einträge/Bilder/Zeitraum,
  `tripcon.analyze_tcdb`) → Rückfrage → Import im Thread
  (`tripcon.import_into_triplog`, ersetzt frühere `tripcon`-Importe). Das CLI
  (`import_tripcon.py`) bleibt für Export (CSV/GPX/Bilder) bestehen.
- **Bedienkomfort:** Nach „✎ Eintrag speichern" springt der **Anlass** zurück
  auf „Routineeintrag" und die **Bemerkung** wird geleert (kein versehentliches
  Übernehmen ins nächste Log). Quellen-Vorlagen entpersonalisiert (B&G: Port
  10110 + GoFree-Suche statt fester IP; Maretron: COM3).
- **Weitergabe/Kommerz:** `LICENSE` (MIT) ergänzt; Analyse & Ideen in
  `docs/WEITERGABE.md` (Weitergabe-Check + kommerzielle Optionen).
- **Umbenennung Masarasi → TripLog:** Python-Paket `masarasi` → `triplog`
  (alle Importe/Skripte/Tests), Produktname „TripLog" in allen Ausgaben
  (Fenstertitel, Karte, Berichte, Crewliste, GPX-Metadaten). „SY MASARASI"
  bleibt als **Schiffsname** erhalten. Datenverzeichnis `~/.masarasi` →
  `~/.triplog` mit **automatischer einmaliger Übernahme** bestehender Daten
  (`config._app_dir`, plus db_path-Umbiegung in `Config.load`).
- **Logo & Copyright:** Marken-Modul `triplog/branding.py` (Inline-SVG-Logo
  `assets/logo.svg`, `APP_NAME`, `COPYRIGHT = "© Peter Haudenschild"`). Das Logo
  erscheint auf der Titelseite der Berichte und im Kopf der Crewliste; der
  Copyright-Hinweis steht im Fuß jeder Ausgabe.
- **App-Icon:** `assets/icon.svg` (quadratisches Boot-Badge) → gerendert nach
  `assets/icon.png` (256, transparent) und `assets/icon.ico` (16–256, für
  Windows-Verknüpfung/späteren Installer). Das **Fenster-Icon** der App wird zur
  Laufzeit aus einem eingebetteten 64px-PNG gesetzt (`branding.ICON_PNG_B64` /
  `branding.set_window_icon`, in `gui.py` beim Start) — kein Dateizugriff nötig.
- **GitHub-Repo umbenannt:** `masarasi` → **`triplog`** (am 2026-07-12 manuell
  in den GitHub-Settings erledigt). GitHub leitet den alten Namen automatisch
  weiter, daher funktionieren bestehende Klone/Pushes weiter; auf dem Laptop
  bei Gelegenheit den Remote umstellen (`git remote set-url origin
  https://github.com/phaudenschild-sketch/triplog.git`).
- **Plotter-Screenshot** (`android_screencap.py`): holt den Bildschirm des
  Android-Tablets (Orca-/Plotter-Anzeige) per **adb** (`exec-out screencap -p`)
  ins Logbuch. Knopf **„📸 Plotter"** (sofort Eintrag mit Bild), Bild-Auswahl
  beim manuellen Eintrag (kein Bild / Plotter / Datei) und Option **„bei jedem
  Auto-Eintrag mitspeichern"**. Einstellungen unter Menü **Extras →
  Plotter-Screenshot (ADB)…** (adb-Pfad, Gerät, Test). Setup: Tablet koppeln
  (Entwickleroptionen → USB-/Drahtlos-Debugging, „immer erlauben"). **WLAN:**
  im Dialog „Per USB für WLAN aktivieren" (setzt `adb tcpip 5555`, liest die
  Tablet-IP, trägt `<ip>:5555` ein und verbindet) — danach USB abziehen. Vor
  jeder Aufnahme wird bei einer Netzwerk-Adresse automatisch neu verbunden
  (übersteht WLAN-Aussetzer). Nach einem Tablet-Neustart einmal per USB
  „aktivieren" wiederholen.
- **Backup** (`backup.py`, Knopf „💾 Backup…"): Logbuch-DB (inkl. Fotos) +
  Einstellungen als zeitgestempelte ZIP; manuell oder automatisch beim Beenden
  (letzte N behalten)
- **Törns** mit Start-/Endwerten (Log/Motorstunden aus NMEA vorbelegt)
- **Einträge bearbeiten & löschen**, ✎-Marker für Bearbeitetes. Im
  Bearbeiten-Fenster **mehrere Bilder je Eintrag**: Vorschau + Blättern
  (◀/▶), Bild hinzufügen (Festplatte **oder** Plotter-Screenshot), löschen,
  extern öffnen. Bilder werden in der **AIS-Karte** im Popup der Markierung
  angezeigt (erstes Bild inline, weitere als Links; Kartenserver liefert sie
  über `/entry_image?id=…`).
- **Zeitzone** (System oder fester UTC-Versatz); intern UTC gespeichert
- SQLite + Migration; **CSV/GPX-Export** (optional pro Törn)
- **Tanken & Verbrauch** (Knopf „⛽ Tanken…"): Tankungen mit Zeit, Liter, Ort,
  „voll getankt" und Motorstunden (aus NMEA vorbelegt); Verbrauch in l/h wird
  „voll-zu-voll" berechnet — unabhängig von der schwankenden Tankanzeige.
  **Restfüllstand + Reichweite** (Rest-Motorstunden) aus Tankgröße (Standard
  160 L, einstellbar) und aktuellen Motorstunden (`fuel.py`)
- **Törn-Ebene (Voyage) über den Etappen:** In triplog ist ein „Trip" eine
  Etappe (Tagesschlag). Mehrere Etappen lassen sich zu einem **Törn** (voyages-
  Tabelle, `trip.voyage_id`) zusammenfassen — Menü **Extras → „Törns/Etappen
  gruppieren…"** (Törn anlegen/umbenennen/löschen, Etappen zuordnen). Löschen
  eines Törns lässt die Etappen bestehen.
- **Eintrag bearbeiten inkl. Messwerte** (`_EditEntryDialog`, Knopf
  „Bearbeiten…" / Doppelklick): neben Zeit, Anlass, Segel/Wetter, Ort/Crew,
  Notiz und Bildern lassen sich jetzt auch die **automatisch erfassten
  Messwerte korrigieren** — Breite/Länge, SOG/COG, Tiefe und wahrer Wind. Für
  Fälle wie eine falsche Koordinate in einem Autolog-Eintrag, die die
  Tagesdistanz verfälscht (z.B. „3500 sm an einem Tag"). Leeres Feld = Wert
  entfernt (None); der Eintrag wird als bearbeitet markiert.
- **Törn bearbeiten** (Knopf „✎ Törn bearbeiten…" in der Törn-Leiste,
  `_TripEditDialog`): Stammdaten eines bestehenden Törns nachträglich ändern —
  Name, Start-/Zielort, Start-/Endzeit (lokale Anzeige, intern UTC) sowie
  Wasser/Diesel/Motorstunden/Log-Stand und Notiz. Für Tippfehler-Korrekturen
  bei von Hand erfassten (älteren) Törns.
- **Neuer Eintrag (manuell, rückdatierbar)** (Knopf „➕ Neuer Eintrag…" unter
  der Tabelle, `_NewEntryDialog`): erfasst einen Logbuch-Eintrag mit **frei
  wählbarer Zeit** und **von Hand eingegebener Position** (Dezimalgrad, S/W als
  Minus) plus SOG/COG/Tiefe/wahrer Wind, Motor/Segel, Wetter, Ort/Crew/Notiz und
  Törn-Auswahl. Für nachträgliche Einträge wie in TripCon, z.B. bei einem
  **Unterbruch des Loggings**. (Der Knopf „✎ Eintrag speichern" oben nimmt
  weiterhin die aktuellen Live-Werte mit Jetzt-Zeit.)
- **Berichte** (`reports.py`, Knopf „📄 Bericht…" in der Törn-Leiste): druckbares
  HTML (im Browser → „Als PDF speichern"), nach TripCon-Vorbild:
  **Törn-Bericht** (ganzer Törn über mehrere Etappen: Titel mit Törnname/Revier/
  Zeitraum, Schiffsdaten, kombinierte Crew, Etappenübersicht + je Etappe
  Detail-Einträge + Zusammenfassung), **Etappen-Bericht** (einzelne Etappe =
  aktueller Trip), jeweils **mit Bildern** (base64-Data-URIs eingebettet, kein
  Server nötig) und **Fahrtenbuch** (alle Törns: Schiffsdaten, Etappen-Karten,
  Meilen-Summe gesegelt/Motor/gesamt aus der GPS-Spur). Eintrags-Raster:
  Position in Grad/Dezimalminuten, FüG/KüG, wahrer Wind, Tiefe, kumulatives
  Log, Luft/Bewölkung/Sicht, Segel, Notiz.
  **Eintragsarten-Filter** (Bericht-Dialog, Sektion „Eintragsarten & Karte"):
  wählbar, welche Typen (Autolog/Manuell/Import) **im Bericht gelistet** werden —
  der Filter gilt zugleich für die **Kartenmarkierung**. Distanz-/Meilen-
  Zusammenfassung wird weiterhin über **alle** Einträge (ganze Spur) berechnet;
  die Eintragszeile zeigt bei Filter „X von Y Einträgen" (`reports._keep`).
  Alle drei (oder keine) angehakt = kein Filter.
  **Karte im Bericht** (optional, dieselbe Sektion): bettet eine Leaflet-Karte
  **ohne AIS** ein — Route als Linie plus **markierte Einträge** (nach obigem
  Typ-Filter), `reports.map_block`, Leaflet vom CDN wie die AIS-Karte. Jeder
  Bericht hat oben einen **„Drucken / Als PDF speichern"-Knopf** (wie die
  Crewliste, `.noprint` beim Druck ausgeblendet).
- **Crewliste** (Ein-/Ausklarieren): Bootsangaben + Ort/Datum (gespeichert)
  + Crew je Törn; **Personen-Speicher** (einmal erfasste Personen sind über
  ein Auswahlmenü wiederverwendbar); druckbare, zweisprachige HTML-Liste
  (DE/EN) im Browser (`crewlist.py`, Knopf „Crewliste…" in der Törn-Leiste)
- **Personen verwalten** (Menü „Stammdaten"): Stammdaten mit Name, Vorname,
  E-Mail, Nationalität, Pass-Nr., Adresse, Geburtsort/-datum und **Foto** (aus
  Datei, verkleinert); Neu/Ändern/Löschen.
- **Schiffe verwalten** (Menü „Stammdaten"): Kennwerte (Typ, Kielart, Länge/
  Breite/Tiefgang, Verdrängung, Durchfahrtshöhe, Flagge, Heimathafen, Rufzeichen,
  MMSI, Echolot-Einbautiefe, **Loggeber-Korrekturfaktor**), Tanks (Wasser/
  Treibstoff), Ausrüstung, Schiffsfoto; mehrere Schiffe, aktives auswählbar. Die
  **Loggeber-Korrektur** des aktiven Schiffs wirkt auf STW/Gesamtlog beim Einlesen.
- **Logbuch-Karte (ohne AIS)** (Knopf „🗺 Logbuch-Karte…", `_LogMapDialog`):
  öffnet dieselbe Leaflet-Karte, aber **ohne AIS-Ziele und ohne Live-Position** —
  nur die Logbuch-Einträge des ausgewählten Törns. Im Dialog wählbar, **welche
  Eintragstypen** gezeigt werden (Autolog / Manuell / Import). Der Knopf „🗺
  AIS-Karte" zeigt weiterhin alles.
- **AIS-Decoder** (`!AIVDM`/`!AIVDO`, Typen 1/2/3/5/18/19/24, Mehrteiler) +
  **AIS-Karte** (Leaflet + OpenFreeMap) mit eigenem Schiff, Zielen, Törn-Track
  und **anklickbaren Logbuch-Einträgen** (Popup mit Details); Ebenen-Umschalter
  - Mehrteiler-Zusammensetzung je Funkkanal (Wetherdock vergibt Sequenz-ID neu)
  - **Automatische COG-Korrektur:** erkennt Feeds, die COG fälschlich in ganzen
    Grad statt Zehntelgrad liefern (B&G-Multiplexer an Bord), und rechnet um
  - `python -m triplog.ais "<!AIVDM-Zeile>"` — Sätze am Boot einzeln prüfen
- Werkzeuge (Repo-Root/Module): `find_sources.py`/`discover.py`
  (`--gofree --iface --raw`), `gofree_probe.py` (GoFree nav-ws/RTSP),
  `orca_probe.py` (Orca Core, siehe `docs/ORCA_CORE.md`), `import_tripcon.py`
  (`--info`/`--show-columns`/`--into-app`), `fix_trips.py` (Einträge
  umhängen/löschen, mit Backup), `inspect_backup.py`, NMEA-Simulator
- **TripCon-Import** (.tcdb): Törns, Messwerte, Tracks, Bilder, **Anlass**
  (LogEvent) + Wetter/Sicht aus den Übersetzungstabellen aufgelöst.
  **Plotterbilder** (B104_BinDat) werden beim Import an die passenden Einträge
  gehängt (**mehrere je Eintrag**) und dabei auf 1600 px (JPEG) verkleinert;
  Bilder ohne LogID (nur am Törn) kommen an den ersten Eintrag des Törns.
  `import_tripcon.py --info <datei.tcdb>` **analysiert** eine Sicherung
  (Integrität, Törns, Einträge, Bild-/Track-Zahlen, Zeitraum), ohne zu
  importieren. **Schiffe und Personen**
  (S003_Ships/S006_Persons) werden als Stammdaten angelegt (idempotent über den
  Namen; vorhandene Einträge werden erkannt, nicht dupliziert) — mit adaptiv
  gemappten Feldern (Kennwerte/Adresse etc.) und ihrem Foto. Die
  Bild-Verknüpfung ist schema-adaptiv (Fremdschlüssel oder Zeitstempel) und
  meldet die verwendete Methode.

## Offene Punkte / nächste Schritte

1. **Orca Core (geparkt, gut vorbereitet):** Live-Daten nur über WebSocket
   9000 (binär). Nächster Schritt: `orca_probe.py --listen --seconds 60` beim
   Bewegen des Boots; sonst App-Traffic mitschneiden (PCAPdroid). Vollständige
   Doku: **`docs/ORCA_CORE.md`**. Kosten/Nutzen gering (B&G liefert
   Krängung/Trimm/Ruder/Heading schon via XDR) — bewusst zurückgestellt.
2. **B&G/GoFree NICHT weiter vertiefen** (Nutzer-Entscheidung, B&G bleibt).
   GoFree-Discovery bleibt als „Gerät finden" (`discover.py --gofree`,
   „🔍 GoFree suchen" im Quellen-Dialog). `gofree_probe.py` (nav-ws:2053)
   liegt für spätere Neubewertung bereit.
3. **Plotter-Screenshot per WLAN-ADB:** Stolpersteine dokumentiert — **VPN
   aus** (blockiert LAN/adb komplett), feste Tablet-IP per DHCP-Reservierung,
   nach Tablet-Neustart einmal `adb tcpip 5555` per USB. Dialog: Extras →
   Plotter-Screenshot (ADB)…
4. **TripCon-Anlass verifizieren** (falls nötig): Mapping in
   `src/triplog/tripcon.py` (`_resolve_code`) anpassen.
5. **Optional:** CSV-Export wahlweise in Lokalzeit; weitere Zeitzonen;
   Rate-of-Turn (`ROT`)-Anzeige.

### Erledigt (Motordaten, Juli 2026)
Maretron `$PMAREPD` dekodiert → Kühlwassertemperatur, Lichtmaschinenspannung,
Motorstunden; `IIRPM` → Drehzahl; `ENV_ATMOS_P`/`ENV_OUTAIR_T`-XDR ergänzt.
Motor-an/aus nutzt jetzt vorrangig die Drehzahl.

## Architektur (Kurz)

```
src/triplog/
  app.py         Einstieg (GUI)
  gui.py         tkinter-Oberfläche (Quellen, Dashboard, Bedingungen,
                 Törns, Tabelle, Bearbeiten/Löschen, AIS-Karte, Zeitzone)
  source.py      Quelle: TCP/UDP/seriell (Thread, Reconnect, AIS-Routing)
  nmea.py        NMEA0183-Parser + FIELD_LABELS + engine_running()
  ais.py         AIS-Decoder (!AIVDM/!AIVDO) + Zielliste
  webmap.py      lokaler Kartenserver (Leaflet + OpenFreeMap)
  crewlist.py    druckbare Crewliste (HTML, DE/EN)
  reports.py     Berichte (Törn-/Etappenbericht, Fahrtenbuch) als HTML
  geo.py         Distanzen (Haversine) — Strecke im Törn aus der GPS-Spur
  fuel.py        Verbrauchsberechnung (l/h) aus den Tank-Einträgen
  livedata.py    thread-sicherer Messwert-Speicher
  logbook.py     Auto-/Manuell-Logging, Bedingungen, Törns
  autolog.py     AutoLog-Auslöser (Intervall/SOG/Kurs/Tiefe/…)
  photos.py      Foto-Import (Ordner-Watcher, Verkleinern auf JPEG)
  android_screencap.py  Plotter-Screenshot per ADB (USB/WLAN, Auto-Reconnect)
  backup.py      Datensicherung (ZIP: DB inkl. Fotos + Einstellungen)
  storage.py     SQLite: LogEntry/Trip, Migration, CSV/GPX, Bilder
  fields.py      Auswahllisten (Segel/Wetter/Sicht)
  timeutil.py    Zeitzonen-Umrechnung (UTC ↔ Anzeige)
  config.py      Einstellungen (~/.triplog/config.json)
  discover.py    Quellen-/Port-/GoFree-Scanner
  plotter_capture.py  Bild laden/als-PNG (Pillow optional, ungenutzt)
  legacy.py / tripcon.py  Analyse & Import alter TripCon-Sicherungen
  simulator.py   NMEA0183-Testsimulator
tests/           unittest (ohne Boot lauffähig)
```

Reine Python-Standardbibliothek (Pillow/pyserial nur optional). Tests laufen
ohne Hardware; GUI wird bei Bedarf unter Xvfb rauchgetestet.
