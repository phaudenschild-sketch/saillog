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
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from saillog import rig
from saillog.i18n import t
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
    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (von BaseHTTPRequestHandler)
        path = self.path.split("?", 1)[0]
        # Vor dem Login erreichbar: Icon + Manifest (für „Zum Home-Bildschirm")
        if path in ("/icon.png", "/apple-touch-icon.png", "/favicon.ico"):
            icon = getattr(self.server, "saillog_icon", b"")  # type: ignore[attr-defined]
            if icon:
                self._send_bytes(icon, "image/png")
            else:
                self.send_response(204)
                self.end_headers()
            return
        if path == "/manifest.webmanifest":
            self._send_bytes(_manifest().encode("utf-8"), "application/manifest+json")
            return
        if not self._authed():
            # Auto-Login per QR-Code: Adresse enthält ?pin=… -> anmelden und auf
            # die saubere Adresse (ohne PIN) umleiten.
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            given = (parse_qs(query).get("pin", [""])[0] or "").strip()
            pin = self.server.saillog_pin              # type: ignore[attr-defined]
            if given and pin and hmac.compare_digest(given, pin):
                token = secrets.token_urlsafe(16)
                self.server.saillog_tokens.add(token)  # type: ignore[attr-defined]
                self._redirect("/", cookie=f"{_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax")
                return
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

    result = {
        "wave_height_m": num("wave"),
        "cloud_cover": sel("cloud", "—"),
        "precipitation": sel("precip", "kein"),
        "visibility": sel("visibility", "—"),
        "logevent": (form.get("logevent") or "").strip(),
        "note": (form.get("note") or "").strip(),
    }
    # Motor(en): ab 2 Motoren je Motor an/aus (motorname_/motorval_), sonst engine_mode
    if "motorname_0" in form:
        mstates: Dict[str, int] = {}
        i = 0
        while f"motorname_{i}" in form:
            mstates[form[f"motorname_{i}"]] = 1 if form.get(f"motorval_{i}") else 0
            i += 1
        result["engine_mode"] = "ein" if any(mstates.values()) else "aus"
        result["motors_json"] = json.dumps(mstates, ensure_ascii=False)
    else:
        result["engine_mode"] = (form.get("engine_mode") or "automatisch").strip()
        result["motors_json"] = ""
    # Segel: adaptiv (sailname_/sailctrl_/sailval_) > klassisch (mainsail) > Motorboot
    if "sailname_0" in form:
        states: Dict[str, object] = {}
        controls = []
        i = 0
        while f"sailname_{i}" in form:
            name = form[f"sailname_{i}"]
            ctrl = form.get(f"sailctrl_{i}", "fixed")
            if ctrl == "roller":
                states[name] = int(num(f"sailval_{i}") or 0)
            elif ctrl == "fixed":
                states[name] = "gesetzt" if form.get(f"sailval_{i}") else "nicht gesetzt"
            else:
                states[name] = (form.get(f"sailval_{i}") or "nicht gesetzt").strip()
            controls.append(rig.SailControl(name=name, category="", control=ctrl))
            i += 1
        spec = rig.RigSpec(sails=controls)
        result["sails_json"] = json.dumps(states, ensure_ascii=False)
        result["mainsail"] = rig.summarize(states, spec)
        result["genoa_percent"] = None
        result["spinnaker"] = None
    elif "mainsail" in form:
        result["mainsail"] = sel("mainsail", "—")
        result["genoa_percent"] = num("genoa")
        result["spinnaker"] = 1 if form.get("spinnaker") else 0
        result["sails_json"] = ""
    else:                                   # Motorboot: keine Segelfelder
        result["mainsail"] = ""
        result["genoa_percent"] = None
        result["spinnaker"] = None
        result["sails_json"] = ""
    return result


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
    # Meta-Tags + Manifest, damit „Zum Home-Bildschirm hinzufügen" auf iOS und
    # Android ein eigenes App-Icon „SailLog" erzeugt und die Seite im Vollbild
    # (wie eine App) startet.
    head = (
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'>"
        "<title>SailLog</title>"
        "<meta name='theme-color' content='#0b3d5c'>"
        "<meta name='mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-capable' content='yes'>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
        "<meta name='apple-mobile-web-app-title' content='SailLog'>"
        "<link rel='apple-touch-icon' href='/icon.png'>"
        "<link rel='icon' href='/icon.png'>"
        "<link rel='manifest' href='/manifest.webmanifest'>"
        "<style>" + _STYLE + "</style>"
    )
    return (
        "<!doctype html><html lang='de'><head>" + head + "</head>"
        "<body><div class='wrap'>" + inner + "</div></body></html>"
    )


def _manifest() -> str:
    return (
        '{"name":"SailLog","short_name":"SailLog","start_url":"/",'
        '"scope":"/","display":"standalone","orientation":"portrait",'
        '"background_color":"#0b3d5c","theme_color":"#0b3d5c",'
        '"icons":[{"src":"/icon.png","sizes":"64x64","type":"image/png","purpose":"any"}]}'
    )


def _login_page(error: bool = False) -> str:
    err = f"<p class='err'>{t('Falsche PIN.')}</p>" if error else ""
    return _page(
        "<h1>⛵ SailLog</h1><div class='card'>"
        "<form method='post' action='/login'>" + err +
        f"<label>{t('PIN')}</label>"
        "<input name='pin' type='password' inputmode='numeric' autocomplete='off' autofocus>"
        f"<button type='submit'>{t('Anmelden')}</button></form></div>"
        f"<p class='muted'>{t('Fern-Erfassung im Bordnetz')}</p>"
    )


def _datalist_input(name: str, values: List[str], selected, list_id: str) -> str:
    """Anlass: editierbares Textfeld + echtes Auswahl-Dropdown darunter.

    Das Textfeld ist der tatsächliche Wert (frei tippen, z. B. „Ankern vor
    Insel XY"). Das Dropdown rollt am Handy zuverlässig aus; eine Auswahl
    füllt das Textfeld (das man danach weiter bearbeiten kann). Robuster als
    <datalist>, das auf vielen Handy-Browsern nicht aufklappt.
    """
    sel = "" if selected is None else str(selected)
    opts = "".join(f"<option>{_esc(v)}</option>" for v in values)
    return (
        f"<input name='{_esc(name)}' value='{_esc(sel)}' autocomplete='off'>"
        "<select style='margin-top:6px' "
        "onchange='var i=this.previousElementSibling;"
        "if(this.value){i.value=this.value;}this.selectedIndex=0;'>"
        f"<option value=''>▾ {t('aus Liste wählen…')}</option>{opts}</select>"
    )


def _options(values: List[str], selected) -> str:
    # value = kanonischer deutscher Code (wird so gespeichert), Anzeige = übersetzt.
    out = []
    sel = "" if selected is None else str(selected)
    for v in values:
        s = " selected" if str(v) == sel else ""
        out.append(f"<option value='{_esc(v)}'{s}>{_esc(t(v))}</option>")
    return "".join(out)


def _motor_fields(rig_info: Optional[Dict], c: Dict) -> str:
    """Motor-Feld(er): ab 2 Motoren je Motor eine „läuft"-Checkbox."""
    motors = (rig_info or {}).get("motors") or []
    if len(motors) >= 2:
        rows = []
        for i, name in enumerate(motors):
            rows.append(
                f"<input type='hidden' name='motorname_{i}' value='{_esc(name)}'>"
                "<label style='display:inline;font-weight:400'>"
                f"<input type='checkbox' name='motorval_{i}' value='1' "
                f"style='width:auto'> {t('{name} läuft', name=_esc(name))}</label><br>")
        return f"<div class='full'><label>{t('Motoren')}</label>{''.join(rows)}</div>"
    return (f"<div><label>{t('Motor')}</label>"
            f"<select name='engine_mode'>"
            f"{_options(['automatisch','ein','aus'], c.get('engine_mode') or 'automatisch')}"
            "</select></div>")


def _sail_fields(rig_info: Optional[Dict], c: Dict) -> str:
    """Segel-/Antriebs-Felder passend zur Ausrüstung des aktiven Schiffs."""
    rig_info = rig_info or {}
    if not rig_info.get("configured"):
        # klassisch (kein Schiff / keine Ausrüstung gepflegt)
        genoa = c.get("genoa_percent")
        genoa_val = "" if genoa is None else f"{genoa:g}"
        spin = " checked" if c.get("spinnaker") else ""
        mainsail_sel = c.get("mainsail") or "—"
        return (
            f"<div><label>{t('Großsegel')}</label>"
            f"<select name='mainsail'>{_options(MAINSAIL_OPTIONS, mainsail_sel)}</select></div>"
            f"<div><label>{t('Genua %')}</label>"
            f"<input name='genoa' type='number' min='0' max='100' value='{_esc(genoa_val)}'></div>"
            "<div class='full'><label style='display:inline;font-weight:400'>"
            "<input type='checkbox' name='spinnaker' value='1' style='width:auto'" + spin +
            f"> {t('Spinnaker gesetzt')}</label></div>"
        )
    if rig_info.get("is_motorboat"):
        motors = ", ".join(rig_info.get("motors") or [])
        extra = f" ({_esc(motors)})" if motors else ""
        return (f"<div class='full'><label>{t('Antrieb')}</label>"
                f"<div class='live'>🛥 {t('Motorboot — keine Segel')}{extra}</div></div>")
    # adaptiv: ein Feld je Segel, Bedienelement nach Reff-Art
    out: List[str] = []
    for i, s in enumerate(rig_info.get("sails") or []):
        name = s.get("name", "")
        ctrl = s.get("control", "fixed")
        hidden = (f"<input type='hidden' name='sailname_{i}' value='{_esc(name)}'>"
                  f"<input type='hidden' name='sailctrl_{i}' value='{_esc(ctrl)}'>")
        if ctrl == "roller":
            control = (
                f"<input type='range' name='sailval_{i}' min='0' max='100' value='0' "
                "oninput=\"this.nextElementSibling.value=this.value+' %'\">"
                "<output style='font-weight:600'>0 %</output>")
        elif ctrl == "slab":
            control = (f"<select name='sailval_{i}'>"
                       f"{_options(rig.SLAB_STATES, 'nicht gesetzt')}</select>")
        else:
            control = ("<label style='display:inline;font-weight:400'>"
                       f"<input type='checkbox' name='sailval_{i}' value='1' "
                       f"style='width:auto'> {t('gesetzt')}</label>")
        out.append(f"<div class='full'><label>{_esc(name)}</label>{hidden}{control}</div>")
    return "".join(out)


def _form_page(info: Dict) -> str:
    info = info or {}
    m = info.get("measurements") or {}
    c = info.get("conditions") or {}
    trip = info.get("trip")
    trip_line = t("Törn: {trip}", trip=_esc(trip)) if trip else t("kein offener Törn")

    lat, lon = m.get("lat"), m.get("lon")
    pos = (f"{_fmt(lat,5)}, {_fmt(lon,5)}"
           if lat is not None and lon is not None else t("keine Position"))
    live = (
        f"<div class='live'>{trip_line} · {pos}<br>"
        f"SOG {_fmt(m.get('sog_kn'))} kn · COG {_fmt(m.get('cog_deg'),0)}° · "
        f"Wind {_fmt(m.get('tws_kn'))} kn · {t('Tiefe')} {_fmt(m.get('depth_m'))} m</div>"
    )

    logevents = info.get("logevents") or _LOGEVENTS
    wave = c.get("wave_height_m")
    wave_val = "" if wave is None else f"{wave:g}"
    cloud_sel = c.get("cloud_cover") or "—"
    precip_sel = c.get("precipitation") or "kein"
    vis_sel = c.get("visibility") or "—"

    return _page(
        f"<h1>⛵ {t('Neuer Eintrag')}</h1>" + live +
        "<div class='card'><form method='post' action='/entry'><div class='grid'>"
        f"<div class='full'><label>{t('Anlass')}</label>"
        + _datalist_input("logevent", logevents,
                          c.get('logevent') or (logevents[0] if logevents else ''),
                          "logevent_list") + "</div>"
        f"<div class='full'><label>{t('Bemerkung')}</label>"
        f"<textarea name='note' placeholder='{t('z.B. Ankermanöver in der Bucht')}'></textarea></div>"
        + _motor_fields(info.get("rig"), c) +
        f"<div><label>{t('Seegang (m)')}</label>"
        f"<input name='wave' type='number' step='0.1' min='0' value='{_esc(wave_val)}'></div>"
        + _sail_fields(info.get("rig"), c) +
        f"<div><label>{t('Bewölkung')}</label>"
        f"<select name='cloud'>{_options(CLOUD_COVER_LABELS, cloud_sel)}</select></div>"
        f"<div><label>{t('Niederschlag')}</label>"
        f"<select name='precip'>{_options(PRECIPITATION, precip_sel)}</select></div>"
        f"<div><label>{t('Sicht')}</label>"
        f"<select name='visibility'>{_options(VISIBILITY_LABELS, vis_sel)}</select></div>"
        f"<div class='full'><button type='submit'>✓ {t('Eintrag speichern')}</button></div>"
        "</div></form></div>"
        f"<p class='muted'>{t('Position, Wind &amp; Tiefe werden automatisch aus dem Bordnetz übernommen.')}</p>"
    )


def _result_page(result: Optional[Dict], error: Optional[str] = None) -> str:
    if error:
        inner = (f"<div class='card'><p class='err'>{t('Konnte nicht gespeichert werden:')}</p>"
                 f"<p>{_esc(error)}</p>"
                 f"<a href='/'><button class='secondary'>{t('Zurück')}</button></a></div>")
        return _page("<h1>⛵ SailLog</h1>" + inner)
    result = result or {}
    lat, lon = result.get("lat"), result.get("lon")
    pos = (f"{_fmt(lat,5)}, {_fmt(lon,5)}"
           if lat is not None and lon is not None else t("ohne Position"))
    inner = (
        f"<div class='card'><p class='ok'>✓ {t('Eintrag gespeichert')}</p>"
        f"<p>{_esc(result.get('time') or '')}<br>{_esc(result.get('logevent') or '')}<br>{pos}</p>"
        f"<a href='/'><button>{t('Nächster Eintrag')}</button></a></div>"
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
        icon_png: bytes = b"",
    ) -> None:
        self._info = info_provider
        self._submit = submit
        self._pin = str(pin)
        self._host = host
        self._port = int(port)
        self._icon = icon_png or b""
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
        httpd.saillog_icon = self._icon          # type: ignore[attr-defined]
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
