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

from saillog import branding, geo, sun, timeutil
from saillog.i18n import t
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

# Plausibilitäts-Grenzen für ein einzelnes Spur-Segment. Ein einzelner
# Koordinaten-Tippfehler (z. B. 102.993 statt 10.2993) erzeugt sonst einen
# Sprung von tausenden Seemeilen und verfälscht den ganzen Meilennachweis.
# Die Grenzen sind bewusst hoch: eine Segeljacht fährt keine 60 kn und legt
# zwischen zwei Einträgen keine 200 sm zurück — echte Segmente (auch grobe
# GPS-Ausreißer bis ~80 kn) bleiben erhalten, nur echte Fehlkoordinaten fallen weg.
_MAX_SEGMENT_NM = 200.0
_MAX_SEGMENT_KN = 100.0


def _plausible_nm(prev_lat, prev_lon, prev_ts, lat, lon, ts) -> float:
    """Segment-Distanz (NM) oder 0.0, wenn Distanz/Geschwindigkeit unplausibel."""
    d = geo.haversine_nm(prev_lat, prev_lon, lat, lon)
    if d > _MAX_SEGMENT_NM:
        return 0.0
    t0 = timeutil.parse_to_utc(prev_ts or "")
    t1 = timeutil.parse_to_utc(ts or "")
    if t0 is not None and t1 is not None:
        hours = (t1 - t0).total_seconds() / 3600.0
        if hours > 0 and d / hours > _MAX_SEGMENT_KN:
            return 0.0
    return d


def leg_stats(entries: List[LogEntry]) -> Dict[str, float]:
    """Gesamt-/Segel-/Motor-Distanz (NM) aus der GPS-Spur + engine_on."""
    total = sailed = motor = 0.0
    prev = None
    prev_engine = None
    for e in entries:
        if e.lat is None or e.lon is None:
            continue
        if prev is not None:
            d = _plausible_nm(prev[0], prev[1], prev[2], e.lat, e.lon, e.timestamp)
            total += d
            # Segment dem Motor zurechnen, wenn am Anfang oder Ende Motor lief
            eng = e.engine_on if e.engine_on is not None else prev_engine
            if eng == 1 or prev_engine == 1:
                motor += d
            else:
                sailed += d
        prev = (e.lat, e.lon, e.timestamp)
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
                run += _plausible_nm(prev[0], prev[1], prev[2], e.lat, e.lon, e.timestamp)
            prev = (e.lat, e.lon, e.timestamp)
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
            # „Länge"/„Breite" hier = Boots-Länge/-Breite (nicht Longitude/
            # Latitude) -> eigener Übersetzungskontext „ship".
            lbl = t(label, _ctx="ship") if attr in ("length_m", "beam_m") else t(label)
            rows.append(f'<div class="sd"><span class="k">{escape(lbl)}</span>'
                        f'<span class="v">{escape(val)}</span></div>')
    extras = []
    if ship.sails:
        extras.append((t("Antrieb/Segel"), ship.sails))
    if ship.equipment:
        extras.append((t("Ausstattung"), ship.equipment))
    tanks = []
    if ship.water_tank_l:
        tanks.append(t("Wassertank ({liter} l)", liter=f"{ship.water_tank_l:.0f}"))
    if ship.fuel_tank_l:
        tanks.append(t("Treibstofftank ({liter} l)", liter=f"{ship.fuel_tank_l:.0f}"))
    if tanks:
        extras.append((t("Tankkapazitäten"), "\n".join(tanks)))
    if ship.power_source:
        extras.append((t("Stromversorgung"), ship.power_source))
    extra_html = "".join(
        f'<div class="sx"><b>{escape(k)}</b><br>{escape(v).replace(chr(10), "<br>")}</div>'
        for k, v in extras
    )
    return (f'<section class="ship"><h2>{escape(ship.name or t("Schiff"))} — '
            f'{t("Technische Daten")}</h2><div class="sdgrid">{"".join(rows)}</div>'
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
    return f'<section class="crew"><h3>{t("Crew")}</h3><ul>{"".join(items)}</ul></section>'


def _grid_cell(label: str, value: str) -> str:
    return f'<div class="c"><span class="cl">{escape(label)}</span>' \
           f'<span class="cv">{escape(value)}</span></div>'


def _data_uri(image: bytes, mime: str) -> str:
    b64 = base64.b64encode(image).decode("ascii")
    return f"data:{mime or 'image/jpeg'};base64,{b64}"


def entry_card(entry: LogEntry, offset: float, cum_nm: float,
               images: Optional[List[dict]] = None) -> str:
    kind = {"auto": "AutoLog", "manual": t("manuell"), "tripcon": "TripCon"}.get(
        entry.entry_type, entry.entry_type)
    sails = []
    if entry.mainsail and entry.mainsail not in ("", "—"):
        # Einzelwert (z.B. „Voll") wird übersetzt; zusammengesetzte Kurzfassungen
        # („Groß Reff 1, Genua 60%") haben keinen Schlüssel und bleiben wie gespeichert.
        sails.append(t("Großsegel: {sail}", sail=t(entry.mainsail)))
    if entry.genoa_percent is not None:
        sails.append(t("Fock/Genua: {pct}%", pct=f"{entry.genoa_percent:.0f}"))
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
        _grid_cell(t("Seegang"), _de(entry.wave_height_m) + " m" if entry.wave_height_m is not None else "---"),
        _grid_cell(t("Wassertiefe"), (_de(entry.depth_m, 1) + " m") if entry.depth_m is not None else "---"),
        _grid_cell(t("Niederschlag"), t(entry.precipitation) or "---"),
        _grid_cell("Log", _de(cum_nm, 1) + " NM"),
        _grid_cell(t("FüG / KüG"), fug),
        _grid_cell("Wind", wind_str(entry.tws_kn, entry.twd_deg)),
        _grid_cell(t("Luft"), luft),
        _grid_cell(t("Bewölkung"), t(entry.cloud_cover) or "---"),
        _grid_cell(t("Sicht"), t(entry.visibility) or "---"),
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
        f'<div class="eanlass">{escape(entry.logevent or t("Eintrag"))}</div>'
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
            f"{c.last_name}" + (f" ({t('Skipper')})" if getattr(c, "position", "") == "Skipper" else "")
            for c in crew)
    return (
        f'<section class="leg">'
        f'<div class="legtop"><b>{escape(trip.start_location or "?")} → '
        f'{escape(trip.end_location or "…")}</b><span>{escape(when)}</span></div>'
        f'<div class="leggrid">'
        f'<div><span class="k">{t("Gesamt")}</span><span class="v">{_de(stats["total"])} NM</span></div>'
        f'<div><span class="k">{t("Gesegelt")}</span><span class="v">{_de(stats["sailed"])} NM</span></div>'
        f'<div><span class="k">{t("Motor")}</span><span class="v">{_de(stats["motor"])} NM</span></div>'
        f'<div><span class="k">Wind</span><span class="v">{escape(wind_txt)}</span></div>'
        f'<div><span class="k">{t("Luftdruck")}</span><span class="v">{escape(baro_txt)}</span></div>'
        f'<div><span class="k">{t("Schiff")}</span><span class="v">{escape(trip.name or "")}</span></div>'
        f'</div>'
        + (f'<div class="legcrew">{t("Crew")}: {escape(crew_names)}</div>' if crew_names else "")
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
.mtab { width:100%; border-collapse:collapse; font-size:12px; margin:8px 0; }
.mtab th, .mtab td { border:1px solid #bcc; padding:5px 7px; text-align:left;
                     vertical-align:top; }
.mtab th { background:#eef3f6; font-size:11px; }
.mtab td.num { text-align:right; font-variant-numeric:tabular-nums; }
.mtab tr.sum td { font-weight:700; background:#f6f9fb; }
.mtab td.sign { min-width:120px; }
.reqbox { background:#f2f7fa; border:1px solid #d6e2ea; border-radius:6px;
          padding:6px 12px; margin:8px 0; }
.reqbox h3 { margin:8px 0 4px; }
.ok { color:#1a7d1a; font-weight:600; } .open { color:#b25000; font-weight:600; }
.sign2 { display:flex; gap:40px; margin-top:30px; }
.sign2 div { flex:1; border-top:1px solid #333; padding-top:4px; color:#333;
             font-size:12px; }
.disc { color:#8592a0; font-size:11px; margin-top:14px; line-height:1.5; }
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

def _toolbar() -> str:
    return ('<div class="toolbar noprint">'
            f'<button onclick="window.print()">{t("Drucken / Als PDF speichern")}</button>'
            '</div>')


def _doc(title: str, body: str, with_map: bool = False) -> str:
    head_extra = _MAP_HEAD if with_map else ""
    footer = (
        f'<div class="rfoot"><span>{escape(branding.COPYRIGHT)}</span>'
        f'<span>{escape(title)}</span>'
        f'<span>{t("erstellt mit {app} ⛵", app=escape(branding.APP_NAME))}</span></div>'
    )
    return (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title><style>{_STYLE}</style>{head_extra}'
            f'</head><body>{_toolbar()}{body}{footer}</body></html>')


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
        return t("{total} Einträge", total=total)
    return t("{shown} von {total} Einträgen (Typ-Filter)", shown=shown, total=total)


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


def map_page_html(track: List[List[float]], marks: List[dict],
                  width: int = 1000, height: int = 640) -> str:
    """Eigenständige Leaflet-Seite (nur Karte, mit OSM-Hintergrund) zum
    Abfotografieren für das PDF — feste Pixelgröße, passt auf die Route ein."""
    data = json.dumps({"track": track, "marks": marks})
    js = (
        "var d=" + data + ";"
        "var map=L.map('map',{zoomControl:false});"
        "L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',"
        "{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);"
        "var line=L.polyline(d.track,{color:'#d6156a',weight:3,opacity:.9}).addTo(map);"
        "d.marks.forEach(function(m){L.circleMarker([m.lat,m.lon],{radius:5,"
        "color:m.stroke,weight:1.3,fillColor:m.fill,fillOpacity:.95}).addTo(map);});"
        "if(d.track.length){"
        "L.circleMarker(d.track[0],{radius:6,color:'#0b5',weight:1.5,"
        "fillColor:'#159c3f',fillOpacity:1}).addTo(map);"
        "L.circleMarker(d.track[d.track.length-1],{radius:6,color:'#7a0012',"
        "weight:1.5,fillColor:'#c0392b',fillOpacity:1}).addTo(map);}"
        "map.fitBounds(line.getBounds().pad(0.12));"
        "setTimeout(function(){map.invalidateSize();"
        "map.fitBounds(line.getBounds().pad(0.12));},250);"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
        f'<style>html,body{{margin:0}}#map{{width:{width}px;height:{height}px}}</style>'
        f'</head><body><div id="map"></div><script>{js}</script></body></html>'
    )


def map_block(entries: List[LogEntry], offset: float = 0.0,
              types: Optional[set] = None, title: Optional[str] = None,
              track: Optional[List[List[float]]] = None,
              static: bool = False, map_renderer=None) -> str:
    """Karte für den Bericht: Route (Linie) + markierte Einträge.

    ``types`` = Eintragstypen, die als Punkt markiert werden (None = alle).
    Die Route wird aus ``track`` gezogen (dichte Spur inkl. Track-Punkte), sonst
    aus ``entries``.

    Reihenfolge der Darstellung:
      * ``map_renderer`` gesetzt → statisches **Bild** mit OSM-Hintergrund
        (Callback ``(track, marks) -> data-URI|None``; für PDF, behält die
        Umgebungskarte). Liefert er None, greift der Fallback:
      * ``static=True`` → eigenständiger **SVG-Plot** (offline, ohne Kacheln),
      * sonst die interaktive **Leaflet-Karte** (HTML im Browser).
    """
    if title is None:
        title = t("Karte")
    if track is None:
        track = [[e.lat, e.lon] for e in entries
                 if e.lat is not None and e.lon is not None]
    marks = _map_marks(entries, offset, types)
    if not track:
        return (f'<h2 class="pb">{escape(title)}</h2>'
                f'<div class="sub">{t("Keine Positionsdaten für die Karte.")}</div>')
    legend = (f'<div class="maplegend"><span style="color:#159c3f">●</span> {t("Start")} '
              f'<span style="color:#c0392b">●</span> {t("Ziel")} '
              f'<span style="color:#e8820c">●</span> {t("Autolog")} '
              f'<span style="color:#d61e3c">●</span> {t("Manuell")} '
              f'<span style="color:#9aa0a6">●</span> {t("Import — Route als Linie")}</div>')
    if map_renderer is not None:
        try:
            uri = map_renderer(track, marks)
        except Exception:  # noqa: BLE001
            uri = None
        if uri:
            return (f'<h2 class="pb">{escape(title)}</h2>{legend}'
                    f'<div class="mapwrap"><img class="trackmap" src="{uri}" '
                    f'alt="Karte"></div>')
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
        "(m.wind?'<br>" + t("Wind wahr") + " '+m.wind:'')+(m.note?'<br><i>'+m.note+'</i>':''));});"
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
                     static_map: bool = False, map_renderer=None) -> str:
    entries = store.all(newest_first=False, trip_id=trip.id, limit=50000)
    ship = _resolve_ship(store, config, trip)
    crew = store.crew_for_trip(trip.id)

    stats = leg_stats(entries)
    cum = _cumulative_nm(entries)
    kind = t("Etappenbericht") if with_images else t("Törnbericht")

    period = _date_only(trip.start_dz, offset)
    if trip.end_dz:
        period += " – " + _date_only(trip.end_dz, offset)

    parts = [
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{escape(kind)}</div>'
        f'<div>{t("Logbuch der")} <b>{escape(ship.name if ship else "")}</b></div>'
        f'<div class="sub">{escape(period)}<br>{escape(trip.name or "")}<br>'
        f'{escape(trip.start_location or "")} → {escape(trip.end_location or "")}</div></div>',
        f'<div class="pb"></div>',
        ship_block(ship),
        crew_block(crew),
    ]
    if with_map:
        parts.append(map_block(entries, offset, map_types, t("Karte"),
                               track=_track_points(store, [trip.id]),
                               static=static_map, map_renderer=map_renderer))
    parts.append(f'<h2 class="pb">{t("Logbuch")}</h2>')
    shown = 0
    for i, e in enumerate(entries):
        if not _keep(e, entry_types):
            continue
        shown += 1
        imgs = store.get_entry_images(e.id) if with_images else None
        parts.append(entry_card(e, offset, cum.get(i, 0.0), imgs))
    parts.append(
        f'<div class="summary"><b>{t("Zusammenfassung")} {escape(trip.name or "")}</b><br>'
        f'{t("Gesamt")}: {_de(stats["total"])} NM · {t("Gesegelt")}: {_de(stats["sailed"])} NM · '
        f'{t("Motor")}: {_de(stats["motor"])} NM<br>{_entry_count(shown, len(entries), entry_types)}</div>')
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


def _resolve_ship(store, config, trip: Trip) -> Optional[Ship]:
    """Auf dem Törn gefahrenes Schiff (fest eingetragen), sonst das aktive.

    So zeigen die Berichte das tatsächlich gefahrene Schiff und nicht einfach
    das gerade eingestellte.
    """
    sid = getattr(trip, "ship_id", None)
    if sid:
        s = store.get_ship(sid)
        if s is not None:
            return s
    return _active_ship(store, config)


def _ships_for_trips(store, config, trips: List[Trip]) -> List[Ship]:
    """Distinkte Schiffe der Törns (Reihenfolge erhalten)."""
    out: List[Ship] = []
    seen = set()
    for t in trips:
        s = _resolve_ship(store, config, t)
        if s is None:
            continue
        key = s.id if s.id is not None else s.name
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _ship_names(ships: List[Ship]) -> str:
    return ", ".join(s.name for s in ships if s.name)


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
                       static_map: bool = False, map_renderer=None) -> str:
    """Törnbericht/Etappenbericht über MEHRERE Etappen (ein Törn)."""
    kind = t("Etappenbericht") if with_images else t("Törnbericht")
    ships = _ships_for_trips(store, config, trips)
    ship_name = _ship_names(ships) or ""
    crew = _combined_crew(store, trips)

    dzs = [tr.start_dz for tr in trips if tr.start_dz] + [tr.end_dz for tr in trips if tr.end_dz]
    period = (_date_only(min(dzs), offset) + " – " + _date_only(max(dzs), offset)) if dzs else ""
    von = trips[0].start_location if trips else ""
    nach = trips[-1].end_location if trips else ""

    parts = [
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{escape(kind)}</div>'
        f'<div>{t("Logbuch der")} <b>{escape(ship_name)}</b></div>'
        f'<div class="sub">{escape(period)}<br><b>{escape(voyage.name)}</b><br>'
        f'{escape(von)} → {escape(nach)}'
        + (f'<br>{t("Revier")}: {escape(voyage.revier)}' if voyage.revier else "")
        + '</div></div><div class="pb"></div>',
        "".join(ship_block(s) for s in ships),
        crew_block(crew),
        f'<h2 class="pb">{t("Etappenübersicht")}</h2>',
    ]

    total = sailed = motor = 0.0
    leg_entries: Dict[int, List[LogEntry]] = {}
    for tr in trips:
        ents = store.all(newest_first=False, trip_id=tr.id, limit=50000)
        leg_entries[tr.id] = ents
        parts.append(leg_card(store, tr, ents, offset, store.crew_for_trip(tr.id)))
        s = leg_stats(ents)
        total += s["total"]; sailed += s["sailed"]; motor += s["motor"]

    if with_map:
        combined = [e for tr in trips for e in leg_entries[tr.id]]
        parts.append(map_block(combined, offset, map_types, t("Karte (ganzer Törn)"),
                               track=_track_points(store, [tr.id for tr in trips]),
                               static=static_map, map_renderer=map_renderer))

    for tr in trips:
        ents = leg_entries[tr.id]
        when = _date_only(tr.start_dz, offset)
        legcrew = store.crew_for_trip(tr.id)
        roles = ""
        if legcrew:
            roles = "<div class='roles'>" + " · ".join(
                f"{escape(getattr(c,'position','') or t('Crew'))}: {escape(c.last_name)}, {escape(c.first_name)}"
                for c in legcrew) + "</div>"
        parts.append(
            f'<h2 class="pb">{t("Etappe")}: {escape(tr.start_location or "?")} → '
            f'{escape(tr.end_location or "…")}</h2>'
            f'<div class="sub">{escape(when)}</div>{roles}')
        cum = _cumulative_nm(ents)
        for i, e in enumerate(ents):
            if not _keep(e, entry_types):
                continue
            imgs = store.get_entry_images(e.id) if with_images else None
            parts.append(entry_card(e, offset, cum.get(i, 0.0), imgs))
        st = leg_stats(ents)
        parts.append(
            f'<div class="summary">{t("Etappe")} {escape(tr.start_location or "")} → '
            f'{escape(tr.end_location or "")}: {t("Gesamt")} {_de(st["total"])} NM · '
            f'{t("Gesegelt")} {_de(st["sailed"])} NM · {t("Motor")} {_de(st["motor"])} NM</div>')

    parts.append(
        f'<div class="summary pb"><b>{t("Zusammenfassung")} {escape(voyage.name)}</b><br>'
        f'{t("{n} Etappen", n=len(trips))} · {t("Gesamt")}: {_de(total)} NM · '
        f'{t("Gesegelt")}: {_de(sailed)} NM · {t("Motor")}: {_de(motor)} NM</div>')
    return _doc(f"{kind} {voyage.name}", "".join(parts),
                with_map=with_map and not static_map)


# --- Fahrtenbuch / Übersicht (mehrere Törns) -------------------------------

def voyage_log_html(store, config, trips: List[Trip], offset: float = 0.0,
                    title: Optional[str] = None, with_map: bool = False,
                    map_types: Optional[set] = None,
                    static_map: bool = False, map_renderer=None) -> str:
    if title is None:
        title = t("Fahrtenbuch")
    total = sailed = motor = 0.0
    parts = []
    combined: List[LogEntry] = []
    for trip in trips:
        entries = store.all(newest_first=False, trip_id=trip.id, limit=50000)
        crew = store.crew_for_trip(trip.id)
        parts.append(leg_card(store, trip, entries, offset, crew))
        combined.extend(entries)
        s = leg_stats(entries)
        total += s["total"]; sailed += s["sailed"]; motor += s["motor"]

    # Nur die tatsächlich auf diesen Törns gefahrenen Schiffe zeigen
    # (nicht pauschal alle angelegten Schiffe).
    ships = _ships_for_trips(store, config, trips)
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
        f'<div class="summary"><b>{t("Zusammenfassung")}</b><br>'
        f'{t("{n} Etappen", n=len(trips))} · {t("Gesamt")}: {_de(total)} NM · '
        f'{t("Gesegelt")}: {_de(sailed)} NM · {t("Motor")}: {_de(motor)} NM</div>'
    )
    map_html = (map_block(combined, offset, map_types, t("Karte"),
                          track=_track_points(store, [tr.id for tr in trips]),
                          static=static_map, map_renderer=map_renderer)
                if with_map else "")
    body = (head + ship_html + map_html
            + f'<h2 class="pb">{t("Fahrtenübersicht")}</h2>' + "".join(parts) + summary)
    return _doc(title, body, with_map=with_map and not static_map)


# --- Seemeilen-Nachweis (Segelscheine DE/AT/CH) ----------------------------

# Übliche Meilen-Anforderungen. (Genaue Zahlen/Bedingungen können sich ändern —
# der Bericht weist ausdrücklich darauf hin, dass beim Prüfungsträger zu
# verifizieren ist.) Tupel: (Bezeichnung, Seemeilen, Nachtmeilen relevant, Hinweis).
LICENSE_REQUIREMENTS = [
    ("🇩🇪 Deutschland (DSV/DMYV)", [
        ("SKS – Sportküstenschifferschein", 300, False,
         "Küstengewässer; Zulassung zur praktischen Prüfung"),
        ("SSS – Sportseeschifferschein", 1000, False,
         "Küsten-/Seegewässer (Teil oft als Wachführer gefordert)"),
        ("SHS – Sporthochseeschifferschein", 1000, True,
         "Hochsee, nach SSS; Nachtmeilen relevant"),
    ]),
    ("🇦🇹 Österreich (Fahrtbereiche FB)", [
        ("FB3 – Küstenfahrt", 300, False,
         "Richtwert küstennahe Fahrt"),
        ("FB4 – Weltweite Fahrt / Hochsee", 1000, True,
         "nach FB3; inkl. Nachtmeilen und längere Törns"),
    ]),
    ("🇨🇭 Schweiz (Hochseeschein CCS/SSA)", [
        ("Hochseeschein", 1000, False,
         "Nachweis im „Meilenbüchlein"),
    ]),
]


def leg_night_nm(entries: List[LogEntry]) -> float:
    """Nachtmeilen einer Etappe: Segmente, deren Mitte bei Dunkelheit liegt."""
    night = 0.0
    prev = None
    prev_dt = None
    for e in entries:
        if e.lat is None or e.lon is None:
            continue
        dt = timeutil.parse_to_utc(e.timestamp)
        if prev is not None and prev_dt is not None and dt is not None:
            d = geo.haversine_nm(prev[0], prev[1], e.lat, e.lon)
            mid_lat = (prev[0] + e.lat) / 2.0
            mid_lon = (prev[1] + e.lon) / 2.0
            mid_dt = prev_dt + (dt - prev_dt) / 2
            if sun.is_night(mid_lat, mid_lon, mid_dt):
                night += d
        prev = (e.lat, e.lon)
        prev_dt = dt
    return night


def _trip_role(store, trip: Trip, applicant: str, default_role: str) -> str:
    """Funktion des Antragstellers auf dieser Etappe (aus der Crewliste, sonst
    Standard)."""
    name = (applicant or "").strip().lower()
    if name:
        for c in store.crew_for_trip(trip.id):
            full = f"{c.first_name} {c.last_name}".strip().lower()
            if name in (full, c.last_name.strip().lower()) and getattr(c, "position", ""):
                return c.position
    return default_role


def meilennachweis_html(store, config, trips: List[Trip], offset: float = 0.0,
                        applicant: str = "", role: str = "Skipper",
                        with_night: bool = True, period_label: str = "") -> str:
    """Seemeilen-Nachweis für Segelscheine (DE/AT/CH) als druckbares HTML."""
    default_ship = _active_ship(store, config)
    _ship_cache: Dict[Optional[int], Optional[Ship]] = {}

    def _ship_for(t) -> Optional[Ship]:
        """Auf dem Törn gefahrenes Schiff (fest eingetragen), sonst aktives."""
        sid = getattr(t, "ship_id", None)
        if sid:
            if sid not in _ship_cache:
                _ship_cache[sid] = store.get_ship(sid)
            if _ship_cache[sid] is not None:
                return _ship_cache[sid]
        return default_ship

    def _ship_detail(s: Optional[Ship]) -> str:
        if not s:
            return ""
        bits = [b for b in (s.ship_type,
                            (f"{s.length_m:.1f} m".replace('.', ',')
                             if s.length_m else "")) if b]
        return " · ".join(bits)

    rows = []
    total = night_total = 0.0
    role_miles: Dict[str, float] = {}
    manual_used = False
    ships_used: Dict[str, Optional[Ship]] = {}   # Name -> Schiff (Reihenfolge)
    for i, tr in enumerate(trips, start=1):
        entries = store.all(newest_first=False, trip_id=tr.id, limit=50000)
        # Manuell bestätigte Seemeilen haben Vorrang vor der GPS-Berechnung
        # (z. B. bei lückenhafter/importierter Spur).
        manual = tr.distance_nm is not None and tr.distance_nm > 0
        nm = tr.distance_nm if manual else leg_stats(entries)["total"]
        if nm <= 0:
            continue
        if manual:
            manual_used = True
        tship = _ship_for(tr)
        tship_name = tship.name if tship else ""
        if tship_name:
            ships_used.setdefault(tship_name, tship)
        night = leg_night_nm(entries) if with_night else 0.0
        rt = _trip_role(store, tr, applicant, role)
        total += nm
        night_total += night
        role_miles[rt] = role_miles.get(rt, 0.0) + nm
        von = tr.start_location or "?"
        nach = tr.end_location or "…"
        zeit = _date_only(tr.start_dz, offset)
        if tr.end_dz and _date_only(tr.end_dz, offset) != zeit:
            zeit += "–" + _date_only(tr.end_dz, offset)
        nm_cell = _de(nm, 0) + (" *" if manual else "")
        night_cell = (f'<td class="num">{_de(night, 0)}</td>' if with_night else "")
        rows.append(
            f'<tr><td class="num">{i}</td><td>{escape(zeit)}</td>'
            f'<td>{escape(von)} → {escape(nach)}</td>'
            f'<td>{escape(tship_name)}</td><td>{escape(rt)}</td>'
            f'<td class="num">{nm_cell}</td>{night_cell}'
            f'<td class="sign"></td></tr>')

    # Kopfzeile: ein Schiff -> mit Details; mehrere -> alle Namen auflisten
    if len(ships_used) == 1:
        only = next(iter(ships_used.values()))
        ship_name = only.name if only else ""
        ship_detail = _ship_detail(only)
    elif ships_used:
        ship_name = ", ".join(ships_used.keys())
        ship_detail = ""
    else:
        ship_name = default_ship.name if default_ship else ""
        ship_detail = _ship_detail(default_ship)

    night_head = f'<th>{t("davon Nacht (sm)")}</th>' if with_night else ""
    night_sum = f'<td class="num">{_de(night_total, 0)}</td>' if with_night else ""
    table = (
        '<table class="mtab"><thead><tr>'
        f'<th>{t("Nr.")}</th><th>{t("Zeitraum")}</th><th>{t("Von → Nach (Revier)")}</th><th>{t("Schiff")}</th>'
        f'<th>{t("Funktion")}</th><th>{t("Seemeilen")}</th>' + night_head +
        f'<th>{t("Bestätigung / Unterschrift Skipper")}</th></tr></thead><tbody>'
        + "".join(rows) +
        f'<tr class="sum"><td></td><td colspan="4">{t("Summe ({n} Törns)", n=len(rows))}</td>'
        f'<td class="num">{_de(total, 0)}</td>{night_sum}<td></td></tr>'
        '</tbody></table>'
    )

    # Rollen-Aufschlüsselung (z.B. „davon als Skipper")
    role_bits = " · ".join(f"{escape(r)}: {_de(m, 0)} sm"
                           for r, m in sorted(role_miles.items(),
                                              key=lambda kv: -kv[1]))

    # Anforderungs-Übersicht mit Ampel
    req_html = [f'<h2 class="pb">{t("Anforderungen")} &amp; {t("Stand")}</h2>']
    for land, lics in LICENSE_REQUIREMENTS:
        req_html.append(f'<div class="reqbox"><h3>{t(land)}</h3>')
        req_html.append(f'<table class="mtab"><thead><tr><th>{t("Schein")}</th>'
                        f'<th>{t("gefordert")}</th><th>{t("vorhanden")}</th><th>{t("Status")}</th>'
                        f'<th>{t("Hinweis")}</th></tr></thead><tbody>')
        for name, miles, night_rel, hint in lics:
            done = total >= miles
            status = (f'<span class="ok">{t("erfüllt ✓")}</span>' if done
                      else f'<span class="open">{t("noch {miles} sm", miles=_de(miles - total, 0))}</span>')
            extra = ""
            if night_rel and with_night:
                extra = f' · {t("Nachtmeilen vorhanden")}: {_de(night_total, 0)} sm'
            req_html.append(
                f'<tr><td>{escape(t(name))}</td>'
                f'<td class="num">{miles} sm</td>'
                f'<td class="num">{_de(total, 0)} sm</td>'
                f'<td>{status}</td>'
                f'<td>{escape(t(hint))}{extra}</td></tr>')
        req_html.append('</tbody></table></div>')

    period = period_label or (
        (_date_only(min(tr.start_dz for tr in trips if tr.start_dz), offset) + " – " +
         _date_only(max((tr.end_dz or tr.start_dz) for tr in trips), offset))
        if trips else "")

    head = (
        f'<div class="title-page"><div class="brandbar">{branding.logo_html(72)}</div>'
        f'<div class="big">{t("Seemeilen-Nachweis")}</div>'
        f'<div>{t("für Segelscheine (Deutschland · Österreich · Schweiz)")}</div>'
        f'<div class="sub">{t("Antragsteller")}: <b>{escape(applicant or "—")}</b><br>'
        f'{escape(period)}<br>{t("Schiff")}: {escape(ship_name)}'
        + (f' ({escape(ship_detail)})' if ship_detail else "")
        + '</div></div><div class="pb"></div>')

    summary = (
        f'<div class="summary"><b>{t("Gesamt: {nm} Seemeilen", nm=_de(total, 0))}</b>'
        + (f' · {t("davon Nacht")}: {_de(night_total, 0)} sm' if with_night else "")
        + (f'<br>{role_bits}' if role_bits else "")
        + f'<br>{t("{n} Törns", n=len(rows))}'
        + '</div>')

    sign = (
        f'<div class="sign2"><div>{t("Ort, Datum")}</div>'
        f'<div>{t("Unterschrift Antragsteller/in")}</div></div>')

    disclaimer = (
        f'<div class="disc"><b>{t("Hinweis")}:</b> '
        + t('Dies ist eine aus dem Logbuch '
            'erzeugte Zusammenstellung. Die Seemeilen wurden aus der GPS-Spur '
            'berechnet (Großkreis). Nachtmeilen = Strecke bei Sonne unter dem '
            'Horizont (astronomische Näherung). Anforderungen und deren genaue '
            'Bedingungen (z.B. Meilen als Wachführer/Skipper, Nachtstunden, '
            'Mindest-Törnlängen, Fahrtgebiete) unterscheiden sich je Schein und '
            'ändern sich — bitte vor der Anmeldung beim jeweiligen Prüfungsträger '
            'bestätigen lassen: DSV/DMYV (DE), zuständige Behörde/OeSV (AT), '
            'Cruising Club der Schweiz / Seeschifffahrtsamt (CH). Jede Etappe ist '
            'vom verantwortlichen Schiffsführer zu bestätigen (Spalte rechts).')
        + '</div>')

    footnote = (
        '<div class="disc" style="margin-top:6px">'
        + t('* Manuell bestätigte Seemeilen (Eingabe im Törn), '
            'nicht aus der GPS-Spur berechnet.')
        + '</div>'
        if manual_used else "")

    body = (head
            + f'<h2 class="pb">{t("Törnübersicht")}</h2>' + table + footnote + summary
            + "".join(req_html) + sign + disclaimer)
    return _doc(t("Seemeilen-Nachweis"), body)
