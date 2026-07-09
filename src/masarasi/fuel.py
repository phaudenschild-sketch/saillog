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
