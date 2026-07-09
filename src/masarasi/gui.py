"""tkinter-GUI für masarasi — das Segel-Logbuch."""

from __future__ import annotations

import datetime
import tkinter as tk
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Deque, Dict, List, Optional

from masarasi import crewlist, timeutil
from masarasi.ais import AisDecoder, AisTargets
from masarasi.config import Config
from masarasi.fields import (
    CLOUD_COVER_LABELS,
    MAINSAIL_OPTIONS,
    PRECIPITATION,
    VISIBILITY_LABELS,
    cloud_hint,
    visibility_hint,
)
from masarasi.livedata import LiveData
from masarasi.logbook import LogbookService, utc_now_iso
from masarasi.nmea import FIELD_LABELS
from masarasi.source import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
    NmeaSource,
)
from masarasi.storage import CrewMember, LogbookStore, Person, Trip
from masarasi.webmap import MapServer

_STATUS_TEXT = {
    STATUS_DISCONNECTED: ("getrennt", "#888888"),
    STATUS_CONNECTING: ("verbinde…", "#c08000"),
    STATUS_CONNECTED: ("verbunden", "#1a8a1a"),
    STATUS_ERROR: ("Fehler", "#c02020"),
}


class Application:
    """Hauptfenster der Anwendung."""

    def __init__(self, root: tk.Tk) -> None:
        self._root = root
        self._config = Config.load()
        self._live = LiveData()
        self._store = LogbookStore(self._config.db_path)
        self._logbook = LogbookService(self._store, self._live)
        # Datenquellen: Definitionen + aktive Verbindungen + Status je Quelle
        self._source_defs = self._load_source_defs()
        self._sources: list = []
        self._src_status: Dict[int, tuple] = {}

        # AIS: gemeinsame Zielliste, je Quelle ein Decoder (Mehrteiler pro Kanal)
        self._ais_targets = AisTargets()
        self._ais_decoders: list = []
        # Lokaler Webserver für die AIS-Karte (Leaflet + OpenFreeMap)
        self._map_server: Optional[MapServer] = None

        self._value_labels: Dict[str, tk.Label] = {}
        # Ringpuffer für die Rohdaten-Anzeige (Thread-sicher via deque.append)
        self._raw_buffer: Deque[str] = deque(maxlen=500)
        self._raw_window: Optional["_RawMonitor"] = None
        # Törn-Auswahl: Anzeigetext -> Trip-ID (None = keinem Törn zugeordnet)
        self._trip_choices: Dict[str, Optional[int]] = {}

        root.title("masarasi — Segel-Logbuch")
        root.geometry("1180x640")
        root.minsize(1000, 560)

        self._build_ui()
        self._refresh_trips()
        self._refresh_logbook()
        self._schedule_live_update()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # --- UI-Aufbau ----------------------------------------------------------

    def _build_ui(self) -> None:
        pad = dict(padx=8, pady=4)

        # Kopfzeile: Datenquellen (mehrere gleichzeitig möglich)
        top = ttk.LabelFrame(self._root, text="Datenquellen")
        top.pack(fill="x", **pad)

        self._connect_btn = ttk.Button(top, text="Verbinden", command=self._on_connect_all)
        self._connect_btn.grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(top, text="Quellen…", command=self._on_manage_sources).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(top, text="Rohdaten…", command=self._on_show_raw).grid(
            row=0, column=2, padx=4
        )
        self._status_label = tk.Label(top, text="getrennt", fg="#888888")
        self._status_label.grid(row=0, column=3, padx=8)
        self._sources_label = ttk.Label(top, text="", foreground="#555")
        self._sources_label.grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 4))
        self._update_sources_label()

        # Törn-Leiste
        trip_bar = ttk.LabelFrame(self._root, text="Törn")
        trip_bar.pack(fill="x", **pad)
        ttk.Label(trip_bar, text="Aktiver Törn:").grid(row=0, column=0, sticky="e", padx=4, pady=6)
        self._trip_var = tk.StringVar()
        self._trip_combo = ttk.Combobox(
            trip_bar, textvariable=self._trip_var, width=42, state="readonly"
        )
        self._trip_combo.grid(row=0, column=1, padx=4)
        self._trip_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_trip_selected())
        ttk.Button(trip_bar, text="Neuer Törn…", command=self._on_new_trip).grid(
            row=0, column=2, padx=6
        )
        self._close_trip_btn = ttk.Button(
            trip_bar, text="Törn abschließen…", command=self._on_close_trip
        )
        self._close_trip_btn.grid(row=0, column=3, padx=4)
        ttk.Button(trip_bar, text="Crewliste…", command=self._on_crewlist).grid(
            row=0, column=4, padx=4
        )
        self._trip_dist_label = ttk.Label(
            trip_bar, text="", foreground="#1a5a8a", font=("TkDefaultFont", 10, "bold")
        )
        self._trip_dist_label.grid(row=0, column=5, padx=12)

        # Hauptzeile: Messwerte | Bedingungen nebeneinander
        main_row = ttk.Frame(self._root)
        main_row.pack(fill="x", **pad)

        # Messwerte kompakt (zwei Spalten, je Zeile "Label  Wert Einheit")
        dash = ttk.LabelFrame(main_row, text="Messwerte")
        dash.pack(side="left", fill="y")
        per_col = (len(FIELD_LABELS) + 1) // 2
        for index, (key, label, _unit) in enumerate(FIELD_LABELS):
            row = index % per_col
            base = (index // per_col) * 2
            ttk.Label(dash, text=label, foreground="#666").grid(
                row=row, column=base, sticky="e", padx=(8, 3), pady=1
            )
            value = tk.Label(dash, text="—", font=("TkDefaultFont", 10, "bold"),
                             width=11, anchor="w")
            value.grid(row=row, column=base + 1, sticky="w", padx=(0, 10), pady=1)
            self._value_labels[key] = value

        cond = ttk.LabelFrame(main_row, text="Bedingungen (bei jedem Log mitgeschrieben)")
        cond.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_conditions(cond)

        # Logging-Steuerung
        controls = ttk.LabelFrame(self._root, text="Logbuch")
        controls.pack(fill="x", **pad)

        ttk.Label(controls, text="Auto-Intervall (Sek.):").grid(
            row=0, column=0, sticky="e", padx=4, pady=6
        )
        self._interval_var = tk.StringVar(value=str(self._config.auto_interval_seconds))
        ttk.Entry(controls, textvariable=self._interval_var, width=8).grid(
            row=0, column=1, padx=4
        )
        self._auto_btn = ttk.Button(
            controls, text="Auto-Logging starten", command=self._on_toggle_auto
        )
        self._auto_btn.grid(row=0, column=2, padx=8)

        ttk.Button(
            controls, text="✎ Eintrag speichern", command=self._on_save_entry
        ).grid(row=0, column=3, padx=8)

        ttk.Button(controls, text="CSV exportieren", command=self._on_export_csv).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(controls, text="GPX exportieren", command=self._on_export_gpx).grid(
            row=0, column=5, padx=4
        )
        ttk.Button(controls, text="🗺 AIS-Karte", command=self._on_open_map).grid(
            row=0, column=6, padx=4
        )

        ttk.Label(controls, text="Zeitzone:").grid(row=0, column=7, sticky="e", padx=(16, 2))
        self._tz_var = tk.StringVar(value=self._tz_current_choice())
        tz = ttk.Combobox(
            controls, textvariable=self._tz_var, width=12, state="readonly",
            values=["System", "UTC", "UTC+1", "UTC+2", "UTC+3", "UTC+4", "UTC-1", "UTC-2"],
        )
        tz.grid(row=0, column=8, padx=2)
        tz.bind("<<ComboboxSelected>>", lambda _e: self._on_tz_change())

        # Logbuch-Tabelle
        table_frame = ttk.Frame(self._root)
        table_frame.pack(fill="both", expand=True, **pad)

        cols = ("time", "ed", "anlass", "type", "pos", "sog", "wind", "depth",
                "motor", "segel", "note")
        headers = {
            "time": "Zeit", "ed": "✎", "anlass": "Anlass", "type": "Typ",
            "pos": "Position", "sog": "SOG", "wind": "Wind", "depth": "Tiefe",
            "motor": "Motor", "segel": "Segel", "note": "Notiz",
        }
        widths = {
            "time": 145, "ed": 26, "anlass": 100, "type": 58, "pos": 140, "sog": 48,
            "wind": 95, "depth": 52, "motor": 46, "segel": 120, "note": 150,
        }
        self._tree = ttk.Treeview(table_frame, columns=cols, show="headings")
        for col in cols:
            self._tree.heading(col, text=headers[col])
            self._tree.column(col, width=widths[col], anchor="w")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._tree.bind("<Delete>", lambda _e: self._on_delete_entry())
        self._tree.bind("<Double-1>", lambda _e: self._on_edit_entry())

        bottom = ttk.Frame(self._root)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Bearbeiten…", command=self._on_edit_entry).pack(side="left")
        ttk.Button(bottom, text="Eintrag löschen", command=self._on_delete_entry).pack(
            side="left", padx=4
        )
        ttk.Label(bottom, text="(Doppelklick = bearbeiten)", foreground="#999").pack(
            side="left", padx=8
        )
        self._count_label = ttk.Label(bottom, text="")
        self._count_label.pack(side="right")

    # --- Bedingungs-Panel (dauerhafte Maskenwerte) -------------------------

    def _build_conditions(self, parent: ttk.LabelFrame) -> None:
        self._cond_vars: Dict[str, tk.Variable] = {}
        # In zwei Spalten anordnen, damit die Maske flach bleibt.
        self._row = 0
        per_col = 5

        def add(label, widget):
            i = self._row
            r = i % per_col
            base = (i // per_col) * 2
            ttk.Label(parent, text=label).grid(
                row=r, column=base, sticky="e", padx=(6, 3), pady=2
            )
            widget.grid(row=r, column=base + 1, sticky="w", padx=(0, 8), pady=2)
            self._row += 1

        self._cond_vars["logevent"] = tk.StringVar(value="Routineeintrag")
        add("Anlass:", ttk.Combobox(
            parent, textvariable=self._cond_vars["logevent"], width=18,
            values=["Routineeintrag", "Wache", "Manöver", "Hafen", "Ankern", "Besonderes"],
        ))
        self._cond_vars["engine_mode"] = tk.StringVar(value="automatisch")
        add("Motor:", ttk.Combobox(
            parent, textvariable=self._cond_vars["engine_mode"], width=18,
            state="readonly", values=["automatisch", "ein", "aus"],
        ))
        self._cond_vars["mainsail"] = tk.StringVar(value="—")
        add("Großsegel:", ttk.Combobox(
            parent, textvariable=self._cond_vars["mainsail"], width=18,
            state="readonly", values=MAINSAIL_OPTIONS,
        ))
        self._cond_vars["genoa"] = tk.StringVar()
        add("Genua %:", ttk.Spinbox(
            parent, from_=0, to=100, textvariable=self._cond_vars["genoa"], width=8,
        ))
        self._cond_vars["spinnaker"] = tk.BooleanVar(value=False)
        add("Spinnaker:", ttk.Checkbutton(
            parent, text="gesetzt", variable=self._cond_vars["spinnaker"],
        ))
        self._cond_vars["cloud"] = tk.StringVar(value="—")
        add("Bewölkung:", ttk.Combobox(
            parent, textvariable=self._cond_vars["cloud"], width=18,
            state="readonly", values=CLOUD_COVER_LABELS,
        ))
        self._cond_vars["precip"] = tk.StringVar(value="kein")
        add("Niederschlag:", ttk.Combobox(
            parent, textvariable=self._cond_vars["precip"], width=18,
            state="readonly", values=PRECIPITATION,
        ))
        self._cond_vars["visibility"] = tk.StringVar(value="—")
        add("Sicht:", ttk.Combobox(
            parent, textvariable=self._cond_vars["visibility"], width=18,
            state="readonly", values=VISIBILITY_LABELS,
        ))
        self._cond_vars["wave"] = tk.StringVar()
        add("Seegang (m):", ttk.Entry(
            parent, textvariable=self._cond_vars["wave"], width=10,
        ))
        self._cond_vars["note"] = tk.StringVar()
        add("Bemerkung:", ttk.Entry(
            parent, textvariable=self._cond_vars["note"], width=20,
        ))

        # Änderungen sofort in den Thread-sicheren Cache übernehmen,
        # damit auch der Auto-Log-Thread die aktuellen Werte sieht.
        for var in self._cond_vars.values():
            var.trace_add("write", lambda *_: self._sync_conditions())
        self._sync_conditions()
        self._logbook.conditions_provider = lambda: dict(self._condition_values)

    def _sync_conditions(self) -> None:
        v = self._cond_vars
        self._condition_values = {
            "engine_mode": v["engine_mode"].get(),
            "mainsail": v["mainsail"].get() if v["mainsail"].get() != "—" else "",
            "genoa_percent": _parse_float(v["genoa"].get()),
            "spinnaker": 1 if v["spinnaker"].get() else 0,
            "wave_height_m": _parse_float(v["wave"].get()),
            "cloud_cover": v["cloud"].get() if v["cloud"].get() != "—" else "",
            "precipitation": v["precip"].get() if v["precip"].get() != "kein" else "",
            "visibility": v["visibility"].get() if v["visibility"].get() != "—" else "",
            "logevent": v["logevent"].get().strip(),
            "note": v["note"].get().strip(),
        }

    def _on_save_entry(self) -> None:
        self._sync_conditions()
        self._logbook.add_current(
            conditions=self._condition_values,
            note=self._condition_values.get("note", ""),
            trip_id=self._logbook.current_trip_id,
        )
        self._refresh_logbook()

    # --- AIS-Karte (Leaflet + OpenFreeMap) ---------------------------------

    def _make_ais_decoder(self):
        """Erzeugt einen AIS-Decoder je Quelle und merkt ihn sich."""
        decoder = AisDecoder(self._ais_targets)
        self._ais_decoders.append(decoder)
        return decoder.add_sentence

    def _ais_info(self) -> str:
        """Hinweistext für die Karte (z.B. aktive COG-Korrektur)."""
        if any(getattr(d, "cog_mode", "") == "whole" for d in self._ais_decoders):
            return "COG-Korrektur aktiv: Feed liefert ganze Grad statt Zehntel."
        return ""

    def _on_open_map(self) -> None:
        """Startet (falls nötig) den lokalen Kartenserver und öffnet den Browser."""
        if self._map_server is None:
            try:
                self._map_server = MapServer(
                    own_provider=self._map_own,
                    targets_provider=lambda: self._ais_targets.all(),
                    track_provider=self._map_track,
                    info_provider=self._ais_info,
                )
                self._map_server.start()
            except OSError as exc:
                self._map_server = None
                messagebox.showerror(
                    "AIS-Karte", f"Kartenserver konnte nicht starten:\n{exc}"
                )
                return
        webbrowser.open(self._map_server.url)

    def _map_own(self) -> Optional[Dict]:
        """Eigene Schiffsposition aus den Live-Daten für die Karte."""
        snap = self._live.snapshot()
        if snap.get("lat") is None or snap.get("lon") is None:
            return None
        return {
            "lat": snap.get("lat"),
            "lon": snap.get("lon"),
            "cog": snap.get("cog_deg"),
            "heading": snap.get("hdg_true_deg", snap.get("hdg_mag_deg")),
            "sog": snap.get("sog_kn"),
        }

    def _map_track(self) -> Dict:
        """Track des ausgewählten Törns (älteste zuerst) für die Karte."""
        trip_id = self._logbook.current_trip_id
        points: List[List[float]] = []
        for entry in self._store.all(newest_first=False, trip_id=trip_id, limit=20000):
            if entry.lat is not None and entry.lon is not None:
                points.append([entry.lat, entry.lon])
        name = ""
        if trip_id is not None:
            trip = self._store.get_trip(trip_id)
            if trip is not None:
                name = trip.name or trip.start_location or f"Törn #{trip_id}"
        return {"name": name, "points": points}

    # --- Verbindung ---------------------------------------------------------

    def _load_source_defs(self) -> list:
        """Quellen-Definitionen aus der Konfiguration (mit Abwärtskompatibilität)."""
        defs = self._config.sources
        if defs:
            return [dict(d) for d in defs]
        # Einzelquelle aus der alten Konfiguration übernehmen
        return [{
            "host": self._config.gateway_host,
            "port": self._config.gateway_port,
            "protocol": self._config.protocol,
        }]

    @property
    def _connected(self) -> bool:
        return bool(self._sources)

    def _on_connect_all(self) -> None:
        if self._connected:
            for src in self._sources:
                src.stop()
            self._sources = []
            self._ais_decoders = []
            self._src_status = {}
            self._connect_btn.config(text="Verbinden")
            self._update_sources_label()
            return
        if not self._source_defs:
            messagebox.showinfo("Quellen", "Bitte zuerst über 'Quellen…' eine Datenquelle anlegen.")
            return
        self._ais_decoders = []
        for index, definition in enumerate(self._source_defs):
            try:
                port = int(definition["port"])
            except (ValueError, KeyError, TypeError):
                messagebox.showerror("Quelle", f"Ungültiger Port bei Quelle {index + 1}.")
                continue
            source = NmeaSource(
                host=str(definition["host"]).strip(),
                port=port,
                live=self._live,
                protocol=definition.get("protocol", "tcp"),
                on_status=self._make_status_cb(index),
                on_raw=self._raw_buffer.append,
                # Eigener Decoder je Quelle (Mehrteiler werden pro Kanal
                # zusammengesetzt), gemeinsame Zielliste.
                on_ais=self._make_ais_decoder(),
            )
            source.start()
            self._sources.append(source)
        self._connect_btn.config(text="Trennen")
        self._update_sources_label()

    def _make_status_cb(self, index: int):
        # Läuft im Quellen-Thread -> KEINE tkinter-Aufrufe hier, nur Dict schreiben.
        # Die Anzeige aktualisiert der periodische GUI-Timer (_schedule_live_update).
        def cb(status: str, message: str) -> None:
            self._src_status[index] = (status, message)
        return cb

    def _update_sources_label(self) -> None:
        parts = []
        connected = 0
        for index, d in enumerate(self._source_defs):
            status = self._src_status.get(index, (STATUS_DISCONNECTED, ""))[0]
            mark = {
                STATUS_CONNECTED: "✓", STATUS_CONNECTING: "…",
                STATUS_ERROR: "✗", STATUS_DISCONNECTED: "·",
            }.get(status, "·")
            if status == STATUS_CONNECTED:
                connected += 1
            proto = d.get("protocol", "tcp")
            parts.append(f"{mark} {proto} {d.get('host')}:{d.get('port')}")
        self._sources_label.config(text="   ".join(parts) if parts else "keine Quellen")
        if not self._connected:
            self._status_label.config(text="getrennt", fg="#888888")
        else:
            total = len(self._sources)
            color = "#1a8a1a" if connected == total else "#c08000"
            self._status_label.config(text=f"{connected}/{total} verbunden", fg=color)

    def _on_manage_sources(self) -> None:
        dialog = _SourcesDialog(self._root, self._source_defs)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._source_defs = dialog.result
        self._config.sources = self._source_defs
        self._config.save()
        if self._connected:
            messagebox.showinfo(
                "Quellen", "Geänderte Quellen werden beim nächsten 'Verbinden' aktiv."
            )
        self._src_status = {}
        self._update_sources_label()

    def _on_show_raw(self) -> None:
        """Öffnet das Fenster mit den rohen NMEA-Sätzen."""
        if self._raw_window is not None and self._raw_window.alive():
            self._raw_window.lift()
            return
        self._raw_window = _RawMonitor(self._root, self._raw_buffer)

    # --- Live-Anzeige -------------------------------------------------------

    def _schedule_live_update(self) -> None:
        self._update_live_labels()
        self._update_sources_label()  # Quellen-Status im GUI-Thread aktualisieren
        self._root.after(1000, self._schedule_live_update)

    def _update_live_labels(self) -> None:
        snapshot = self._live.snapshot()
        for key, _label, unit in FIELD_LABELS:
            value = snapshot.get(key)
            if value is None:
                self._value_labels[key].config(text="—", fg="#bbbbbb")
            else:
                text = f"{value:.5f}" if key in ("lat", "lon") else f"{value:.1f}"
                self._value_labels[key].config(text=f"{text} {unit}", fg="#111111")
        self._update_trip_distance(snapshot)

    def _update_trip_distance(self, snapshot) -> None:
        """Strecke im aktiven Törn = Gesamtlog − Log-Stand bei Törnbeginn."""
        start = getattr(self, "_active_trip_start_log", None)
        total = snapshot.get("log_total_nm")
        if self._logbook.current_trip_id is None:
            self._trip_dist_label.config(text="")
        elif start is None:
            self._trip_dist_label.config(text="Strecke Törn: — (kein Start-Log)")
        elif total is None:
            self._trip_dist_label.config(text="Strecke Törn: … (kein Log-Signal)")
        else:
            self._trip_dist_label.config(text=f"Strecke Törn: {max(0.0, total - start):.1f} NM")

    # --- Auto-Logging -------------------------------------------------------

    def _on_toggle_auto(self) -> None:
        if self._logbook.auto_running:
            self._logbook.stop_auto()
            self._auto_btn.config(text="Auto-Logging starten")
            return
        try:
            interval = int(self._interval_var.get())
        except ValueError:
            messagebox.showerror("Ungültig", "Intervall muss eine Zahl (Sekunden) sein.")
            return
        self._config.auto_interval_seconds = interval
        self._config.save()
        self._logbook.start_auto(interval, on_entry=self._on_auto_entry)
        self._auto_btn.config(text="Auto-Logging stoppen")

    def _on_auto_entry(self, entry) -> None:
        # Läuft im Auto-Log-Thread: die Tabelle im GUI-Thread aktualisieren.
        self._root.after(0, self._refresh_logbook)

    # --- Törns --------------------------------------------------------------

    def _refresh_trips(self) -> None:
        trips = self._store.all_trips(newest_first=True)
        self._trip_choices = {"— (kein Törn)": None}
        open_display = None
        for t in trips:
            route = f"{t.start_location or '?'} → {t.end_location or '…'}"
            status = "offen" if t.status == "open" else "abgeschlossen"
            disp = f"#{t.id} {t.name or route}  [{status}]"
            self._trip_choices[disp] = t.id
            if t.status == "open" and open_display is None:
                open_display = disp
        self._trip_combo["values"] = list(self._trip_choices.keys())

        active_id = self._logbook.current_trip_id
        if active_id is None and open_display is not None:
            self._trip_var.set(open_display)
            self._logbook.current_trip_id = self._trip_choices[open_display]
        else:
            disp = next(
                (d for d, i in self._trip_choices.items() if i == active_id),
                "— (kein Törn)",
            )
            self._trip_var.set(disp)
        self._update_close_button()

    def _update_close_button(self) -> None:
        tid = self._logbook.current_trip_id
        trip = self._store.get_trip(tid) if tid else None
        self._close_trip_btn.config(
            state="normal" if trip and trip.status == "open" else "disabled"
        )
        # Log-Stand bei Törnbeginn cachen (für die Strecke im Törn)
        self._active_trip_start_log = trip.start_log_nm if trip else None

    def _on_trip_selected(self) -> None:
        self._logbook.current_trip_id = self._trip_choices.get(self._trip_var.get())
        self._update_close_button()  # aktualisiert auch den Törn-Start-Log-Cache
        self._refresh_logbook()

    def _on_new_trip(self) -> None:
        dialog = _TripStartDialog(self._root, self._live.snapshot())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        trip = self._logbook.start_trip(Trip(**dialog.result))
        self._logbook.current_trip_id = trip.id
        self._refresh_trips()
        self._refresh_logbook()

    def _on_close_trip(self) -> None:
        tid = self._logbook.current_trip_id
        trip = self._store.get_trip(tid) if tid else None
        if trip is None or trip.status != "open":
            return
        dialog = _TripCloseDialog(self._root, trip, self._live.snapshot())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        for key, value in dialog.result.items():
            setattr(trip, key, value)
        self._logbook.close_trip(trip)
        self._refresh_trips()
        self._refresh_logbook()

    # --- Crewliste ----------------------------------------------------------

    def _on_crewlist(self) -> None:
        trip = None
        tid = self._logbook.current_trip_id
        if tid is not None:
            trip = self._store.get_trip(tid)
        dialog = _CrewListDialog(self._root, self._config, self._store, trip)
        self._root.wait_window(dialog.top)

    # --- manuelle Einträge --------------------------------------------------

    def _on_manual_entry(self) -> None:
        dialog = _ManualEntryDialog(self._root, self._live.snapshot())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        r = dialog.result
        self._logbook.add_manual(
            note=r["note"],
            crew=r["crew"],
            location=r["location"],
            trip_id=self._logbook.current_trip_id,
            engine_on=r["engine_on"],
            mainsail=r["mainsail"],
            genoa_percent=r["genoa_percent"],
            spinnaker=r["spinnaker"],
            wave_height_m=r["wave_height_m"],
            cloud_cover=r["cloud_cover"],
            precipitation=r["precipitation"],
            visibility=r["visibility"],
        )
        self._refresh_logbook()

    # --- Zeitzone -----------------------------------------------------------

    def _tz_offset(self) -> float:
        return timeutil.effective_offset(
            self._config.timezone_mode, self._config.timezone_offset_hours
        )

    def _tz_current_choice(self) -> str:
        if self._config.timezone_mode == "system":
            return "System"
        h = self._config.timezone_offset_hours
        if h == 0:
            return "UTC"
        return f"UTC{'+' if h >= 0 else '-'}{int(abs(h))}"

    def _on_tz_change(self) -> None:
        choice = self._tz_var.get()
        if choice == "System":
            self._config.timezone_mode = "system"
        else:
            self._config.timezone_mode = "fixed"
            self._config.timezone_offset_hours = 0.0 if choice == "UTC" else float(
                choice.replace("UTC", "")
            )
        self._config.save()
        self._refresh_logbook()

    # --- Logbuch-Tabelle ----------------------------------------------------

    def _refresh_logbook(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        trip_id = self._logbook.current_trip_id
        offset = self._tz_offset()
        self._tree.heading("time", text=f"Zeit ({timeutil.label(self._config.timezone_mode, self._config.timezone_offset_hours)})")
        for entry in self._store.all(limit=5000, newest_first=True, trip_id=trip_id):
            pos = ""
            if entry.lat is not None and entry.lon is not None:
                pos = f"{entry.lat:.4f}, {entry.lon:.4f}"
            wind = ""
            if entry.aws_kn is not None:
                wind = f"{entry.aws_kn:.0f}kn @ {entry.awa_deg or 0:.0f}°"
            motor = "ein" if entry.engine_on == 1 else ("aus" if entry.engine_on == 0 else "")
            sail_parts = []
            if entry.mainsail and entry.mainsail != "—":
                sail_parts.append(entry.mainsail)
            if entry.genoa_percent is not None:
                sail_parts.append(f"Genua {entry.genoa_percent:.0f}%")
            if entry.spinnaker:
                sail_parts.append("Spi")
            self._tree.insert(
                "", "end", iid=str(entry.id),
                values=(
                    timeutil.to_display(entry.timestamp, offset),
                    "✎" if entry.edited == 1 else "",
                    entry.logevent,
                    entry.entry_type,
                    pos,
                    "" if entry.sog_kn is None else f"{entry.sog_kn:.1f}",
                    wind,
                    "" if entry.depth_m is None else f"{entry.depth_m:.1f}",
                    motor,
                    ", ".join(sail_parts),
                    entry.note,
                ),
            )
        total = self._store.count(trip_id=trip_id)
        scope = "im Törn" if trip_id else "gesamt"
        self._count_label.config(text=f"{total} Einträge ({scope})")

    def _on_delete_entry(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        if not messagebox.askyesno("Löschen", "Ausgewählte Einträge löschen?"):
            return
        for iid in selection:
            self._store.delete(int(iid))
        self._refresh_logbook()

    def _on_edit_entry(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        entry = self._store.get(int(selection[0]))
        if entry is None:
            return
        offset = self._tz_offset()
        ts_display = timeutil.to_display(entry.timestamp, offset)
        dialog = _EditEntryDialog(self._root, entry, ts_display)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        result = dict(dialog.result)
        new_ts = timeutil.from_display(result.pop("timestamp", ""), offset)
        if new_ts:
            entry.timestamp = new_ts
        for key, value in result.items():
            setattr(entry, key, value)
        entry.edited = 1
        entry.edited_dz = utc_now_iso()
        self._store.update(entry)
        self._refresh_logbook()

    # --- Export -------------------------------------------------------------

    def _on_export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="logbuch.csv",
        )
        if not path:
            return
        count = self._store.export_csv(path, trip_id=self._logbook.current_trip_id)
        messagebox.showinfo("Export", f"{count} Einträge nach CSV exportiert.")

    def _on_export_gpx(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".gpx", filetypes=[("GPX", "*.gpx")],
            initialfile="toern.gpx",
        )
        if not path:
            return
        count = self._store.export_gpx(path, trip_id=self._logbook.current_trip_id)
        messagebox.showinfo("Export", f"{count} Positionspunkte nach GPX exportiert.")

    # --- Schließen ----------------------------------------------------------

    def _on_close(self) -> None:
        self._logbook.stop_auto()
        for src in self._sources:
            src.stop()
        if self._map_server is not None:
            self._map_server.stop()
        self._root.destroy()


def _parse_float(text: str) -> Optional[float]:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class _EditEntryDialog:
    """Dialog zum Bearbeiten eines bestehenden Logbuch-Eintrags.

    Messwerte (Position, Wind …) werden nur informativ angezeigt; geändert
    werden die manuellen Felder, Zeit und Notiz.
    """

    _ENGINE = {None: "—", 1: "ein", 0: "aus"}

    def __init__(self, parent: tk.Tk, entry, ts_display: str = "") -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(f"Eintrag bearbeiten (#{entry.id})")
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        info = []
        if entry.lat is not None and entry.lon is not None:
            info.append(f"Pos {entry.lat:.4f}, {entry.lon:.4f}")
        if entry.sog_kn is not None:
            info.append(f"SOG {entry.sog_kn:.1f} kn")
        if entry.depth_m is not None:
            info.append(f"Tiefe {entry.depth_m:.1f} m")
        info.append(f"Typ: {entry.entry_type}")
        ttk.Label(frame, text="  ·  ".join(info),
                  foreground="#555").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

        r = 1

        def lab(text, row, col=0):
            ttk.Label(frame, text=text).grid(row=row, column=col, sticky="e", padx=4, pady=2)

        lab("Zeit (lokal):", r)
        self._ts = tk.StringVar(value=ts_display or entry.timestamp)
        ttk.Entry(frame, textvariable=self._ts, width=24).grid(row=r, column=1, sticky="w")
        lab("Anlass:", r, 2)
        self._logevent = tk.StringVar(value=entry.logevent)
        ttk.Combobox(frame, textvariable=self._logevent, width=18,
                     values=["Routineeintrag", "Wache", "Manöver", "Hafen", "Ankern",
                             "Besonderes"]).grid(row=r, column=3, sticky="w")
        r += 1

        lab("Motor:", r)
        self._engine = tk.StringVar(value=self._ENGINE.get(entry.engine_on, "—"))
        ttk.Combobox(frame, textvariable=self._engine, width=18, state="readonly",
                     values=["—", "ein", "aus"]).grid(row=r, column=1, sticky="w")
        lab("Großsegel:", r, 2)
        self._mainsail = tk.StringVar(value=entry.mainsail or "—")
        ttk.Combobox(frame, textvariable=self._mainsail, width=18, state="readonly",
                     values=MAINSAIL_OPTIONS).grid(row=r, column=3, sticky="w")
        r += 1

        lab("Genua %:", r)
        self._genoa = tk.StringVar(value="" if entry.genoa_percent is None else f"{entry.genoa_percent:g}")
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._genoa, width=8).grid(
            row=r, column=1, sticky="w")
        lab("Spinnaker:", r, 2)
        self._spinnaker = tk.BooleanVar(value=bool(entry.spinnaker))
        ttk.Checkbutton(frame, text="gesetzt", variable=self._spinnaker).grid(
            row=r, column=3, sticky="w")
        r += 1

        lab("Bewölkung:", r)
        self._cloud = tk.StringVar(value=entry.cloud_cover or "—")
        ttk.Combobox(frame, textvariable=self._cloud, width=18, state="readonly",
                     values=CLOUD_COVER_LABELS).grid(row=r, column=1, sticky="w")
        lab("Niederschlag:", r, 2)
        self._precip = tk.StringVar(value=entry.precipitation or "kein")
        ttk.Combobox(frame, textvariable=self._precip, width=18, state="readonly",
                     values=PRECIPITATION).grid(row=r, column=3, sticky="w")
        r += 1

        lab("Sicht:", r)
        self._visibility = tk.StringVar(value=entry.visibility or "—")
        ttk.Combobox(frame, textvariable=self._visibility, width=18, state="readonly",
                     values=VISIBILITY_LABELS).grid(row=r, column=1, sticky="w")
        lab("Seegang (m):", r, 2)
        self._wave = tk.StringVar(value="" if entry.wave_height_m is None else f"{entry.wave_height_m:g}")
        ttk.Entry(frame, textvariable=self._wave, width=10).grid(row=r, column=3, sticky="w")
        r += 1

        lab("Ort / Hafen:", r)
        self._location = tk.StringVar(value=entry.location)
        ttk.Entry(frame, textvariable=self._location, width=24).grid(row=r, column=1, sticky="w")
        lab("Crew:", r, 2)
        self._crew = tk.StringVar(value=entry.crew)
        ttk.Entry(frame, textvariable=self._crew, width=18).grid(row=r, column=3, sticky="w")
        r += 1

        ttk.Label(frame, text="Notiz:").grid(row=r, column=0, sticky="ne", padx=4, pady=2)
        self._note = tk.Text(frame, width=52, height=4)
        self._note.insert("1.0", entry.note or "")
        self._note.grid(row=r, column=1, columnspan=3, sticky="w", pady=2)
        r += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=r, column=0, columnspan=4, pady=(10, 0))
        ttk.Button(buttons, text="Speichern", command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        engine_map = {"—": None, "ein": 1, "aus": 0}
        self.result = {
            "timestamp": self._ts.get().strip(),
            "logevent": self._logevent.get().strip(),
            "engine_on": engine_map.get(self._engine.get()),
            "mainsail": self._mainsail.get() if self._mainsail.get() != "—" else "",
            "genoa_percent": _parse_float(self._genoa.get()),
            "spinnaker": 1 if self._spinnaker.get() else 0,
            "cloud_cover": self._cloud.get() if self._cloud.get() != "—" else "",
            "precipitation": self._precip.get() if self._precip.get() != "kein" else "",
            "visibility": self._visibility.get() if self._visibility.get() != "—" else "",
            "wave_height_m": _parse_float(self._wave.get()),
            "location": self._location.get().strip(),
            "crew": self._crew.get().strip(),
            "note": self._note.get("1.0", "end").strip(),
        }
        self.top.destroy()


class _ManualEntryDialog:
    """Dialog für einen manuellen Logbuch-Eintrag mit Auto-Fill."""

    def __init__(self, parent: tk.Tk, snapshot: Dict[str, float]) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title("Manueller Eintrag")
        self.top.transient(parent)
        self.top.grab_set()

        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        parts = []
        if "lat" in snapshot and "lon" in snapshot:
            parts.append(f"Pos {snapshot['lat']:.4f}, {snapshot['lon']:.4f}")
        if "sog_kn" in snapshot:
            parts.append(f"SOG {snapshot['sog_kn']:.1f}kn")
        if "aws_kn" in snapshot:
            parts.append(f"Wind {snapshot['aws_kn']:.0f}kn")
        if snapshot.get("engine_rpm") is not None:
            parts.append(f"Motor {snapshot['engine_rpm']:.0f} U/min")
        info = " · ".join(parts) if parts else "Keine Live-Messwerte (nur Text wird gespeichert)."
        ttk.Label(frame, text=info, foreground="#555").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )

        row = 1

        def add_label(text, r, c=0):
            ttk.Label(frame, text=text).grid(row=r, column=c, sticky="e", padx=4, pady=3)

        # Ort / Crew
        add_label("Ort / Hafen:", row)
        self._location = tk.StringVar()
        ttk.Entry(frame, textvariable=self._location, width=28).grid(row=row, column=1, pady=3, sticky="w")
        add_label("Crew:", row, 2)
        self._crew = tk.StringVar()
        ttk.Entry(frame, textvariable=self._crew, width=22).grid(row=row, column=3, pady=3, sticky="w")
        row += 1

        # Motor
        add_label("Motor:", row)
        auto = snapshot.get("engine_rpm")
        auto_hint = ""
        if auto is not None:
            auto_hint = f" (erkannt: {'ein' if auto > 0 else 'aus'})"
        self._engine = tk.StringVar(value="automatisch")
        ttk.Combobox(
            frame, textvariable=self._engine, width=25, state="readonly",
            values=["automatisch", "ein", "aus"],
        ).grid(row=row, column=1, pady=3, sticky="w")
        ttk.Label(frame, text=auto_hint, foreground="#888").grid(row=row, column=2, columnspan=2, sticky="w")
        row += 1

        # Großsegel / Genua / Spinnaker
        add_label("Großsegel:", row)
        self._mainsail = tk.StringVar(value="—")
        ttk.Combobox(
            frame, textvariable=self._mainsail, width=25, state="readonly",
            values=MAINSAIL_OPTIONS,
        ).grid(row=row, column=1, pady=3, sticky="w")
        add_label("Genua %:", row, 2)
        self._genoa = tk.StringVar()
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._genoa, width=8).grid(
            row=row, column=3, pady=3, sticky="w"
        )
        row += 1

        add_label("Spinnaker:", row)
        self._spinnaker = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="gesetzt", variable=self._spinnaker).grid(
            row=row, column=1, sticky="w", pady=3
        )
        row += 1

        # Wetter
        add_label("Wellenhöhe (m):", row)
        self._wave = tk.StringVar()
        ttk.Entry(frame, textvariable=self._wave, width=10).grid(row=row, column=1, pady=3, sticky="w")
        row += 1

        add_label("Bewölkung:", row)
        self._cloud = tk.StringVar(value="—")
        self._cloud_combo = ttk.Combobox(
            frame, textvariable=self._cloud, width=25, state="readonly",
            values=CLOUD_COVER_LABELS,
        )
        self._cloud_combo.grid(row=row, column=1, pady=3, sticky="w")
        self._cloud_hint = ttk.Label(frame, text="", foreground="#888")
        self._cloud_hint.grid(row=row, column=2, columnspan=2, sticky="w")
        self._cloud_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._cloud_hint.config(text=cloud_hint(self._cloud.get())),
        )
        row += 1

        add_label("Niederschlag:", row)
        self._precip = tk.StringVar(value="kein")
        ttk.Combobox(
            frame, textvariable=self._precip, width=25, state="readonly",
            values=PRECIPITATION,
        ).grid(row=row, column=1, pady=3, sticky="w")
        row += 1

        add_label("Sicht:", row)
        self._visibility = tk.StringVar(value="—")
        self._vis_combo = ttk.Combobox(
            frame, textvariable=self._visibility, width=25, state="readonly",
            values=VISIBILITY_LABELS,
        )
        self._vis_combo.grid(row=row, column=1, pady=3, sticky="w")
        self._vis_hint = ttk.Label(frame, text="", foreground="#888")
        self._vis_hint.grid(row=row, column=2, columnspan=2, sticky="w")
        self._vis_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._vis_hint.config(text=visibility_hint(self._visibility.get())),
        )
        row += 1

        # Notiz
        ttk.Label(frame, text="Notiz:").grid(row=row, column=0, sticky="ne", padx=4, pady=3)
        self._note = tk.Text(frame, width=52, height=5)
        self._note.grid(row=row, column=1, columnspan=3, pady=3, sticky="w")
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=4, pady=(10, 0))
        ttk.Button(buttons, text="Speichern", command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

        self._note.focus_set()

    def _on_save(self) -> None:
        engine_map = {"automatisch": None, "ein": 1, "aus": 0}
        genoa = _parse_float(self._genoa.get())
        self.result = {
            "note": self._note.get("1.0", "end").strip(),
            "crew": self._crew.get().strip(),
            "location": self._location.get().strip(),
            "engine_on": engine_map.get(self._engine.get()),
            "mainsail": self._mainsail.get() if self._mainsail.get() != "—" else "",
            "genoa_percent": genoa,
            "spinnaker": 1 if self._spinnaker.get() else 0,
            "wave_height_m": _parse_float(self._wave.get()),
            "cloud_cover": self._cloud.get() if self._cloud.get() != "—" else "",
            "precipitation": self._precip.get() if self._precip.get() != "kein" else "",
            "visibility": self._visibility.get() if self._visibility.get() != "—" else "",
        }
        self.top.destroy()


def _fmt_live(snapshot: Dict[str, float], key: str) -> str:
    value = (snapshot or {}).get(key)
    return "" if value is None else f"{value:.1f}"


class _CrewListDialog:
    """Crewliste zum aktuellen Törn: Bootsangaben + Crew, druckbar."""

    _BOAT_FIELDS = [
        ("ship_name", "Schiffsname:"),
        ("ship_type", "Bootstyp:"),
        ("ship_flag", "Flagge:"),
        ("home_port", "Heimathafen:"),
        ("call_sign", "Rufzeichen:"),
        ("ship_mmsi", "MMSI:"),
        ("registration_no", "Registriernummer:"),
        ("ship_length", "Länge über alles:"),
    ]

    def __init__(self, parent, config, store, trip) -> None:
        self._config = config
        self._store = store
        self._trip = trip
        self._trip_id = trip.id if trip else None
        self.top = tk.Toplevel(parent)
        self.top.title("Crewliste" + (f" — Törn #{trip.id}" if trip else ""))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("740x580")
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        # Bootsangaben (aus der Konfiguration vorbelegt, werden gespeichert)
        boat = ttk.LabelFrame(frame, text="Bootsangaben (werden gespeichert)")
        boat.pack(fill="x")
        self._boat_vars: Dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(self._BOAT_FIELDS):
            r, c = i % 4, (i // 4) * 2
            ttk.Label(boat, text=label).grid(row=r, column=c, sticky="e", padx=(8, 3), pady=3)
            var = tk.StringVar(value=str(getattr(config, key, "") or ""))
            ttk.Entry(boat, textvariable=var, width=26).grid(row=r, column=c + 1, sticky="w", pady=3)
            self._boat_vars[key] = var

        # Ort/Datum für den Ausdruck
        pd = ttk.Frame(frame)
        pd.pack(fill="x", pady=(8, 0))
        ttk.Label(pd, text="Ort (Ausklarierung):").pack(side="left", padx=(4, 3))
        # Zuletzt gespeicherten Ort/Datum bevorzugen, sonst Törn-Ort / heute.
        default_place = getattr(config, "clearance_place", "") or ""
        if not default_place and trip:
            default_place = trip.end_location or trip.start_location or ""
        self._place = tk.StringVar(value=default_place)
        ttk.Entry(pd, textvariable=self._place, width=22).pack(side="left")
        ttk.Label(pd, text="Datum:").pack(side="left", padx=(12, 3))
        default_date = getattr(config, "clearance_date", "") or \
            datetime.date.today().strftime("%d.%m.%Y")
        self._date = tk.StringVar(value=default_date)
        ttk.Entry(pd, textvariable=self._date, width=12).pack(side="left")

        # Crew-Tabelle
        title = "Crew" if trip else "Crew (kein Törn gewählt — allgemeine Liste)"
        crew = ttk.LabelFrame(frame, text=title)
        crew.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("pos", "name", "first", "birth", "place", "nat", "pass")
        headers = {
            "pos": "Funktion", "name": "Name", "first": "Vorname",
            "birth": "Geburtsdatum", "place": "Geburtsort",
            "nat": "Staatsang.", "pass": "Pass-Nr.",
        }
        widths = {"pos": 70, "name": 110, "first": 100, "birth": 90,
                  "place": 100, "nat": 90, "pass": 100}
        self._tree = ttk.Treeview(crew, columns=cols, show="headings", height=8)
        for col in cols:
            self._tree.heading(col, text=headers[col])
            self._tree.column(col, width=widths[col])
        self._tree.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(crew, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._tree.bind("<Double-1>", lambda _e: self._on_edit())

        cbtn = ttk.Frame(frame)
        cbtn.pack(fill="x", pady=(6, 0))
        ttk.Button(cbtn, text="Person hinzufügen…", command=self._on_add).pack(side="left")
        ttk.Button(cbtn, text="Bearbeiten…", command=self._on_edit).pack(side="left", padx=4)
        ttk.Button(cbtn, text="Entfernen", command=self._on_remove).pack(side="left")

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text="Crewliste drucken…", command=self._on_print).pack(side="left")
        ttk.Button(bottom, text="Speichern & schließen", command=self._on_close).pack(
            side="right"
        )
        ttk.Button(bottom, text="Schließen", command=self.top.destroy).pack(
            side="right", padx=4
        )

        self._refresh()

    def _refresh(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        for m in self._store.crew_for_trip(self._trip_id):
            self._tree.insert(
                "", "end", iid=str(m.id),
                values=(m.position, m.last_name, m.first_name, m.birth_date,
                        m.birth_place, m.nationality, m.passport_no),
            )

    def _selected_id(self) -> Optional[int]:
        sel = self._tree.selection()
        return int(sel[0]) if sel else None

    def _member(self, cid: int):
        return next(
            (m for m in self._store.crew_for_trip(self._trip_id) if m.id == cid), None
        )

    def _on_add(self) -> None:
        count = len(self._store.crew_for_trip(self._trip_id))
        member = CrewMember(
            trip_id=self._trip_id,
            position="Skipper" if count == 0 else "Crew",
            sort_order=count,
        )
        dlg = _CrewMemberDialog(self.top, member, self._store.all_persons())
        self.top.wait_window(dlg.top)
        if dlg.result is not None:
            self._store.add_crew(dlg.result)
            self._store.save_person(self._to_person(dlg.result))
            self._refresh()

    def _on_edit(self) -> None:
        cid = self._selected_id()
        if cid is None:
            return
        member = self._member(cid)
        if member is None:
            return
        dlg = _CrewMemberDialog(self.top, member, self._store.all_persons())
        self.top.wait_window(dlg.top)
        if dlg.result is not None:
            self._store.update_crew(dlg.result)
            self._store.save_person(self._to_person(dlg.result))
            self._refresh()

    @staticmethod
    def _to_person(m: CrewMember) -> Person:
        return Person(
            last_name=m.last_name, first_name=m.first_name,
            birth_date=m.birth_date, birth_place=m.birth_place,
            nationality=m.nationality, passport_no=m.passport_no,
        )

    def _on_remove(self) -> None:
        cid = self._selected_id()
        if cid is None:
            return
        if messagebox.askyesno("Entfernen", "Person aus der Crewliste entfernen?"):
            self._store.delete_crew(cid)
            self._refresh()

    def _save_boat(self) -> None:
        for key, var in self._boat_vars.items():
            setattr(self._config, key, var.get().strip())
        # Ort/Datum der Ausklarierung merken
        self._config.clearance_place = self._place.get().strip()
        self._config.clearance_date = self._date.get().strip()
        self._config.save()

    def _on_print(self) -> None:
        self._save_boat()
        crew = self._store.crew_for_trip(self._trip_id)
        html = crewlist.build_html(
            {k: v.get().strip() for k, v in self._boat_vars.items()},
            crew,
            self._place.get().strip(),
            self._date.get().strip(),
        )
        path = Path(self._config.db_path).parent / "crewliste.html"
        try:
            path.write_text(html, encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            messagebox.showerror("Crewliste", f"Konnte Datei nicht schreiben:\n{exc}")
            return
        webbrowser.open(path.as_uri())

    def _on_close(self) -> None:
        self._save_boat()
        self.top.destroy()


class _CrewMemberDialog:
    """Dialog für ein einzelnes Crew-Mitglied."""

    _ROWS = [
        ("position", "Funktion:"),
        ("last_name", "Name:"),
        ("first_name", "Vorname:"),
        ("birth_date", "Geburtsdatum:"),
        ("birth_place", "Geburtsort:"),
        ("nationality", "Staatsangehörigkeit:"),
        ("passport_no", "Reisepass-Nr.:"),
    ]

    def __init__(self, parent, member: CrewMember, persons=None) -> None:
        self.result: Optional[CrewMember] = None
        self._member = member
        self._persons = list(persons or [])
        self.top = tk.Toplevel(parent)
        self.top.title("Crew-Mitglied")
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        row0 = 0
        # Auswahl aus gespeicherten Personen (falls vorhanden)
        if self._persons:
            ttk.Label(frame, text="Gespeicherte Person:").grid(
                row=0, column=0, sticky="e", padx=4, pady=(0, 6)
            )
            self._pick = tk.StringVar(value="— neu —")
            choices = ["— neu —"] + [
                f"{p.last_name}, {p.first_name}".strip(", ") for p in self._persons
            ]
            combo = ttk.Combobox(frame, textvariable=self._pick, width=26,
                                 state="readonly", values=choices)
            combo.grid(row=0, column=1, sticky="w", pady=(0, 6))
            combo.bind("<<ComboboxSelected>>", lambda _e: self._on_pick())
            row0 = 1

        self._vars: Dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(self._ROWS):
            r = row0 + i
            ttk.Label(frame, text=label).grid(row=r, column=0, sticky="e", padx=4, pady=3)
            if key == "position":
                var = tk.StringVar(value=getattr(member, key) or "Crew")
                ttk.Combobox(frame, textvariable=var, width=26, state="readonly",
                             values=["Skipper", "Crew"]).grid(row=r, column=1, sticky="w", pady=3)
            else:
                var = tk.StringVar(value=getattr(member, key) or "")
                ttk.Entry(frame, textvariable=var, width=28).grid(row=r, column=1, sticky="w", pady=3)
            self._vars[key] = var

        btns = ttk.Frame(frame)
        btns.grid(row=row0 + len(self._ROWS), column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btns, text="Übernehmen", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

    def _on_pick(self) -> None:
        """Füllt die Felder aus einer gespeicherten Person (Funktion bleibt)."""
        choice = self._pick.get()
        labels = [f"{p.last_name}, {p.first_name}".strip(", ") for p in self._persons]
        if choice not in labels:
            return
        person = self._persons[labels.index(choice)]
        for key in ("last_name", "first_name", "birth_date", "birth_place",
                    "nationality", "passport_no"):
            if key in self._vars:
                self._vars[key].set(getattr(person, key) or "")

    def _on_ok(self) -> None:
        for key, var in self._vars.items():
            setattr(self._member, key, var.get().strip())
        self.result = self._member
        self.top.destroy()


class _TripStartDialog:
    """Dialog zum Beginnen eines Törns (Start-Kennwerte wie in TripCon).

    Log-Stand und Motorenstunden werden — falls im NMEA-Netz vorhanden —
    aus den Live-Werten vorbelegt.
    """

    def __init__(self, parent: tk.Tk, snapshot: Optional[Dict[str, float]] = None) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title("Neuen Törn beginnen")
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._vars: Dict[str, tk.StringVar] = {}
        rows = [
            ("name", "Törn-Name:", ""),
            ("start_location", "Startort:", ""),
            ("start_water_l", "Wasser (Liter):", ""),
            ("start_diesel_l", "Diesel (Liter):", ""),
            ("start_engine_hours", "Motorenstunden:", _fmt_live(snapshot, "engine_hours")),
            ("start_log_nm", "Log-Stand (Nm):", _fmt_live(snapshot, "log_total_nm")),
        ]
        for i, (key, label, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=4, pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=30).grid(row=i, column=1, pady=4)
            self._vars[key] = var
        if snapshot and (snapshot.get("engine_hours") or snapshot.get("log_total_nm")):
            ttk.Label(
                frame, text="(Motorstunden/Log aus NMEA vorbelegt)", foreground="#888"
            ).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text="Törn beginnen", command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        self.result = {
            "name": self._vars["name"].get().strip(),
            "start_location": self._vars["start_location"].get().strip(),
            "start_water_l": _parse_float(self._vars["start_water_l"].get()),
            "start_diesel_l": _parse_float(self._vars["start_diesel_l"].get()),
            "start_engine_hours": _parse_float(self._vars["start_engine_hours"].get()),
            "start_log_nm": _parse_float(self._vars["start_log_nm"].get()),
        }
        self.top.destroy()


class _TripCloseDialog:
    """Dialog zum Abschließen eines Törns (End-Kennwerte)."""

    def __init__(
        self, parent: tk.Tk, trip: Trip, snapshot: Optional[Dict[str, float]] = None
    ) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(f"Törn abschließen: {trip.name or trip.start_location}")
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        # Endwerte bevorzugt aus NMEA (Log/Motorstunden), sonst Startwert
        live_hours = _fmt_live(snapshot, "engine_hours")
        live_log = _fmt_live(snapshot, "log_total_nm")
        self._vars: Dict[str, tk.StringVar] = {}
        rows = [
            ("end_location", "Zielort:", ""),
            ("end_water_l", "Wasser (Liter):", ""),
            ("end_diesel_l", "Diesel (Liter):", ""),
            ("end_engine_hours", "Motorenstunden:",
             live_hours or ("" if trip.start_engine_hours is None else str(trip.start_engine_hours))),
            ("end_log_nm", "Log-Stand (Nm):",
             live_log or ("" if trip.start_log_nm is None else str(trip.start_log_nm))),
        ]
        for i, (key, label, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=4, pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=30).grid(row=i, column=1, pady=4)
            self._vars[key] = var

        ttk.Label(frame, text="Abschluss-Notiz:").grid(
            row=len(rows), column=0, sticky="ne", padx=4, pady=4
        )
        self._note = tk.Text(frame, width=32, height=4)
        self._note.grid(row=len(rows), column=1, pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text="Törn abschließen", command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        self.result = {
            "end_location": self._vars["end_location"].get().strip(),
            "end_water_l": _parse_float(self._vars["end_water_l"].get()),
            "end_diesel_l": _parse_float(self._vars["end_diesel_l"].get()),
            "end_engine_hours": _parse_float(self._vars["end_engine_hours"].get()),
            "end_log_nm": _parse_float(self._vars["end_log_nm"].get()),
            "note": self._note.get("1.0", "end").strip(),
        }
        self.top.destroy()


class _SourcesDialog:
    """Verwaltet die Liste der Datenquellen (mehrere gleichzeitig möglich)."""

    def __init__(self, parent: tk.Tk, defs: list) -> None:
        self.result: Optional[list] = None
        self._defs = [dict(d) for d in defs]
        self.top = tk.Toplevel(parent)
        self.top.title("Datenquellen")
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Aktive Quellen (alle werden gleichzeitig gelesen):").grid(
            row=0, column=0, columnspan=6, sticky="w"
        )
        self._listbox = tk.Listbox(frame, width=52, height=5)
        self._listbox.grid(row=1, column=0, columnspan=5, pady=6, sticky="w")
        ttk.Button(frame, text="Entfernen", command=self._on_remove).grid(
            row=1, column=5, sticky="n", padx=4
        )
        self._refresh_list()

        # Eingabezeile zum Hinzufügen
        ttk.Label(frame, text="Protokoll:").grid(row=2, column=0, sticky="e", pady=6)
        self._proto = tk.StringVar(value="tcp")
        proto = ttk.Combobox(
            frame, textvariable=self._proto, values=["tcp", "udp", "serial"],
            width=8, state="readonly",
        )
        proto.grid(row=2, column=1, sticky="w")
        proto.bind("<<ComboboxSelected>>", lambda _e: self._update_hint())
        ttk.Label(frame, text="Host / COM:").grid(row=2, column=2, sticky="e")
        self._host = tk.StringVar()
        ttk.Entry(frame, textvariable=self._host, width=16).grid(row=2, column=3, sticky="w")
        ttk.Label(frame, text="Port / Baud:").grid(row=2, column=4, sticky="e")
        self._port = tk.StringVar()
        ttk.Entry(frame, textvariable=self._port, width=8).grid(row=2, column=5, sticky="w")

        ttk.Button(frame, text="+ Quelle hinzufügen", command=self._on_add).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=6
        )
        self._hint = ttk.Label(frame, text="", foreground="#888")
        self._hint.grid(row=3, column=2, columnspan=4, sticky="w")
        self._update_hint()

        # Vorlagen
        tmpl = ttk.Frame(frame)
        tmpl.grid(row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(tmpl, text="Vorlagen:", foreground="#555").pack(side="left")
        ttk.Button(tmpl, text="B&G (TCP 10110)", command=self._tmpl_bg).pack(side="left", padx=3)
        ttk.Button(tmpl, text="Maretron (COM)", command=self._tmpl_maretron).pack(side="left", padx=3)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=6, pady=(12, 0))
        ttk.Button(buttons, text="Übernehmen", command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

    def _refresh_list(self) -> None:
        self._listbox.delete(0, "end")
        for d in self._defs:
            self._listbox.insert(
                "end", f"{d.get('protocol', 'tcp')}   {d.get('host')} : {d.get('port')}"
            )

    def _update_hint(self) -> None:
        if self._proto.get() == "serial":
            self._hint.config(text="seriell: Host = COM-Port (COM11), Port = Baud (115200)")
        else:
            self._hint.config(text="")

    def _on_add(self) -> None:
        host = self._host.get().strip()
        port = self._port.get().strip()
        if not host or not port:
            return
        self._defs.append({"host": host, "port": port, "protocol": self._proto.get()})
        self._host.set("")
        self._port.set("")
        self._refresh_list()

    def _on_remove(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            del self._defs[sel[0]]
            self._refresh_list()

    def _tmpl_bg(self) -> None:
        self._proto.set("tcp"); self._host.set("192.168.9.224"); self._port.set("10110")
        self._update_hint()

    def _tmpl_maretron(self) -> None:
        self._proto.set("serial"); self._host.set("COM11"); self._port.set("115200")
        self._update_hint()

    def _on_ok(self) -> None:
        self.result = self._defs
        self.top.destroy()


class _RawMonitor:
    """Fenster, das die eingehenden NMEA-Rohsätze live anzeigt.

    Praktisch am Boot, um zu prüfen, ob die Verbindung steht und welche
    Sätze Orca Core / B&G tatsächlich senden.
    """

    def __init__(self, parent: tk.Tk, buffer: "Deque[str]") -> None:
        self._buffer = buffer
        self._paused = False
        self.top = tk.Toplevel(parent)
        self.top.title("NMEA-Rohdaten")
        self.top.geometry("640x420")

        toolbar = ttk.Frame(self.top, padding=6)
        toolbar.pack(fill="x")
        self._pause_btn = ttk.Button(toolbar, text="Pause", command=self._toggle_pause)
        self._pause_btn.pack(side="left")
        ttk.Button(toolbar, text="Leeren", command=self._clear).pack(side="left", padx=6)
        self._info = ttk.Label(toolbar, text="")
        self._info.pack(side="right")

        self._text = tk.Text(self.top, wrap="none", font=("Courier", 9))
        scroll = ttk.Scrollbar(self.top, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set, state="disabled")
        self._text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self._alive = True
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        self._refresh()

    def alive(self) -> bool:
        return self._alive

    def lift(self) -> None:
        self.top.lift()

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        self._pause_btn.config(text="Weiter" if self._paused else "Pause")

    def _clear(self) -> None:
        self._buffer.clear()

    def _refresh(self) -> None:
        if not self._alive:
            return
        if not self._paused:
            lines = list(self._buffer)[-300:]
            self._text.config(state="normal")
            self._text.delete("1.0", "end")
            self._text.insert("1.0", "\n".join(lines))
            self._text.see("end")
            self._text.config(state="disabled")
            self._info.config(text=f"{len(self._buffer)} Sätze gepuffert")
        self.top.after(700, self._refresh)

    def _close(self) -> None:
        self._alive = False
        self.top.destroy()
