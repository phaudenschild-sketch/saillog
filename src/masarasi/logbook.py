"""Logbuch-Dienst: verbindet LiveData, Speicher und Auto-Logging."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from masarasi.livedata import LiveData
from masarasi.storage import LogbookStore, LogEntry


def utc_now_iso() -> str:
    """Aktueller UTC-Zeitstempel als ISO-8601 mit 'Z'."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


class LogbookService:
    """Kapselt das Erstellen automatischer und manueller Einträge."""

    def __init__(self, store: LogbookStore, live: LiveData) -> None:
        self._store = store
        self._live = live
        self._auto_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._interval = 300
        self._on_auto_entry: Optional[Callable[[LogEntry], None]] = None

    # --- manuelle Einträge --------------------------------------------------

    def add_manual(
        self,
        note: str = "",
        crew: str = "",
        location: str = "",
        include_measurements: bool = True,
    ) -> LogEntry:
        """Erstellt einen manuellen Eintrag, optional mit aktuellen Messwerten."""
        measurements = self._live.snapshot() if include_measurements else {}
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="manual",
            measurements=measurements,
            note=note,
            crew=crew,
            location=location,
        )
        self._store.add(entry)
        return entry

    # --- automatische Einträge ---------------------------------------------

    def record_auto(self) -> Optional[LogEntry]:
        """Schreibt einen Auto-Eintrag aus dem aktuellen Snapshot.

        Gibt None zurück, wenn (noch) keine Messwerte vorliegen.
        """
        measurements = self._live.snapshot()
        if not measurements:
            return None
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="auto",
            measurements=measurements,
        )
        self._store.add(entry)
        return entry

    def start_auto(
        self,
        interval_seconds: int,
        on_entry: Optional[Callable[[LogEntry], None]] = None,
    ) -> None:
        """Startet das automatische Logging im gegebenen Intervall."""
        self.stop_auto()
        self._interval = max(5, int(interval_seconds))
        self._on_auto_entry = on_entry
        self._stop.clear()
        self._auto_thread = threading.Thread(target=self._auto_loop, daemon=True)
        self._auto_thread.start()

    def stop_auto(self) -> None:
        self._stop.set()
        thread = self._auto_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._auto_thread = None

    @property
    def auto_running(self) -> bool:
        return self._auto_thread is not None and self._auto_thread.is_alive()

    def _auto_loop(self) -> None:
        while not self._stop.is_set():
            entry = self.record_auto()
            if entry is not None and self._on_auto_entry is not None:
                self._on_auto_entry(entry)
            # In kleinen Schritten warten, damit stop_auto schnell greift
            waited = 0.0
            while waited < self._interval and not self._stop.is_set():
                self._stop.wait(min(1.0, self._interval - waited))
                waited += 1.0
