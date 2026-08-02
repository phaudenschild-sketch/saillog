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

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from math import atan2, cos, degrees, radians, sin
from typing import List, Optional, Tuple, Union

from saillog import geo
from saillog.storage import LogEntry, LogbookStore
from saillog.timeutil import parse_to_utc

# Marker im logevent-Feld importierter Track-Punkte (zum Wiederfinden/Ersetzen).
GPX_LOGEVENT = "GPX"

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


def import_gpx(
    store: LogbookStore,
    data: Union[str, bytes],
    trip_id: Optional[int] = None,
    source: Optional[str] = None,
    replace: bool = True,
    motion: bool = True,
) -> dict:
    """Importiert einen GPX-Track als Kartenspur in den ``LogbookStore``.

    ``trip_id`` ordnet die Punkte einem Törn zu (damit die Karte sie zeigt).
    ``source`` ist die Kennung fürs erneute Ersetzen (Standard: Track-Name).
    Gibt eine Zusammenfassung (Anzahl, Zeitraum, Distanz, ersetzt) zurück.
    """
    try:
        track = parse_gpx(data)
    except ET.ParseError as exc:
        raise ValueError(f"GPX konnte nicht gelesen werden: {exc}") from exc
    if not track.points:
        raise ValueError("Keine Trackpunkte in der GPX-Datei gefunden.")

    src = (source or track.name or "GPX-Import").strip() or "GPX-Import"
    entries = build_entries(track, trip_id, src, motion=motion)
    replaced = store.delete_track_import(src) if replace else 0
    imported = store.add_many(entries)

    times = sorted(t for t, _lat, _lon in track.points if t)
    dist = geo.track_distance_nm([(lat, lon) for _ts, lat, lon in track.points])
    return {
        "name": track.name,
        "source": src,
        "points": len(track.points),
        "imported": imported,
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
) -> dict:
    """Wie :func:`import_gpx`, liest den Inhalt aus einer Datei."""
    import os

    with open(path, "rb") as fh:
        data = fh.read()
    if source is None:
        source = os.path.basename(path)
    return import_gpx(store, data, trip_id=trip_id, source=source,
                      replace=replace, motion=motion)
