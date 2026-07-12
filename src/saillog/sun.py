"""Sonnenstand (Näherung) — für die Tag/Nacht-Einstufung der Nachtmeilen.

Reine Standardbibliothek. Die Höhe der Sonne über dem Horizont wird mit einem
niedrig-präzisen Standardalgorithmus berechnet (Genauigkeit ~0,1–0,5°, für
Tag/Nacht mehr als ausreichend). „Nacht" = Sonne unter dem Horizont
(Höhe < -0,833°, inkl. Refraktion/Sonnenradius, wie bei Auf-/Untergang).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

# Sonnenauf-/-untergang: Oberkante der Sonne am Horizont (Refraktion + Radius)
HORIZON_DEG = -0.833


def _julian_day(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() / 86400.0 + 2440587.5


def altitude_deg(lat: float, lon: float, dt: datetime) -> float:
    """Höhe der Sonne über dem Horizont in Grad (positiv = über Horizont)."""
    n = _julian_day(dt) - 2451545.0
    # Mittlere ekliptikale Länge und Anomalie der Sonne
    L = math.radians((280.460 + 0.9856474 * n) % 360.0)
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    # Deklination und Rektaszension
    dec = math.asin(math.sin(eps) * math.sin(lam))
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    # Sternzeit -> Stundenwinkel
    gmst = (280.46061837 + 360.98564736629 * n) % 360.0
    lst = math.radians(gmst + lon)
    ha = lst - ra
    latr = math.radians(lat)
    alt = math.asin(math.sin(latr) * math.sin(dec) +
                    math.cos(latr) * math.cos(dec) * math.cos(ha))
    return math.degrees(alt)


def is_night(lat: Optional[float], lon: Optional[float],
             dt: Optional[datetime]) -> bool:
    """True, wenn die Sonne (an Ort/Zeit) unter dem Horizont steht."""
    if lat is None or lon is None or dt is None:
        return False
    return altitude_deg(lat, lon, dt) < HORIZON_DEG
