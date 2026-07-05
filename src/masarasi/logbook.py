"""Logbuch-Dienst: verbindet LiveData, Speicher und Auto-Logging."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from masarasi.livedata import LiveData
from masarasi.nmea import engine_running
from masarasi.storage import LogbookStore, LogEntry, Trip


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
        # Aktiver Törn, dem neue Einträge zugeordnet werden (vom GUI gesetzt)
        self.current_trip_id: Optional[int] = None

    # --- manuelle Einträge --------------------------------------------------

    def add_manual(
        self,
        note: str = "",
        crew: str = "",
        location: str = "",
        include_measurements: bool = True,
        trip_id: Optional[int] = None,
        engine_on: Optional[int] = None,
        mainsail: str = "",
        genoa_percent: Optional[float] = None,
        spinnaker: Optional[int] = None,
        wave_height_m: Optional[float] = None,
        cloud_cover: str = "",
        precipitation: str = "",
        visibility: str = "",
    ) -> LogEntry:
        """Erstellt einen manuellen Eintrag, optional mit aktuellen Messwerten.

        engine_on: None -> automatisch aus NMEA ableiten (falls möglich).
        """
        measurements = self._live.snapshot() if include_measurements else {}
        if engine_on is None:
            engine_on = engine_running(measurements)
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="manual",
            measurements=measurements,
            note=note,
            crew=crew,
            location=location,
            trip_id=trip_id,
            engine_on=engine_on,
            mainsail=mainsail,
            genoa_percent=genoa_percent,
            spinnaker=spinnaker,
            wave_height_m=wave_height_m,
            cloud_cover=cloud_cover,
            precipitation=precipitation,
            visibility=visibility,
        )
        self._store.add(entry)
        return entry

    # --- Törns --------------------------------------------------------------

    def start_trip(self, trip: Trip) -> Trip:
        """Beginnt einen neuen Törn (Startort/Wasser/Diesel/Std/Log)."""
        if not trip.start_dz:
            trip.start_dz = utc_now_iso()
        trip.status = "open"
        self._store.add_trip(trip)
        return trip

    def close_trip(self, trip: Trip) -> Trip:
        """Schließt einen Törn ab (Endwerte setzen, Status = closed)."""
        if not trip.end_dz:
            trip.end_dz = utc_now_iso()
        trip.status = "closed"
        self._store.update_trip(trip)
        return trip

    # --- automatische Einträge ---------------------------------------------

    def record_auto(self, trip_id: Optional[int] = None) -> Optional[LogEntry]:
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
            trip_id=trip_id,
            engine_on=engine_running(measurements),
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
            entry = self.record_auto(trip_id=self.current_trip_id)
            if entry is not None and self._on_auto_entry is not None:
                self._on_auto_entry(entry)
            # In kleinen Schritten warten, damit stop_auto schnell greift
            waited = 0.0
            while waited < self._interval and not self._stop.is_set():
                self._stop.wait(min(1.0, self._interval - waited))
                waited += 1.0
