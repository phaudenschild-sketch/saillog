"""Konfiguration für masarasi.

Die Einstellungen werden als JSON unter ~/.masarasi/config.json abgelegt,
sodass sie über die GUI dauerhaft geändert werden können. Ebenda liegt
standardmäßig die Logbuch-Datenbank.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _app_dir() -> Path:
    """Verzeichnis für Konfiguration und Datenbank."""
    path = Path.home() / ".masarasi"
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

    # Foto-Import: Ordner überwachen, Bilder verkleinern, Auto-Eintrag anlegen
    photo_folder: str = ""
    photo_import_enabled: bool = False
    photo_max_px: int = 1600

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
        return cls(**filtered)

    def save(self, path: Path = CONFIG_PATH) -> None:
        """Speichert die Konfiguration als JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
