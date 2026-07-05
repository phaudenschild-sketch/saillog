"""SQLite-Speicher für die Logbuch-Einträge inkl. CSV-/GPX-Export."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

# Spalten des Logbuchs. Reihenfolge = Anzeige-/Exportreihenfolge.
MEASUREMENT_COLUMNS = [
    "lat",
    "lon",
    "sog_kn",
    "cog_deg",
    "stw_kn",
    "hdg_true_deg",
    "hdg_mag_deg",
    "aws_kn",
    "awa_deg",
    "tws_kn",
    "twd_deg",
    "depth_m",
    "water_temp_c",
]


@dataclass
class LogEntry:
    """Ein einzelner Logbuch-Eintrag."""

    id: Optional[int] = None
    timestamp: str = ""          # UTC ISO-8601, z.B. "2026-07-05T09:30:00Z"
    entry_type: str = "auto"     # "auto" oder "manual"
    lat: Optional[float] = None
    lon: Optional[float] = None
    sog_kn: Optional[float] = None
    cog_deg: Optional[float] = None
    stw_kn: Optional[float] = None
    hdg_true_deg: Optional[float] = None
    hdg_mag_deg: Optional[float] = None
    aws_kn: Optional[float] = None
    awa_deg: Optional[float] = None
    tws_kn: Optional[float] = None
    twd_deg: Optional[float] = None
    depth_m: Optional[float] = None
    water_temp_c: Optional[float] = None
    note: str = ""
    crew: str = ""
    location: str = ""

    @classmethod
    def from_snapshot(
        cls,
        timestamp: str,
        entry_type: str,
        measurements: Dict[str, float],
        note: str = "",
        crew: str = "",
        location: str = "",
    ) -> "LogEntry":
        entry = cls(
            timestamp=timestamp,
            entry_type=entry_type,
            note=note,
            crew=crew,
            location=location,
        )
        for column in MEASUREMENT_COLUMNS:
            if column in measurements and measurements[column] is not None:
                setattr(entry, column, float(measurements[column]))
        return entry


_COLUMN_NAMES = [f.name for f in fields(LogEntry)]


class LogbookStore:
    """Persistiert Logbuch-Einträge in einer SQLite-Datei."""

    def __init__(self, db_path: str) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        columns = ",\n".join(
            [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "timestamp TEXT NOT NULL",
                "entry_type TEXT NOT NULL DEFAULT 'auto'",
                *[f"{col} REAL" for col in MEASUREMENT_COLUMNS],
                "note TEXT DEFAULT ''",
                "crew TEXT DEFAULT ''",
                "location TEXT DEFAULT ''",
            ]
        )
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS log_entries (\n{columns}\n)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_log_timestamp "
                "ON log_entries(timestamp)"
            )

    def add(self, entry: LogEntry) -> int:
        cols = [c for c in _COLUMN_NAMES if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        values = [getattr(entry, c) for c in cols]
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO log_entries ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            entry.id = cursor.lastrowid
        return entry.id

    def add_many(self, entries: List[LogEntry]) -> int:
        """Fügt viele Einträge in einer Transaktion ein (für Importe)."""
        cols = [c for c in _COLUMN_NAMES if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        rows = [[getattr(e, c) for c in cols] for e in entries]
        with self._connect() as conn:
            conn.executemany(
                f"INSERT INTO log_entries ({', '.join(cols)}) VALUES ({placeholders})",
                rows,
            )
        return len(rows)

    def delete_by_type(self, entry_type: str) -> int:
        """Löscht alle Einträge eines Typs (z.B. vor erneutem Import)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM log_entries WHERE entry_type = ?", (entry_type,)
            )
            return cursor.rowcount

    def all(self, limit: Optional[int] = None, newest_first: bool = True) -> List[LogEntry]:
        order = "DESC" if newest_first else "ASC"
        query = f"SELECT * FROM log_entries ORDER BY timestamp {order}, id {order}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete(self, entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM log_entries WHERE id = ?", (entry_id,))

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LogEntry:
        data: Dict[str, Any] = {k: row[k] for k in row.keys() if k in _COLUMN_NAMES}
        return LogEntry(**data)

    # --- Export -------------------------------------------------------------

    def export_csv(self, path: str) -> int:
        """Exportiert alle Einträge als CSV (chronologisch). Gibt Anzahl zurück."""
        entries = self.all(newest_first=False)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(_COLUMN_NAMES)
            for entry in entries:
                writer.writerow([getattr(entry, c) for c in _COLUMN_NAMES])
        return len(entries)

    def export_gpx(self, path: str, track_name: str = "masarasi Törn") -> int:
        """Exportiert Einträge mit Position als GPX-Track. Gibt Punktzahl zurück."""
        entries = [
            e
            for e in self.all(newest_first=False)
            if e.lat is not None and e.lon is not None
        ]
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<gpx version="1.1" creator="masarasi" '
            'xmlns="http://www.topografix.com/GPX/1/1">',
            "  <trk>",
            f"    <name>{escape(track_name)}</name>",
            "    <trkseg>",
        ]
        for entry in entries:
            lines.append(f'      <trkpt lat="{entry.lat:.6f}" lon="{entry.lon:.6f}">')
            if entry.timestamp:
                lines.append(f"        <time>{escape(entry.timestamp)}</time>")
            if entry.note:
                lines.append(f"        <name>{escape(entry.note)}</name>")
            lines.append("      </trkpt>")
        lines += ["    </trkseg>", "  </trk>", "</gpx>", ""]
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        return len(entries)
