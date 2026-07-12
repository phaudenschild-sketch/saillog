"""Import alter TripCon-Logbücher (.tcdb) in saillog.

Eine TripCon-Sicherung ist eine SQLite-Datenbank. Dieses Modul liest die
Törns, Logbuch-Einträge, Messwerte, Kommentare, GPS-Tracks und Bilder aus
und macht sie wieder zugänglich:

- Export als CSV (alle Einträge mit Messwerten)
- GPX-Track pro Törn (aus B111_TrackInfo)
- Extraktion aller Bilder (Kartenplotter-Screenshots, Wetter, Schiff, Crew)
- optionaler Import in die saillog-Logbuch-Datenbank (zeigt sich in der App)

Wichtige Schema-Erkenntnisse (TripCon DB-Version 366):
- Koordinaten sind in DEZIMAL-BOGENMINUTEN gespeichert -> Grad = Wert / 60
- Zeitstempel "YYYY-MM-DD HH:MM:SS.fffZ" -> ISO "YYYY-MM-DDTHH:MM:SSZ"
- Messwerte hängen über LogID an B100_Log; Position/Wind haben zwei Spalten
"""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from saillog.legacy import image_ext
from saillog.storage import LogbookStore, LogEntry, Trip

# Messwert-Tabellen mit genau einer Wertspalte: saillog-Feld -> (Tabelle, Spalte)
_SINGLE_VALUE_TABLES = {
    "sog_kn": ("VSpeedOverGround", "Value"),
    "cog_deg": ("VCourseOverGround", "Value"),
    "stw_kn": ("VSpeedThroughWater", "Value"),
    "depth_m": ("VWaterDepth", "Value"),
    "water_temp_c": ("VWaterTemperature", "Value"),
}


# --- Hilfsfunktionen --------------------------------------------------------

def coord_to_degrees(value) -> Optional[float]:
    """Wandelt TripCon-Koordinate (Dezimal-Bogenminuten) in Grad um."""
    f = to_float(value)
    return None if f is None else f / 60.0


def to_float(value) -> Optional[float]:
    """Robustes float-Parsen (Komma oder Punkt, None/leer -> None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def to_iso(dz: Optional[str]) -> str:
    """TripCon-Zeitstempel -> ISO-8601 mit 'Z'. Leer -> ''."""
    if not dz:
        return ""
    s = str(dz).strip().rstrip("Z").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".", 1)[0]
    s = s.replace(" ", "T")
    return s + "Z"


def _safe_name(text: str) -> str:
    text = (text or "").strip() or "unbenannt"
    return re.sub(r"[^0-9A-Za-zÄÖÜäöüß _-]", "_", text)[:60]


def connect(path: str) -> sqlite3.Connection:
    """Öffnet die TripCon-DB schreibgeschützt."""
    conn = sqlite3.connect(f"file:{Path(path).expanduser()}?mode=ro", uri=True)
    return conn


def _table_exists(conn, name: str) -> bool:
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            is not None
        )
    except sqlite3.Error:      # z.B. beschädigte Datei
        return False


def _columns(conn, table: str) -> List[str]:
    """Spaltennamen einer Tabelle (leer, falls Tabelle fehlt)."""
    if not _table_exists(conn, table):
        return []
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


# --- Törns & Einträge -------------------------------------------------------

def load_trips(conn) -> Dict[int, Dict[str, str]]:
    """{Trip-ID: {from, to, from_dz, to_dz}} aus B105_Trips."""
    trips: Dict[int, Dict[str, str]] = {}
    for row in conn.execute(
        "SELECT ID, FromLocation, FromDZ, ToLocation, ToDZ FROM B105_Trips"
    ):
        trips[row[0]] = {
            "from": row[1] or "",
            "from_dz": row[2] or "",
            "to": row[3] or "",
            "to_dz": row[4] or "",
        }
    return trips


def _single_map(conn, table: str, column: str) -> Dict[int, float]:
    result: Dict[int, float] = {}
    if not _table_exists(conn, table):
        return result
    for log_id, value in conn.execute(f"SELECT LogID, {column} FROM {table}"):
        f = to_float(value)
        if log_id is not None and f is not None:
            result[log_id] = f
    return result


def _pair_map(conn, table: str, col_a: str, col_b: str) -> Dict[int, Tuple]:
    result: Dict[int, Tuple] = {}
    if not _table_exists(conn, table):
        return result
    for log_id, a, b in conn.execute(f"SELECT LogID, {col_a}, {col_b} FROM {table}"):
        result[log_id] = (to_float(a), to_float(b))
    return result


def _comments(conn) -> Dict[int, str]:
    result: Dict[int, str] = {}
    if not _table_exists(conn, "B103_Comment"):
        return result
    for log_id, comment in conn.execute("SELECT LogID, Comment FROM B103_Comment"):
        if log_id is not None and comment:
            text = comment.decode("utf-8", "replace") if isinstance(comment, bytes) else str(comment)
            if text.strip():
                result[log_id] = text.strip()
    return result


def _primary_language(conn) -> Optional[int]:
    """LangID mit den meisten Übersetzungen (i.d.R. die aktive Sprache)."""
    if not _table_exists(conn, "S000_Translation"):
        return None
    rows = conn.execute(
        "SELECT LangID, COUNT(*) FROM S000_Translation GROUP BY LangID "
        "ORDER BY COUNT(*) DESC"
    ).fetchall()
    return rows[0][0] if rows else None


def _label_texts(conn) -> Dict[int, str]:
    """{LabelID: Text} in der Hauptsprache."""
    if not _table_exists(conn, "S000_Translation"):
        return {}
    lang = _primary_language(conn)
    result: Dict[int, str] = {}
    query = "SELECT LabelID, Label FROM S000_Translation"
    params: tuple = ()
    if lang is not None:
        query += " WHERE LangID = ?"
        params = (lang,)
    for label_id, label in conn.execute(query, params):
        if isinstance(label, bytes):
            label = label.decode("utf-8", "replace")
        result[label_id] = label
    return result


def _paramvalue_labelids(conn) -> Dict[int, int]:
    """{ParamValue.ID: LabelID} zum Auflösen von codierten Feldern (z.B. LogEvent)."""
    if not _table_exists(conn, "S005_ParamValue"):
        return {}
    return {
        row[0]: row[1]
        for row in conn.execute("SELECT ID, LabelID FROM S005_ParamValue")
        if row[1] is not None
    }


def _resolve_code(code, pv_labels: Dict[int, int], texts: Dict[int, str]) -> str:
    """Löst eine ParamValue-ID (z.B. LogEvent) in ihren Text auf."""
    if code is None:
        return ""
    label_id = pv_labels.get(code)
    if label_id is None:
        return ""
    return texts.get(label_id, "")


def _iter_entries(conn, trip_id_map: Optional[Dict[int, int]] = None):
    """Erzeugt (TripCon-LogID, LogEntry)-Paare aus B100_Log + Messwerten.

    trip_id_map: {TripCon-Trip-ID: saillog-Trip-ID} zum Verknüpfen der
    Einträge mit den importierten Törns.
    """
    trips = load_trips(conn)
    positions = _pair_map(conn, "VPosition", "Latitude", "Longitude")
    apparent = _pair_map(conn, "VApparentWind", "Direction", "Speed")
    true_wind = _pair_map(conn, "VTrueWind", "Direction", "Speed")
    comments = _comments(conn)
    singles = {
        field: _single_map(conn, table, col)
        for field, (table, col) in _SINGLE_VALUE_TABLES.items()
    }
    air_temp = _single_map(conn, "VAirTemperature", "Value")
    air_press = _single_map(conn, "VAirPressure", "Value")

    # Codierte Felder auflösen: LogEvent (Anlass), Bewölkung, Niederschlag, Sicht
    pv_labels = _paramvalue_labelids(conn)
    texts = _label_texts(conn)

    for log_id, trip_id, trip_dz, create_dz, logevent_code, clouds, precip, sight in conn.execute(
        "SELECT ID, Trip, TripDZ, CreateDZ, LogEvent, Clouds, Precipitation, Sight "
        "FROM B100_Log ORDER BY TripDZ, ID"
    ):
        measurements: Dict[str, float] = {}

        pos = positions.get(log_id)
        if pos:
            lat = coord_to_degrees(pos[0])
            lon = coord_to_degrees(pos[1])
            if lat is not None:
                measurements["lat"] = lat
            if lon is not None:
                measurements["lon"] = lon

        for field, mapping in singles.items():
            if log_id in mapping:
                measurements[field] = mapping[log_id]

        if log_id in apparent:
            awd, aws = apparent[log_id]
            if awd is not None:
                measurements["awa_deg"] = awd
            if aws is not None:
                measurements["aws_kn"] = aws
        if log_id in true_wind:
            twd, tws = true_wind[log_id]
            if twd is not None:
                measurements["twd_deg"] = twd
            if tws is not None:
                measurements["tws_kn"] = tws

        # Zusatzinfos, die saillog nicht als eigenes Feld hat -> in die Notiz
        extras = []
        if log_id in air_temp:
            extras.append(f"Luft {air_temp[log_id]:.0f}°C")
        if log_id in air_press:
            extras.append(f"{air_press[log_id]:.0f} hPa")
        note = comments.get(log_id, "")
        if extras:
            note = (note + "  [" + ", ".join(extras) + "]").strip()

        trip = trips.get(trip_id, {})
        location = ""
        if trip.get("from") or trip.get("to"):
            location = f"{trip.get('from', '')} → {trip.get('to', '')}".strip(" →")

        timestamp = to_iso(trip_dz) or to_iso(create_dz)
        # Einträge ohne Zeit und ohne Messwerte überspringen
        if not timestamp and not measurements and not note:
            continue

        entry_trip = trip_id_map.get(trip_id) if trip_id_map else None
        yield log_id, LogEntry.from_snapshot(
            timestamp=timestamp,
            entry_type="tripcon",
            measurements=measurements,
            note=note,
            location=location,
            trip_id=entry_trip,
            logevent=_resolve_code(logevent_code, pv_labels, texts),
            cloud_cover=_resolve_code(clouds, pv_labels, texts),
            precipitation=_resolve_code(precip, pv_labels, texts),
            visibility=_resolve_code(sight, pv_labels, texts),
        )


def build_entries(conn, trip_id_map: Optional[Dict[int, int]] = None) -> List[LogEntry]:
    """Baut die saillog-Logbuch-Einträge (ohne die TripCon-LogID)."""
    return [entry for _log_id, entry in _iter_entries(conn, trip_id_map)]


def _trip_engine_hours(conn, old_trip_id: int) -> Tuple[Optional[float], Optional[float]]:
    if not _table_exists(conn, "B112_HoursOfMotoring"):
        return None, None
    row = conn.execute(
        "SELECT MIN(HoursOfMotoring), MAX(HoursOfMotoring) "
        "FROM B112_HoursOfMotoring WHERE TripID = ?",
        (old_trip_id,),
    ).fetchone()
    return (to_float(row[0]), to_float(row[1])) if row else (None, None)


def _trip_log_range(conn, old_trip_id: int) -> Tuple[Optional[float], Optional[float]]:
    if not (_table_exists(conn, "VTriplog") and _table_exists(conn, "B100_Log")):
        return None, None
    row = conn.execute(
        "SELECT MIN(v.Value), MAX(v.Value) FROM VTriplog v "
        "JOIN B100_Log l ON v.LogID = l.ID WHERE l.Trip = ?",
        (old_trip_id,),
    ).fetchone()
    return (to_float(row[0]), to_float(row[1])) if row else (None, None)


def import_trips(conn, store: LogbookStore) -> Dict[int, int]:
    """Legt für jeden TripCon-Törn einen saillog-Törn an (idempotent).

    Gibt {TripCon-Trip-ID: saillog-Trip-ID} zurück.
    """
    existing = {(t.name, t.start_dz): t.id for t in store.all_trips()}
    mapping: Dict[int, int] = {}
    for old_id, info in load_trips(conn).items():
        route = f"{info['from']} → {info['to']}".strip(" →")
        name = f"TripCon #{old_id}: {route}" if route else f"TripCon #{old_id}"
        start_dz = to_iso(info["from_dz"])
        key = (name, start_dz)
        if key in existing:
            mapping[old_id] = existing[key]
            continue
        eh_start, eh_end = _trip_engine_hours(conn, old_id)
        log_start, log_end = _trip_log_range(conn, old_id)
        trip = Trip(
            name=name,
            status="closed",
            start_location=info["from"],
            start_dz=start_dz,
            end_location=info["to"],
            end_dz=to_iso(info["to_dz"]),
            start_engine_hours=eh_start,
            end_engine_hours=eh_end,
            start_log_nm=log_start,
            end_log_nm=log_end,
        )
        store.add_trip(trip)
        mapping[old_id] = trip.id
    return mapping


# --- Bilder an importierte Einträge hängen ----------------------------------

# Mögliche Spaltennamen, mit denen B104_BinDat direkt auf B100_Log.ID zeigt.
_BINDAT_LOGID_COLS = ("LogID", "Log", "B100_LogID", "LogRef", "B100Log")
# Mögliche Spaltennamen in B100_Log, die auf B104_BinDat.ID zeigen.
_LOG_BINDAT_COLS = (
    "BinDat", "BinDatID", "B104_BinDatID", "B104_ID",
    "Picture", "Image", "ImageID", "Plotter", "PlotterID",
)


def _bindat_images(conn) -> Dict[int, bytes]:
    """{B104_BinDat.ID: rohe Bild-Bytes} für alle echten Bilder."""
    result: Dict[int, bytes] = {}
    if not _table_exists(conn, "B104_BinDat"):
        return result
    try:
        cursor = conn.execute("SELECT ID, Value FROM B104_BinDat")
    except sqlite3.Error:
        return result
    for row_id, blob in cursor:
        if row_id is None or not isinstance(blob, (bytes, bytearray)):
            continue
        data = bytes(blob)
        if image_ext(data[:16]):
            result[row_id] = data
    return result


def _entry_image_links(conn) -> Tuple[Dict[int, int], str]:
    """Ermittelt {B100_Log.ID: B104_BinDat.ID} und die verwendete Methode.

    Das Schema der Bild-Verknüpfung ist je TripCon-Version unterschiedlich, daher
    probieren wir mehrere Wege durch und melden, welcher gegriffen hat:

    - ``bindat_logid``: B104_BinDat hat eine Spalte, die auf B100_Log.ID zeigt.
    - ``log_bindat``:   B100_Log hat eine Spalte, die auf B104_BinDat.ID zeigt.
    - ``timestamp``:    Notlösung über gleiche Zeitstempel (CreateDZ).
    - ``none``:         keine Verknüpfung gefunden.
    """
    bindat_cols = _columns(conn, "B104_BinDat")
    log_cols = _columns(conn, "B100_Log")

    # Methode A: Fremdschlüssel in B104_BinDat -> B100_Log.ID
    for col in _BINDAT_LOGID_COLS:
        if col in bindat_cols:
            links: Dict[int, int] = {}
            try:
                cursor = conn.execute(f"SELECT ID, {col} FROM B104_BinDat")
            except sqlite3.Error:
                continue
            for bindat_id, log_id in cursor:
                if bindat_id is not None and log_id is not None:
                    links.setdefault(int(log_id), int(bindat_id))
            if links:
                return links, "bindat_logid"

    # Methode B: Fremdschlüssel in B100_Log -> B104_BinDat.ID
    for col in _LOG_BINDAT_COLS:
        if col in log_cols:
            links = {}
            try:
                cursor = conn.execute(f"SELECT ID, {col} FROM B100_Log")
            except sqlite3.Error:
                continue
            for log_id, bindat_id in cursor:
                if log_id is not None and bindat_id is not None:
                    links[int(log_id)] = int(bindat_id)
            if links:
                return links, "log_bindat"

    # Methode C: Notlösung über gleiche Zeitstempel
    if "CreateDZ" in bindat_cols and "CreateDZ" in log_cols:
        by_time: Dict[str, int] = {}
        try:
            for bindat_id, create_dz in conn.execute(
                "SELECT ID, CreateDZ FROM B104_BinDat"
            ):
                iso = to_iso(create_dz)
                if iso and bindat_id is not None:
                    by_time.setdefault(iso, int(bindat_id))
        except sqlite3.Error:
            by_time = {}
        if by_time:
            links = {}
            for log_id, create_dz in conn.execute("SELECT ID, CreateDZ FROM B100_Log"):
                iso = to_iso(create_dz)
                if log_id is not None and iso in by_time:
                    links[int(log_id)] = by_time[iso]
            if links:
                return links, "timestamp"

    return {}, "none"


def _bindat_per_log_trip(conn, images: Dict[int, bytes]):
    """Ordnet Bilder Einträgen/Törns zu — MEHRERE je Eintrag möglich.

    Gibt (per_log, per_trip, method):
    - per_log:  {B100_Log.ID: [B104.ID, …]}  (Bilder mit LogID)
    - per_trip: {TripCon-Trip-ID: [B104.ID, …]}  (Bilder nur mit TripID)
    Fällt auf die alten Einzel-Verknüpfungen zurück, wenn keine LogID-Spalte da.
    """
    bindat_cols = _columns(conn, "B104_BinDat")
    logid_col = _pick_col(bindat_cols, _BINDAT_LOGID_COLS)
    trip_col = _pick_col(bindat_cols, ("TripID", "Trip", "TripId", "B105_TripID"))
    per_log: Dict[int, List[int]] = {}
    per_trip: Dict[int, List[int]] = {}

    if logid_col:
        wanted = ["ID", logid_col] + ([trip_col] if trip_col else [])
        for row in _row_dict(conn, "B104_BinDat", wanted):
            bindat_id = row.get("ID")
            if bindat_id is None or int(bindat_id) not in images:
                continue
            log_id = row.get(logid_col)
            trip_id = row.get(trip_col) if trip_col else None
            if log_id is not None:
                per_log.setdefault(int(log_id), []).append(int(bindat_id))
            elif trip_id is not None:
                per_trip.setdefault(int(trip_id), []).append(int(bindat_id))
        if per_log or per_trip:
            return per_log, per_trip, "bindat_logid"

    # Notlösungen (log_bindat / timestamp): nur je ein Bild pro Eintrag
    links, method = _entry_image_links(conn)
    for log_id, bindat_id in links.items():
        if bindat_id in images:
            per_log.setdefault(int(log_id), []).append(int(bindat_id))
    return per_log, per_trip, method


def _trip_first_entry(conn, logid_map: Dict[int, int]) -> Dict[int, int]:
    """{TripCon-Trip-ID: erster saillog-Eintrag des Törns} (nach Zeit)."""
    result: Dict[int, int] = {}
    for log_id, trip in conn.execute(
        "SELECT ID, Trip FROM B100_Log ORDER BY TripDZ, ID"
    ):
        if trip is None:
            continue
        entry_id = logid_map.get(int(log_id))
        if entry_id is not None:
            result.setdefault(int(trip), entry_id)   # erster gewinnt
    return result


def import_entry_images(conn, store: LogbookStore, logid_map: Dict[int, int],
                        max_px: int = 1600) -> Dict[str, object]:
    """Hängt TripCon-Plotterbilder (B104_BinDat) an die importierten Einträge.

    Mehrere Bilder je Eintrag werden angehängt. Bilder ohne LogID (nur am Törn)
    werden an den ersten Eintrag des jeweiligen Törns gehängt, damit nichts
    verloren geht. Gibt {"images", "trip_images", "method"} zurück.
    """
    from saillog import photos

    def attach(entry_id: int, raw: bytes) -> bool:
        jpeg = photos.resize_bytes_to_jpeg(raw, max_px=max_px)
        if jpeg:
            store.add_entry_image(entry_id, jpeg, "image/jpeg")
        else:
            ext = image_ext(raw[:16])
            mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
            store.add_entry_image(entry_id, raw, mime)
        return True

    images = _bindat_images(conn)
    if not images:
        return {"images": 0, "trip_images": 0, "method": "none"}

    per_log, per_trip, method = _bindat_per_log_trip(conn, images)

    count = 0
    for log_id, bindat_ids in per_log.items():
        entry_id = logid_map.get(log_id)
        if entry_id is None:
            continue
        for bindat_id in bindat_ids:
            if attach(entry_id, images[bindat_id]):
                count += 1

    trip_count = 0
    if per_trip:
        first_entry = _trip_first_entry(conn, logid_map)
        for trip_id, bindat_ids in per_trip.items():
            entry_id = first_entry.get(trip_id)
            if entry_id is None:
                continue
            for bindat_id in bindat_ids:
                if attach(entry_id, images[bindat_id]):
                    trip_count += 1

    return {"images": count, "trip_images": trip_count, "method": method}


# --- Schiffe & Personen als Stammdaten übernehmen ---------------------------

def _norm(text) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return " ".join(str(text).split()).strip().lower()


def _pick_col(cols: List[str], candidates) -> Optional[str]:
    lower = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


# Feld-Zuordnung TripCon-Spalte -> saillog-Attribut (adaptiv über Kandidaten).
# Eintrag: (saillog-Attribut, (Spalten-Kandidaten…), "text"|"float")
_SHIP_FIELD_MAP = [
    ("name", ("ShipName", "Name"), "text"),
    ("ship_type", ("ShipType", "Type", "BoatType", "Typ"), "text"),
    ("keel_type", ("KeelType", "Keeltype", "Keel", "Kiel"), "text"),
    ("ship_number", ("ShipNumber", "Number", "RegistrationNo", "Registration",
                     "Nummer"), "text"),
    ("length_m", ("LoA", "Length", "LengthOverAll", "LOA", "Loa",
                  "Laenge"), "float"),
    ("beam_m", ("WoA", "Beam", "Width", "Breadth", "Breite"), "float"),
    ("max_draft_m", ("Draft_Max", "Draft", "Draught", "MaxDraft",
                     "Tiefgang"), "float"),
    ("displacement_t", ("Displace", "Displacement", "Weight",
                        "Verdraengung"), "float"),
    ("clearance_height_m", ("PassHeight", "ClearanceHeight", "AirDraft",
                            "MastHeight", "Durchfahrtshoehe"), "float"),
    ("flag", ("FlagOf", "Flag", "Flagge"), "text"),
    ("home_port", ("PortOfRegistry", "HomePort", "Port", "HomeHarbour",
                   "HomeHarbor", "Heimathafen"), "text"),
    ("call_sign", ("CallSign", "Callsign", "Call", "Rufzeichen"), "text"),
    ("mmsi", ("MMSI", "Mmsi"), "text"),
    ("echo_depth_m", ("TransInstDepth", "EchoDepth", "DepthOffset",
                      "SounderOffset", "Echolot"), "float"),
    ("log_correction", ("CorrFactLog", "LogCorrection", "LogFactor",
                        "CorrectionFactor", "Korrekturfaktor"), "float"),
    ("water_tank_l", ("WaterTank", "WaterCapacity", "FreshWater",
                      "Wassertank"), "float"),
    ("fuel_tank_l", ("FuelTank", "FuelCapacity", "Fuel", "Diesel",
                     "Treibstoff"), "float"),
    ("sails", ("TypeOfDrive", "Sails", "Sail", "Segel", "Antrieb"), "text"),
    ("equipment", ("Equipment", "Gear", "Ausruestung", "Ausstattung"), "text"),
    ("power_source", ("PowerSource", "Power", "Electric", "Strom"), "text"),
]

_PERSON_FIELD_MAP = [
    ("last_name", ("LastName", "Name", "Surname", "Nachname"), "text"),
    ("first_name", ("FirstName", "Vorname", "GivenName", "PreName"), "text"),
    ("birth_date", ("Birthday", "BirthDate", "DateOfBirth", "BirthDZ",
                    "Geburtsdatum"), "date"),
    ("birth_place", ("PlaceOfBirth", "BirthPlace", "BirthLocation",
                     "Geburtsort"), "text"),
    ("nationality", ("Nationality", "Nation", "Citizenship",
                     "Nationalitaet"), "text"),
    ("passport_no", ("Passport_Nr", "PassportNo", "Passport", "PassNo",
                     "PassportNumber", "IDNumber", "Ausweis",
                     "Reisepass"), "text"),
    ("email", ("Email", "EMail", "Mail"), "text"),
    ("street", ("Address", "Street", "Addr", "Address1", "Strasse",
                "Adresse"), "text"),
    ("zip_code", ("ZipCode", "Zip", "PostalCode", "PLZ"), "text"),
    ("city", ("City", "Town", "Ort", "Stadt"), "text"),
]


def _resolve_field_map(cols: List[str], field_map) -> Dict[str, Tuple[str, str]]:
    """{saillog-Attribut: (TripCon-Spalte, kind)} für vorhandene Spalten."""
    resolved: Dict[str, Tuple[str, str]] = {}
    for attr, candidates, kind in field_map:
        col = _pick_col(cols, candidates)
        if col is not None:
            resolved[attr] = (col, kind)
    return resolved


def _is_empty(attr: str, value) -> bool:
    """True, wenn das Feld noch „leer" ist (für das Nachfüllen bestehender Sätze)."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if attr == "log_correction":
        return value == 1.0        # Standardwert = noch nicht gesetzt
    return False


def _clean_date(value) -> str:
    """TripCon-Geburtsdatum -> „YYYY-MM-DD" (ohne Uhrzeit/Z)."""
    iso = to_iso(value)
    return iso.split("T", 1)[0].rstrip("Z") if iso else ""


def _apply_fields(obj, rowd: Dict[str, object], fmap: Dict[str, Tuple[str, str]],
                  skip=("name", "last_name", "first_name"),
                  only_empty: bool = False) -> None:
    """Überträgt die aufgelösten Felder aus einer Zeile auf das Dataobjekt.

    only_empty=True füllt nur Felder, die im Objekt noch leer sind (zum
    Nachrüsten bereits importierter Stammdaten, ohne Eingaben zu überschreiben).
    """
    for attr, (col, kind) in fmap.items():
        if attr in skip:
            continue
        if only_empty and not _is_empty(attr, getattr(obj, attr, None)):
            continue
        value = rowd.get(col)
        if kind == "float":
            fv = to_float(value)
            if fv is not None:
                setattr(obj, attr, fv)
        elif kind == "date":
            text = _clean_date(value)
            if text:
                setattr(obj, attr, text)
        else:
            if value is None:
                continue
            text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
            text = text.strip()
            if text:
                setattr(obj, attr, text)


def _row_dict(conn, table: str, cols: List[str]):
    """Liefert je Zeile ein {Spalte: Wert}-Dict (nur die gewünschten Spalten)."""
    quoted = ", ".join(f'"{c}"' for c in cols)
    try:
        cursor = conn.execute(f"SELECT {quoted} FROM {table}")
    except sqlite3.Error:
        return
    for row in cursor:
        yield dict(zip(cols, row))


def _attach_photo(raw: bytes, max_px: int):
    """(bytes, mime) für ein Stammdaten-Foto; None, wenn kein gültiges Bild."""
    from saillog import photos
    ext = image_ext(raw[:16])
    if not ext:
        return None
    jpeg = photos.resize_bytes_to_jpeg(raw, max_px=max_px)
    if jpeg:
        return jpeg, "image/jpeg"
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return raw, mime


def import_ships(conn, store: LogbookStore, max_px: int = 1600) -> Dict[str, object]:
    """Legt Schiffe aus S003_Ships an (idempotent über den Namen) + Foto.

    Gibt {"created": n, "matched": m, "photos": p, "fields": [Attribute]} zurück.
    """
    from saillog.storage import Ship

    result = {"created": 0, "matched": 0, "photos": 0, "fields": []}
    cols = _columns(conn, "S003_Ships")
    if not cols:
        return result
    fmap = _resolve_field_map(cols, _SHIP_FIELD_MAP)
    name_col = fmap.get("name", (None, None))[0]
    if not name_col:
        return result
    pic_col = _pick_col(cols, ("Picture", "Image", "Photo", "Bild"))
    result["fields"] = sorted(fmap.keys())

    wanted = [c for c, _ in fmap.values()]
    if pic_col and pic_col not in wanted:
        wanted.append(pic_col)

    existing = {_norm(s.name): s for s in store.all_ships() if s.name.strip()}
    for rowd in _row_dict(conn, "S003_Ships", wanted):
        name = str(rowd.get(name_col) or "").strip()
        if not name:
            continue
        ship = existing.get(_norm(name))
        if ship is None:
            ship = Ship(name=name)
            _apply_fields(ship, rowd, fmap)
            store.add_ship(ship)
            existing[_norm(name)] = ship
            result["created"] += 1
        else:
            # bestehendes Schiff: nur leere Felder nachfüllen
            _apply_fields(ship, rowd, fmap, only_empty=True)
            store.update_ship(ship)
            result["matched"] += 1
        if pic_col and isinstance(rowd.get(pic_col), (bytes, bytearray)):
            attach = _attach_photo(bytes(rowd[pic_col]), max_px)
            if attach:
                store.set_ship_photo(ship.id, attach[0], attach[1])
                result["photos"] += 1
    return result


def import_persons(conn, store: LogbookStore, max_px: int = 1600) -> Dict[str, object]:
    """Legt Personen aus S006_Persons an (idempotent über Name) + Foto.

    Gibt {"created": n, "matched": m, "photos": p, "fields": [Attribute]} zurück.
    """
    from saillog.storage import Person

    result = {"created": 0, "matched": 0, "photos": 0, "fields": []}
    cols = _columns(conn, "S006_Persons")
    if not cols:
        return result
    fmap = _resolve_field_map(cols, _PERSON_FIELD_MAP)
    last_col = fmap.get("last_name", (None, None))[0]
    first_col = fmap.get("first_name", (None, None))[0]
    if not (last_col or first_col):
        return result
    pic_col = _pick_col(cols, ("Picture", "Image", "Photo", "Bild"))
    result["fields"] = sorted(fmap.keys())

    wanted = [c for c, _ in fmap.values()]
    if pic_col and pic_col not in wanted:
        wanted.append(pic_col)

    existing = {}
    for p in store.all_persons():
        existing.setdefault((_norm(p.last_name), _norm(p.first_name)), p)

    for rowd in _row_dict(conn, "S006_Persons", wanted):
        last = str(rowd.get(last_col) or "").strip() if last_col else ""
        first = str(rowd.get(first_col) or "").strip() if first_col else ""
        if not (last or first):
            continue
        key = (_norm(last), _norm(first))
        person = existing.get(key)
        if person is None:
            person = Person(last_name=last, first_name=first)
            _apply_fields(person, rowd, fmap)
            store.add_person(person)
            existing[key] = person
            result["created"] += 1
        else:
            # bestehende Person: nur leere Felder nachfüllen
            _apply_fields(person, rowd, fmap, only_empty=True)
            store.update_person(person)
            result["matched"] += 1
        if pic_col and isinstance(rowd.get(pic_col), (bytes, bytearray)):
            attach = _attach_photo(bytes(rowd[pic_col]), max_px)
            if attach:
                store.set_person_photo(person.id, attach[0], attach[1])
                result["photos"] += 1
    return result


def import_into_saillog(conn, db_path: str, replace: bool = True,
                         max_px: int = 1600) -> Dict[str, object]:
    """Importiert Törns, Einträge, Bilder und Stammdaten in die saillog-DB.

    Schiffe und Personen aus TripCon werden als Stammdaten angelegt (idempotent
    über den Namen) und ihre Fotos angehängt. Gibt ein Ergebnis-Dict zurück.
    """
    store = LogbookStore(db_path)
    if replace:
        store.delete_by_type("tripcon")
    trip_map = import_trips(conn, store)

    pairs = list(_iter_entries(conn, trip_id_map=trip_map))
    store.add_many_returning_ids([entry for _log_id, entry in pairs])
    logid_map = {
        old_log_id: entry.id for old_log_id, entry in pairs if old_log_id is not None
    }

    image_info = import_entry_images(conn, store, logid_map, max_px=max_px)
    ship_info = import_ships(conn, store, max_px=max_px)
    person_info = import_persons(conn, store, max_px=max_px)

    return {
        "entries": len(pairs),
        "images": image_info["images"],
        "trip_images": image_info.get("trip_images", 0),
        "image_method": image_info["method"],
        "ships_created": ship_info["created"],
        "ships_matched": ship_info["matched"],
        "ship_photos": ship_info["photos"],
        "ship_fields": ship_info["fields"],
        "persons_created": person_info["created"],
        "persons_matched": person_info["matched"],
        "person_photos": person_info["photos"],
        "person_fields": person_info["fields"],
    }


# --- Analyse (prüfen, ob ein .tcdb in Ordnung/vollständig ist) -------------

def _count(conn, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0


def _valid_image_count(conn, table: str, blob_col: str) -> int:
    if not _table_exists(conn, table):
        return 0
    n = 0
    try:
        cursor = conn.execute(f"SELECT {blob_col} FROM {table}")
    except sqlite3.Error:
        return 0
    for (blob,) in cursor:
        if isinstance(blob, (bytes, bytearray)) and image_ext(bytes(blob[:16])):
            n += 1
    return n


def analyze_tcdb(conn) -> Dict[str, object]:
    """Liest Kennzahlen einer TripCon-Sicherung, um sie zu beurteilen.

    Enthält u.a. Integritätsprüfung, Törn-/Eintrags-/Bildzahlen und den
    Zeitraum. Verändert nichts (nur Lesezugriff)."""
    info: Dict[str, object] = {}
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        info["integrity"] = "; ".join(str(r[0]) for r in rows) if rows else "?"
    except sqlite3.Error as exc:
        info["integrity"] = f"Fehler: {exc}"

    # Bei beschädigten Dateien darf keine Einzelmessung den Rest umwerfen —
    # jede Kennzahl einzeln absichern, damit wir zeigen, was noch lesbar ist.
    def safe(fn, default=0):
        try:
            return fn()
        except sqlite3.Error as exc:
            return f"Fehler: {exc}"

    def trips_info():
        trips = load_trips(conn)
        dzs = [to_iso(t.get(k)) for t in trips.values() for k in ("from_dz", "to_dz")]
        dzs = [d for d in dzs if d]
        return len(trips), (min(dzs) if dzs else ""), (max(dzs) if dzs else "")

    try:
        info["trips"], info["date_from"], info["date_to"] = trips_info()
    except sqlite3.Error as exc:
        info["trips"] = f"Fehler: {exc}"
        info["date_from"] = info["date_to"] = ""

    info["log_entries"] = _count(conn, "B100_Log")
    info["track_points"] = _count(conn, "B111_TrackInfo")

    def images_info():
        images = _bindat_images(conn)
        if not images:
            return 0, 0, 0, "none"
        per_log, per_trip, method = _bindat_per_log_trip(conn, images)
        return (len(images), sum(len(v) for v in per_log.values()),
                sum(len(v) for v in per_trip.values()), method)

    try:
        (info["plotter_images"], info["plotter_with_log"],
         info["plotter_trip_only"], info["image_method"]) = images_info()
    except sqlite3.Error as exc:
        info["plotter_images"] = f"Fehler: {exc}"
        info["plotter_with_log"] = info["plotter_trip_only"] = 0
        info["image_method"] = "?"

    info["weather_images"] = safe(lambda: _valid_image_count(conn, "B109_Weather", "Value"))
    info["ships"] = _count(conn, "S003_Ships")
    info["ship_images"] = safe(lambda: _valid_image_count(conn, "S003_Ships", "Picture"))
    info["persons"] = _count(conn, "S006_Persons")
    info["person_images"] = safe(lambda: _valid_image_count(conn, "S006_Persons", "Picture"))
    return info


# --- CSV --------------------------------------------------------------------

def export_csv(entries: List[LogEntry], path: str) -> int:
    from saillog.storage import _COLUMN_NAMES

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(_COLUMN_NAMES)
        for entry in entries:
            writer.writerow([getattr(entry, c) for c in _COLUMN_NAMES])
    return len(entries)


# --- GPX-Tracks pro Törn ----------------------------------------------------

def export_gpx_tracks(conn, trips: Dict[int, Dict[str, str]], out_dir: Path) -> int:
    """Schreibt je Törn einen GPX-Track aus B111_TrackInfo. Gibt Dateizahl zurück."""
    if not _table_exists(conn, "B111_TrackInfo"):
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    from xml.sax.saxutils import escape

    files = 0
    current_trip = None
    handle = None

    def close_track():
        nonlocal handle
        if handle is not None:
            handle.write("    </trkseg>\n  </trk>\n</gpx>\n")
            handle.close()
            handle = None

    for trip_id, lat_raw, lon_raw, create_dz in conn.execute(
        "SELECT Trip, Latitude, Longitude, CreateDZ FROM B111_TrackInfo "
        "ORDER BY Trip, ID"
    ):
        lat = coord_to_degrees(lat_raw)
        lon = coord_to_degrees(lon_raw)
        if lat is None or lon is None:
            continue
        if trip_id != current_trip:
            close_track()
            current_trip = trip_id
            trip = trips.get(trip_id, {})
            label = _safe_name(f"Trip{trip_id}_{trip.get('from', '')}-{trip.get('to', '')}")
            path = out_dir / f"{label}.gpx"
            handle = open(path, "w", encoding="utf-8")
            name = escape(f"{trip.get('from', '')} → {trip.get('to', '')}".strip(" →") or f"Törn {trip_id}")
            handle.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="SailLog" '
                'xmlns="http://www.topografix.com/GPX/1/1">\n'
                f"  <trk>\n    <name>{name}</name>\n    <trkseg>\n"
            )
            files += 1
        handle.write(f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}">')
        iso = to_iso(create_dz)
        if iso:
            handle.write(f"<time>{iso}</time>")
        handle.write("</trkpt>\n")
    close_track()
    return files


# --- Bilder -----------------------------------------------------------------

_IMAGE_SOURCES = [
    # (Tabelle, ID-Spalte, BLOB-Spalte, Unterordner, optionale Namensspalte)
    ("B104_BinDat", "ID", "Value", "plotter", "CreateDZ"),
    ("B109_Weather", "ID", "Value", "wetter", "Filename"),
    ("S003_Ships", "ID", "Picture", "schiffe", "ShipName"),
    ("S006_Persons", "ID", "Picture", "crew", "LastName"),
]


def extract_images(conn, out_dir: Path) -> Dict[str, int]:
    """Extrahiert alle Bilder nach Unterordnern. Gibt {ordner: anzahl} zurück."""
    counts: Dict[str, int] = {}
    for table, id_col, blob_col, subdir, name_col in _IMAGE_SOURCES:
        if not _table_exists(conn, table):
            continue
        target = out_dir / subdir
        n = 0
        query = f"SELECT {id_col}, {blob_col}, {name_col} FROM {table}"
        try:
            cursor = conn.execute(query)
        except sqlite3.Error:
            continue
        for row_id, blob, name in cursor:
            if not isinstance(blob, (bytes, bytearray)):
                continue
            ext = image_ext(bytes(blob[:16]))
            if not ext:
                continue
            target.mkdir(parents=True, exist_ok=True)
            label = _safe_name(str(name)) if name else ""
            fname = f"{row_id:06d}_{label}.{ext}" if label else f"{row_id:06d}.{ext}"
            (target / fname).write_bytes(blob)
            n += 1
        if n:
            counts[subdir] = n
    return counts
