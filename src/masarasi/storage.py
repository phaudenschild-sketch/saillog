"""SQLite-Speicher für Logbuch-Einträge und Törns inkl. CSV-/GPX-Export."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

# Automatisch aus NMEA gefüllte Messwert-Spalten (REAL).
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
    "log_total_nm",
    "engine_hours",
    "engine_temp_c",
    "alternator_v",
]


@dataclass
class LogEntry:
    """Ein einzelner Logbuch-Eintrag."""

    id: Optional[int] = None
    timestamp: str = ""          # UTC ISO-8601, z.B. "2026-07-05T09:30:00Z"
    entry_type: str = "auto"     # "auto", "manual" oder "tripcon"
    trip_id: Optional[int] = None
    # Messwerte (aus NMEA)
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
    log_total_nm: Optional[float] = None
    engine_hours: Optional[float] = None
    engine_temp_c: Optional[float] = None
    alternator_v: Optional[float] = None
    # Manuelle / abgeleitete Felder
    engine_on: Optional[int] = None      # 1=Motor läuft, 0=aus, None=unbekannt
    mainsail: str = ""                   # Voll / Reff 1 / Reff 2 / Geborgen
    genoa_percent: Optional[float] = None  # 0–100
    spinnaker: Optional[int] = None      # 1=gesetzt, 0=nicht
    wave_height_m: Optional[float] = None
    cloud_cover: str = ""                # siehe fields.CLOUD_COVER
    precipitation: str = ""              # siehe fields.PRECIPITATION
    visibility: str = ""                 # siehe fields.VISIBILITY
    logevent: str = ""                   # Anlass, z.B. "Routineeintrag"
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
        trip_id: Optional[int] = None,
        engine_on: Optional[int] = None,
        mainsail: str = "",
        genoa_percent: Optional[float] = None,
        spinnaker: Optional[int] = None,
        wave_height_m: Optional[float] = None,
        cloud_cover: str = "",
        precipitation: str = "",
        visibility: str = "",
        logevent: str = "",
    ) -> "LogEntry":
        entry = cls(
            timestamp=timestamp,
            entry_type=entry_type,
            trip_id=trip_id,
            note=note,
            crew=crew,
            location=location,
            engine_on=engine_on,
            mainsail=mainsail,
            genoa_percent=genoa_percent,
            spinnaker=spinnaker,
            wave_height_m=wave_height_m,
            cloud_cover=cloud_cover,
            precipitation=precipitation,
            visibility=visibility,
            logevent=logevent,
        )
        for column in MEASUREMENT_COLUMNS:
            if column in measurements and measurements[column] is not None:
                setattr(entry, column, float(measurements[column]))
        return entry


@dataclass
class Trip:
    """Ein Törn — gruppiert Logbuch-Einträge und hält Start-/Endwerte."""

    id: Optional[int] = None
    name: str = ""
    status: str = "open"          # "open" oder "closed"
    start_location: str = ""
    start_dz: str = ""            # ISO-Zeitstempel
    end_location: str = ""
    end_dz: str = ""
    start_water_l: Optional[float] = None
    start_diesel_l: Optional[float] = None
    start_engine_hours: Optional[float] = None
    start_log_nm: Optional[float] = None
    end_water_l: Optional[float] = None
    end_diesel_l: Optional[float] = None
    end_engine_hours: Optional[float] = None
    end_log_nm: Optional[float] = None
    note: str = ""


_COLUMN_NAMES = [f.name for f in fields(LogEntry)]
_TRIP_COLUMNS = [f.name for f in fields(Trip)]

# Spalten, die bei bestehenden Datenbanken nachgezogen werden (ohne NOT NULL).
_MIGRATE_LOG = [
    ("trip_id", "INTEGER"),
    *[(c, "REAL") for c in MEASUREMENT_COLUMNS],
    ("engine_on", "INTEGER"),
    ("mainsail", "TEXT DEFAULT ''"),
    ("genoa_percent", "REAL"),
    ("spinnaker", "INTEGER"),
    ("wave_height_m", "REAL"),
    ("cloud_cover", "TEXT DEFAULT ''"),
    ("precipitation", "TEXT DEFAULT ''"),
    ("visibility", "TEXT DEFAULT ''"),
    ("logevent", "TEXT DEFAULT ''"),
    ("note", "TEXT DEFAULT ''"),
    ("crew", "TEXT DEFAULT ''"),
    ("location", "TEXT DEFAULT ''"),
]


class LogbookStore:
    """Persistiert Logbuch-Einträge und Törns in einer SQLite-Datei."""

    def __init__(self, db_path: str) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        log_columns = ",\n".join(
            [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "timestamp TEXT NOT NULL",
                "entry_type TEXT NOT NULL DEFAULT 'auto'",
                "trip_id INTEGER",
                *[f"{col} REAL" for col in MEASUREMENT_COLUMNS],
                "engine_on INTEGER",
                "mainsail TEXT DEFAULT ''",
                "genoa_percent REAL",
                "spinnaker INTEGER",
                "wave_height_m REAL",
                "cloud_cover TEXT DEFAULT ''",
                "precipitation TEXT DEFAULT ''",
                "visibility TEXT DEFAULT ''",
                "logevent TEXT DEFAULT ''",
                "note TEXT DEFAULT ''",
                "crew TEXT DEFAULT ''",
                "location TEXT DEFAULT ''",
            ]
        )
        trip_columns = ",\n".join(
            [
                "id INTEGER PRIMARY KEY AUTOINCREMENT",
                "name TEXT DEFAULT ''",
                "status TEXT NOT NULL DEFAULT 'open'",
                "start_location TEXT DEFAULT ''",
                "start_dz TEXT DEFAULT ''",
                "end_location TEXT DEFAULT ''",
                "end_dz TEXT DEFAULT ''",
                "start_water_l REAL",
                "start_diesel_l REAL",
                "start_engine_hours REAL",
                "start_log_nm REAL",
                "end_water_l REAL",
                "end_diesel_l REAL",
                "end_engine_hours REAL",
                "end_log_nm REAL",
                "note TEXT DEFAULT ''",
            ]
        )
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS log_entries (\n{log_columns}\n)")
            conn.execute(f"CREATE TABLE IF NOT EXISTS trips (\n{trip_columns}\n)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entry_images (\n"
                "  entry_id INTEGER PRIMARY KEY,\n"
                "  image BLOB NOT NULL,\n"
                "  mime TEXT DEFAULT 'image/png',\n"
                "  created_dz TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_log_timestamp "
                "ON log_entries(timestamp)"
            )
            self._migrate(conn)

    def _migrate(self, conn) -> None:
        """Fügt fehlende Spalten in bestehenden Datenbanken hinzu."""
        existing = {r[1] for r in conn.execute("PRAGMA table_info(log_entries)")}
        for name, sql_type in _MIGRATE_LOG:
            if name not in existing:
                conn.execute(f"ALTER TABLE log_entries ADD COLUMN {name} {sql_type}")

    # --- Einträge -----------------------------------------------------------

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
            conn.execute(
                "DELETE FROM entry_images WHERE entry_id IN "
                "(SELECT id FROM log_entries WHERE entry_type = ?)",
                (entry_type,),
            )
            cursor = conn.execute(
                "DELETE FROM log_entries WHERE entry_type = ?", (entry_type,)
            )
            return cursor.rowcount

    def all(
        self,
        limit: Optional[int] = None,
        newest_first: bool = True,
        trip_id: Optional[int] = None,
    ) -> List[LogEntry]:
        order = "DESC" if newest_first else "ASC"
        query = "SELECT * FROM log_entries"
        params: List[Any] = []
        if trip_id is not None:
            query += " WHERE trip_id = ?"
            params.append(trip_id)
        query += f" ORDER BY timestamp {order}, id {order}"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def delete(self, entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM entry_images WHERE entry_id = ?", (entry_id,))
            conn.execute("DELETE FROM log_entries WHERE id = ?", (entry_id,))

    # --- Bilder pro Eintrag (z.B. Kartenplotter-Screenshots) ---------------

    def set_image(self, entry_id: int, data: bytes, mime: str = "image/png",
                  created_dz: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entry_images (entry_id, image, mime, created_dz) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, sqlite3.Binary(data), mime, created_dz),
            )

    def get_image(self, entry_id: int) -> Optional[bytes]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT image FROM entry_images WHERE entry_id = ?", (entry_id,)
            ).fetchone()
        return bytes(row[0]) if row else None

    def entries_with_images(self) -> set:
        with self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT entry_id FROM entry_images")}

    def export_entry_images(self, out_dir: str) -> int:
        """Schreibt alle Eintrags-Bilder als Dateien (nach Zeitstempel benannt)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        count = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT i.entry_id, i.image, i.mime, e.timestamp "
                "FROM entry_images i LEFT JOIN log_entries e ON e.id = i.entry_id"
            ).fetchall()
        for entry_id, image, mime, timestamp in rows:
            ext = "jpg" if mime and "jpeg" in mime else "png"
            stamp = (timestamp or "").replace(":", "").replace("-", "").replace("T", "_")
            name = f"{stamp}_{entry_id}.{ext}" if stamp else f"eintrag_{entry_id}.{ext}"
            (out / name).write_bytes(bytes(image))
            count += 1
        return count

    def count(self, trip_id: Optional[int] = None) -> int:
        with self._connect() as conn:
            if trip_id is not None:
                return conn.execute(
                    "SELECT COUNT(*) FROM log_entries WHERE trip_id = ?", (trip_id,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> LogEntry:
        data: Dict[str, Any] = {k: row[k] for k in row.keys() if k in _COLUMN_NAMES}
        return LogEntry(**data)

    # --- Törns --------------------------------------------------------------

    def add_trip(self, trip: Trip) -> int:
        cols = [c for c in _TRIP_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        values = [getattr(trip, c) for c in cols]
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO trips ({', '.join(cols)}) VALUES ({placeholders})", values
            )
            trip.id = cursor.lastrowid
        return trip.id

    def update_trip(self, trip: Trip) -> None:
        cols = [c for c in _TRIP_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [getattr(trip, c) for c in cols] + [trip.id]
        with self._connect() as conn:
            conn.execute(f"UPDATE trips SET {assignments} WHERE id = ?", values)

    def get_trip(self, trip_id: int) -> Optional[Trip]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
        return self._row_to_trip(row) if row else None

    def all_trips(self, newest_first: bool = True) -> List[Trip]:
        order = "DESC" if newest_first else "ASC"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM trips ORDER BY start_dz {order}, id {order}"
            ).fetchall()
        return [self._row_to_trip(r) for r in rows]

    def open_trip(self) -> Optional[Trip]:
        """Der zuletzt begonnene, noch offene Törn (oder None)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM trips WHERE status = 'open' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return self._row_to_trip(row) if row else None

    def delete_trip(self, trip_id: int, unassign_entries: bool = True) -> None:
        with self._connect() as conn:
            if unassign_entries:
                conn.execute(
                    "UPDATE log_entries SET trip_id = NULL WHERE trip_id = ?", (trip_id,)
                )
            conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))

    @staticmethod
    def _row_to_trip(row: sqlite3.Row) -> Trip:
        data = {k: row[k] for k in row.keys() if k in _TRIP_COLUMNS}
        return Trip(**data)

    # --- Export -------------------------------------------------------------

    def export_csv(self, path: str, trip_id: Optional[int] = None) -> int:
        entries = self.all(newest_first=False, trip_id=trip_id)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(_COLUMN_NAMES)
            for entry in entries:
                writer.writerow([getattr(entry, c) for c in _COLUMN_NAMES])
        return len(entries)

    def export_gpx(
        self, path: str, track_name: str = "masarasi Törn", trip_id: Optional[int] = None
    ) -> int:
        entries = [
            e
            for e in self.all(newest_first=False, trip_id=trip_id)
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
