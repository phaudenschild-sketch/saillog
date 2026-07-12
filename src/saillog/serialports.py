"""Serielle Schnittstellen auflisten (für die GPS-Maus / USB-NMEA).

Dünner Wrapper um ``serial.tools.list_ports`` (pyserial). pyserial ist
optional — fehlt es, liefern die Funktionen leere Listen statt zu scheitern,
damit die App ohne die Abhängigkeit sauber weiterläuft.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

# Hinweise in der Port-Beschreibung, die auf eine GPS-Maus / einen USB-Seriell-
# Adapter deuten (übliche Chipsätze der G-Mouse-Empfänger und ihrer Adapter).
_GPS_HINTS = (
    "gps", "u-blox", "ublox", "glonass", "gnss", "nmea", "g-mouse", "gmouse",
    "prolific", "pl2303", "ftdi", "ft232", "ch340", "ch341", "cp210", "silicon labs",
    "usb serial", "usb-serial", "usb-seriell",
)


def available_ports() -> List[Tuple[str, str]]:
    """[(Gerät, Beschreibung)] aller seriellen Ports; [] ohne pyserial."""
    try:
        from serial.tools import list_ports  # pyserial
    except ImportError:
        return []
    out: List[Tuple[str, str]] = []
    for p in list_ports.comports():
        device = getattr(p, "device", "") or ""
        if not device:
            continue
        desc = getattr(p, "description", "") or device
        out.append((device, desc))
    # Stabile, natürliche Sortierung (COM2 vor COM10)
    out.sort(key=lambda dp: _port_sort_key(dp[0]))
    return out


def _port_sort_key(device: str):
    digits = "".join(ch for ch in device if ch.isdigit())
    return (0, int(digits)) if digits else (1, device)


def _looks_like_gps(description: str) -> bool:
    d = (description or "").lower()
    return any(h in d for h in _GPS_HINTS)


def gps_first(ports: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Ports so ordnen, dass wahrscheinliche GPS-/USB-Seriell-Ports vorn stehen."""
    gps = [p for p in ports if _looks_like_gps(p[1])]
    rest = [p for p in ports if not _looks_like_gps(p[1])]
    return gps + rest


def guess_gps_port(ports: Optional[List[Tuple[str, str]]] = None) -> Optional[str]:
    """Bestes Rate-Ergebnis für den GPS-Port (Gerätename) oder None."""
    if ports is None:
        ports = available_ports()
    ordered = gps_first(ports)
    if not ordered:
        return None
    # Nur zurückgeben, wenn er wie GPS aussieht oder es genau einen Port gibt.
    if _looks_like_gps(ordered[0][1]) or len(ordered) == 1:
        return ordered[0][0]
    return None
