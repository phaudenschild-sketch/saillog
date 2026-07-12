"""Zeitzonen-Hilfen für die Anzeige.

Intern werden alle Zeitstempel als UTC (ISO-8601 mit 'Z') gespeichert.
Für die Anzeige/Eingabe wird in die eingestellte Zone umgerechnet — entweder
nach der Systemzeit oder nach einem festen UTC-Versatz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def system_offset_hours() -> float:
    """Aktueller lokaler UTC-Versatz des Rechners in Stunden."""
    offset = datetime.now().astimezone().utcoffset()
    return offset.total_seconds() / 3600.0 if offset else 0.0


def effective_offset(mode: str, offset_hours: float) -> float:
    """Wirksamer Versatz: 'system' -> Systemzeit, sonst der feste Wert."""
    if mode == "system":
        return system_offset_hours()
    try:
        return float(offset_hours)
    except (TypeError, ValueError):
        return 0.0


def parse_to_utc(ts: str) -> Optional[datetime]:
    """Parst einen ISO-Zeitstempel (mit 'Z' oder Versatz) nach UTC."""
    if not ts:
        return None
    text = ts.strip().replace("Z", "+00:00").replace(" ", "T")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_display(ts: str, offset_hours: float) -> str:
    """UTC-Zeitstempel -> lokale Anzeige 'YYYY-MM-DD HH:MM:SS'."""
    dt = parse_to_utc(ts)
    if dt is None:
        return ts or ""
    local = dt + timedelta(hours=offset_hours)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def from_display(text: str, offset_hours: float) -> str:
    """Lokale Eingabe -> UTC ISO 'Z'. Nicht parsebares bleibt unverändert."""
    raw = (text or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            dt = None
    if dt is None:
        return text or ""
    utc = dt - timedelta(hours=offset_hours)
    return utc.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def label(mode: str, offset_hours: float) -> str:
    """Kurzes Zonen-Label, z.B. 'UTC+2' (bei System aus der aktuellen Zeit)."""
    hours = effective_offset(mode, offset_hours)
    sign = "+" if hours >= 0 else "-"
    a = abs(hours)
    base = f"UTC{sign}{int(a)}" if a == int(a) else f"UTC{sign}{a:.1f}"
    return f"System ({base})" if mode == "system" else base
