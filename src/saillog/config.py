"""Konfiguration für SailLog.

Die Einstellungen werden als JSON unter ~/.saillog/config.json abgelegt,
sodass sie über die GUI dauerhaft geändert werden können. Ebenda liegt
standardmäßig die Logbuch-Datenbank.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional


_LEGACY_DIRS = (".triplog", ".masarasi")     # frühere Programmnamen


def _app_dir() -> Path:
    """Verzeichnis für Konfiguration und Datenbank.

    Übernimmt einmalig die alten Daten aus einem früheren Namen
    (``~/.triplog`` bzw. ``~/.masarasi``), damit bestehende Logbücher nach der
    Umbenennung erhalten bleiben.
    """
    path = Path.home() / ".saillog"
    if not path.exists():
        for old in _LEGACY_DIRS:
            legacy = Path.home() / old
            if legacy.is_dir():
                try:
                    legacy.rename(path)      # nahtlose Übernahme (gleiches Volume)
                except OSError:
                    import shutil
                    shutil.copytree(legacy, path)
                break
    path.mkdir(parents=True, exist_ok=True)
    return path


CONFIG_PATH = _app_dir() / "config.json"


@dataclass
class Config:
    """Typisierte Anwendungskonfiguration."""

    # Gateway-Anbindung (Einzelquelle, für Abwärtskompatibilität)
    gateway_host: str = "192.168.4.1"
    gateway_port: int = 2000
    protocol: str = "tcp"  # "tcp", "udp" oder "serial"

    # Mehrere Datenquellen gleichzeitig: Liste von
    # {"host": ..., "port": ..., "protocol": ...}
    sources: Optional[list] = None

    # Automatisches Logging
    auto_interval_seconds: int = 300  # alle 5 Minuten (Alt-Wert)
    auto_enabled_on_start: bool = False
    # AutoLog-Auslöser (siehe autolog.AutoLogSettings); None = Standardwerte
    autolog: Optional[dict] = None

    # PDF-Export der Berichte: optionaler Pfad zu einem Chromium-Browser
    # (Edge/Chrome); leer = automatisch suchen (siehe pdf.find_browser)
    pdf_browser_path: str = ""

    # Foto-Import: Ordner überwachen, Bilder verkleinern, Auto-Eintrag anlegen
    photo_folder: str = ""               # Einzelordner (Abwärtskompatibilität)
    photo_folders: Optional[list] = None  # mehrere Ordner (z.B. je Gerät/App)
    photo_recursive: bool = False         # auch Unterordner mit überwachen
    photo_import_enabled: bool = False
    photo_max_px: int = 1600
    # Mehrere kurz nacheinander eintreffende Fotos zu EINEM Eintrag bündeln:
    # Zeitfenster in Sekunden (rollend). 0 = jedes Foto ein eigener Eintrag.
    photo_group_seconds: int = 90

    # Plotter-Screenshot per ADB (Android-Tablet mit Orca-/Plotter-Anzeige)
    plotter_adb_path: str = "adb"       # Pfad zu adb(.exe); "adb" = im PATH
    plotter_adb_serial: str = ""        # Geräte-Serial (leer = einziges Gerät)
    plotter_autolog: bool = False       # bei jedem Auto-Eintrag mitspeichern

    # Datensicherung (ZIP): Zielordner, automatisch beim Beenden, wie viele behalten
    backup_folder: str = ""
    backup_on_close: bool = False
    backup_keep: int = 5

    # Aktives Schiff (Stammdaten); dessen Loggeber-Korrektur wirkt auf STW/Log
    active_ship_id: Optional[int] = None

    # Speicherort der Datenbank
    db_path: str = str(_app_dir() / "logbook.sqlite3")

    # Bootsangaben (für Auto-Fill manueller Einträge)
    boat_name: str = ""

    # Bootsangaben für die Crewliste (Ein-/Ausklarieren)
    ship_name: str = ""            # Schiffsname / Name of yacht
    ship_type: str = ""            # Bootstyp (z.B. Segelyacht) / Type of boat
    ship_flag: str = ""            # Flagge / Flag
    home_port: str = ""            # Heimathafen / Port of registry
    call_sign: str = ""            # Rufzeichen / Call sign
    ship_mmsi: str = ""            # MMSI
    registration_no: str = ""      # Registriernummer / Registration No.
    ship_length: str = ""          # Länge über alles / Length overall

    # Zuletzt verwendeter Ort/Datum für die Crewliste (werden gemerkt)
    clearance_place: str = ""
    clearance_date: str = ""

    # Dieseltank-Größe in Litern (für Restfüllstand-/Reichweitenschätzung)
    tank_capacity_l: float = 160.0

    # Kartenplotter-Bildschirmausschnitt (GoFree): [links, oben, rechts, unten]
    plotter_region: Optional[list] = None
    plotter_interval_seconds: int = 15

    # Anzeige-Zeitzone: "system" (Rechnerzeit) oder "fixed" mit festem Versatz
    timezone_mode: str = "system"
    timezone_offset_hours: float = 0.0

    def photo_folder_list(self) -> list:
        """Effektive Liste der überwachten Foto-Ordner.

        Nutzt ``photo_folders`` (mehrere, z.B. je Gerät). Ist die Liste leer,
        wird der frühere Einzelordner ``photo_folder`` verwendet
        (Abwärtskompatibilität). Duplikate werden entfernt.
        """
        folders = [str(f).strip() for f in (self.photo_folders or []) if str(f).strip()]
        if not folders and self.photo_folder.strip():
            folders = [self.photo_folder.strip()]
        seen: set = set()
        out: list = []
        for f in folders:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        """Lädt die Konfiguration oder gibt Standardwerte zurück."""
        if not path.exists():
            return cls()
        try:
            data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls().__dict__}
        filtered = {k: v for k, v in data.items() if k in known}
        cfg = cls(**filtered)
        # Alten Datenpfad (~/.triplog bzw. ~/.masarasi) auf den neuen Ort
        # umbiegen, falls das migrierte config.json noch den früheren Pfad hält.
        for old in _LEGACY_DIRS:
            if cfg.db_path and old in cfg.db_path:
                moved = cfg.db_path.replace(old, ".saillog")
                if Path(moved).exists():
                    cfg.db_path = moved
                break
        return cfg

    def save(self, path: Path = CONFIG_PATH) -> None:
        """Speichert die Konfiguration als JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
