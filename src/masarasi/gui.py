"""tkinter-GUI für masarasi — das Segel-Logbuch."""

from __future__ import annotations

import base64
import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk
from typing import Deque, Dict, Optional

from masarasi import plotter_capture
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
from masarasi.logbook import LogbookService
from masarasi.nmea import FIELD_LABELS
from masarasi.source import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
    NmeaSource,
)
from masarasi.storage import LogbookStore, Trip

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
        self._source: Optional[NmeaSource] = None

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

        # Kopfzeile: Verbindung
        top = ttk.LabelFrame(self._root, text="Gateway-Verbindung")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Host:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self._host_var = tk.StringVar(value=self._config.gateway_host)
        ttk.Entry(top, textvariable=self._host_var, width=16).grid(row=0, column=1, padx=4)

        ttk.Label(top, text="Port:").grid(row=0, column=2, sticky="e", padx=4)
        self._port_var = tk.StringVar(value=str(self._config.gateway_port))
        ttk.Entry(top, textvariable=self._port_var, width=7).grid(row=0, column=3, padx=4)

        ttk.Label(top, text="Protokoll:").grid(row=0, column=4, sticky="e", padx=4)
        self._proto_var = tk.StringVar(value=self._config.protocol)
        proto = ttk.Combobox(
            top, textvariable=self._proto_var, values=["tcp", "udp", "serial"],
            width=7, state="readonly",
        )
        proto.grid(row=0, column=5, padx=4)
        proto.bind("<<ComboboxSelected>>", lambda _e: self._update_conn_hint())

        self._connect_btn = ttk.Button(top, text="Verbinden", command=self._on_connect)
        self._connect_btn.grid(row=0, column=6, padx=8)

        ttk.Button(top, text="Rohdaten…", command=self._on_show_raw).grid(
            row=0, column=7, padx=4
        )

        self._status_label = tk.Label(top, text="getrennt", fg="#888888")
        self._status_label.grid(row=0, column=8, padx=8)

        self._conn_hint = ttk.Label(top, text="", foreground="#888")
        self._conn_hint.grid(row=1, column=0, columnspan=9, sticky="w", padx=6)
        self._update_conn_hint()

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

        # Hauptzeile: Messwerte | Bedingungen | Kartenplotter nebeneinander
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

        plotter = ttk.LabelFrame(main_row, text="Kartenplotter")
        plotter.pack(side="left", fill="y", padx=(8, 0))
        self._build_plotter(plotter)

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
        ttk.Button(controls, text="Bilder exportieren", command=self._on_export_images).grid(
            row=0, column=6, padx=4
        )

        # Logbuch-Tabelle
        table_frame = ttk.Frame(self._root)
        table_frame.pack(fill="both", expand=True, **pad)

        cols = ("time", "type", "pos", "sog", "wind", "depth", "motor", "segel", "img", "note")
        headers = {
            "time": "Zeit (UTC)", "type": "Typ", "pos": "Position",
            "sog": "SOG", "wind": "Wind", "depth": "Tiefe",
            "motor": "Motor", "segel": "Segel", "img": "📷", "note": "Notiz",
        }
        widths = {
            "time": 150, "type": 60, "pos": 150, "sog": 50,
            "wind": 100, "depth": 55, "motor": 50, "segel": 130, "img": 30, "note": 150,
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
        self._tree.bind("<Double-1>", lambda _e: self._on_view_image())

        bottom = ttk.Frame(self._root)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Eintrag löschen", command=self._on_delete_entry).pack(
            side="left"
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
        entry = self._logbook.add_current(
            conditions=self._condition_values,
            note=self._condition_values.get("note", ""),
            trip_id=self._logbook.current_trip_id,
        )
        self._attach_plotter_image(entry.id)
        self._refresh_logbook()

    def _attach_plotter_image(self, entry_id) -> None:
        """Speichert den letzten Plotter-Screenshot zum Eintrag (falls vorhanden)."""
        png = self._latest_plotter_png
        if png and entry_id is not None:
            self._store.set_image(entry_id, png, "image/png")

    # --- Kartenplotter-Panel (GoFree) --------------------------------------

    def _build_plotter(self, parent: ttk.LabelFrame) -> None:
        self._plotter_img = None            # tk.PhotoImage (Referenz halten)
        self._latest_plotter_png: Optional[bytes] = None  # wartet auf nächsten Eintrag
        self._capture_enabled = False       # (Auto-Aufnahme derzeit nicht genutzt)
        self._plotter_label = tk.Label(
            parent,
            text="(kein Bild)\n\nPlotter-Screenshot\nladen",
            width=32, height=8, background="#1f2d36", foreground="#c8d2d8",
        )
        self._plotter_label.pack(padx=6, pady=4)

        btns = ttk.Frame(parent)
        btns.pack(fill="x", padx=6, pady=(0, 2))
        ttk.Button(btns, text="Screenshot laden…", command=self._on_load_plotter).pack(
            side="left"
        )
        ttk.Button(btns, text="Entfernen", command=self._on_clear_plotter).pack(
            side="left", padx=4
        )

        self._plotter_hint = ttk.Label(
            parent, text="Kein Bild geladen.", foreground="#888"
        )
        self._plotter_hint.pack(anchor="w", padx=6, pady=(0, 6))
        if not plotter_capture.available():
            self._plotter_hint.config(text="Hinweis: JPG-Screenshots brauchen Pillow.")

    def _show_plotter_png(self, png: bytes) -> None:
        try:
            img = tk.PhotoImage(data=base64.b64encode(png))
        except Exception:  # noqa: BLE001
            return
        factor = max(1, img.width() // 360)
        if factor > 1:
            img = img.subsample(factor, factor)
        self._plotter_img = img
        self._plotter_label.config(image=img, text="")

    def _on_load_plotter(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Bilder", "*.png *.gif *.jpg *.jpeg *.bmp"), ("Alle Dateien", "*.*")]
        )
        if not path:
            return
        png = plotter_capture.load_image_as_png(path)
        if png is None:
            messagebox.showerror(
                "Bild",
                "Konnte das Bild nicht laden.\n\nPNG/GIF gehen direkt; für JPG/BMP "
                "bitte Pillow installieren:\n    pip install pillow",
            )
            return
        self._latest_plotter_png = png
        self._show_plotter_png(png)
        self._plotter_hint.config(text="Bild wird an den nächsten Eintrag gehängt.")

    def _on_clear_plotter(self) -> None:
        self._latest_plotter_png = None
        self._plotter_img = None
        self._plotter_label.config(
            image="", text="(kein Bild)\n\nPlotter-Screenshot laden"
        )
        self._plotter_hint.config(text="Kein Bild geladen.")

    # --- Verbindung ---------------------------------------------------------

    def _on_connect(self) -> None:
        if self._source is not None:
            self._source.stop()
            self._source = None
            self._connect_btn.config(text="Verbinden")
            self._set_status(STATUS_DISCONNECTED, "getrennt")
            return

        try:
            port = int(self._port_var.get())
        except ValueError:
            messagebox.showerror("Ungültiger Port", "Bitte eine Portnummer eingeben.")
            return

        host = self._host_var.get().strip()
        protocol = self._proto_var.get()
        self._config.gateway_host = host
        self._config.gateway_port = port
        self._config.protocol = protocol
        self._config.save()

        self._source = NmeaSource(
            host=host, port=port, live=self._live, protocol=protocol,
            on_status=self._on_source_status,
            on_raw=self._raw_buffer.append,  # deque.append ist thread-sicher
        )
        self._source.start()
        self._connect_btn.config(text="Trennen")

    def _update_conn_hint(self) -> None:
        if self._proto_var.get() == "serial":
            self._conn_hint.config(
                text="Seriell (z.B. Maretron USB100): Host = COM-Port (z.B. COM5), "
                     "Port = Baudrate (z.B. 115200) · benötigt pyserial"
            )
        else:
            self._conn_hint.config(text="")

    def _on_show_raw(self) -> None:
        """Öffnet das Fenster mit den rohen NMEA-Sätzen."""
        if self._raw_window is not None and self._raw_window.alive():
            self._raw_window.lift()
            return
        self._raw_window = _RawMonitor(self._root, self._raw_buffer)

    def _on_source_status(self, status: str, message: str) -> None:
        # Aus dem Netzwerk-Thread -> in den GUI-Thread verlagern
        self._root.after(0, lambda: self._set_status(status, message))

    def _set_status(self, status: str, message: str) -> None:
        text, color = _STATUS_TEXT.get(status, (status, "#000"))
        if message and status in (STATUS_ERROR, STATUS_CONNECTING):
            text = f"{text}: {message}"
        self._status_label.config(text=text, fg=color)

    # --- Live-Anzeige -------------------------------------------------------

    def _schedule_live_update(self) -> None:
        self._update_live_labels()
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
        # Läuft im Auto-Log-Thread: Bild anhängen (SQLite ist je Aufruf eigen),
        # dann die Tabelle im GUI-Thread aktualisieren.
        if entry is not None:
            self._attach_plotter_image(entry.id)
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

    def _on_trip_selected(self) -> None:
        self._logbook.current_trip_id = self._trip_choices.get(self._trip_var.get())
        self._update_close_button()
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

    # --- Logbuch-Tabelle ----------------------------------------------------

    def _refresh_logbook(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        trip_id = self._logbook.current_trip_id
        with_images = self._store.entries_with_images()
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
                    entry.timestamp,
                    entry.entry_type,
                    pos,
                    "" if entry.sog_kn is None else f"{entry.sog_kn:.1f}",
                    wind,
                    "" if entry.depth_m is None else f"{entry.depth_m:.1f}",
                    motor,
                    ", ".join(sail_parts),
                    "📷" if entry.id in with_images else "",
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

    def _on_view_image(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        png = self._store.get_image(int(selection[0]))
        if not png:
            return
        win = tk.Toplevel(self._root)
        win.title("Kartenplotter-Bild")
        try:
            img = tk.PhotoImage(data=base64.b64encode(png))
        except Exception:  # noqa: BLE001
            messagebox.showinfo("Bild", "Bild kann nicht angezeigt werden (kein PNG).")
            win.destroy()
            return
        label = tk.Label(win, image=img)
        label.image = img  # Referenz halten
        label.pack()

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

    def _on_export_images(self) -> None:
        directory = filedialog.askdirectory(title="Zielordner für Kartenplotter-Bilder")
        if not directory:
            return
        count = self._store.export_entry_images(directory)
        messagebox.showinfo("Export", f"{count} Kartenplotter-Bild(er) exportiert.")

    # --- Schließen ----------------------------------------------------------

    def _on_close(self) -> None:
        self._capture_enabled = False
        self._logbook.stop_auto()
        if self._source is not None:
            self._source.stop()
        self._root.destroy()


def _parse_float(text: str) -> Optional[float]:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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


class _RegionSelector:
    """Vollbild-Overlay: der Nutzer zieht ein Rechteck über den Kartenplotter.

    Liefert die gewählte Bildschirmfläche (links, oben, rechts, unten) an den
    Callback — genau die Fläche, die die Auto-Aufnahme dann abgreift.
    """

    def __init__(self, parent: tk.Tk, on_done) -> None:
        self._on_done = on_done
        self.top = tk.Toplevel(parent)
        self.top.attributes("-fullscreen", True)
        try:
            self.top.attributes("-alpha", 0.25)
            self.top.attributes("-topmost", True)
        except tk.TclError:
            pass
        self._canvas = tk.Canvas(self.top, bg="gray20", highlightthickness=0, cursor="cross")
        self._canvas.pack(fill="both", expand=True)
        self._canvas.create_text(
            20, 20, anchor="nw", fill="white",
            text="Rechteck über den Kartenplotter ziehen  ·  Esc = Abbrechen",
            font=("TkDefaultFont", 14),
        )
        self._rect = None
        self._start_canvas = (0, 0)
        self._start_root = (0, 0)
        self._canvas.bind("<ButtonPress-1>", self._press)
        self._canvas.bind("<B1-Motion>", self._drag)
        self._canvas.bind("<ButtonRelease-1>", self._release)
        self.top.bind("<Escape>", lambda _e: self.top.destroy())

    def _press(self, event) -> None:
        self._start_canvas = (event.x, event.y)
        self._start_root = (event.x_root, event.y_root)
        self._rect = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="red", width=2
        )

    def _drag(self, event) -> None:
        if self._rect is not None:
            self._canvas.coords(self._rect, *self._start_canvas, event.x, event.y)

    def _release(self, event) -> None:
        if self._rect is None:
            self.top.destroy()
            return
        x0, y0 = self._start_root
        x1, y1 = event.x_root, event.y_root
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        self.top.destroy()
        if right - left >= 5 and bottom - top >= 5:
            self._on_done((left, top, right, bottom))


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
