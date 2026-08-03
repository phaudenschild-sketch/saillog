"""Import von GPX-Track-Dateien als Kartenspur.

Manche Geräte (z.B. der **Orca**) zeichnen den Track lückenlos auf und können
ihn tageweise als **GPX** exportieren. Lief SailLog zwischendurch nicht, fehlen
in der eigenen Spur Stücke. Dieses Modul liest solche GPX-Dateien und legt ihre
``<trkpt>`` als **reine Track-Punkte** (``entry_type='track'``) an — genau wie
die eigene dichte Trackaufzeichnung: nur Zeit + Position, dazu zur schöneren
Kartendarstellung **SOG/COG** (aus dem Abstand/der Zeit zum nächsten Punkt
berechnet). Sie erscheinen also **nicht** in der Logbuch-Liste, sondern nur auf
der Karte und im GPX-Export — und füllen die Lücken.

Die importierten Punkte werden mit ``logevent='GPX'`` markiert und tragen die
**Quelle** (Dateiname bzw. Track-Name) im ``note``-Feld. Ein erneuter Import
derselben Quelle **ersetzt** die vorherigen Punkte (keine Dubletten).

Reine Python-Standardbibliothek (``xml.etree``).
"""

from __future__ import annotations

import bisect
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from math import atan2, cos, degrees, radians, sin
from typing import List, Optional, Tuple, Union

from saillog import geo
from saillog.storage import LogEntry, LogbookStore
from saillog.timeutil import parse_to_utc

# Marker im logevent-Feld importierter Track-Punkte (zum Wiederfinden/Ersetzen).
GPX_LOGEVENT = "GPX"

# Standard-Zeitfenster für „nur Lücken füllen": liegt schon ein eigener
# Track-Punkt so nah (Sekunden), gilt der Zeitraum als abgedeckt und der
# GPX-Punkt wird übersprungen (verhindert die doppelte, zickzackende Spur).
DEFAULT_GAP_SECONDS = 90.0

# Standard-Mindestbewegung fürs Ausdünnen (Seemeilen). Punkte, die weniger als
# das vom letzten behaltenen Punkt entfernt sind, werden verworfen — gegen den
# Punkt-Knäuel beim Ankern/im Hafen (Schwojen + GPS-Rauschen). 0 = aus.
DEFAULT_MIN_MOVE_NM = 0.0
NM_PER_METER = 1.0 / 1852.0

# (Zeit-ISO-UTC, lat, lon)
GpxPoint = Tuple[str, float, float]


@dataclass
class GpxTrack:
    """Ein aus einer GPX-Datei gelesener Track."""

    name: str = ""
    points: List[GpxPoint] = field(default_factory=list)


def _local(tag: str) -> str:
    """Lokaler Elementname ohne XML-Namespace (``{ns}trkpt`` -> ``trkpt``)."""
    return tag.rsplit("}", 1)[-1]


def parse_gpx(data: Union[str, bytes]) -> GpxTrack:
    """Liest GPX-Inhalt (Text/Bytes) und gibt Name + Trackpunkte zurück.

    Namespace-unabhängig; unterstützt mehrere ``<trk>``/``<trkseg>``. Punkte
    ohne gültige lat/lon werden übersprungen. Zeiten werden auf das interne
    UTC-Format ``YYYY-MM-DDTHH:MM:SSZ`` normiert (leer, wenn nicht lesbar).
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8", "replace")
    root = ET.fromstring(data)

    name = ""
    for elem in root.iter():
        if _local(elem.tag) == "trk":
            for child in elem:
                if _local(child.tag) == "name" and (child.text or "").strip():
                    name = child.text.strip()
                    break
            if name:
                break
    if not name:                       # Fallback: irgendein <name> (z.B. metadata)
        for elem in root.iter():
            if _local(elem.tag) == "name" and (elem.text or "").strip():
                name = elem.text.strip()
                break

    points: List[GpxPoint] = []
    for pt in root.iter():
        if _local(pt.tag) != "trkpt":
            continue
        try:
            lat = float(pt.get("lat"))
            lon = float(pt.get("lon"))
        except (TypeError, ValueError):
            continue
        ts = ""
        for child in pt:
            if _local(child.tag) == "time" and (child.text or "").strip():
                dt = parse_to_utc(child.text.strip())
                if dt is not None:
                    ts = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                break
        points.append((ts, lat, lon))
    return GpxTrack(name=name, points=points)


def parse_gpx_file(path: str) -> GpxTrack:
    with open(path, "rb") as fh:
        return parse_gpx(fh.read())


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Anfangskurs (rechtweisend, 0..360°) von Punkt 1 nach Punkt 2."""
    p1, p2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    y = sin(dlon) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dlon)
    return (degrees(atan2(y, x)) + 360.0) % 360.0


def build_entries(
    track: GpxTrack,
    trip_id: Optional[int],
    source: str,
    motion: bool = True,
) -> List[LogEntry]:
    """Wandelt die Trackpunkte in ``entry_type='track'``-Einträge.

    Ist ``motion`` gesetzt, werden **COG** (Peilung zum nächsten Punkt) und
    **SOG** (Distanz/Zeit) berechnet — für die Richtungspfeile auf der Karte.
    Der letzte Punkt übernimmt die Bewegungswerte des vorangehenden.
    """
    pts = track.points
    entries: List[LogEntry] = []
    prev_sog: Optional[float] = None
    prev_cog: Optional[float] = None
    for i, (ts, lat, lon) in enumerate(pts):
        sog: Optional[float] = None
        cog: Optional[float] = None
        if motion and i + 1 < len(pts):
            _ts2, lat2, lon2 = pts[i + 1]
            dist = geo.haversine_nm(lat, lon, lat2, lon2)
            cog = _bearing(lat, lon, lat2, lon2) if dist > 1e-6 else prev_cog
            d1, d2 = parse_to_utc(ts), parse_to_utc(pts[i + 1][0])
            if d1 is not None and d2 is not None:
                hours = (d2 - d1).total_seconds() / 3600.0
                if hours > 0:
                    sog = dist / hours
            prev_sog, prev_cog = sog, cog
        elif motion:                    # letzter Punkt: Bewegung fortschreiben
            sog, cog = prev_sog, prev_cog
        entries.append(LogEntry(
            timestamp=ts,
            entry_type="track",
            trip_id=trip_id,
            lat=lat,
            lon=lon,
            sog_kn=sog,
            cog_deg=cog,
            logevent=GPX_LOGEVENT,
            note=source,
        ))
    return entries


def _thin_by_distance(entries, min_move_nm):
    """Dünnt Punkte aus, die sich zu wenig vom letzten behaltenen wegbewegt haben.

    So schrumpft der Punkt-Knäuel beim Ankern/im Hafen auf wenige Punkte,
    während echte Fahrt (große Abstände) voll erhalten bleibt. Gibt
    (behaltene, ausgedünnt-Anzahl) zurück.
    """
    kept, thinned = [], 0
    last = None
    for entry in entries:
        if entry.lat is None or entry.lon is None:
            kept.append(entry)
            continue
        if last is None or geo.haversine_nm(
                last[0], last[1], entry.lat, entry.lon) >= min_move_nm:
            kept.append(entry)
            last = (entry.lat, entry.lon)
        else:
            thinned += 1
    return kept, thinned


def _epoch(iso: str) -> Optional[float]:
    dt = parse_to_utc(iso)
    return dt.timestamp() if dt is not None else None


def _filter_gaps(entries, existing_iso, near_seconds):
    """Behält nur Punkte, die **nicht** nahe an vorhandenen Track-Punkten liegen.

    So werden bereits (live) abgedeckte Zeiträume nicht doppelt gezeichnet — nur
    echte Lücken werden gefüllt. Gibt (behaltene, übersprungen-Anzahl) zurück.
    """
    covered = sorted(e for e in (_epoch(t) for t in existing_iso) if e is not None)
    if not covered:
        return entries, 0
    kept, skipped = [], 0
    for entry in entries:
        te = _epoch(entry.timestamp)
        if te is None:
            kept.append(entry)
            continue
        i = bisect.bisect_left(covered, te)
        nearest = min(
            (abs(te - covered[j]) for j in (i - 1, i) if 0 <= j < len(covered)),
            default=None,
        )
        if nearest is not None and nearest <= near_seconds:
            skipped += 1
        else:
            kept.append(entry)
    return kept, skipped


def import_gpx(
    store: LogbookStore,
    data: Union[str, bytes],
    trip_id: Optional[int] = None,
    source: Optional[str] = None,
    replace: bool = True,
    motion: bool = True,
    gap_only: bool = True,
    near_seconds: float = DEFAULT_GAP_SECONDS,
    min_move_nm: float = DEFAULT_MIN_MOVE_NM,
) -> dict:
    """Importiert einen GPX-Track als Kartenspur in den ``LogbookStore``.

    ``trip_id`` ordnet die Punkte einem Törn zu (damit die Karte sie zeigt).
    ``source`` ist die Kennung fürs erneute Ersetzen (Standard: Track-Name).
    ``gap_only`` (Standard) fügt Punkte **nur in Lücken** ein — dort, wo der Törn
    noch keine eigene Trackspur hat (im Umkreis von ``near_seconds``). Das
    verhindert eine doppelte, zickzackende Linie, wo Live- und GPX-Spur denselben
    Zeitraum abdecken. ``min_move_nm`` > 0 **dünnt** Punkte aus, die sich zu wenig
    bewegt haben (gegen den Anker-/Hafen-Knäuel). Gibt eine Zusammenfassung
    zurück.
    """
    try:
        track = parse_gpx(data)
    except ET.ParseError as exc:
        raise ValueError(f"GPX konnte nicht gelesen werden: {exc}") from exc
    if not track.points:
        raise ValueError("Keine Trackpunkte in der GPX-Datei gefunden.")

    src = (source or track.name or "GPX-Import").strip() or "GPX-Import"
    entries = build_entries(track, trip_id, src, motion=motion)
    # 1) Anker-/Hafen-Punkte ausdünnen (bevor gegen die Lücken geprüft wird).
    thinned = 0
    if min_move_nm and min_move_nm > 0:
        entries, thinned = _thin_by_distance(entries, min_move_nm)
    # 2) Erst die eigenen (vorherigen) Punkte dieser Quelle entfernen, DANN die
    #    Lücken gegen die verbleibende (echte) Spur bestimmen.
    replaced = store.delete_track_import(src) if replace else 0
    skipped = 0
    if gap_only and trip_id is not None:
        existing = store.track_timestamps(trip_id)
        entries, skipped = _filter_gaps(entries, existing, near_seconds)
    imported = store.add_many(entries)

    times = sorted(t for t, _lat, _lon in track.points if t)
    dist = geo.track_distance_nm([(lat, lon) for _ts, lat, lon in track.points])
    return {
        "name": track.name,
        "source": src,
        "points": len(track.points),
        "imported": imported,
        "skipped": skipped,
        "thinned": thinned,
        "replaced": replaced,
        "first": times[0] if times else "",
        "last": times[-1] if times else "",
        "distance_nm": dist,
        "trip_id": trip_id,
    }


def import_gpx_file(
    store: LogbookStore,
    path: str,
    trip_id: Optional[int] = None,
    source: Optional[str] = None,
    replace: bool = True,
    motion: bool = True,
    gap_only: bool = True,
    near_seconds: float = DEFAULT_GAP_SECONDS,
    min_move_nm: float = DEFAULT_MIN_MOVE_NM,
) -> dict:
    """Wie :func:`import_gpx`, liest den Inhalt aus einer Datei."""
    import os

    with open(path, "rb") as fh:
        data = fh.read()
    if source is None:
        source = os.path.basename(path)
    return import_gpx(store, data, trip_id=trip_id, source=source,
                      replace=replace, motion=motion,
                      gap_only=gap_only, near_seconds=near_seconds,
                      min_move_nm=min_move_nm)
