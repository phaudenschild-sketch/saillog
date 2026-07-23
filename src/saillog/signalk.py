"""Signal K als Datenquelle (Empfang per REST-Polling, reine Standardbibliothek).

Signal K (https://signalk.org) ist ein offener, JSON-basierter Marine-Datenstandard.
Ein Signal-K-Server (z.B. auf einem Raspberry Pi oder manchen Plottern) bündelt
NMEA 0183/2000, AIS, Motor- und Tankdaten zu einem einheitlichen Datenmodell.

Diese Quelle fragt den Server im Sekundentakt über seine REST-API ab
(`GET http://host:port/signalk/v1/api/vessels/self`) und rechnet die
Signal-K-Pfade (SI-Einheiten: m/s, Radiant, Kelvin, Pascal …) auf die
internen SailLog-Schlüssel (Knoten, Grad, Celsius, mbar …) um. Damit fügt sie
sich nahtlos neben ``source.NmeaSource`` in den Mehrquellen-Betrieb ein: beide
speisen denselben ``LiveData``-Schnappschuss.

Bewusst nur REST-Polling (1 Hz genügt fürs Logbuch) und reine
Standardbibliothek — kein WebSocket, keine Zusatzabhängigkeit.
"""

from __future__ import annotations

import json
import math
import threading
import urllib.request
from typing import Callable, Dict, Optional

from saillog.livedata import LiveData
from saillog.source import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
    StatusCallback,
)

DEFAULT_PORT = 3000  # Standardport des Signal-K-Servers (HTTP/WS)

# Einheiten-Umrechnung SI -> Bordeinheiten
_MS_TO_KN = 1.9438444924406      # m/s  -> Knoten
_RAD_TO_DEG = 180.0 / math.pi    # rad  -> Grad
_M_TO_NM = 1.0 / 1852.0          # Meter -> Seemeilen
_PA_TO_MBAR = 1.0 / 100.0        # Pascal -> Millibar/Hektopascal
_PA_TO_BAR = 1.0 / 100000.0      # Pascal -> Bar
_HZ_TO_RPM = 60.0                # Hz -> Umdrehungen/Minute
_S_TO_H = 1.0 / 3600.0           # Sekunden -> Stunden


def _val(tree: Dict, *path: str):
    """Wert eines Signal-K-Pfads holen (die Blätter liegen unter ``value``)."""
    node = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if isinstance(node, dict) and "value" in node:
        node = node["value"]
    return node


def _first_of(node, *path: str):
    """Erster nicht-leerer Feldwert über alle Instanz-Dicts eines Knotens."""
    if not isinstance(node, dict):
        return None
    for inst in node.values():
        if isinstance(inst, dict):
            v = _val(inst, *path)
            if v is not None:
                return v
    return None


def _first(tree: Dict, group: str, *path: str):
    """Erster nicht-leerer Feldwert über alle Instanzen einer Top-Gruppe."""
    return _first_of(tree.get(group), *path)


def _deg(rad):
    """Radiant -> Grad, auf 0..360 normiert (auch für negative Windwinkel)."""
    if rad is None:
        return None
    return (rad * _RAD_TO_DEG) % 360.0


def _num(v):
    """Nur echte Zahlen durchlassen (kein bool, kein None, kein Text)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _utc_hms(iso):
    """ISO-8601-Zeitstempel -> "hhmmss" (wie im NMEA-Schnappschuss)."""
    if not isinstance(iso, str) or "T" not in iso:
        return None
    t = iso.split("T", 1)[1]
    digits = "".join(ch for ch in t if ch.isdigit())
    return digits[:6] if len(digits) >= 6 else None


def signalk_to_snapshot(tree: Dict) -> Dict:
    """Rechnet einen Signal-K-``vessels/self``-Baum in SailLog-Messwerte um.

    Es werden nur tatsächlich vorhandene Werte gesetzt (wie beim NMEA-Parser),
    damit ``LiveData.update`` keine belegten Felder mit ``None`` überschreibt.
    """
    if not isinstance(tree, dict):
        return {}
    out: Dict = {}

    def put(key, value, factor=1.0, offset=0.0):
        n = _num(value)
        if n is not None:
            out[key] = n * factor + offset

    # Navigation ------------------------------------------------------------
    # position/attitude sind verschachtelte Objekte: der value selbst ist ein
    # Dict ({latitude, longitude} bzw. {roll, pitch, yaw}).
    pos = _val(tree, "navigation", "position")
    if isinstance(pos, dict):
        lat = _num(pos.get("latitude"))
        lon = _num(pos.get("longitude"))
        if lat is not None and lon is not None:
            out["lat"] = lat
            out["lon"] = lon

    put("sog_kn", _val(tree, "navigation", "speedOverGround"), _MS_TO_KN)
    put("stw_kn", _val(tree, "navigation", "speedThroughWater"), _MS_TO_KN)
    cog = _deg(_num(_val(tree, "navigation", "courseOverGroundTrue")))
    if cog is not None:
        out["cog_deg"] = cog
    hdt = _deg(_num(_val(tree, "navigation", "headingTrue")))
    if hdt is not None:
        out["hdg_true_deg"] = hdt
    hdm = _deg(_num(_val(tree, "navigation", "headingMagnetic")))
    if hdm is not None:
        out["hdg_mag_deg"] = hdm
    put("log_total_nm", _val(tree, "navigation", "log"), _M_TO_NM)

    # Lage: Krängung (roll) / Trimm (pitch)
    att = _val(tree, "navigation", "attitude")
    if isinstance(att, dict):
        roll = _num(att.get("roll"))
        if roll is not None:
            out["heel_deg"] = roll * _RAD_TO_DEG
        pitch = _num(att.get("pitch"))
        if pitch is not None:
            out["trim_deg"] = pitch * _RAD_TO_DEG

    utc = _utc_hms(_val(tree, "navigation", "datetime"))
    if utc:
        out["utc_time"] = utc

    # Wind -----------------------------------------------------------------
    put("aws_kn", _val(tree, "environment", "wind", "speedApparent"), _MS_TO_KN)
    awa = _deg(_num(_val(tree, "environment", "wind", "angleApparent")))
    if awa is not None:
        out["awa_deg"] = awa
    put("tws_kn", _val(tree, "environment", "wind", "speedTrue"), _MS_TO_KN)
    twd = _deg(_num(_val(tree, "environment", "wind", "directionTrue")))
    if twd is not None:
        out["twd_deg"] = twd
    twa = _deg(_num(_val(tree, "environment", "wind", "angleTrueWater")))
    if twa is None:
        twa = _deg(_num(_val(tree, "environment", "wind", "angleTrueGround")))
    if twa is not None:
        out["twa_deg"] = twa
    put("gust_kn", _val(tree, "environment", "wind", "gust"), _MS_TO_KN)

    # Tiefe / Temperatur / Druck -------------------------------------------
    depth = (_val(tree, "environment", "depth", "belowTransducer")
             or _val(tree, "environment", "depth", "belowKeel")
             or _val(tree, "environment", "depth", "belowSurface"))
    put("depth_m", depth)
    put("water_temp_c", _val(tree, "environment", "water", "temperature"),
        1.0, -273.15)
    put("air_temp_c", _val(tree, "environment", "outside", "temperature"),
        1.0, -273.15)
    put("baro_mbar", _val(tree, "environment", "outside", "pressure"), _PA_TO_MBAR)

    # Motor (erste Instanz mit dem jeweiligen Feld) ------------------------
    put("engine_rpm", _first(tree, "propulsion", "revolutions"), _HZ_TO_RPM)
    put("engine_temp_c", _first(tree, "propulsion", "temperature"), 1.0, -273.15)
    put("oil_pressure_bar", _first(tree, "propulsion", "oilPressure"), _PA_TO_BAR)
    put("engine_hours", _first(tree, "propulsion", "runTime"), _S_TO_H)

    # Bordspannung (Lichtmaschine / Batterie): electrical.batteries.<x>.voltage
    electrical = tree.get("electrical")
    batteries = electrical.get("batteries") if isinstance(electrical, dict) else None
    put("alternator_v", _first_of(batteries, "voltage"))

    # Ruder ----------------------------------------------------------------
    rudder = _num(_val(tree, "steering", "rudderAngle"))
    if rudder is not None:
        out["rudder_deg"] = rudder * _RAD_TO_DEG

    # Treibstofftank: tanks.fuel.<x>.currentLevel / currentVolume
    tanks = tree.get("tanks")
    fuel = tanks.get("fuel") if isinstance(tanks, dict) else None
    level = _num(_first_of(fuel, "currentLevel"))   # 0..1
    if level is not None:
        out["fuel_pct"] = level * 100.0
    vol = _num(_first_of(fuel, "currentVolume"))    # m³
    if vol is not None:
        out["fuel_l"] = vol * 1000.0

    return out


class SignalKSource:
    """Fragt einen Signal-K-Server per REST ab und speist ``LiveData``.

    Schnittstellengleich zu ``source.NmeaSource`` (start/stop/status), damit die
    GUI beide Quellentypen gleich behandeln kann.
    """

    def __init__(
        self,
        host: str,
        port: int,
        live: LiveData,
        protocol: str = "signalk",
        on_status: Optional[StatusCallback] = None,
        on_raw: Optional[Callable[[str], None]] = None,
        on_ais: Optional[Callable[[str], None]] = None,   # (noch) ungenutzt
        reconnect_delay: float = 3.0,
        log_correction: float = 1.0,
        poll_interval: float = 1.0,
    ) -> None:
        self._host = str(host).strip()
        try:
            p = int(port)
        except (TypeError, ValueError):
            p = 0
        self._port = p if p > 0 else DEFAULT_PORT
        self._live = live
        self._on_status = on_status
        self._on_raw = on_raw
        self._reconnect_delay = reconnect_delay
        self._poll_interval = max(0.2, float(poll_interval))
        self.log_correction = float(log_correction) if log_correction else 1.0
        self._url = (f"http://{self._host}:{self._port}"
                     "/signalk/v1/api/vessels/self")
        # LAN-Gerät: Proxys umgehen (sonst würde ein System-/Umgebungsproxy die
        # Verbindung ins Bordnetz kapern).
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}))

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._status = STATUS_DISCONNECTED

    # --- öffentliche Steuerung (wie NmeaSource) ---------------------------

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
            thread.join(timeout=2.0)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    @property
    def status(self) -> str:
        return self._status

    # --- interne Logik ----------------------------------------------------

    def _set_status(self, status: str, message: str = "") -> None:
        self._status = status
        if self._on_status is not None:
            self._on_status(status, message)

    def _run(self) -> None:
        self._set_status(STATUS_CONNECTING, f"verbinde mit {self._host}:{self._port}")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001 - Netz-/JSON-Fehler tolerieren
                self._set_status(STATUS_ERROR, str(exc))
                self._stop.wait(self._reconnect_delay)
                continue
            self._stop.wait(self._poll_interval)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    def _poll_once(self) -> None:
        with self._opener.open(self._url, timeout=5.0) as resp:
            raw = resp.read()
        tree = json.loads(raw.decode("utf-8", "replace"))
        values = signalk_to_snapshot(tree)
        if values:
            if self.log_correction != 1.0:
                for key in ("stw_kn", "log_total_nm"):
                    if values.get(key) is not None:
                        values[key] = values[key] * self.log_correction
            self._live.update(values)
            self._set_status(STATUS_CONNECTED, f"{len(values)} Werte")
            if self._on_raw is not None:
                self._on_raw(self._summary(values))
        else:
            # Verbindung steht, aber (noch) keine verwertbaren Werte.
            self._set_status(STATUS_CONNECTED, "verbunden (keine Messwerte)")

    def _summary(self, values: Dict) -> str:
        """Kompakte Statuszeile für die Rohdaten-Anzeige."""
        bits = []
        if "sog_kn" in values:
            bits.append(f"SOG {values['sog_kn']:.1f}kn")
        if "cog_deg" in values:
            bits.append(f"COG {values['cog_deg']:.0f}°")
        if "depth_m" in values:
            bits.append(f"Tiefe {values['depth_m']:.1f}m")
        if "aws_kn" in values:
            bits.append(f"AWS {values['aws_kn']:.1f}kn")
        return f"[SignalK {self._host}] " + "  ".join(bits) if bits else \
               f"[SignalK {self._host}] {len(values)} Werte"
