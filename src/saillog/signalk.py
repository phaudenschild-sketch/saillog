"""Signal-K-Anbindung für SailLog.

Statt NMEA2000 selbst zu dekodieren, überlässt SailLog das der
**Signal-K-Serversoftware** (große Community, gepflegte PGN-Datenbank), die
z.B. auf einem **Victron Cerbo GX** oder einem Raspberry Pi läuft. Signal K
liest den NMEA2000-Bus, führt alle Quellen in *ein* Datenmodell zusammen und
stellt es als **JSON über HTTP** (und WebSocket) bereit.

Dieses Modul besteht aus zwei Teilen:

* :func:`map_values` — eine **reine** Funktion, die den Signal-K-Datenbaum
  (``.../signalk/v1/api/vessels/self``) in die kanonischen SailLog-Messwerte
  (:mod:`saillog.nmea`) übersetzt. Signal K verwendet **SI-Einheiten**
  (m/s, Kelvin, Radiant, Pascal, Hertz, Sekunden) — hier wird auf die in
  SailLog üblichen Einheiten (Knoten, °C, Grad, mbar/bar, U/min, Stunden)
  umgerechnet. Voll offline testbar.
* :class:`SignalKSource` — ein **HTTP-Polling-Reader** (reine
  Standardbibliothek, ``urllib``), der im Sekundentakt den Datenbaum abruft,
  über :func:`map_values` übersetzt und in :class:`~saillog.livedata.LiveData`
  schreibt. Gleiche Steuer-Schnittstelle (``start``/``stop``/``status``) wie
  :class:`saillog.source.NmeaSource`, damit die GUI beide gleich behandelt.

Am Boot prüfen (Einmal-Abruf, gibt die erkannten Messwerte aus)::

    python -m saillog.signalk 192.168.9.150
    python -m saillog.signalk http://cerbo.local:3000
"""

from __future__ import annotations

import json
import math
import threading
import urllib.request
from typing import Callable, Dict, Optional
from urllib.error import URLError

from saillog import nmea
from saillog.livedata import LiveData
from saillog.source import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
)

# Standard-Port und -Pfad eines Signal-K-Servers (auch Victron Cerbo GX).
DEFAULT_PORT = 3000
SELF_PATH = "/signalk/v1/api/vessels/self"

# Einheiten-Faktoren (Signal K ist SI).
_MS_TO_KN = 1.943844          # m/s  -> Knoten
_M_TO_NM = 1.0 / 1852.0       # Meter -> Seemeilen
_PA_TO_MBAR = 1.0 / 100.0     # Pascal -> mbar (hPa)
_PA_TO_BAR = 1.0 / 100000.0   # Pascal -> bar
_HZ_TO_RPM = 60.0             # Hertz -> Umdrehungen/min
_S_TO_H = 1.0 / 3600.0        # Sekunden -> Stunden


# --- Baum-Navigation --------------------------------------------------------

def _node(tree: Dict, path: str):
    """Folgt einem punktierten Pfad im Baum; None, wenn nicht vorhanden."""
    node = tree
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def _value(tree: Dict, path: str):
    """Der Nutzwert an ``path``.

    Signal K verpackt Blätter als ``{"value": …, "timestamp": …}``; einige
    (vereinfachte) Feeds liefern den Wert direkt. Beides wird unterstützt.
    """
    node = _node(tree, path)
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _num(tree: Dict, path: str) -> Optional[float]:
    v = _value(tree, path)
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _deg(rad: Optional[float]) -> Optional[float]:
    return None if rad is None else math.degrees(rad)


def _deg360(rad: Optional[float]) -> Optional[float]:
    return None if rad is None else math.degrees(rad) % 360.0


def _pick_engine_instance(propulsion: Dict, instance: Optional[str]) -> Optional[Dict]:
    """Wählt die Motor-Instanz (bei einem Motor die einzige/erste)."""
    if not isinstance(propulsion, dict) or not propulsion:
        return None
    if instance is not None and instance in propulsion:
        node = propulsion[instance]
        return node if isinstance(node, dict) else None
    # Deterministisch die erste Instanz (alphabetisch) mit dict-Inhalt.
    for key in sorted(propulsion):
        node = propulsion[key]
        if isinstance(node, dict):
            return node
    return None


# --- Übersetzung Signal K -> SailLog-Messwerte ------------------------------

def map_values(tree: Dict, instance: Optional[str] = None) -> Dict[str, float]:
    """Übersetzt einen Signal-K-``vessels/self``-Baum in SailLog-Messwerte.

    ``instance`` wählt bei mehreren Motoren die Propulsion-Instanz (Schlüssel
    unter ``propulsion``); ohne Angabe wird die erste genommen. Fehlende oder
    ``null``-Werte werden ausgelassen.
    """
    out: Dict[str, float] = {}
    if not isinstance(tree, dict):
        return out

    def put(key: str, value: Optional[float]) -> None:
        if value is not None:
            out[key] = value

    # --- Position (Grad, kein SI-Umrechnen nötig) --------------------------
    pos = _value(tree, "navigation.position")
    if isinstance(pos, dict):
        lat, lon = pos.get("latitude"), pos.get("longitude")
        if isinstance(lat, (int, float)) and not isinstance(lat, bool):
            put(nmea.LAT, float(lat))
        if isinstance(lon, (int, float)) and not isinstance(lon, bool):
            put(nmea.LON, float(lon))

    # --- Navigation --------------------------------------------------------
    sog = _num(tree, "navigation.speedOverGround")
    put(nmea.SOG, None if sog is None else sog * _MS_TO_KN)
    put(nmea.COG, _deg360(_num(tree, "navigation.courseOverGroundTrue")))
    stw = _num(tree, "navigation.speedThroughWater")
    put(nmea.STW, None if stw is None else stw * _MS_TO_KN)
    put(nmea.HDG_TRUE, _deg360(_num(tree, "navigation.headingTrue")))
    put(nmea.HDG_MAG, _deg360(_num(tree, "navigation.headingMagnetic")))
    log_m = _num(tree, "navigation.log")
    put(nmea.LOG_TOTAL, None if log_m is None else log_m * _M_TO_NM)

    # Lage (Krängung/Trimm) aus navigation.attitude {roll, pitch, yaw} in rad
    att = _value(tree, "navigation.attitude")
    if isinstance(att, dict):
        put(nmea.HEEL, _deg(att.get("roll")))
        put(nmea.TRIM, _deg(att.get("pitch")))
    put(nmea.RUDDER, _deg(_num(tree, "steering.rudderAngle")))

    # --- Tiefe (bevorzugt unter Geber; sonst Oberfläche/Kiel) --------------
    for path in ("environment.depth.belowTransducer",
                 "environment.depth.belowSurface",
                 "environment.depth.belowKeel"):
        d = _num(tree, path)
        if d is not None:
            put(nmea.DEPTH, d)
            break

    # --- Temperaturen / Luftdruck (Kelvin -> °C, Pa -> mbar) ---------------
    wt = _num(tree, "environment.water.temperature")
    put(nmea.WATER_TEMP, None if wt is None else wt - 273.15)
    at = _num(tree, "environment.outside.temperature")
    put(nmea.AIR_TEMP, None if at is None else at - 273.15)
    baro = _num(tree, "environment.outside.pressure")
    put(nmea.BARO, None if baro is None else baro * _PA_TO_MBAR)

    # --- Wind (m/s -> kn, rad -> Grad) -------------------------------------
    aws = _num(tree, "environment.wind.speedApparent")
    put(nmea.AWS, None if aws is None else aws * _MS_TO_KN)
    put(nmea.AWA, _deg360(_num(tree, "environment.wind.angleApparent")))
    tws = _num(tree, "environment.wind.speedTrue")
    put(nmea.TWS, None if tws is None else tws * _MS_TO_KN)
    put(nmea.TWD, _deg360(_num(tree, "environment.wind.directionTrue")))
    # Wahrer Windwinkel relativ zum Bug: bevorzugt "…TrueWater", sonst Ground.
    twa = _num(tree, "environment.wind.angleTrueWater")
    if twa is None:
        twa = _num(tree, "environment.wind.angleTrueGround")
    put(nmea.TWA, _deg360(twa))

    # --- Motor (Propulsion) ------------------------------------------------
    eng = _pick_engine_instance(tree.get("propulsion"), instance)
    if eng is not None:
        rpm = _leaf_num(eng, "revolutions")
        put(nmea.ENGINE_RPM, None if rpm is None else rpm * _HZ_TO_RPM)
        temp = _leaf_num(eng, "temperature")
        put(nmea.ENGINE_TEMP, None if temp is None else temp - 273.15)
        put(nmea.ALT_VOLTAGE, _leaf_num(eng, "alternatorVoltage"))
        rt = _leaf_num(eng, "runTime")
        put(nmea.ENGINE_HOURS, None if rt is None else rt * _S_TO_H)
        oil = _leaf_num(eng, "oilPressure")
        put(nmea.OIL_PRESSURE, None if oil is None else oil * _PA_TO_BAR)

    return out


def _leaf_num(node: Dict, key: str) -> Optional[float]:
    """Nutzwert eines direkten Kind-Blattes (``{"value": …}`` oder direkt)."""
    child = node.get(key)
    if isinstance(child, dict) and "value" in child:
        child = child["value"]
    if isinstance(child, bool) or child is None:
        return None
    try:
        return float(child)
    except (TypeError, ValueError):
        return None


# --- HTTP-Polling-Quelle ----------------------------------------------------

StatusCallback = Callable[[str, str], None]


class SignalKSource:
    """Pollt einen Signal-K-Server und speist :class:`LiveData`.

    Gleiche Steuer-Schnittstelle wie :class:`saillog.source.NmeaSource`
    (``start``/``stop``/``status``), damit die GUI beide gleich verwaltet.
    Reine Standardbibliothek (``urllib`` + ``json``).
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        live: Optional[LiveData] = None,
        on_status: Optional[StatusCallback] = None,
        on_raw: Optional[Callable[[str], None]] = None,
        poll_interval: float = 1.0,
        path: str = SELF_PATH,
        instance: Optional[str] = None,
        reconnect_delay: float = 3.0,
        timeout: float = 5.0,
        log_correction: float = 1.0,
    ) -> None:
        self._host = str(host).strip()
        try:
            self._port = int(port) if port else DEFAULT_PORT
        except (TypeError, ValueError):
            self._port = DEFAULT_PORT
        self._live = live if live is not None else LiveData()
        self._on_status = on_status
        self._on_raw = on_raw
        self._poll_interval = max(0.2, float(poll_interval))
        self._path = path if str(path).startswith("/") else "/" + str(path)
        self._instance = instance
        self._reconnect_delay = reconnect_delay
        self._timeout = timeout
        self.log_correction = float(log_correction) if log_correction else 1.0
        # Lokales Gerät: nie über einen (geerbten) Proxy leiten.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status = STATUS_DISCONNECTED

    # --- Steuerung ---------------------------------------------------------

    @property
    def url(self) -> str:
        host = self._host
        if "://" in host:                      # ganze URL erlaubt
            base = host.rstrip("/")
            if base.count("/") <= 2:           # nur Schema+Host -> Port+Pfad
                base = f"{base}:{self._port}{self._path}"
            return base
        return f"http://{host}:{self._port}{self._path}"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._timeout + 1.0)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    @property
    def status(self) -> str:
        return self._status

    # --- interne Logik -----------------------------------------------------

    def _set_status(self, status: str, message: str = "") -> None:
        self._status = status
        if self._on_status is not None:
            self._on_status(status, message)

    def _run(self) -> None:
        url = self.url
        while not self._stop.is_set():
            self._set_status(STATUS_CONNECTING, f"verbinde mit {url}")
            connected = False
            while not self._stop.is_set():
                try:
                    values = self.fetch_once()
                except (URLError, OSError, ValueError) as exc:
                    self._set_status(STATUS_ERROR, _short(str(exc)))
                    break                      # -> Reconnect-Pause
                if not connected:
                    self._set_status(STATUS_CONNECTED, "verbunden (Signal K)")
                    connected = True
                if self._on_raw is not None:
                    self._on_raw(_summary(values))
                if values:
                    if self.log_correction != 1.0:
                        for key in (nmea.STW, nmea.LOG_TOTAL):
                            if values.get(key) is not None:
                                values[key] = values[key] * self.log_correction
                    self._live.update(values)
                self._stop.wait(self._poll_interval)
            if self._stop.is_set():
                break
            self._stop.wait(self._reconnect_delay)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    def fetch_once(self) -> Dict[str, float]:
        """Ruft den Datenbaum einmal ab und gibt die Messwerte zurück."""
        req = urllib.request.Request(
            self.url, headers={"Accept": "application/json"})
        with self._opener.open(req, timeout=self._timeout) as resp:
            raw = resp.read()
        tree = json.loads(raw.decode("utf-8", "replace"))
        return map_values(tree, self._instance)


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary(values: Dict[str, float]) -> str:
    """Kompakte, lesbare Zeile für das Rohdaten-Fenster."""
    if not values:
        return "[SignalK] (keine Messwerte)"
    parts = [f"{k}={v:g}" for k, v in sorted(values.items())]
    return "[SignalK] " + " ".join(parts)


def main(argv=None) -> int:
    """CLI: einmal abrufen und die erkannten Messwerte ausgeben."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Aufruf: python -m saillog.signalk <host|url> [port] [instanz]")
        print("Beispiel: python -m saillog.signalk 192.168.9.150")
        return 2
    host = args[0]
    port = int(args[1]) if len(args) > 1 and args[1].isdigit() else DEFAULT_PORT
    instance = args[2] if len(args) > 2 else None
    src = SignalKSource(host, port=port, instance=instance)
    print(f"Abruf: {src.url}")
    try:
        values = src.fetch_once()
    except Exception as exc:  # noqa: BLE001 - CLI: Fehler klar melden
        print(f"Fehler: {exc}")
        return 1
    if not values:
        print("Verbunden, aber keine bekannten Messwerte im Baum gefunden.")
        return 0
    labels = {key: (label, unit) for key, label, unit in nmea.FIELD_LABELS}
    for key in sorted(values):
        label, unit = labels.get(key, (key, ""))
        print(f"  {label:<18} {values[key]:>10.3f} {unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
