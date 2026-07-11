"""Logbuch-Dienst: verbindet LiveData, Speicher und Auto-Logging."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from masarasi.autolog import AutoLogEngine, AutoLogSettings
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
        self._engine: Optional[AutoLogEngine] = None
        self._on_auto_entry: Optional[Callable[[LogEntry], None]] = None
        # Zum Ansehen ausgewählter Törn (vom GUI gesetzt): steuert Tabelle,
        # Karte und Export. NICHT maßgeblich für automatische Live-Einträge —
        # die gehen immer in den offenen Törn (siehe open_trip_id()).
        self.current_trip_id: Optional[int] = None
        # Liefert die aktuell in der Maske eingestellten Bedingungen (dict).
        # Wird vom Hauptthread aktuell gehalten; der Auto-Thread liest nur.
        self.conditions_provider: Optional[Callable[[], dict]] = None
        # Optional: liefert einen Plotter-Screenshot als JPEG-Bytes (oder None).
        # Ist es gesetzt, hängt der Auto-Thread jedem Auto-Eintrag das Bild an.
        self.screenshot_provider: Optional[Callable[[], Optional[bytes]]] = None

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

    def open_trip_id(self) -> Optional[int]:
        """ID des aktuell *offenen* Törns (status='open').

        Dahin gehen automatische Live-Einträge (AutoLog, Foto-Import) — egal
        welcher Törn gerade zum Ansehen ausgewählt ist. Gibt None zurück, wenn
        kein Törn offen ist (dann bleiben die Einträge ohne Törn-Zuordnung).
        Bei mehreren offenen Törns wird der neueste genommen.
        """
        trip = self._store.open_trip()
        return trip.id if trip else None

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

    # --- Bedingungen (dauerhafte Maskenwerte) ------------------------------

    @staticmethod
    def _conditions_to_fields(conditions: Optional[dict], measurements: dict) -> dict:
        """Übersetzt die Maskenwerte in LogEntry-Felder inkl. Motor-Ableitung."""
        conditions = conditions or {}
        mode = conditions.get("engine_mode", "automatisch")
        if mode == "ein":
            engine_on = 1
        elif mode == "aus":
            engine_on = 0
        else:
            engine_on = engine_running(measurements)
        return {
            "engine_on": engine_on,
            "mainsail": conditions.get("mainsail", ""),
            "genoa_percent": conditions.get("genoa_percent"),
            "spinnaker": conditions.get("spinnaker"),
            "wave_height_m": conditions.get("wave_height_m"),
            "cloud_cover": conditions.get("cloud_cover", ""),
            "precipitation": conditions.get("precipitation", ""),
            "visibility": conditions.get("visibility", ""),
            "logevent": conditions.get("logevent", ""),
        }

    def add_current(
        self,
        conditions: Optional[dict] = None,
        note: str = "",
        trip_id: Optional[int] = None,
    ) -> LogEntry:
        """Schreibt sofort einen Eintrag mit aktuellen Mess- und Maskenwerten."""
        measurements = self._live.snapshot()
        fields = self._conditions_to_fields(conditions, measurements)
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="manual",
            measurements=measurements,
            note=note or (conditions or {}).get("note", ""),
            trip_id=trip_id,
            **fields,
        )
        self._store.add(entry)
        return entry

    # --- automatische Einträge ---------------------------------------------

    def record_auto(
        self,
        trip_id: Optional[int] = None,
        conditions: Optional[dict] = None,
        reason: Optional[str] = None,
    ) -> Optional[LogEntry]:
        """Schreibt einen Auto-Eintrag aus Snapshot + Maskenwerten.

        `reason` (der AutoLog-Auslöser) wird als Anlass gespeichert.
        Gibt None zurück, wenn (noch) keine Messwerte vorliegen.
        """
        measurements = self._live.snapshot()
        if not measurements:
            return None
        fields = self._conditions_to_fields(conditions, measurements)
        if reason:
            fields["logevent"] = reason
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="auto",
            measurements=measurements,
            trip_id=trip_id,
            note=(conditions or {}).get("note", ""),
            **fields,
        )
        self._store.add(entry)
        return entry

    def record_photo(
        self,
        trip_id: Optional[int] = None,
        conditions: Optional[dict] = None,
        reason: str = "Foto",
    ) -> LogEntry:
        """Erzeugt einen Auto-Eintrag für ein importiertes Foto.

        Anders als record_auto wird immer ein Eintrag angelegt — auch ohne
        Live-Messwerte (das Foto soll nicht verloren gehen)."""
        measurements = self._live.snapshot()
        fields = self._conditions_to_fields(conditions, measurements)
        fields["logevent"] = reason
        entry = LogEntry.from_snapshot(
            timestamp=utc_now_iso(),
            entry_type="auto",
            measurements=measurements,
            trip_id=trip_id,
            note=(conditions or {}).get("note", ""),
            **fields,
        )
        self._store.add(entry)
        return entry

    def _maybe_attach_screenshot(self, entry: LogEntry) -> None:
        """Hängt (falls aktiviert) einen Plotter-Screenshot an den Eintrag."""
        if self.screenshot_provider is None or entry.id is None:
            return
        try:
            jpeg = self.screenshot_provider()
        except Exception:  # noqa: BLE001
            jpeg = None
        if jpeg:
            self._store.set_image(entry.id, jpeg, "image/jpeg", created_dz=utc_now_iso())

    def start_auto(
        self,
        settings: AutoLogSettings,
        on_entry: Optional[Callable[[LogEntry], None]] = None,
    ) -> None:
        """Startet das automatische Logging mit den AutoLog-Auslösern."""
        self.stop_auto()
        self._engine = AutoLogEngine(settings)
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
        self._engine.start(time.time())
        while not self._stop.is_set():
            now = time.time()
            snapshot = self._live.snapshot()
            reason = self._engine.evaluate(snapshot, now)
            if reason:
                conditions = (
                    self.conditions_provider() if self.conditions_provider else None
                )
                entry = self.record_auto(
                    trip_id=self.open_trip_id(), conditions=conditions, reason=reason
                )
                if entry is not None:
                    self._maybe_attach_screenshot(entry)
                    self._engine.note_entry(now, snapshot)
                    if self._on_auto_entry is not None:
                        self._on_auto_entry(entry)
            # alle 2 s prüfen (responsiv für Tiefe/Verzögerung/Kurs)
            self._stop.wait(2.0)
