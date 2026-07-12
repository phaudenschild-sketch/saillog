"""Datensicherung: Logbuch-Datenbank (inkl. Fotos) + Einstellungen als ZIP.

Alles Wichtige liegt in der SQLite-Datei (Einträge, Törns, Crew, Personen,
Tanken, Bilder). Ein Backup ist eine **zeitgestempelte ZIP** mit einer
konsistenten Kopie der Datenbank (via SQLite-Backup-API — sicher auch während
die App läuft) plus der `config.json`. Eine Datei zum Kopieren (USB-Stick).
"""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import List, Optional

_PREFIX = "triplog-backup-"


def create_backup(
    db_path: str,
    config_path: Optional[str],
    dest_folder: str,
    timestamp: str,
) -> Path:
    """Erzeugt `<dest>/triplog-backup-<timestamp>.zip`. Gibt den Pfad zurück.

    `timestamp` z.B. "20260710-143000" (wird vom Aufrufer gestellt, damit die
    Funktion gut testbar bleibt).
    """
    dest = Path(dest_folder)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{_PREFIX}{timestamp}.zip"
    tmp_db = dest / f".tmp-{timestamp}.sqlite3"

    # Konsistente DB-Kopie über die SQLite-Backup-API (auch bei offener DB).
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(str(tmp_db))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db, "logbook.sqlite3")
            if config_path and Path(config_path).exists():
                zf.write(config_path, "config.json")
    finally:
        try:
            tmp_db.unlink()
        except OSError:
            pass
    return out


def list_backups(dest_folder: str) -> List[Path]:
    """Vorhandene Backups, chronologisch (Zeitstempel = lexikografisch sortierbar)."""
    try:
        return sorted(Path(dest_folder).glob(f"{_PREFIX}*.zip"))
    except OSError:
        return []


def prune_backups(dest_folder: str, keep: int) -> int:
    """Behält die neuesten `keep` Backups, löscht ältere. Gibt Anzahl gelöscht."""
    if keep <= 0:
        return 0
    files = list_backups(dest_folder)
    removed = 0
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed
