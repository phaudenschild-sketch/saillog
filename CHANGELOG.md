# Änderungsprotokoll

Alle nennenswerten Änderungen an **SailLog** werden hier festgehalten.
Format lose nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
Versionen nach [SemVer](https://semver.org/lang/de/).

## [0.1.1] – 2026-08-02

### Neu
- **Signal-K-Anbindung** (`signalk.py`, Quellen-Protokoll `signalk`, Vorlage
  „Signal K (GX)"): SailLog liest das Datenmodell eines **Signal-K-Servers**
  (z.B. **Victron Cerbo GX** oder Raspberry Pi) als **JSON über HTTP**. Damit
  übernimmt Signal K das NMEA2000-Decoding, SailLog bleibt ein dünner Client —
  **verlustfreie Motordaten** ohne USB, Laptop per WLAN. Reine
  Standardbibliothek; SI-Einheiten werden automatisch umgerechnet. Am Boot
  prüfbar mit `python -m saillog.signalk <host>`.

### Geändert
- **AutoLog-Kurswechsel** wird erst **ab einer Mindestfahrt über Grund**
  gewertet (neuer Wert `course_min_sog`, Standard **2 kn**, im AutoLog-Dialog
  einstellbar). Das verhindert dauernde Kursänderungs-Einträge bei Stillstand
  (Kompass-/GPS-Rauschen im Hafen/beim Ankern). `0` schaltet die Sperre aus.

## [0.1.0]

- Erste Version: Segel-Logbuch mit Live-Dashboard, AutoLog, Törns/Etappen,
  AIS-Karte, Berichten (PDF), Seemeilen-Nachweis, Crewliste, Tanken/Verbrauch,
  Backup, TripCon-Import und Mehrquellen-Eingang (TCP/UDP/seriell).

[0.1.1]: https://github.com/phaudenschild-sketch/saillog/releases/tag/v0.1.1
[0.1.0]: https://github.com/phaudenschild-sketch/saillog/releases/tag/v0.1.0
