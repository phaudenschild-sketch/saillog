"""Thread-sicherer Speicher für die zuletzt empfangenen Messwerte."""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

# Kumulative Werte, die nur wachsen (z.B. der Gesamtlog): kleinere Werte von
# Zweit-/Reset-Quellen werden ignoriert, solange der Höchstwert aktuell ist.
_MONOTONIC_MAX = {"log_total_nm"}

# Langsam gesendete Motor-„Dynamikdaten" (Temp, Ladespannung, Betriebsstunden,
# Öldruck): manche Geräte (z.B. Maretron $PMAREPD) senden sie nur alle paar
# Sekunden bis Minuten. Für sie gilt ein längeres Frische-Fenster, damit sie
# zwischen den Updates nicht ständig auf „—" fallen. Navigationswerte bleiben
# beim kurzen Standard-Fenster (schnelle Reaktion bei Verbindungsabriss).
_SLOW_STALE = {
    "engine_temp_c": 60.0,
    "alternator_v": 60.0,
    "engine_hours": 60.0,
    "oil_pressure": 60.0,
    # Tankfüllstand ändert sich kaum und wird oft nur selten gesendet — länger
    # frisch halten, damit ein Logeintrag den letzten Wert noch mitschreibt.
    "fuel_pct": 60.0,
    "fuel_l": 60.0,
}


class LiveData:
    """Hält die aktuellsten Messwerte inkl. Zeitstempel.

    Wird vom Netzwerk-Thread beschrieben und vom GUI-Thread gelesen,
    daher durch ein Lock geschützt.
    """

    def __init__(self, stale_after: float = 10.0) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, float] = {}
        self._timestamps: Dict[str, float] = {}
        # Priorität der Quelle, die einen Schlüssel zuletzt gesetzt hat.
        self._priorities: Dict[str, int] = {}
        self._stale_after = stale_after
        self._last_update: Optional[float] = None

    def _ttl(self, key: str) -> float:
        """Frische-Fenster für einen Schlüssel (langsame Motorwerte länger)."""
        return _SLOW_STALE.get(key, self._stale_after)

    def update(
        self,
        values: Dict[str, float],
        now: Optional[float] = None,
        priority: int = 1,
    ) -> None:
        """Übernimmt neue Messwerte (überschreibt bestehende Schlüssel).

        ``priority`` steuert das Zusammenführen mehrerer Quellen: **je höher,
        desto bevorzugter**. Ein Wert einer **niedriger** priorisierten Quelle
        überschreibt einen noch **frischen** Wert einer höher priorisierten
        Quelle nicht — erst wenn deren Wert veraltet ist (die bevorzugte Quelle
        also ausfällt), springt die nächste Quelle ein. Gleich hohe Prioritäten
        verhalten sich wie bisher (der zuletzt eintreffende Wert gewinnt).
        """
        if not values:
            return
        if now is None:
            now = time.time()
        with self._lock:
            for key, value in values.items():
                if key in self._values:
                    # Prioritäts-Gate: frischen Wert einer bevorzugten Quelle
                    # nicht von einer schwächeren Quelle überschreiben lassen.
                    prev_prio = self._priorities.get(key, priority)
                    if priority < prev_prio and (
                        now - self._timestamps[key]
                    ) <= self._ttl(key):
                        continue
                    if key in _MONOTONIC_MAX:
                        fresh = (now - self._timestamps[key]) <= self._stale_after
                        try:
                            if fresh and value < self._values[key]:
                                continue  # kleineren Ausreißer/Reset ignorieren
                        except TypeError:
                            pass
                self._values[key] = value
                self._timestamps[key] = now
                self._priorities[key] = priority
            self._last_update = now

    def snapshot(self, now: Optional[float] = None) -> Dict[str, float]:
        """Gibt eine Kopie aller aktuellen (nicht veralteten) Werte zurück."""
        if now is None:
            now = time.time()
        with self._lock:
            return {
                key: value
                for key, value in self._values.items()
                if now - self._timestamps[key] <= self._ttl(key)
            }

    def get(self, key: str, now: Optional[float] = None) -> Optional[float]:
        """Liefert einen einzelnen Wert, falls aktuell."""
        if now is None:
            now = time.time()
        with self._lock:
            if key not in self._values:
                return None
            if now - self._timestamps[key] > self._ttl(key):
                return None
            return self._values[key]

    def age(self, now: Optional[float] = None) -> Optional[float]:
        """Sekunden seit dem letzten empfangenen Wert (None = noch keiner)."""
        if now is None:
            now = time.time()
        with self._lock:
            if self._last_update is None:
                return None
            return now - self._last_update

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._timestamps.clear()
            self._priorities.clear()
            self._last_update = None
