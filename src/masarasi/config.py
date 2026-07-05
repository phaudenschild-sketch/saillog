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
    auto_interval_seconds: int = 300  # alle 5 Minuten
    auto_enabled_on_start: bool = False

    # Speicherort der Datenbank
    db_path: str = str(_app_dir() / "logbook.sqlite3")

    # Bootsangaben (für Auto-Fill manueller Einträge)
    boat_name: str = ""

    # Kartenplotter-Bildschirmausschnitt (GoFree): [links, oben, rechts, unten]
    plotter_region: Optional[list] = None
    plotter_interval_seconds: int = 15

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
