"""Geografische Hilfsfunktionen (Distanzen)."""

from __future__ import annotations

import math
from typing import Iterable, Optional, Tuple

# Mittlerer Erdradius in Seemeilen (1 NM = 1852 m).
_EARTH_NM = 6371008.8 / 1852.0

Point = Tuple[Optional[float], Optional[float]]


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Großkreis-Distanz zwischen zwei Punkten in Seemeilen."""
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def track_distance_nm(points: Iterable[Point]) -> float:
    """Summe der Distanzen entlang einer Punktfolge (lat, lon) in Seemeilen.

    Punkte ohne gültige Koordinaten werden übersprungen.
    """
    total = 0.0
    prev: Optional[Tuple[float, float]] = None
    for p in points:
        if not p:
            continue
        lat, lon = p
        if lat is None or lon is None:
            continue
        if prev is not None:
            total += haversine_nm(prev[0], prev[1], lat, lon)
        prev = (lat, lon)
    return total
