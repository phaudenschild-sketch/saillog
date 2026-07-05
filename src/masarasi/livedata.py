"""Thread-sicherer Speicher für die zuletzt empfangenen Messwerte."""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional


class LiveData:
    """Hält die aktuellsten Messwerte inkl. Zeitstempel.

    Wird vom Netzwerk-Thread beschrieben und vom GUI-Thread gelesen,
    daher durch ein Lock geschützt.
    """

    def __init__(self, stale_after: float = 10.0) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, float] = {}
        self._timestamps: Dict[str, float] = {}
        self._stale_after = stale_after
        self._last_update: Optional[float] = None

    def update(self, values: Dict[str, float], now: Optional[float] = None) -> None:
        """Übernimmt neue Messwerte (überschreibt bestehende Schlüssel)."""
        if not values:
            return
        if now is None:
            now = time.time()
        with self._lock:
            for key, value in values.items():
                self._values[key] = value
                self._timestamps[key] = now
            self._last_update = now

    def snapshot(self, now: Optional[float] = None) -> Dict[str, float]:
        """Gibt eine Kopie aller aktuellen (nicht veralteten) Werte zurück."""
        if now is None:
            now = time.time()
        with self._lock:
            return {
                key: value
                for key, value in self._values.items()
                if now - self._timestamps[key] <= self._stale_after
            }

    def get(self, key: str, now: Optional[float] = None) -> Optional[float]:
        """Liefert einen einzelnen Wert, falls aktuell."""
        if now is None:
            now = time.time()
        with self._lock:
            if key not in self._values:
                return None
            if now - self._timestamps[key] > self._stale_after:
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
            self._last_update = None
