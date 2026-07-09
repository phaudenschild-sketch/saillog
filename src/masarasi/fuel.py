"""Verbrauchsberechnung aus den Tank-Einträgen.

Methode „voll zu voll": Zwischen zwei als **voll getankt** markierten
Tankungen ist der Startfüllstand gleich dem Endfüllstand (beide voll). Die in
diesem Zeitraum getankte Menge entspricht daher dem Verbrauch. Geteilt durch
die dazwischen vergangenen **Motorstunden** ergibt sich der Verbrauch in
Litern pro Stunde — unabhängig von einer schwankenden Tankanzeige.
"""

from __future__ import annotations

from typing import Dict, List, Optional


def consumption_stats(entries: List) -> Dict:
    """Berechnet l/h aus den Tank-Einträgen.

    Erwartet Objekte mit `timestamp` (ISO-UTC, sortierbar), `liters`,
    `full_tank` (1/0) und `engine_hours`. Rückgabe:

        last_rate    l/h des jüngsten Voll-zu-Voll-Intervalls (oder None)
        avg_rate     l/h über alle Intervalle (Gesamtliter / Gesamtstunden)
        intervals    Liste der Einzelintervalle
        total_liters / total_hours   Summen über alle Intervalle
    """
    fills = sorted(entries, key=lambda e: (e.timestamp or "", e.id or 0))
    # Bezugspunkte: „voll getankt" MIT bekannten Motorstunden
    anchors = [
        e for e in fills
        if e.full_tank and e.engine_hours is not None
    ]

    intervals: List[Dict] = []
    for a, b in zip(anchors, anchors[1:]):
        hours = (b.engine_hours or 0.0) - (a.engine_hours or 0.0)
        if hours <= 0:
            continue
        # Alle Tankungen NACH a bis einschließlich b (a's Menge zählt nicht).
        consumed = sum(
            (f.liters or 0.0)
            for f in fills
            if a.timestamp < f.timestamp <= b.timestamp
        )
        intervals.append({
            "from": a.timestamp,
            "to": b.timestamp,
            "liters": consumed,
            "hours": hours,
            "rate": consumed / hours,
        })

    total_liters = sum(i["liters"] for i in intervals)
    total_hours = sum(i["hours"] for i in intervals)
    last_rate: Optional[float] = intervals[-1]["rate"] if intervals else None
    avg_rate: Optional[float] = (
        total_liters / total_hours if total_hours > 0 else None
    )
    return {
        "last_rate": last_rate,
        "avg_rate": avg_rate,
        "intervals": intervals,
        "total_liters": total_liters,
        "total_hours": total_hours,
    }


def remaining_estimate(
    entries: List,
    capacity_l: Optional[float],
    current_engine_hours: Optional[float],
    rate: Optional[float],
) -> Optional[Dict]:
    """Schätzt den Restfüllstand und die Rest-Motorlaufzeit.

    Ab der letzten „voll getankt"-Tankung (Tank = Kapazität bei deren
    Motorstunden) wird mit `rate` (l/h) verbraucht; spätere Teiltankungen
    kommen hinzu. Braucht Kapazität, aktuelle Motorstunden und eine
    Verbrauchsrate. Gibt None zurück, wenn etwas fehlt.
    """
    if not capacity_l or capacity_l <= 0 or not rate or rate <= 0:
        return None
    if current_engine_hours is None:
        return None
    fills = sorted(entries, key=lambda e: (e.timestamp or "", e.id or 0))
    fulls = [e for e in fills if e.full_tank and e.engine_hours is not None]
    if not fulls:
        return None
    ref = fulls[-1]
    hours_since = current_engine_hours - (ref.engine_hours or 0.0)
    if hours_since < 0:
        return None  # Motorstunden kleiner als beim letzten Volltanken (Reset?)
    consumed = rate * hours_since
    partial_after = sum(
        (e.liters or 0.0) for e in fills if e.timestamp > ref.timestamp
    )
    remaining = max(0.0, min(capacity_l, capacity_l - consumed + partial_after))
    return {
        "remaining_l": remaining,
        "remaining_hours": remaining / rate,
        "capacity_l": capacity_l,
        "rate": rate,
        "hours_since_full": hours_since,
    }
