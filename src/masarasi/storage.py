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
    "air_temp_c",
    "baro_mbar",
    "heel_deg",
    "trim_deg",
    "rudder_deg",
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
    air_temp_c: Optional[float] = None
    baro_mbar: Optional[float] = None
    heel_deg: Optional[float] = None
    trim_deg: Optional[float] = None
    rudder_deg: Optional[float] = None
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
    edited: Optional[int] = None         # 1 = nachträglich bearbeitet
    edited_dz: str = ""                  # Zeitpunkt der Bearbeitung (ISO)
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
    voyage_id: Optional[int] = None     # Zugehöriger Törn (mehrere Etappen)


@dataclass
class Voyage:
    """Ein Törn — fasst mehrere Etappen (Trips) zusammen."""

    id: Optional[int] = None
    name: str = ""
    revier: str = ""
    note: str = ""


@dataclass
class CrewMember:
    """Ein Crew-Mitglied (für die Crewliste beim Ein-/Ausklarieren)."""

    id: Optional[int] = None
    trip_id: Optional[int] = None
    position: str = "Crew"        # "Skipper" oder "Crew"
    last_name: str = ""           # Name / Surname
    first_name: str = ""          # Vorname / First name
    birth_date: str = ""          # Geburtsdatum / Date of birth
    birth_place: str = ""         # Geburtsort / Place of birth
    nationality: str = ""         # Staatsangehörigkeit / Nationality
    passport_no: str = ""         # Reisepass-/Ausweis-Nr. / Passport No.
    sort_order: int = 0           # Reihenfolge (Skipper zuerst)


@dataclass
class Person:
    """Eine gespeicherte Person (wiederverwendbar über Törns hinweg)."""

    id: Optional[int] = None
    last_name: str = ""
    first_name: str = ""
    birth_date: str = ""
    birth_place: str = ""
    nationality: str = ""
    passport_no: str = ""
    email: str = ""
    street: str = ""
    zip_code: str = ""
    city: str = ""


@dataclass
class Ship:
    """Ein Schiff (Stammdaten, nach TripCon-Vorlage „Schiffe verwalten")."""

    id: Optional[int] = None
    name: str = ""
    ship_type: str = ""               # Schiffstyp (z.B. Sailing Vessel)
    keel_type: str = ""               # Kielart
    ship_number: str = ""             # Schiffsnummer
    length_m: Optional[float] = None  # Länge über alles
    beam_m: Optional[float] = None    # Breite
    max_draft_m: Optional[float] = None       # maximaler Tiefgang
    displacement_t: Optional[float] = None    # Verdrängung
    clearance_height_m: Optional[float] = None  # Durchfahrtshöhe
    flag: str = ""                    # Flagge
    home_port: str = ""               # Heimathafen
    call_sign: str = ""               # Rufzeichen
    mmsi: str = ""                    # MMSI
    echo_depth_m: Optional[float] = None      # Einbautiefe Echolot
    log_correction: float = 1.0       # Korrekturfaktor Loggeber
    water_tank_l: Optional[float] = None      # Wassertank (Liter)
    fuel_tank_l: Optional[float] = None       # Treibstofftank (Liter)
    sails: str = ""                   # Antrieb/Segel (Freitext)
    equipment: str = ""               # Ausstattung (Freitext)
    power_source: str = ""            # Stromversorgung (Freitext)


@dataclass
class FuelEntry:
    """Ein Tank-Vorgang (für Verbrauchsberechnung l/h)."""

    id: Optional[int] = None
    trip_id: Optional[int] = None
    timestamp: str = ""              # UTC ISO-8601
    liters: Optional[float] = None   # getankte Menge
    location: str = ""               # Tankort
    full_tank: int = 0               # 1 = voll getankt (Bezugspunkt)
    engine_hours: Optional[float] = None  # Motorstunden zum Zeitpunkt (aus NMEA)
    note: str = ""


_COLUMN_NAMES = [f.name for f in fields(LogEntry)]
_TRIP_COLUMNS = [f.name for f in fields(Trip)]
_VOYAGE_COLUMNS = [f.name for f in fields(Voyage)]
_CREW_COLUMNS = [f.name for f in fields(CrewMember)]
_PERSON_COLUMNS = [f.name for f in fields(Person)]
_FUEL_COLUMNS = [f.name for f in fields(FuelEntry)]
_SHIP_COLUMNS = [f.name for f in fields(Ship)]

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
    ("edited", "INTEGER"),
    ("edited_dz", "TEXT DEFAULT ''"),
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
                "edited INTEGER",
                "edited_dz TEXT DEFAULT ''",
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
                "voyage_id INTEGER",
            ]
        )
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS log_entries (\n{log_columns}\n)")
            conn.execute(f"CREATE TABLE IF NOT EXISTS trips (\n{trip_columns}\n)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS voyages (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  name TEXT DEFAULT '',\n"
                "  revier TEXT DEFAULT '',\n"
                "  note TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS entry_images (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  entry_id INTEGER NOT NULL,\n"
                "  image BLOB NOT NULL,\n"
                "  mime TEXT DEFAULT 'image/png',\n"
                "  created_dz TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entry_images_entry "
                "ON entry_images(entry_id)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS crew_members (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  trip_id INTEGER,\n"
                "  position TEXT DEFAULT 'Crew',\n"
                "  last_name TEXT DEFAULT '',\n"
                "  first_name TEXT DEFAULT '',\n"
                "  birth_date TEXT DEFAULT '',\n"
                "  birth_place TEXT DEFAULT '',\n"
                "  nationality TEXT DEFAULT '',\n"
                "  passport_no TEXT DEFAULT '',\n"
                "  sort_order INTEGER DEFAULT 0\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS persons (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  last_name TEXT DEFAULT '',\n"
                "  first_name TEXT DEFAULT '',\n"
                "  birth_date TEXT DEFAULT '',\n"
                "  birth_place TEXT DEFAULT '',\n"
                "  nationality TEXT DEFAULT '',\n"
                "  passport_no TEXT DEFAULT '',\n"
                "  email TEXT DEFAULT '',\n"
                "  street TEXT DEFAULT '',\n"
                "  zip_code TEXT DEFAULT '',\n"
                "  city TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS person_photos (\n"
                "  person_id INTEGER PRIMARY KEY,\n"
                "  image BLOB NOT NULL,\n"
                "  mime TEXT DEFAULT 'image/jpeg'\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ships (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  name TEXT DEFAULT '',\n"
                "  ship_type TEXT DEFAULT '',\n"
                "  keel_type TEXT DEFAULT '',\n"
                "  ship_number TEXT DEFAULT '',\n"
                "  length_m REAL, beam_m REAL, max_draft_m REAL,\n"
                "  displacement_t REAL, clearance_height_m REAL,\n"
                "  flag TEXT DEFAULT '', home_port TEXT DEFAULT '',\n"
                "  call_sign TEXT DEFAULT '', mmsi TEXT DEFAULT '',\n"
                "  echo_depth_m REAL, log_correction REAL DEFAULT 1.0,\n"
                "  water_tank_l REAL, fuel_tank_l REAL,\n"
                "  sails TEXT DEFAULT '', equipment TEXT DEFAULT '',\n"
                "  power_source TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ship_photos (\n"
                "  ship_id INTEGER PRIMARY KEY,\n"
                "  image BLOB NOT NULL,\n"
                "  mime TEXT DEFAULT 'image/jpeg'\n)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS fuel_entries (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  trip_id INTEGER,\n"
                "  timestamp TEXT NOT NULL,\n"
                "  liters REAL,\n"
                "  location TEXT DEFAULT '',\n"
                "  full_tank INTEGER DEFAULT 0,\n"
                "  engine_hours REAL,\n"
                "  note TEXT DEFAULT ''\n)"
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
        # Personen: E-Mail/Adresse nachziehen
        existing_p = {r[1] for r in conn.execute("PRAGMA table_info(persons)")}
        for name in ("email", "street", "zip_code", "city"):
            if name not in existing_p:
                conn.execute(f"ALTER TABLE persons ADD COLUMN {name} TEXT DEFAULT ''")

        # Törn-Gruppierung: Etappen (trips) können zu einem Törn (voyage) gehören
        existing_t = {r[1] for r in conn.execute("PRAGMA table_info(trips)")}
        if "voyage_id" not in existing_t:
            conn.execute("ALTER TABLE trips ADD COLUMN voyage_id INTEGER")

        # entry_images: von „ein Bild pro Eintrag" (entry_id = PK) auf mehrere
        # Bilder je Eintrag umstellen (eigene id, entry_id nur noch indiziert).
        img_cols = {r[1] for r in conn.execute("PRAGMA table_info(entry_images)")}
        if img_cols and "id" not in img_cols:
            conn.execute("ALTER TABLE entry_images RENAME TO entry_images_old")
            conn.execute(
                "CREATE TABLE entry_images (\n"
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "  entry_id INTEGER NOT NULL,\n"
                "  image BLOB NOT NULL,\n"
                "  mime TEXT DEFAULT 'image/png',\n"
                "  created_dz TEXT DEFAULT ''\n)"
            )
            conn.execute(
                "INSERT INTO entry_images (entry_id, image, mime, created_dz) "
                "SELECT entry_id, image, mime, created_dz FROM entry_images_old"
            )
            conn.execute("DROP TABLE entry_images_old")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entry_images_entry "
                "ON entry_images(entry_id)"
            )

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

    def update(self, entry: LogEntry) -> None:
        """Aktualisiert einen bestehenden Eintrag (alle Felder außer id)."""
        cols = [c for c in _COLUMN_NAMES if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [getattr(entry, c) for c in cols] + [entry.id]
        with self._connect() as conn:
            conn.execute(f"UPDATE log_entries SET {assignments} WHERE id = ?", values)

    def get(self, entry_id: int) -> Optional[LogEntry]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM log_entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._row_to_entry(row) if row else None

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

    def add_many_returning_ids(self, entries: List[LogEntry]) -> List[int]:
        """Fügt viele Einträge in EINER Transaktion ein und setzt/liefert die ids."""
        cols = [c for c in _COLUMN_NAMES if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        ids: List[int] = []
        with self._connect() as conn:
            for entry in entries:
                cursor = conn.execute(
                    f"INSERT INTO log_entries ({', '.join(cols)}) VALUES ({placeholders})",
                    [getattr(entry, c) for c in cols],
                )
                entry.id = cursor.lastrowid
                ids.append(entry.id)
        return ids

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
        include_track: bool = False,
    ) -> List[LogEntry]:
        """Logbuch-Einträge. Reine Track-Punkte (entry_type='track') dienen nur
        der dichten Kartenspur und werden standardmäßig **ausgeblendet**; mit
        ``include_track=True`` sind sie enthalten (für Karte/GPX)."""
        order = "DESC" if newest_first else "ASC"
        query = "SELECT * FROM log_entries"
        clauses: List[str] = []
        params: List[Any] = []
        if trip_id is not None:
            clauses.append("trip_id = ?")
            params.append(trip_id)
        if not include_track:
            clauses.append("entry_type != 'track'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
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

    # --- Bilder pro Eintrag (mehrere je Eintrag möglich) -------------------

    def add_entry_image(self, entry_id: int, data: bytes,
                        mime: str = "image/jpeg", created_dz: str = "") -> int:
        """Hängt EIN weiteres Bild an den Eintrag an. Gibt die Bild-id zurück."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO entry_images (entry_id, image, mime, created_dz) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, sqlite3.Binary(data), mime, created_dz),
            )
            return cursor.lastrowid

    def set_image(self, entry_id: int, data: bytes, mime: str = "image/png",
                  created_dz: str = "") -> int:
        """Ersetzt ALLE Bilder des Eintrags durch dieses eine (für Einzel-Import)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM entry_images WHERE entry_id = ?", (entry_id,))
            cursor = conn.execute(
                "INSERT INTO entry_images (entry_id, image, mime, created_dz) "
                "VALUES (?, ?, ?, ?)",
                (entry_id, sqlite3.Binary(data), mime, created_dz),
            )
            return cursor.lastrowid

    def get_image(self, entry_id: int) -> Optional[bytes]:
        """Erstes Bild des Eintrags (Rückwärtskompatibilität / Einzelanzeige)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT image FROM entry_images WHERE entry_id = ? ORDER BY id LIMIT 1",
                (entry_id,),
            ).fetchone()
        return bytes(row[0]) if row else None

    def get_entry_images(self, entry_id: int) -> List[Dict[str, Any]]:
        """Alle Bilder eines Eintrags als [{id, image, mime, created_dz}, …]."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, image, mime, created_dz FROM entry_images "
                "WHERE entry_id = ? ORDER BY id",
                (entry_id,),
            ).fetchall()
        return [
            {"id": r[0], "image": bytes(r[1]), "mime": r[2], "created_dz": r[3]}
            for r in rows
        ]

    def get_image_by_id(self, image_id: int) -> Optional[tuple]:
        """(Bytes, MIME) eines einzelnen Bildes (für Karte/Bearbeiten)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT image, mime FROM entry_images WHERE id = ?", (image_id,)
            ).fetchone()
        return (bytes(row[0]), row[1]) if row else None

    def image_ids(self, entry_id: int) -> List[int]:
        with self._connect() as conn:
            return [
                r[0] for r in conn.execute(
                    "SELECT id FROM entry_images WHERE entry_id = ? ORDER BY id",
                    (entry_id,),
                )
            ]

    def image_ids_map(self, entry_ids: List[int]) -> Dict[int, List[int]]:
        """{entry_id: [Bild-ids]} für viele Einträge in EINER Abfrage (Karte)."""
        result: Dict[int, List[int]] = {}
        if not entry_ids:
            return result
        placeholders = ",".join("?" for _ in entry_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT entry_id, id FROM entry_images "
                f"WHERE entry_id IN ({placeholders}) ORDER BY id",
                list(entry_ids),
            ).fetchall()
        for entry_id, image_id in rows:
            result.setdefault(entry_id, []).append(image_id)
        return result

    def count_entry_images(self, entry_id: int) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM entry_images WHERE entry_id = ?", (entry_id,)
            ).fetchone()[0]

    def delete_entry_image(self, image_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM entry_images WHERE id = ?", (image_id,))

    def entries_with_images(self) -> set:
        with self._connect() as conn:
            return {
                r[0] for r in conn.execute("SELECT DISTINCT entry_id FROM entry_images")
            }

    def export_entry_images(self, out_dir: str) -> int:
        """Schreibt alle Eintrags-Bilder als Dateien (nach Zeitstempel benannt)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        count = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT i.id, i.entry_id, i.image, i.mime, e.timestamp "
                "FROM entry_images i LEFT JOIN log_entries e ON e.id = i.entry_id"
            ).fetchall()
        for img_id, entry_id, image, mime, timestamp in rows:
            ext = "jpg" if mime and "jpeg" in mime else "png"
            stamp = (timestamp or "").replace(":", "").replace("-", "").replace("T", "_")
            base = f"{stamp}_{entry_id}" if stamp else f"eintrag_{entry_id}"
            (out / f"{base}_{img_id}.{ext}").write_bytes(bytes(image))
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

    # --- Törns (Voyage: fasst mehrere Etappen/Trips zusammen) --------------

    def add_voyage(self, voyage: Voyage) -> int:
        cols = [c for c in _VOYAGE_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO voyages ({', '.join(cols)}) VALUES ({placeholders})",
                [getattr(voyage, c) for c in cols],
            )
            voyage.id = cursor.lastrowid
        return voyage.id

    def update_voyage(self, voyage: Voyage) -> None:
        cols = [c for c in _VOYAGE_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE voyages SET {assignments} WHERE id = ?",
                [getattr(voyage, c) for c in cols] + [voyage.id],
            )

    def get_voyage(self, voyage_id: int) -> Optional[Voyage]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM voyages WHERE id = ?", (voyage_id,)).fetchone()
        return self._row_to_voyage(row) if row else None

    def all_voyages(self) -> List[Voyage]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM voyages ORDER BY name, id").fetchall()
        return [self._row_to_voyage(r) for r in rows]

    def delete_voyage(self, voyage_id: int) -> None:
        """Löscht den Törn; die Etappen bleiben erhalten (voyage_id -> NULL)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET voyage_id = NULL WHERE voyage_id = ?", (voyage_id,))
            conn.execute("DELETE FROM voyages WHERE id = ?", (voyage_id,))

    def set_trip_voyage(self, trip_id: int, voyage_id: Optional[int]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trips SET voyage_id = ? WHERE id = ?", (voyage_id, trip_id))

    def trips_for_voyage(self, voyage_id: int) -> List[Trip]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trips WHERE voyage_id = ? ORDER BY start_dz, id",
                (voyage_id,),
            ).fetchall()
        return [self._row_to_trip(r) for r in rows]

    @staticmethod
    def _row_to_voyage(row: sqlite3.Row) -> Voyage:
        data = {k: row[k] for k in row.keys() if k in _VOYAGE_COLUMNS}
        return Voyage(**data)

    # --- Crew (Crewliste) ---------------------------------------------------

    def add_crew(self, member: CrewMember) -> int:
        cols = [c for c in _CREW_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        values = [getattr(member, c) for c in cols]
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO crew_members ({', '.join(cols)}) VALUES ({placeholders})",
                values,
            )
            member.id = cursor.lastrowid
        return member.id

    def update_crew(self, member: CrewMember) -> None:
        cols = [c for c in _CREW_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        values = [getattr(member, c) for c in cols] + [member.id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE crew_members SET {assignments} WHERE id = ?", values
            )

    def delete_crew(self, crew_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM crew_members WHERE id = ?", (crew_id,))

    def crew_for_trip(self, trip_id: Optional[int]) -> List[CrewMember]:
        """Crew des Törns (Skipper zuerst), geordnet nach sort_order und id."""
        with self._connect() as conn:
            if trip_id is None:
                rows = conn.execute(
                    "SELECT * FROM crew_members WHERE trip_id IS NULL "
                    "ORDER BY sort_order, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM crew_members WHERE trip_id = ? "
                    "ORDER BY sort_order, id",
                    (trip_id,),
                ).fetchall()
        return [self._row_to_crew(r) for r in rows]

    @staticmethod
    def _row_to_crew(row: sqlite3.Row) -> CrewMember:
        data = {k: row[k] for k in row.keys() if k in _CREW_COLUMNS}
        return CrewMember(**data)

    # --- Personen (wiederverwendbare Crew-Liste) ----------------------------

    def all_persons(self) -> List[Person]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM persons ORDER BY last_name, first_name"
            ).fetchall()
        return [self._row_to_person(r) for r in rows]

    def save_person(self, person: Person) -> Optional[int]:
        """Legt eine Person an oder aktualisiert sie (für spätere Verwendung).

        Ohne Namen wird nichts gespeichert. Existiert bereits eine Person mit
        gleichem Namen (und Geburtsdatum), werden deren Angaben aktualisiert,
        statt eine Dublette anzulegen.
        """
        if not (person.last_name.strip() or person.first_name.strip()):
            return None
        cols = [c for c in _PERSON_COLUMNS if c != "id"]
        with self._connect() as conn:
            pid = person.id
            if pid is None:
                row = conn.execute(
                    "SELECT id FROM persons WHERE "
                    "lower(last_name) = lower(?) AND lower(first_name) = lower(?) "
                    "AND birth_date = ?",
                    (person.last_name.strip(), person.first_name.strip(),
                     person.birth_date.strip()),
                ).fetchone()
                pid = row["id"] if row else None
            if pid is None:
                placeholders = ", ".join("?" for _ in cols)
                cursor = conn.execute(
                    f"INSERT INTO persons ({', '.join(cols)}) VALUES ({placeholders})",
                    [getattr(person, c) for c in cols],
                )
                return cursor.lastrowid
            assignments = ", ".join(f"{c} = ?" for c in cols)
            conn.execute(
                f"UPDATE persons SET {assignments} WHERE id = ?",
                [getattr(person, c) for c in cols] + [pid],
            )
            return pid

    def add_person(self, person: Person) -> int:
        """Legt immer eine neue Person an (für „Neu" in der Verwaltung)."""
        cols = [c for c in _PERSON_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO persons ({', '.join(cols)}) VALUES ({placeholders})",
                [getattr(person, c) for c in cols],
            )
            person.id = cursor.lastrowid
        return person.id

    def update_person(self, person: Person) -> None:
        """Aktualisiert eine Person anhand ihrer id."""
        cols = [c for c in _PERSON_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE persons SET {assignments} WHERE id = ?",
                [getattr(person, c) for c in cols] + [person.id],
            )

    def get_person(self, person_id: int) -> Optional[Person]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM persons WHERE id = ?", (person_id,)
            ).fetchone()
        return self._row_to_person(row) if row else None

    def delete_person(self, person_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM person_photos WHERE person_id = ?", (person_id,))
            conn.execute("DELETE FROM persons WHERE id = ?", (person_id,))

    def set_person_photo(self, person_id: int, data: bytes,
                         mime: str = "image/jpeg") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO person_photos (person_id, image, mime) "
                "VALUES (?, ?, ?)",
                (person_id, sqlite3.Binary(data), mime),
            )

    def get_person_photo(self, person_id: int) -> Optional[bytes]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT image FROM person_photos WHERE person_id = ?", (person_id,)
            ).fetchone()
        return bytes(row[0]) if row else None

    def delete_person_photo(self, person_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM person_photos WHERE person_id = ?", (person_id,))

    def persons_with_photos(self) -> set:
        with self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT person_id FROM person_photos")}

    @staticmethod
    def _row_to_person(row: sqlite3.Row) -> Person:
        data = {k: row[k] for k in row.keys() if k in _PERSON_COLUMNS}
        return Person(**data)

    # --- Schiffe (Stammdaten) ----------------------------------------------

    def add_ship(self, ship: Ship) -> int:
        cols = [c for c in _SHIP_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO ships ({', '.join(cols)}) VALUES ({placeholders})",
                [getattr(ship, c) for c in cols],
            )
            ship.id = cursor.lastrowid
        return ship.id

    def update_ship(self, ship: Ship) -> None:
        cols = [c for c in _SHIP_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE ships SET {assignments} WHERE id = ?",
                [getattr(ship, c) for c in cols] + [ship.id],
            )

    def get_ship(self, ship_id: int) -> Optional[Ship]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM ships WHERE id = ?", (ship_id,)).fetchone()
        return self._row_to_ship(row) if row else None

    def all_ships(self) -> List[Ship]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM ships ORDER BY name, id").fetchall()
        return [self._row_to_ship(r) for r in rows]

    def delete_ship(self, ship_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ship_photos WHERE ship_id = ?", (ship_id,))
            conn.execute("DELETE FROM ships WHERE id = ?", (ship_id,))

    def set_ship_photo(self, ship_id: int, data: bytes,
                       mime: str = "image/jpeg") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ship_photos (ship_id, image, mime) "
                "VALUES (?, ?, ?)",
                (ship_id, sqlite3.Binary(data), mime),
            )

    def get_ship_photo(self, ship_id: int) -> Optional[bytes]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT image FROM ship_photos WHERE ship_id = ?", (ship_id,)
            ).fetchone()
        return bytes(row[0]) if row else None

    def delete_ship_photo(self, ship_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ship_photos WHERE ship_id = ?", (ship_id,))

    @staticmethod
    def _row_to_ship(row: sqlite3.Row) -> Ship:
        data = {k: row[k] for k in row.keys() if k in _SHIP_COLUMNS}
        return Ship(**data)

    # --- Tanken (Kraftstoff) ------------------------------------------------

    def add_fuel(self, entry: FuelEntry) -> int:
        cols = [c for c in _FUEL_COLUMNS if c != "id"]
        placeholders = ", ".join("?" for _ in cols)
        with self._connect() as conn:
            cursor = conn.execute(
                f"INSERT INTO fuel_entries ({', '.join(cols)}) VALUES ({placeholders})",
                [getattr(entry, c) for c in cols],
            )
            entry.id = cursor.lastrowid
        return entry.id

    def update_fuel(self, entry: FuelEntry) -> None:
        cols = [c for c in _FUEL_COLUMNS if c != "id"]
        assignments = ", ".join(f"{c} = ?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE fuel_entries SET {assignments} WHERE id = ?",
                [getattr(entry, c) for c in cols] + [entry.id],
            )

    def delete_fuel(self, fuel_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM fuel_entries WHERE id = ?", (fuel_id,))

    def all_fuel(self, newest_first: bool = False) -> List[FuelEntry]:
        order = "DESC" if newest_first else "ASC"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM fuel_entries ORDER BY timestamp {order}, id {order}"
            ).fetchall()
        return [self._row_to_fuel(r) for r in rows]

    @staticmethod
    def _row_to_fuel(row: sqlite3.Row) -> FuelEntry:
        data = {k: row[k] for k in row.keys() if k in _FUEL_COLUMNS}
        return FuelEntry(**data)

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
        # GPX = dichte Spur: Track-Punkte einschließen.
        entries = [
            e
            for e in self.all(newest_first=False, trip_id=trip_id, include_track=True)
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
