"""Berichte aus den Logdaten — druckbares HTML (im Browser als PDF druckbar).

Nach dem Vorbild von TripCon:

- **Törn-Bericht** (ein Törn, detailliert): Titel, Schiffsdaten, Crew, jeder
  Logbuch-Eintrag mit vollem Raster, Zusammenfassung — wahlweise **mit Bildern**
  (entspricht TripCons „Etappenbericht") oder ohne („Törnbericht").
- **Fahrtenbuch / Übersicht** (mehrere Törns): Schiffe, alle Etappen als
  Karten (von/nach, Distanzen, Wind, Wetter, Crew), Meilen-Zusammenfassung —
  entspricht TripCons „Törnübersicht"/„Fahrtenbuch".

Reine Standardbibliothek. Distanzen (gesamt/gesegelt/Motor) werden aus der
GPS-Spur berechnet (Haversine) und über ``engine_on`` auf Segeln/Motor
aufgeteilt. Fehlende Werte erscheinen als „---" (wie bei TripCon).
"""

from __future__ import annotations

import base64
import json
import math
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

from saillog import branding, geo, timeutil
from saillog.storage import LogEntry, Ship, Trip, Voyage

_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


# --- Formatierung ----------------------------------------------------------

def _de(value: Optional[float], dec: int = 1) -> str:
    """Deutsche Zahl mit Komma; None -> '---'."""
    if value is None:
        return "---"
    return f"{value:.{dec}f}".replace(".", ",")


def compass(deg: Optional[float]) -> str:
    if deg is None:
        return ""
    return _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def latlon_dm(lat: Optional[float], lon: Optional[float]) -> str:
    """'54° 48,361' N / 009° 27,096' E' (Grad + Dezimalminuten)."""
    if lat is None or lon is None:
        return "---"

    def one(v: float, pos: str, neg: str, deg_width: int) -> str:
        hemi = pos if v >= 0 else neg
        v = abs(v)
        d = int(v)
        m = (v - d) * 60.0
        return f"{d:0{deg_width}d}° {m:06.3f}'".replace(".", ",") + f" {hemi}"

    return f"{one(lat, 'N', 'S', 2)} / {one(lon, 'E', 'W', 3)}"


def wind_str(tws: Optional[float], twd: Optional[float]) -> str:
    """Wahrer Wind: '12 kn, ENE' (aus Richtung)."""
    if tws is None:
        return "---"
    d = f", {compass(twd)}" if twd is not None else ""
    return f"{tws:.0f} kn{d}"


def _fmt_dt(iso: str, offset: float, with_date: bool = True) -> str:
    disp = timeutil.to_display(iso, offset)  # "YYYY-MM-DD HH:MM"
    if not disp or " " not in disp:
        return disp
    d, t = disp.split(" ", 1)
    y, mo, day = d.split("-")
    de_date = f"{day}.{mo}.{y}"
    return f"{de_date}     {t}" if with_date else t


def _date_only(iso: str, offset: float) -> str:
    disp = timeutil.to_display(iso, offset)
    d = disp.split(" ", 1)[0] if disp else ""
    if d.count("-") == 2:
        y, mo, day = d.split("-")
        return f"{day}.{mo}.{y}"
    return d


# --- Distanz/Statistik pro Törn --------------------------------------------

def leg_stats(entries: List[LogEntry]) -> Dict[str, float]:
    """Gesamt-/Segel-/Motor-Distanz (NM) aus der GPS-Spur + engine_on."""
    total = sailed = motor = 0.0
    prev = None
    prev_engine = None
    for e in entries:
        if e.lat is None or e.lon is None:
            continue
        if prev is not None:
            d = geo.haversine_nm(prev[0], prev[1], e.lat, e.lon)
            total += d
            # Segment dem Motor zurechnen, wenn am Anfang oder Ende Motor lief
            eng = e.engine_on if e.engine_on is not None else prev_engine
            if eng == 1 or prev_engine == 1:
                motor += d
            else:
                sailed += d
        prev = (e.lat, e.lon)
        prev_engine = e.engine_on if e.engine_on is not None else prev_engine
    return {"total": total, "sailed": sailed, "motor": motor}


def _cumulative_nm(entries: List[LogEntry]) -> Dict[int, float]:
    """{Eintrags-Index: zurückgelegte NM seit Etappenstart} (GPS-basiert)."""
    out: Dict[int, float] = {}
    run = 0.0
    prev = None
    for i, e in enumerate(entries):
        if e.lat is not None and e.lon is not None:
            if prev is not None:
                run += geo.haversine_nm(prev[0], prev[1], e.lat, e.lon)
            prev = (e.lat, e.lon)
        out[i] = run
    return out


# --- HTML-Bausteine --------------------------------------------------------

_SHIP_FIELDS = [
    ("ship_type", "Schiffstyp"), ("ship_number", "Schiffsnummer"),
    ("length_m", "Länge"), ("beam_m", "Breite"), ("keel_type", "Kielart"),
    ("max_draft_m", "Tiefgang"), ("clearance_height_m", "Durchfahrtshöhe"),
    ("flag", "Flagge"), ("home_port", "Heimathafen"),
    ("call_sign", "Rufzeichen"), ("mmsi", "MMSI"),
]


def _ship_value(ship: Ship, attr: str) -> str:
    val = getattr(ship, attr, None)
    if val in (None, ""):
        return ""
    if attr in ("length_m", "beam_m", "max_draft_m", "clearance_height_m"):
        return f"{val:.2f} m".replace(".", ",")
    return str(val)


def ship_block(ship: Optional[Ship]) -> str:
    if ship is None:
        return ""
    rows = []
    for attr, label in _SHIP_FIELDS:
        val = _ship_value(ship, attr)
        if val:
            rows.append(f'<div class="sd"><span class="k">{escape(label)}</span>'
                        f'<span class="v">{escape(val)}</span></div>')
    extras = []
    if ship.sails:
        extras.append(("Antrieb/Segel", ship.sails))
    if ship.equipment:
        extras.append(("Ausstattung", ship.equipment))
    tanks = []
    if ship.water_tank_l:
        tanks.append(f"Wassertank ({ship.water_tank_l:.0f} l)")
    if ship.fuel_tank_l:
        tanks.append(f"Treibstofftank ({ship.fuel_tank_l:.0f} l)")
    if tanks:
        extras.append(("Tankkapazitäten", "\n".join(tanks)))
    if ship.power_source:
        extras.append(("Stromversorgung", ship.power_source))
    extra_html = "".join(
        f'<div class="sx"><b>{escape(k)}</b><br>{escape(v).replace(chr(10), "<br>")}</div>'
        for k, v in extras
    )
    return (f'<section class="ship"><h2>{escape(ship.name or "Schiff")} — '
            f'Technische Daten</h2><div class="sdgrid">{"".join(rows)}</div>'
            f'{extra_html}</section>')


def crew_block(crew: List) -> str:
    if not crew:
        return ""
    items = []
    for c in crew:
        name = f"{c.last_name}, {c.first_name}".strip(", ")
        role = getattr(c, "position", "") or ""
        tag = f' <span class="role">({escape(role)})</span>' if role and role != "Crew" else ""
        items.append(f"<li>{escape(name)}{tag}</li>")
    return f'<section class="crew"><h3>Crew</h3><ul>{"".join(items)}</ul></section>'


def _grid_cell(label: str, value: str) -> str:
    return f'<div class="c"><span class="cl">{escape(label)}</span>' \
           f'<span class="cv">{escape(value)}</span></div>'


def _data_uri(image: bytes, mime: str) -> str:
    b64 = base64.b64encode(image).decode("ascii")
    return f"data:{mime or 'image/jpeg'};base64,{b64}"


def entry_card(entry: LogEntry, offset: float, cum_nm: float,
               images: Optional[List[dict]] = None) -> str:
    kind = {"auto": "AutoLog", "manual": "manuell", "tripcon": "TripCon"}.get(
        entry.entry_type, entry.entry_type)
    sails = []
    if entry.mainsail and entry.mainsail not in ("", "—"):
        sails.append(f"Großsegel: {entry.mainsail}")
    if entry.genoa_percent is not None:
        sails.append(f"Fock/Genua: {entry.genoa_percent:.0f}%")
    if entry.spinnaker:
        sails.append("Spinnaker")
    sail_html = (f'<div class="sails">{escape(" · ".join(sails))}</div>'
                 if sails else "")
    note_html = f'<div class="note">{escape(entry.note)}</div>' if entry.note else ""

    fug = "---"
    if entry.sog_kn is not None:
        cog = f", {entry.cog_deg:.0f}°" if entry.cog_deg is not None else ""
        fug = f"{_de(entry.sog_kn, 2)} kn{cog}"

    luft = "---"
    if entry.baro_mbar is not None or entry.air_temp_c is not None:
        parts = []
        if entry.baro_mbar is not None:
            parts.append(f"{entry.baro_mbar:.0f} mBar")
        if entry.air_temp_c is not None:
            parts.append(f"{entry.air_temp_c:.0f}°C")
        luft = ", ".join(parts)

    cells = "".join([
        _grid_cell("Seegang", _de(entry.wave_height_m) + " m" if entry.wave_height_m is not None else "---"),
        _grid_cell("Wassertiefe", (_de(entry.depth_m, 1) + " m") if entry.depth_m is not None else "---"),
        _grid_cell("Niederschlag", entry.precipitation or "---"),
        _grid_cell("Log", _de(cum_nm, 1) + " NM"),
        _grid_cell("FüG / KüG", fug),
        _grid_cell("Wind", wind_str(entry.tws_kn, entry.twd_deg)),
        _grid_cell("Luft", luft),
        _grid_cell("Bewölkung", entry.cloud_cover or "---"),
        _grid_cell("Sicht", entry.visibility or "---"),
    ])

    img_html = ""
    if images:
        imgs = "".join(
            f'<img src="{_data_uri(im["image"], im.get("mime", "image/jpeg"))}" alt="">'
            for im in images
        )
        img_html = f'<div class="imgs">{imgs}</div>'

    return (
        f'<article class="entry">'
        f'<div class="ehead"><span class="etime">{escape(_fmt_dt(entry.timestamp, offset))}</span>'
        f'<span class="ekind">{escape(kind)}</span></div>'
        f'<div class="eanlass">{escape(entry.logevent or "Eintrag")}</div>'
        f'<div class="epos">{escape(latlon_dm(entry.lat, entry.lon))}</div>'
        f'<div class="egrid">{cells}</div>'
        f'{sail_html}{note_html}{img_html}'
        f'</article>'
    )


def leg_card(store, trip: Trip, entries: List[LogEntry], offset: float,
             crew: Optional[List] = None) -> str:
    stats = leg_stats(entries)
    start = _fmt_dt(trip.start_dz, offset) if trip.start_dz else ""
    end_t = _fmt_dt(trip.end_dz, offset, with_date=False) if trip.end_dz else ""
    when = f"{start} – {end_t}" if end_t else start
    winds = [e.tws_kn for e in entries if e.tws_kn is not None]
    wind_txt = "---"
    if winds:
        lo, hi = min(winds), max(winds)
        wind_txt = f"{lo:.0f} – {hi:.0f} kn" if hi - lo >= 1 else f"{hi:.0f} kn"
    baros = [e.baro_mbar for e in entries if e.baro_mbar is not None]
    baro_txt = f"{min(baros):.0f} – {max(baros):.0f} mBar" if baros else "---"
    crew_names = ""
    if crew:
        crew_names = ", ".join(
            f"{c.last_name}" + (" (Skipper)" if getattr(c, "position", "") == "Skipper" else "")
            for c in crew)
    return (
        f'<section class="leg">'
        f'<div class="legtop"><b>{escape(trip.start_location or "?")} → '
        f'{escape(trip.end_location or "…")}</b><span>{escape(when)}</span></div>'
        f'<div class="leggrid">'
        f'<div><span class="k">Gesamt</span><span class="v">{_de(stats["total"])} NM</span></div>'
        f'<div><span class="k">Gesegelt</span><span class="v">{_de(stats["sailed"])} NM</span></div>'
        f'<div><span class="k">Motor</span><span class="v">{_de(stats["motor"])} NM</span></div>'
        f'<div><span class="k">Wind</span><span class="v">{escape(wind_txt)}</span></div>'
        f'<div><span class="k">Luftdruck</span><span class="v">{escape(baro_txt)}</span></div>'
        f'<div><span class="k">Schiff</span><span class="v">{escape(trip.name or "")}</span></div>'
        f'</div>'
        + (f'<div class="legcrew">Crew: {escape(crew_names)}</div>' if crew_names else "")
        + f'</section>'
    )


_STYLE = """
* { box-sizing: border-box; }
body { font-family: -apple-system, Segoe UI, Arial, sans-serif; color:#1a1a1a;
       margin: 0; padding: 24px; font-size: 13px; line-height: 1.35; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 17px; border-bottom: 2px solid #244; padding-bottom: 3px; margin: 22px 0 10px; }
h3 { font-size: 15px; margin: 16px 0 6px; }
.sub { color:#556; margin-bottom: 20px; }
.title-page { min-height: 60vh; display:flex; flex-direction:column;
              justify-content:center; align-items:center; text-align:center; }
.title-page .big { font-size: 34px; font-weight: 700; }
.sdgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 24px; max-width: 640px; }
.sd { display:flex; justify-content:space-between; border-bottom:1px dotted #ccc; padding:2px 0; }
.sd .k { color:#556; } .sd .v { font-weight:600; }
.sx { margin-top: 10px; }
.crew ul { margin: 4px 0; padding-left: 18px; } .role { color:#777; }
.entry { border:1px solid #dde; border-radius:6px; padding:8px 10px; margin:8px 0;
         page-break-inside: avoid; }
.ehead { display:flex; justify-content:space-between; }
.etime { font-weight:700; } .ekind { color:#888; font-size:11px; }
.eanlass { font-weight:600; margin:2px 0; }
.epos { color:#556; font-variant-numeric: tabular-nums; }
.egrid { display:grid; grid-template-columns: repeat(3, 1fr); gap:2px 14px; margin:6px 0; }
.c { display:flex; justify-content:space-between; border-bottom:1px dotted #eee; }
.cl { color:#667; } .cv { font-weight:600; }
.sails { color:#245; font-size:12px; } .note { margin-top:4px; font-style:italic; }
.imgs { margin-top:6px; display:flex; flex-wrap:wrap; gap:6px; }
.imgs img { max-width: 260px; max-height: 200px; border-radius:4px; }
.leg { border:1px solid #dde; border-radius:6px; padding:8px 10px; margin:8px 0;
       page-break-inside: avoid; }
.legtop { display:flex; justify-content:space-between; margin-bottom:4px; }
.leggrid { display:grid; grid-template-columns: repeat(3, 1fr); gap:2px 16px; }
.leggrid .k { color:#667; } .leggrid .v { font-weight:600; margin-left:6px; }
.legcrew { color:#556; margin-top:4px; font-size:12px; }
.roles { color:#556; margin: 2px 0 8px; font-size:12px; }
.summary { margin-top:16px; padding:10px; background:#f2f5f7; border-radius:6px; font-size:15px; }
.pb { page-break-before: always; }
.toolbar { margin: 0 0 14px; }
.toolbar button { font-size: 14px; padding: 6px 14px; cursor: pointer; }
#map { height: 460px; border:1px solid #ccd; border-radius:6px; margin: 6px 0 4px;
       page-break-inside: avoid; }
.maplegend { color:#556; font-size:12px; margin-bottom: 8px; }
.maplegend span { font-weight:600; }
.mapwrap { border:1px solid #ccd; border-radius:6px; overflow:hidden;
           page-break-inside: avoid; }
.trackmap { display:block; width:100%; height:auto; }
.brandbar { display:flex; justify-content:center; margin: 4px 0 6px; }
.rfoot { margin-top: 26px; padding-top: 8px; border-top: 1px solid #dde;
         color:#8592a0; font-size: 11px; display:flex; justify-content:space-between;
         align-items:center; gap:12px; }
@media print {
  body { padding: 0; } .noprint { display:none; }
  #map { height: 150mm; }
  .rfoot { position: fixed; bottom: 0; left: 0; right: 0; }
}
"""

# Leaflet-Assets (nur für Berichte mit Karte) — an Bord über Starlink erreichbar.
_MAP_HEAD = (
    '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
)

_TOOLBAR = ('<div class="toolbar noprint">'
            '<button onclick="window.print()">Drucken / Als PDF speichern</button>'
            '</div>')


def _doc(title: str, body: str, with_map: bool = False) -> str:
    head_extra = _MAP_HEAD if with_map else ""
    footer = (
        f'<div class="rfoot"><span>{escape(branding.COPYRIGHT)}</span>'
        f'<span>{escape(title)}</span>'
        f'<span>erstellt mit {escape(branding.APP_NAME)} ⛵</span></div>'
    )
    return (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title><style>{_STYLE}</style>{head_extra}'
            f'</head><body>{_TOOLBAR}{body}{footer}</body></html>')


# --- Karte im Bericht (Leaflet, ohne AIS) ----------------------------------

_ENTRY_COLORS = {
    "manual": ("#7a0012", "#d61e3c"),      # rot
    "tripcon": ("#333333", "#9aa0a6"),     # grau (Import)
    "auto": ("#7a3d00", "#e8820c"),        # orange
}


def _keep(entry: LogEntry, types: Optional[set]) -> bool:
    """True, wenn der Eintrag nach dem Typ-Filter angezeigt wird (None = alle)."""
    return types is None or entry.entry_type in types


def _entry_count(shown: int, total: int, types: Optional[set]) -> str:
    """Textzeile für die Anzahl gezeigter Einträge (mit Hinweis bei Filter)."""
    if types is None or shown == total:
        return f"{total} Einträge"
    return f"{shown} von {total} Einträgen (Typ-Filter)"


def _nice_step(span: float, target_divs: int = 4) -> float:
    """Runder Gitterabstand (1/2/5·10^n), der ``span`` in ~target Teile teilt."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target_divs)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def track_svg(track: List[List[float]], marks: List[dict],
              width: int = 760, height: int = 470) -> str:
    """Eigenständiger SVG-Kartenplot: Route + Marker + Gitter + Maßstab.

    Ohne externe Kacheln/JS — rendert im Browser **und** im PDF identisch und
    funktioniert offline. Äquirektangulare Projektion mit Breiten-Korrektur.
    """
    pts = [(p[0], p[1]) for p in track if p and p[0] is not None and p[1] is not None]
    if not pts:
        return ""
    lats = [a for a, _ in pts] + [m["lat"] for m in marks]
    lons = [b for _, b in pts] + [m["lon"] for m in marks]
    lat0, lat1 = min(lats), max(lats)
    lon0, lon1 = min(lons), max(lons)
    meanlat = (lat0 + lat1) / 2.0
    kx = max(0.05, math.cos(math.radians(meanlat)))    # Längengrad-Stauchung

    def X(lon):
        return lon * kx

    def Y(lat):
        return -lat

    minx, maxx = X(lon0), X(lon1)
    miny, maxy = Y(lat1), Y(lat0)                        # Y ist invertiert
    dx = max(maxx - minx, 1e-6)
    dy = max(maxy - miny, 1e-6)
    pad = 46
    iw, ih = width - 2 * pad, height - 2 * pad
    scale = min(iw / dx, ih / dy)                        # gleichmäßig, Nordung erhalten
    # zentrieren
    ox = pad + (iw - dx * scale) / 2.0
    oy = pad + (ih - dy * scale) / 2.0

    def px(lat, lon):
        return ox + (X(lon) - minx) * scale, oy + (Y(lat) - miny) * scale

    # Route (bei sehr vielen Punkten ausdünnen)
    step = max(1, len(pts) // 3000)
    poly = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (px(a, b) for a, b in pts[::step]))
    # letzten Punkt sicher mitnehmen
    lx, ly = px(*pts[-1])
    poly += f" {lx:.1f},{ly:.1f}"

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'class="trackmap" role="img" aria-label="Kartenplot">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#eef4f8" '
        f'stroke="#cdd8e0"/>',
    ]
    # Gitter (Längen-/Breitengrade) mit Beschriftung
    lon_step = _nice_step(lon1 - lon0)
    lat_step = _nice_step(lat1 - lat0)

    def _fmt_deg(v, pos, neg):
        h = pos if v >= 0 else neg
        return f"{abs(v):.2f}°{h}".replace(".", ",")

    g = math.ceil(lon0 / lon_step) * lon_step
    while g <= lon1 + 1e-9:
        x, _ = px(lat0, g)
        parts.append(f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{height-pad}" '
                     f'stroke="#d3dde6" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{height-pad+16}" font-size="10" '
                     f'text-anchor="middle" fill="#7089">'
                     f'{escape(_fmt_deg(g, "E", "W"))}</text>')
        g += lon_step
    g = math.ceil(lat0 / lat_step) * lat_step
    while g <= lat1 + 1e-9:
        _, y = px(g, lon0)
        parts.append(f'<line x1="{pad}" y1="{y:.1f}" x2="{width-pad}" y2="{y:.1f}" '
                     f'stroke="#d3dde6" stroke-width="1"/>')
        parts.append(f'<text x="{pad-4}" y="{y+3:.1f}" font-size="10" '
                     f'text-anchor="end" fill="#7089">'
                     f'{escape(_fmt_deg(g, "N", "S"))}</text>')
        g += lat_step

    # Route
    parts.append(f'<polyline points="{poly}" fill="none" stroke="#d6156a" '
                 f'stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>')
    # Start/Ende
    sx, sy = px(*pts[0])
    ex, ey = px(*pts[-1])
    parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5" fill="#159c3f" '
                 f'stroke="#0b5" stroke-width="1"/>')
    parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="5" fill="#c0392b" '
                 f'stroke="#7a0012" stroke-width="1"/>')
    # Marker je Eintrag
    for m in marks:
        mx, my = px(m["lat"], m["lon"])
        parts.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="3.4" '
                     f'fill="{m["fill"]}" stroke="{m["stroke"]}" stroke-width="1"/>')
    # Maßstab (nm): 1 nm = 1/60 Grad Breite
    px_per_nm = scale / 60.0
    if px_per_nm > 0:
        nm = _nice_step(iw / px_per_nm * 0.3, 1) or 1
        barpx = nm * px_per_nm
        bx, by = pad + 6, height - pad - 10
        parts.append(f'<line x1="{bx}" y1="{by}" x2="{bx+barpx:.1f}" y2="{by}" '
                     f'stroke="#334" stroke-width="2.5"/>')
        parts.append(f'<text x="{bx}" y="{by-5}" font-size="10" fill="#334">'
                     f'{("%g" % nm)} sm</text>')
    # Nordpfeil
    nx, ny = width - pad - 6, pad + 4
    parts.append(f'<g transform="translate({nx},{ny})"><polygon points="0,-14 4,4 0,0 -4,4" '
                 f'fill="#334"/><text x="0" y="18" font-size="10" text-anchor="middle" '
                 f'fill="#334">N</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def _map_marks(entries: List[LogEntry], offset: float,
               types: Optional[set]) -> List[dict]:
    marks = []
    for e in entries:
        if e.lat is None or e.lon is None:
            continue
        if types is not None and e.entry_type not in types:
            continue
        stroke, fill = _ENTRY_COLORS.get(e.entry_type, _ENTRY_COLORS["auto"])
        marks.append({
            "lat": e.lat, "lon": e.lon, "stroke": stroke, "fill": fill,
            "time": _fmt_dt(e.timestamp, offset), "type": e.entry_type,
            "anlass": e.logevent or "", "note": e.note or "",
            "wind": wind_str(e.tws_kn, e.twd_deg) if e.tws_kn is not None else "",
        })
    return marks


def map_block(entries: List[LogEntry], offset: float = 0.0,
              types: Optional[set] = None, title: str = "Karte",
              track: Optional[List[List[float]]] = None,
              static: bool = False) -> str:
    """Karte für den Bericht: Route (Linie) + markierte Einträge.

    ``types`` = Eintragstypen, die als Punkt markiert werden (None = alle).
    Die Route wird aus ``track`` gezogen (dichte Spur inkl. Track-Punkte), sonst
    aus ``entries``. ``static=True`` erzeugt einen eigenständigen SVG-Plot
    (offline, druckfest — für PDF), sonst die interaktive Leaflet-Karte.
    """
    if track is None:
        track = [[e.lat, e.lon] for e in entries
                 if e.lat is not None and e.lon is not None]
    marks = _map_marks(entries, offset, types)
    if not track:
        return (f'<h2 class="pb">{escape(title)}</h2>'
                f'<div class="sub">Keine Positionsdaten für die Karte.</div>')
    legend = ('<div class="maplegend"><span style="color:#159c3f">●</span> Start '
              '<span style="color:#c0392b">●</span> Ziel '
              '<span style="color:#e8820c">●</span> Autolog '
              '<span style="color:#d61e3c">●</span> Manuell '
              '<span style="color:#9aa0a6">●</span> Import — Route als Linie</div>')
    if static:
        return (f'<h2 class="pb">{escape(title)}</h2>{legend}'
                f'<div class="mapwrap">{track_svg(track, marks)}</div>')
    data = json.dumps({"track": track, "marks": marks})
    script = (
        "<script>(function(){var d=" + data + ";"
        "var map=L.map('map');"
        "try{L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',"
        "{attribution:'© OpenStreetMap',maxZoom:19}).addTo(map);}catch(e){}"
        "var line=L.polyline(d.track,{color:'#d6156a',weight:3,opacity:.85}).addTo(map);"
        "d.marks.forEach(function(m){"
        "L.circleMarker([m.lat,m.lon],{radius:5,color:m.stroke,weight:1.3,"
        "fillColor:m.fill,fillOpacity:.95}).addTo(map).bindPopup("
        "'<b>'+m.time+'</b>'+(m.anlass?'<br>'+m.anlass:'')+"
        "'<br>'+m.lat.toFixed(4)+', '+m.lon.toFixed(4)+"
        "(m.wind?'<br>Wind wahr '+m.wind:'')+(m.note?'<br><i>'+m.note+'</i>':''));});"
        "map.fitBounds(line.getBounds().pad(0.15));"
        "setTimeout(function(){map.invalidateSize();},200);})();</script>"
    )
    return (f'<h2 class="pb">{escape(title)}</h2>{legend}<div id="map"></div>{script}')


def _track_points(store, trip_ids: List[int]) -> List[List[float]]:
    """Dichte Kartenspur (inkl. reiner Track-Punkte) über die gegebenen Etappen."""
    pts: List[List[float]] = []
    for tid in trip_ids:
        for e in store.all(newest_first=False, trip_id=tid, limit=200000,
                           include_track=True):
            if e.lat is not None and e.lon is not None:
                pts.append([e.lat, e.lon])
    return pts


# --- Törn-Bericht (detailliert, ein Törn) ----------------------------------

def trip_report_html(store, config, trip: Trip, offset: float = 0.0,
                     with_images: bool = False, with_map: bool = False,
                     map_types: Optional[set] = None,
                     entry_types: Optional[set] = None,
                     static_map: bool = False) -> str:
    entries = store.all(newest_first=False, trip_id=trip.id, limit=50000)
    ship = None
    if config.active_ship_id:
        ship = store.get_ship(config.active_ship_id)
    if ship is None:
        ships = store.all_ships()
        ship = ships[0] if ships else None
    crew = store.crew_for_trip(trip.id)

    stats = leg_stats(entries)
    cum = _cumulative_nm(entries)
    kind = "Etappenbericht" if with_images else "Törnbericht"

    period = _date_only(trip.start_dz, offset)
    if trip.end_dz:
        period += " – " + _date_only(trip.end_dz, offset)

    parts = [
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{escape(kind)}</div>'
        f'<div>Logbuch der <b>{escape(ship.name if ship else "")}</b></div>'
        f'<div class="sub">{escape(period)}<br>{escape(trip.name or "")}<br>'
        f'{escape(trip.start_location or "")} → {escape(trip.end_location or "")}</div></div>',
        f'<div class="pb"></div>',
        ship_block(ship),
        crew_block(crew),
    ]
    if with_map:
        parts.append(map_block(entries, offset, map_types, "Karte",
                               track=_track_points(store, [trip.id]),
                               static=static_map))
    parts.append(f'<h2 class="pb">Logbuch</h2>')
    shown = 0
    for i, e in enumerate(entries):
        if not _keep(e, entry_types):
            continue
        shown += 1
        imgs = store.get_entry_images(e.id) if with_images else None
        parts.append(entry_card(e, offset, cum.get(i, 0.0), imgs))
    parts.append(
        f'<div class="summary"><b>Zusammenfassung {escape(trip.name or "")}</b><br>'
        f'Gesamt: {_de(stats["total"])} NM · Gesegelt: {_de(stats["sailed"])} NM · '
        f'Motor: {_de(stats["motor"])} NM<br>{_entry_count(shown, len(entries), entry_types)}</div>')
    return _doc(f"{kind} {trip.name}", "".join(parts),
                with_map=with_map and not static_map)


# --- Törn-Bericht über mehrere Etappen (Voyage) ----------------------------

def _active_ship(store, config) -> Optional[Ship]:
    ship = None
    if getattr(config, "active_ship_id", None):
        ship = store.get_ship(config.active_ship_id)
    if ship is None:
        ships = store.all_ships()
        ship = ships[0] if ships else None
    return ship


def _combined_crew(store, trips: List[Trip]) -> List:
    seen = set()
    out = []
    for t in trips:
        for c in store.crew_for_trip(t.id):
            key = (c.last_name.lower(), c.first_name.lower())
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def voyage_report_html(store, config, voyage: Voyage, trips: List[Trip],
                       offset: float = 0.0, with_images: bool = False,
                       with_map: bool = False, map_types: Optional[set] = None,
                       entry_types: Optional[set] = None,
                       static_map: bool = False) -> str:
    """Törnbericht/Etappenbericht über MEHRERE Etappen (ein Törn)."""
    kind = "Etappenbericht" if with_images else "Törnbericht"
    ship = _active_ship(store, config)
    crew = _combined_crew(store, trips)

    dzs = [t.start_dz for t in trips if t.start_dz] + [t.end_dz for t in trips if t.end_dz]
    period = (_date_only(min(dzs), offset) + " – " + _date_only(max(dzs), offset)) if dzs else ""
    von = trips[0].start_location if trips else ""
    nach = trips[-1].end_location if trips else ""

    parts = [
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{escape(kind)}</div>'
        f'<div>Logbuch der <b>{escape(ship.name if ship else "")}</b></div>'
        f'<div class="sub">{escape(period)}<br><b>{escape(voyage.name)}</b><br>'
        f'{escape(von)} → {escape(nach)}'
        + (f'<br>Revier: {escape(voyage.revier)}' if voyage.revier else "")
        + '</div></div><div class="pb"></div>',
        ship_block(ship),
        crew_block(crew),
        '<h2 class="pb">Etappenübersicht</h2>',
    ]

    total = sailed = motor = 0.0
    leg_entries: Dict[int, List[LogEntry]] = {}
    for t in trips:
        ents = store.all(newest_first=False, trip_id=t.id, limit=50000)
        leg_entries[t.id] = ents
        parts.append(leg_card(store, t, ents, offset, store.crew_for_trip(t.id)))
        s = leg_stats(ents)
        total += s["total"]; sailed += s["sailed"]; motor += s["motor"]

    if with_map:
        combined = [e for t in trips for e in leg_entries[t.id]]
        parts.append(map_block(combined, offset, map_types, "Karte (ganzer Törn)",
                               track=_track_points(store, [t.id for t in trips]),
                               static=static_map))

    for t in trips:
        ents = leg_entries[t.id]
        when = _date_only(t.start_dz, offset)
        legcrew = store.crew_for_trip(t.id)
        roles = ""
        if legcrew:
            roles = "<div class='roles'>" + " · ".join(
                f"{escape(getattr(c,'position','') or 'Crew')}: {escape(c.last_name)}, {escape(c.first_name)}"
                for c in legcrew) + "</div>"
        parts.append(
            f'<h2 class="pb">Etappe: {escape(t.start_location or "?")} → '
            f'{escape(t.end_location or "…")}</h2>'
            f'<div class="sub">{escape(when)}</div>{roles}')
        cum = _cumulative_nm(ents)
        for i, e in enumerate(ents):
            if not _keep(e, entry_types):
                continue
            imgs = store.get_entry_images(e.id) if with_images else None
            parts.append(entry_card(e, offset, cum.get(i, 0.0), imgs))
        st = leg_stats(ents)
        parts.append(
            f'<div class="summary">Etappe {escape(t.start_location or "")} → '
            f'{escape(t.end_location or "")}: Gesamt {_de(st["total"])} NM · '
            f'Gesegelt {_de(st["sailed"])} NM · Motor {_de(st["motor"])} NM</div>')

    parts.append(
        f'<div class="summary pb"><b>Zusammenfassung {escape(voyage.name)}</b><br>'
        f'{len(trips)} Etappen · Gesamt: {_de(total)} NM · '
        f'Gesegelt: {_de(sailed)} NM · Motor: {_de(motor)} NM</div>')
    return _doc(f"{kind} {voyage.name}", "".join(parts),
                with_map=with_map and not static_map)


# --- Fahrtenbuch / Übersicht (mehrere Törns) -------------------------------

def voyage_log_html(store, config, trips: List[Trip], offset: float = 0.0,
                    title: str = "Fahrtenbuch", with_map: bool = False,
                    map_types: Optional[set] = None,
                    static_map: bool = False) -> str:
    total = sailed = motor = 0.0
    ship_ids = set()
    parts = []
    combined: List[LogEntry] = []
    for trip in trips:
        entries = store.all(newest_first=False, trip_id=trip.id, limit=50000)
        crew = store.crew_for_trip(trip.id)
        parts.append(leg_card(store, trip, entries, offset, crew))
        combined.extend(entries)
        s = leg_stats(entries)
        total += s["total"]; sailed += s["sailed"]; motor += s["motor"]

    ships = store.all_ships()
    ship_html = "".join(ship_block(s) for s in ships)

    period = ""
    dzs = [t.start_dz for t in trips if t.start_dz] + [t.end_dz for t in trips if t.end_dz]
    if dzs:
        period = _date_only(min(dzs), offset) + " – " + _date_only(max(dzs), offset)

    head = (
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{escape(title)}</div>'
        f'<div class="sub">{escape(period)}</div></div><div class="pb"></div>'
    )
    summary = (
        f'<div class="summary"><b>Zusammenfassung</b><br>'
        f'{len(trips)} Etappen · Gesamt: {_de(total)} NM · '
        f'Gesegelt: {_de(sailed)} NM · Motor: {_de(motor)} NM</div>'
    )
    map_html = (map_block(combined, offset, map_types, "Karte",
                          track=_track_points(store, [t.id for t in trips]),
                          static=static_map)
                if with_map else "")
    body = (head + ship_html + map_html
            + '<h2 class="pb">Fahrtenübersicht</h2>' + "".join(parts) + summary)
    return _doc(title, body, with_map=with_map and not static_map)
