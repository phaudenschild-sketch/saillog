"""Fern-Erfassung: kleiner HTTP-Server für Logbuch-Einträge vom Handy.

Der Laptop stellt im **Bordnetz (WLAN/LAN)** eine handy-optimierte Seite
bereit. Crew öffnet sie im Browser (oder legt sie als Homescreen-Icon ab),
tippt einen Eintrag und speichert ihn — der Eintrag landet direkt im selben
Logbuch wie am Laptop.

* Reine Standardbibliothek (`http.server`), wie der Kartenserver.
* **PIN-Schutz**: ohne gültige PIN kein Zugriff (einfacher Cookie-Login).
* Klassisches HTML-Formular (POST) — funktioniert auf jedem Handy-Browser,
  auch ohne JavaScript und bei wackliger Verbindung.

Der Server läuft in einem eigenen Daemon-Thread. Das Anlegen der Einträge
und das Lesen von Live-/Maskenwerten laufen über Callbacks der App; sie
greifen nur auf thread-sichere Strukturen zu (SQLite pro Aufruf, LiveData
mit Lock, der Bedingungs-Cache als einfaches dict) — genau wie das AutoLog.
"""

from __future__ import annotations

import html
import hmac
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from saillog.fields import (
    CLOUD_COVER_LABELS,
    MAINSAIL_OPTIONS,
    PRECIPITATION,
    VISIBILITY_LABELS,
)

# Callback-Typen
InfoProvider = Callable[[], Dict]          # () -> {trip, measurements, conditions}
SubmitEntry = Callable[[Dict], Dict]       # (conditions) -> {time, lat, lon, logevent}

_LOGEVENTS = ["Routineeintrag", "Wache", "Manöver", "Hafen", "Ankern", "Besonderes"]
_COOKIE = "saillog_remote"


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


class _Handler(BaseHTTPRequestHandler):
    # Zugriff auf den Server über self.server (siehe RemoteServer.start)

    def log_message(self, *args) -> None:  # noqa: D401 – Konsole ruhig halten
        pass

    # --- Hilfen ---------------------------------------------------------
    def _authed(self) -> bool:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return False
        try:
            cookie = SimpleCookie(raw)
        except Exception:  # noqa: BLE001
            return False
        token = cookie[_COOKIE].value if _COOKIE in cookie else ""
        return bool(token) and token in self.server.saillog_tokens  # type: ignore[attr-defined]

    def _send_html(self, body: str, code: int = 200,
                   cookie: Optional[str] = None) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, cookie: Optional[str] = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def _read_form(self) -> Dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        parsed = parse_qs(raw, keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    # --- Routen ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (von BaseHTTPRequestHandler)
        path = self.path.split("?", 1)[0]
        if path in ("/favicon.ico",):
            self.send_response(204)
            self.end_headers()
            return
        if not self._authed():
            self._send_html(_login_page())
            return
        if path == "/":
            self._send_html(_form_page(self.server.saillog_info()))  # type: ignore[attr-defined]
        else:
            self._send_html(_login_page(), code=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        form = self._read_form()
        if path == "/login":
            pin = self.server.saillog_pin              # type: ignore[attr-defined]
            given = (form.get("pin") or "").strip()
            if pin and hmac.compare_digest(given, pin):
                token = secrets.token_urlsafe(16)
                self.server.saillog_tokens.add(token)  # type: ignore[attr-defined]
                self._redirect("/", cookie=f"{_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax")
            else:
                self._send_html(_login_page(error=True), code=401)
            return
        if not self._authed():
            self._redirect("/")
            return
        if path == "/entry":
            conditions = _conditions_from_form(form)
            try:
                result = self.server.saillog_submit(conditions)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                self._send_html(_result_page(None, error=str(exc)))
                return
            self._send_html(_result_page(result))
            return
        self._redirect("/")


def _conditions_from_form(form: Dict[str, str]) -> Dict:
    def num(key):
        raw = (form.get(key) or "").strip().replace(",", ".")
        if raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def sel(key, blank_value):
        val = (form.get(key) or "").strip()
        return "" if val in ("—", blank_value, "") else val

    return {
        "engine_mode": (form.get("engine_mode") or "automatisch").strip(),
        "mainsail": sel("mainsail", "—"),
        "genoa_percent": num("genoa"),
        "spinnaker": 1 if form.get("spinnaker") else 0,
        "wave_height_m": num("wave"),
        "cloud_cover": sel("cloud", "—"),
        "precipitation": sel("precip", "kein"),
        "visibility": sel("visibility", "—"),
        "logevent": (form.get("logevent") or "").strip(),
        "note": (form.get("note") or "").strip(),
    }


# --- HTML-Seiten -----------------------------------------------------------

_STYLE = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
  background:#0b3d5c;color:#eaf2f8}
.wrap{max-width:560px;margin:0 auto;padding:16px}
h1{font-size:1.3rem;margin:.2rem 0 1rem;display:flex;align-items:center;gap:.4rem}
.card{background:#ffffff;color:#111;border-radius:14px;padding:16px;
  box-shadow:0 2px 10px rgba(0,0,0,.25);margin-bottom:14px}
label{display:block;font-weight:600;margin:.55rem 0 .25rem;font-size:.95rem}
input,select,textarea{width:100%;font-size:1.05rem;padding:.6rem .55rem;
  border:1px solid #c3ccd4;border-radius:9px;background:#fff;color:#111}
textarea{min-height:80px}
.grid{display:grid;grid-template-columns:1fr;gap:0 18px}
.full{grid-column:1/-1}
button{width:100%;font-size:1.15rem;font-weight:700;padding:.85rem;margin-top:1rem;
  border:0;border-radius:11px;background:#0a7d34;color:#fff}
button.secondary{background:#0b3d5c}
.live{font-size:.9rem;color:#41525f;background:#eef3f7;border-radius:9px;
  padding:.55rem .65rem;margin-bottom:.4rem}
.ok{font-size:1.3rem;color:#0a7d34;font-weight:700}
.err{color:#b3261e;font-weight:600}
.muted{color:#9aa7b2;font-size:.8rem;text-align:center;margin-top:8px}
/* Tablet & größer: breiter, zweispaltiges Formular, größere Touch-Ziele */
@media (min-width:760px){
  .wrap{max-width:880px;padding:26px}
  h1{font-size:1.7rem}
  .card{padding:22px}
  .grid{grid-template-columns:1fr 1fr;gap:0 22px}
  input,select,textarea{font-size:1.12rem;padding:.7rem .6rem}
  .full button{max-width:380px;margin-left:auto;margin-right:auto;display:block}
}
"""


def _page(inner: str) -> str:
    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>SailLog — Eintrag</title><style>" + _STYLE + "</style></head>"
        "<body><div class='wrap'>" + inner + "</div></body></html>"
    )


def _login_page(error: bool = False) -> str:
    err = "<p class='err'>Falsche PIN.</p>" if error else ""
    return _page(
        "<h1>⛵ SailLog</h1><div class='card'>"
        "<form method='post' action='/login'>" + err +
        "<label>PIN</label>"
        "<input name='pin' type='password' inputmode='numeric' autocomplete='off' autofocus>"
        "<button type='submit'>Anmelden</button></form></div>"
        "<p class='muted'>Fern-Erfassung im Bordnetz</p>"
    )


def _options(values: List[str], selected) -> str:
    out = []
    sel = "" if selected is None else str(selected)
    for v in values:
        s = " selected" if str(v) == sel else ""
        out.append(f"<option value='{_esc(v)}'{s}>{_esc(v)}</option>")
    return "".join(out)


def _form_page(info: Dict) -> str:
    info = info or {}
    m = info.get("measurements") or {}
    c = info.get("conditions") or {}
    trip = info.get("trip")
    trip_line = f"Törn: {_esc(trip)}" if trip else "kein offener Törn"

    lat, lon = m.get("lat"), m.get("lon")
    pos = (f"{_fmt(lat,5)}, {_fmt(lon,5)}"
           if lat is not None and lon is not None else "keine Position")
    live = (
        f"<div class='live'>{trip_line} · {pos}<br>"
        f"SOG {_fmt(m.get('sog_kn'))} kn · COG {_fmt(m.get('cog_deg'),0)}° · "
        f"Wind {_fmt(m.get('tws_kn'))} kn · Tiefe {_fmt(m.get('depth_m'))} m</div>"
    )

    genoa = c.get("genoa_percent")
    genoa_val = "" if genoa is None else f"{genoa:g}"
    wave = c.get("wave_height_m")
    wave_val = "" if wave is None else f"{wave:g}"
    spin = " checked" if c.get("spinnaker") else ""
    mainsail_sel = c.get("mainsail") or "—"
    cloud_sel = c.get("cloud_cover") or "—"
    precip_sel = c.get("precipitation") or "kein"
    vis_sel = c.get("visibility") or "—"

    return _page(
        "<h1>⛵ Neuer Eintrag</h1>" + live +
        "<div class='card'><form method='post' action='/entry'><div class='grid'>"
        "<div class='full'><label>Anlass</label>"
        f"<select name='logevent'>{_options(_LOGEVENTS, c.get('logevent') or 'Routineeintrag')}</select></div>"
        "<div class='full'><label>Bemerkung</label>"
        "<textarea name='note' placeholder='z.B. Ankermanöver in der Bucht'></textarea></div>"
        "<div><label>Motor</label>"
        f"<select name='engine_mode'>{_options(['automatisch','ein','aus'], c.get('engine_mode') or 'automatisch')}</select></div>"
        "<div><label>Großsegel</label>"
        f"<select name='mainsail'>{_options(MAINSAIL_OPTIONS, mainsail_sel)}</select></div>"
        "<div><label>Genua %</label>"
        f"<input name='genoa' type='number' min='0' max='100' value='{_esc(genoa_val)}'></div>"
        "<div><label>Seegang (m)</label>"
        f"<input name='wave' type='number' step='0.1' min='0' value='{_esc(wave_val)}'></div>"
        "<div><label>Bewölkung</label>"
        f"<select name='cloud'>{_options(CLOUD_COVER_LABELS, cloud_sel)}</select></div>"
        "<div><label>Niederschlag</label>"
        f"<select name='precip'>{_options(PRECIPITATION, precip_sel)}</select></div>"
        "<div><label>Sicht</label>"
        f"<select name='visibility'>{_options(VISIBILITY_LABELS, vis_sel)}</select></div>"
        "<div><label style='margin-top:1.9rem'>"
        "<input type='checkbox' name='spinnaker' value='1' style='width:auto'" + spin +
        "> Spinnaker gesetzt</label></div>"
        "<div class='full'><button type='submit'>✓ Eintrag speichern</button></div>"
        "</div></form></div>"
        "<p class='muted'>Position, Wind &amp; Tiefe werden automatisch aus dem Bordnetz übernommen.</p>"
    )


def _result_page(result: Optional[Dict], error: Optional[str] = None) -> str:
    if error:
        inner = ("<div class='card'><p class='err'>Konnte nicht gespeichert werden:</p>"
                 f"<p>{_esc(error)}</p>"
                 "<a href='/'><button class='secondary'>Zurück</button></a></div>")
        return _page("<h1>⛵ SailLog</h1>" + inner)
    result = result or {}
    lat, lon = result.get("lat"), result.get("lon")
    pos = (f"{_fmt(lat,5)}, {_fmt(lon,5)}"
           if lat is not None and lon is not None else "ohne Position")
    inner = (
        "<div class='card'><p class='ok'>✓ Eintrag gespeichert</p>"
        f"<p>{_esc(result.get('time') or '')}<br>{_esc(result.get('logevent') or '')}<br>{pos}</p>"
        "<a href='/'><button>Nächster Eintrag</button></a></div>"
    )
    return _page("<h1>⛵ SailLog</h1>" + inner)


class RemoteServer:
    """HTTP-Server für die Handy-Fern-Erfassung (an das Bordnetz gebunden)."""

    def __init__(
        self,
        info_provider: InfoProvider,
        submit: SubmitEntry,
        pin: str,
        host: str = "0.0.0.0",
        port: int = 8770,
    ) -> None:
        self._info = info_provider
        self._submit = submit
        self._pin = str(pin)
        self._host = host
        self._port = int(port)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        httpd.daemon_threads = True
        httpd.saillog_info = self._info          # type: ignore[attr-defined]
        httpd.saillog_submit = self._submit      # type: ignore[attr-defined]
        httpd.saillog_pin = self._pin            # type: ignore[attr-defined]
        httpd.saillog_tokens = set()             # type: ignore[attr-defined]
        self._httpd = httpd
        self._port = httpd.server_address[1]
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._port
