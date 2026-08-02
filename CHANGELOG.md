# Änderungsprotokoll

Alle nennenswerten Änderungen an **SailLog** werden hier festgehalten.
Format lose nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/);
Versionen nach [SemVer](https://semver.org/lang/de/).

## [0.1.1] – 2026-08-02

### Neu
- **Signal K als Datenquelle** (`signalk.py`, Quellen-Protokoll `signalk`,
  Vorlage „Signal K"): fragt einen **Signal-K-Server** (z.B. auf einem Victron
  Cerbo GX oder Raspberry Pi) im Sekundentakt über die REST-API ab
  (`…/signalk/v1/api/vessels/self`, Standard-Port 3000) und rechnet die
  SI-Einheiten auf die internen Bordeinheiten um. Reine Standardbibliothek,
  läuft im Mehrquellen-Betrieb neben TCP/UDP/seriell.
- **Demo-Datenbus** (Knopf „🎮 Demo-Datenbus") und **Demo-Daten beim ersten
  Start** (Beispiel-Törn) — Tester sehen sofort Live-Werte bzw. ein gefülltes
  Logbuch, ohne echtes Gateway.
- **Landingpage** (`docs/index.html`) für GitHub Pages / saillog.ch.

### Geändert
- **AutoLog-Kurswechsel entschärft** (weniger Einträge): **Motor-Ausnahme**
  (unter Maschine kein Kurswechsel-Eintrag, `course_skip_motor`),
  **Mindestabstand** zwischen Einträgen statt Mittelwertfenster
  (`course_cooldown_seconds`), festes 5-s-Glättungsfenster und Auswertung nur
  **unter Fahrt** (≥ 2 kn). Eine 90°-Wende erzeugt so nur noch **einen** Eintrag.
- **Quellen einzeln zu- und wegschalten** über eine **Priorität** (0 = aus)
  statt An/Aus-Haken — ohne die Quelle zu löschen.
- **Tankgröße bevorzugt aus den Schiffsdaten** (Treibstofftank des aktiven
  Schiffs) für die Restfüllstand-Schätzung.
- **Auto-Verbinden beim Start** (nur wenn eine Quelle konfiguriert ist).
- **Beim Beenden nach Backup fragen** (Vorgabe „Ja", Enter genügt).
- Kontakt-E-Mail auf `phaudenschild@saillog.ch` (neue Projekt-Domäne).

### Intern
- Testdaten/Fixtures anonymisiert; Wording in `docs/TESTING.md` präzisiert.

## [0.1.0]

- Erste getaggte Version: Segel-Logbuch mit Live-Dashboard, AutoLog,
  Törns/Etappen, AIS-Karte, Berichten (PDF), Seemeilen-Nachweis, Crewliste,
  Tanken/Verbrauch, Fern-Erfassung (Handy/Tablet), Backup, TripCon-Import,
  Mehrquellen-Eingang (TCP/UDP/seriell) und Windows-Build (EXE + Installer).

[0.1.1]: https://github.com/phaudenschild-sketch/saillog/releases/tag/v0.1.1
[0.1.0]: https://github.com/phaudenschild-sketch/saillog/releases/tag/v0.1.0
