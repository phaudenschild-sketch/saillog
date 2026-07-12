"""AutoLog-Auslöser: entscheidet, wann ein automatischer Eintrag fällig ist.

Nach dem Vorbild von TripCon. Ein Eintrag wird ausgelöst, sobald **eine** der
aktivierten Bedingungen zutrifft (ODER-Verknüpfung):

* Intervall (z.B. zur vollen Stunde)
* Fahrt über Grund ≥ Schwelle (steigende Flanke)
* Fahrt durchs Wasser ≥ Schwelle (steigende Flanke)
* Kurswechsel ≥ Schwelle (über ein Mittelungsfenster geglättet)
* Wassertiefe ≤ Schwelle (beim Unterschreiten, mit Hysterese)
* abrupte Fahrtreduzierung ≥ Schwelle (kn/s)
* Entfernung zum letzten Eintrag ≥ Schwelle (NM)

Reine Standardbibliothek; die Distanz nutzt `geo.haversine_nm`.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from triplog import geo


@dataclass
class AutoLogSettings:
    """Einstellungen der AutoLog-Auslöser (persistierbar als dict)."""

    enabled: bool = True

    interval_enabled: bool = True
    interval_seconds: int = 3600      # „volle Stunde"
    align_boundary: bool = True       # an Intervallgrenzen ausrichten

    sog_enabled: bool = True
    sog_threshold: float = 8.0        # kn

    stw_enabled: bool = False
    stw_threshold: float = 4.0        # kn

    course_enabled: bool = False
    course_threshold: float = 40.0    # Grad
    course_avg_seconds: int = 120

    depth_enabled: bool = True
    depth_threshold: float = 2.0      # m

    decel_enabled: bool = True
    decel_threshold: float = 5.0      # kn/s

    distance_enabled: bool = False
    distance_threshold: float = 0.5   # NM

    # Trackaufzeichnung: dichte, reine Positionspunkte (entry_type='track')
    # nur für die Kartenspur — unabhängig von den Log-Auslösern oben.
    track_enabled: bool = True
    track_interval_seconds: int = 60       # regelmäßiger Punkt (auch geradeaus)
    track_course_threshold: float = 10.0   # Grad — Punkt bei jeder Kursänderung
    track_min_move_nm: float = 0.02        # gegen Punkt-Spam im Hafen (~37 m)

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> "AutoLogSettings":
        if not data:
            return cls()
        known = {f for f in cls().__dict__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _heading(snapshot: Dict) -> Optional[float]:
    """Beste verfügbare Richtung: COG, sonst Steuerkurs."""
    for key in ("cog_deg", "hdg_true_deg", "hdg_mag_deg"):
        v = snapshot.get(key)
        if v is not None:
            return v
    return None


def _angle_diff(a: float, b: float) -> float:
    """Kleinster Winkelabstand in Grad (0..180)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _circular_mean(angles: List[float]) -> Optional[float]:
    if not angles:
        return None
    s = sum(math.sin(math.radians(a)) for a in angles)
    c = sum(math.cos(math.radians(a)) for a in angles)
    if s == 0.0 and c == 0.0:
        return None
    return math.degrees(math.atan2(s, c)) % 360.0


class AutoLogEngine:
    """Bewertet die Auslöser bei jedem Tick und meldet den Grund zurück."""

    def __init__(self, settings: AutoLogSettings) -> None:
        self.settings = settings
        self._next_interval: Optional[float] = None
        self._last_pos = None                 # (lat, lon) des letzten Eintrags
        self._course_ref: Optional[float] = None
        self._course_hist: deque = deque()    # (t, heading)
        self._depth_armed = True
        self._sog_armed = True
        self._stw_armed = True
        self._prev_sog: Optional[float] = None
        self._prev_sog_t: Optional[float] = None
        # Trackaufzeichnung (eigener Zustand, unabhängig von den Log-Auslösern)
        self._track_next: Optional[float] = None
        self._track_course_ref: Optional[float] = None
        self._track_last_pos = None

    def start(self, now: float) -> None:
        if self.settings.interval_enabled:
            self._next_interval = self._interval_boundary(now)
        if self.settings.track_enabled:
            self._track_next = now                 # erster Trackpunkt sofort

    def _interval_boundary(self, now: float) -> float:
        iv = max(1, int(self.settings.interval_seconds))
        if self.settings.align_boundary:
            return (int(now // iv) + 1) * iv
        return now + iv

    def note_entry(self, now: float, snapshot: Dict) -> None:
        """Nach einem geschriebenen Eintrag den Bezugspunkt aktualisieren."""
        lat, lon = snapshot.get("lat"), snapshot.get("lon")
        if lat is not None and lon is not None:
            self._last_pos = (lat, lon)

    def evaluate(self, snapshot: Dict, now: float) -> Optional[str]:
        """Gibt den (kombinierten) Auslösegrund zurück oder None."""
        s = self.settings
        if not s.enabled:
            return None
        reasons: List[str] = []

        if s.interval_enabled:
            if self._next_interval is None:
                self._next_interval = self._interval_boundary(now)
            if now >= self._next_interval:
                reasons.append("Intervall")
                self._next_interval = self._interval_boundary(now)

        if s.distance_enabled:
            lat, lon = snapshot.get("lat"), snapshot.get("lon")
            if lat is not None and lon is not None and self._last_pos is not None:
                d = geo.haversine_nm(self._last_pos[0], self._last_pos[1], lat, lon)
                if d >= s.distance_threshold:
                    reasons.append(f"Strecke ≥ {s.distance_threshold:g} NM")

        if s.course_enabled:
            h = _heading(snapshot)
            if h is not None:
                self._course_hist.append((now, h))
                cutoff = now - max(1, int(s.course_avg_seconds))
                while self._course_hist and self._course_hist[0][0] < cutoff:
                    self._course_hist.popleft()
                cur = _circular_mean([hh for _, hh in self._course_hist])
                if cur is not None:
                    if self._course_ref is None:
                        self._course_ref = cur
                    elif _angle_diff(cur, self._course_ref) >= s.course_threshold:
                        reasons.append(f"Kurswechsel ≥ {s.course_threshold:g}°")
                        self._course_ref = cur

        if s.depth_enabled:
            d = snapshot.get("depth_m")
            if d is not None:
                if d <= s.depth_threshold and self._depth_armed:
                    reasons.append(f"Flachwasser ≤ {s.depth_threshold:g} m")
                    self._depth_armed = False
                elif d > s.depth_threshold + 1.0:
                    self._depth_armed = True

        if s.decel_enabled:
            sog = snapshot.get("sog_kn")
            if sog is not None:
                if self._prev_sog is not None and self._prev_sog_t is not None:
                    dt = now - self._prev_sog_t
                    if dt > 0 and (self._prev_sog - sog) / dt >= s.decel_threshold:
                        reasons.append("Abrupte Verzögerung")
                self._prev_sog, self._prev_sog_t = sog, now

        if s.sog_enabled:
            sog = snapshot.get("sog_kn")
            if sog is not None:
                if sog >= s.sog_threshold and self._sog_armed:
                    reasons.append(f"SOG ≥ {s.sog_threshold:g} kn")
                    self._sog_armed = False
                elif sog < s.sog_threshold - 0.5:
                    self._sog_armed = True

        if s.stw_enabled:
            stw = snapshot.get("stw_kn")
            if stw is not None:
                if stw >= s.stw_threshold and self._stw_armed:
                    reasons.append(f"STW ≥ {s.stw_threshold:g} kn")
                    self._stw_armed = False
                elif stw < s.stw_threshold - 0.5:
                    self._stw_armed = True

        return "; ".join(reasons) if reasons else None

    # --- Trackaufzeichnung (dichte Positionsspur, map-only) ----------------

    def evaluate_track(self, snapshot: Dict, now: float) -> Optional[str]:
        """True-artig (Grund), wenn ein reiner Track-Punkt fällig ist.

        Feuert bei **jeder Kursänderung** (ab ``track_course_threshold``, mit
        Mindestfahrt gegen GPS-Rauschen) und zusätzlich in einem kurzen
        Intervall, damit auch gerade Strecken dichte Punkte bekommen. Ein
        Mindestbewegungs-Filter verhindert Punkt-Spam im Hafen.
        """
        s = self.settings
        if not (s.enabled and s.track_enabled):
            return None
        lat, lon = snapshot.get("lat"), snapshot.get("lon")
        if lat is None or lon is None:
            return None
        if self._track_next is None:
            self._track_next = now

        reason = ""
        heading = _heading(snapshot)
        sog = snapshot.get("sog_kn")
        if heading is not None and (sog is None or sog >= 1.0):
            if self._track_course_ref is None:
                self._track_course_ref = heading
            elif _angle_diff(heading, self._track_course_ref) >= s.track_course_threshold:
                reason = "Kurswechsel"
        if not reason and now >= self._track_next:
            reason = "Intervall"
        if not reason:
            return None

        # Mindestbewegung nur für Intervall-Punkte erzwingen; eine echte
        # Kursänderung wird immer festgehalten.
        if reason == "Intervall" and self._track_last_pos is not None:
            moved = geo.haversine_nm(
                self._track_last_pos[0], self._track_last_pos[1], lat, lon)
            if moved < s.track_min_move_nm:
                self._track_next = now + max(5, int(s.track_interval_seconds))
                return None
        return reason

    def note_track(self, now: float, snapshot: Dict) -> None:
        """Nach einem geschriebenen Track-Punkt den Track-Bezug aktualisieren."""
        self._track_next = now + max(5, int(self.settings.track_interval_seconds))
        heading = _heading(snapshot)
        if heading is not None:
            self._track_course_ref = heading
        lat, lon = snapshot.get("lat"), snapshot.get("lon")
        if lat is not None and lon is not None:
            self._track_last_pos = (lat, lon)
