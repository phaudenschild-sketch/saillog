"""tkinter-GUI für SailLog — das Segel-Logbuch."""

from __future__ import annotations

import datetime
import json
import time
import tkinter as tk
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Deque, Dict, List, Optional

from saillog import (
    backup, branding, crewlist, fields, fuel, geo, photos, qrcode, reports,
    rig, timeutil, tripcon,
)
from saillog.ais import AisDecoder, AisTargets
from saillog.autolog import AutoLogSettings
from saillog.config import CONFIG_PATH, Config
from saillog.fields import (
    CLOUD_COVER_LABELS,
    MAINSAIL_OPTIONS,
    PRECIPITATION,
    VISIBILITY_LABELS,
    cloud_hint,
    visibility_hint,
)
from saillog.i18n import available_languages, current_language, set_language, t
from saillog.livedata import LiveData
from saillog.logbook import LogbookService, utc_now_iso
from saillog.nmea import FIELD_LABELS
from saillog.remote import RemoteServer
from saillog.source import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISCONNECTED,
    STATUS_ERROR,
    NmeaSource,
)
from saillog.signalk import SignalKSource
from saillog.storage import (
    CrewMember, EquipmentParam, EQUIP_CATEGORIES, FuelEntry, LogbookStore,
    LogEntry, Person, REEF_TYPES, Ship, ShipEquipment, Trip, Voyage,
)
from saillog.webmap import MapServer

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
        set_language(self._config.language)   # Oberflächensprache aktivieren
        self._live = LiveData()
        # Brandneue Installation? -> danach einen Demo-Törn anlegen, damit Tester
        # nicht vor einem leeren Logbuch sitzen (nur beim allerersten Start).
        fresh_install = not Path(self._config.db_path).exists()
        self._store = LogbookStore(self._config.db_path)
        if fresh_install:
            try:
                from saillog import demo
                demo.seed_demo_data(self._store)
            except Exception:  # noqa: BLE001 - Demo-Daten dürfen den Start nie verhindern
                pass
        self._logbook = LogbookService(self._store, self._live)
        # Plotter-Screenshot bei Auto-Einträgen (falls aktiviert)
        self._apply_plotter_autolog()
        # Datenquellen: Definitionen + aktive Verbindungen + Status je Quelle
        self._source_defs = self._load_source_defs()
        self._sources: list = []
        self._src_status: Dict[int, tuple] = {}

        # AIS: gemeinsame Zielliste, je Quelle ein Decoder (Mehrteiler pro Kanal)
        self._ais_targets = AisTargets()
        self._ais_decoders: list = []
        # Lokaler Webserver für die AIS-Karte (Leaflet + OpenFreeMap)
        self._map_server: Optional[MapServer] = None
        # Karten-Filter: AIS-Ziele anzeigen? / welche Eintragstypen zeigen?
        # (_map_entry_types = None -> alle Typen)
        self._map_show_ais = True
        self._map_entry_types: Optional[set] = None
        # AutoLog-Auslöser (aus der Konfiguration)
        self._autolog_settings = AutoLogSettings.from_dict(self._config.autolog)
        # Anlass-Auswahl (anpassbar; None -> Standardliste)
        self._logevents = fields.logevents(self._config.logevents)
        # Foto-Import (Ordner-Überwachung; mehrere Ordner = mehrere Watcher)
        self._photo_watchers: List[photos.PhotoWatcher] = []
        # Bündelung kurz nacheinander eintreffender Fotos zu einem Eintrag
        self._photo_grouper = photos.PhotoGrouper()

        self._value_labels: Dict[str, tk.Label] = {}
        # Ringpuffer für die Rohdaten-Anzeige (Thread-sicher via deque.append)
        self._raw_buffer: Deque[str] = deque(maxlen=500)
        self._raw_window: Optional["_RawMonitor"] = None
        # Törn-Auswahl: Anzeigetext -> Trip-ID (None = keinem Törn zugeordnet)
        self._trip_choices: Dict[str, Optional[int]] = {}

        root.title(t("SailLog — Segel-Logbuch"))
        branding.set_window_icon(root)
        root.minsize(1000, 560)

        self._build_ui()
        self._refresh_trips()
        self._refresh_logbook()
        self._fit_window()
        self._schedule_live_update()
        self._maybe_start_photo_watcher()
        self._autostart_logging()
        self._autostart_connect()
        self._remote_server: Optional[RemoteServer] = None
        self._maybe_start_remote()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _save_img_source(self) -> None:
        val = self._entry_img_src.get()
        if val != self._config.entry_image_source:
            self._config.entry_image_source = val
            self._config.save()

    # Zielhöhe des Startfensters: passt auf kleinere Laptop-Bildschirme; die
    # Logbuch-Tabelle darüber ist scrollbar, die untere Knopfzeile bleibt so
    # immer sichtbar.
    _TARGET_HEIGHT = 640

    def _fit_window(self) -> None:
        """Fenster so groß, dass alles (inkl. untere Knopfzeile) sichtbar ist —
        aber nie höher als ~640 px bzw. der Bildschirm (Tabelle scrollt)."""
        r = self._root
        r.update_idletasks()
        req_w = max(1180, r.winfo_reqwidth())
        req_h = r.winfo_reqheight()
        scr_w, scr_h = r.winfo_screenwidth(), r.winfo_screenheight()
        w = min(req_w, int(scr_w * 0.96))
        h = min(req_h + 8, self._TARGET_HEIGHT, int(scr_h * 0.92))
        x = max(0, (scr_w - w) // 2)
        y = max(0, (scr_h - h) // 4)
        r.geometry(f"{w}x{h}+{x}+{y}")

    # --- UI-Aufbau ----------------------------------------------------------

    def _build_ui(self) -> None:
        pad = dict(padx=8, pady=2)

        # Menüleiste: Stammdaten (Personen/Schiffe verwalten)
        menubar = tk.Menu(self._root)
        stamm = tk.Menu(menubar, tearoff=0)
        stamm.add_command(label=t("Personen verwalten…"), command=self._on_manage_persons)
        stamm.add_command(label=t("Schiffe verwalten…"), command=self._on_manage_ships)
        stamm.add_separator()
        stamm.add_command(label=t("Anlass-Liste bearbeiten…"),
                          command=self._on_edit_logevents)
        menubar.add_cascade(label=t("Stammdaten"), menu=stamm)
        extras = tk.Menu(menubar, tearoff=0)
        extras.add_command(label=t("Törns/Etappen gruppieren…"),
                           command=self._on_manage_voyages)
        extras.add_command(label=t("Plotter-Screenshot (ADB)…"),
                           command=self._on_plotter_settings)
        extras.add_command(label=t("🎓 Seemeilen-Nachweis (Segelscheine)…"),
                           command=self._on_meilennachweis)
        extras.add_separator()
        extras.add_command(label=t("TripCon-Backup importieren…"),
                           command=self._on_import_tripcon)
        self._build_language_menu(extras)
        menubar.add_cascade(label=t("Extras"), menu=extras)
        self._root.config(menu=menubar)

        # Kopfzeile: Datenquellen (mehrere gleichzeitig möglich)
        top = ttk.LabelFrame(self._root, text=t("Datenquellen"))
        top.pack(fill="x", **pad)

        self._connect_btn = ttk.Button(top, text=t("Verbinden"), command=self._on_connect_all)
        self._connect_btn.grid(row=0, column=0, padx=6, pady=6)
        ttk.Button(top, text=t("Quellen…"), command=self._on_manage_sources).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(top, text=t("Rohdaten…"), command=self._on_show_raw).grid(
            row=0, column=2, padx=4
        )
        # Ein-Klick-Demo: startet einen eingebetteten NMEA-Simulator und
        # verbindet darauf — Tester sehen sofort Live-Werte, ganz ohne Boot.
        ttk.Button(top, text=t("🎮 Demo-Datenbus"), command=self._on_demo_bus).grid(
            row=0, column=3, padx=(14, 4)
        )
        self._status_label = tk.Label(top, text=t("getrennt"), fg="#888888")
        self._status_label.grid(row=0, column=4, padx=8)
        self._sources_label = ttk.Label(top, text="", foreground="#555")
        self._sources_label.grid(row=1, column=0, columnspan=5, sticky="w", padx=8, pady=(0, 4))
        self._update_sources_label()

        # Törn-Leiste
        trip_bar = ttk.LabelFrame(self._root, text=t("Törn"))
        trip_bar.pack(fill="x", **pad)
        ttk.Label(trip_bar, text=t("Aktiver Törn:")).grid(row=0, column=0, sticky="e", padx=4, pady=6)
        self._trip_var = tk.StringVar()
        self._trip_combo = ttk.Combobox(
            trip_bar, textvariable=self._trip_var, width=42, state="readonly"
        )
        self._trip_combo.grid(row=0, column=1, padx=4)
        self._trip_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_trip_selected())
        ttk.Button(trip_bar, text=t("Neuer Törn…"), command=self._on_new_trip).grid(
            row=0, column=2, padx=6
        )
        ttk.Button(trip_bar, text=t("✎ Törn bearbeiten…"), command=self._on_edit_trip).grid(
            row=0, column=3, padx=4
        )
        self._close_trip_btn = ttk.Button(
            trip_bar, text=t("Törn abschließen…"), command=self._on_close_trip
        )
        self._close_trip_btn.grid(row=0, column=4, padx=4)
        ttk.Button(trip_bar, text=t("Crewliste…"), command=self._on_crewlist).grid(
            row=0, column=5, padx=4
        )
        ttk.Button(trip_bar, text=t("📄 Bericht…"), command=self._on_report).grid(
            row=0, column=6, padx=4
        )
        ttk.Button(trip_bar, text=t("⛽ Tanken…"), command=self._on_fuel).grid(
            row=0, column=7, padx=4
        )
        self._trip_dist_label = ttk.Label(
            trip_bar, text="", foreground="#1a5a8a", font=("TkDefaultFont", 10, "bold")
        )
        self._trip_dist_label.grid(row=0, column=8, padx=12)

        # Hauptzeile: Messwerte | Bedingungen nebeneinander
        main_row = ttk.Frame(self._root)
        main_row.pack(fill="x", **pad)

        # Messwerte kompakt (zwei Spalten, je Zeile "Label  Wert Einheit").
        # Enges Zeilen-Padding hält das Panel flach, ohne die Breite zu erhöhen
        # (drei Spalten würden das Fenster zu breit für Laptop-Bildschirme machen).
        dash = ttk.LabelFrame(main_row, text=t("Messwerte"))
        dash.pack(side="left", fill="y")
        per_col = (len(FIELD_LABELS) + 1) // 2
        for index, (key, label, _unit) in enumerate(FIELD_LABELS):
            row = index % per_col
            base = (index // per_col) * 2
            ttk.Label(dash, text=t(label), foreground="#666").grid(
                row=row, column=base, sticky="e", padx=(8, 3), pady=0
            )
            value = tk.Label(dash, text="—", font=("TkDefaultFont", 10, "bold"),
                             width=11, anchor="w")
            value.grid(row=row, column=base + 1, sticky="w", padx=(0, 10), pady=0)
            self._value_labels[key] = value

        cond = ttk.LabelFrame(main_row, text=t("Bedingungen (bei jedem Log mitgeschrieben)"))
        cond.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_conditions(cond)

        # Logging-Steuerung
        controls = ttk.LabelFrame(self._root, text=t("Logbuch"))
        controls.pack(fill="x", **pad)

        self._auto_btn = ttk.Button(
            controls, text=t("Auto-Logging starten"), command=self._on_toggle_auto
        )
        self._auto_btn.grid(row=0, column=0, padx=(8, 4), pady=6)
        ttk.Button(controls, text=t("AutoLog…"), command=self._on_autolog_settings).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(controls, text=t("📷 Foto-Import…"), command=self._on_photo_settings).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(controls, text=t("📱 Handy/Tablet…"),
                   command=self._on_remote_settings).grid(row=1, column=0, padx=(8, 4),
                                                          pady=(0, 6), sticky="w")

        entry_grp = ttk.Frame(controls)
        entry_grp.grid(row=0, column=3, padx=8)
        ttk.Label(entry_grp, text=t("Bild:")).pack(side="left")
        self._entry_img_src = tk.StringVar(
            value=self._config.entry_image_source or "kein Bild")
        ttk.Combobox(
            entry_grp, textvariable=self._entry_img_src, width=17, state="readonly",
            values=["kein Bild", "Plotter-Screenshot", "Bild von Festplatte…"],
        ).pack(side="left", padx=(2, 6))
        # gewählte Bildquelle merken (beim nächsten Start vorbelegen)
        self._entry_img_src.trace_add("write", lambda *_: self._save_img_source())
        ttk.Button(
            entry_grp, text=t("✎ Eintrag speichern"), command=self._on_save_entry
        ).pack(side="left")
        ttk.Button(
            entry_grp, text=t("📸 Plotter"), command=self._on_plotter_entry
        ).pack(side="left", padx=(6, 0))

        ttk.Button(controls, text=t("CSV exportieren"), command=self._on_export_csv).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(controls, text=t("GPX exportieren"), command=self._on_export_gpx).grid(
            row=0, column=5, padx=4
        )
        ttk.Button(controls, text=t("🗺 AIS-Karte"), command=self._on_open_map).grid(
            row=0, column=6, padx=4
        )
        ttk.Button(controls, text=t("🗺 Logbuch-Karte…"),
                   command=self._on_open_log_map).grid(row=0, column=7, padx=4)

        # Logbuch-Tabelle
        table_frame = ttk.Frame(self._root)
        table_frame.pack(fill="both", expand=True, **pad)

        cols = ("time", "ed", "anlass", "type", "pos", "sog", "wind", "depth",
                "motor", "segel", "img", "note")
        headers = {
            "time": t("Zeit"), "ed": "✎", "anlass": t("Anlass"), "type": t("Typ"),
            "pos": t("Position"), "sog": "SOG", "wind": t("Wind wahr"), "depth": t("Tiefe"),
            "motor": t("Motor"), "segel": t("Segel"), "img": "📷", "note": t("Notiz"),
        }
        widths = {
            "time": 145, "ed": 26, "anlass": 100, "type": 58, "pos": 140, "sog": 48,
            "wind": 95, "depth": 52, "motor": 46, "segel": 120, "img": 28, "note": 150,
        }
        # Standardhöhe klein halten (scrollbar) — hält das Startfenster niedrig,
        # damit die untere Knopfzeile auf Laptop-Bildschirmen sichtbar bleibt.
        self._tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=4)
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
        bottom.pack(side="bottom", fill="x", **pad)
        ttk.Button(bottom, text=t("➕ Neuer Eintrag…"), command=self._on_new_entry).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(bottom, text=t("Bearbeiten…"), command=self._on_edit_entry).pack(side="left")
        ttk.Button(bottom, text=t("Eintrag löschen"), command=self._on_delete_entry).pack(
            side="left", padx=4
        )
        ttk.Button(bottom, text=t("Bild ansehen"), command=self._on_view_image).pack(
            side="left", padx=4
        )
        ttk.Button(bottom, text=t("💾 Backup…"), command=self._on_backup).pack(
            side="left", padx=(12, 4)
        )
        self._show_track = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bottom, text=t("Trackpunkte anzeigen"), variable=self._show_track,
            command=self._refresh_logbook,
        ).pack(side="left", padx=(12, 4))
        ttk.Label(bottom, text=t("(Doppelklick = bearbeiten)"), foreground="#999").pack(
            side="left", padx=8
        )
        self._count_label = ttk.Label(bottom, text="")
        self._count_label.pack(side="right", padx=(0, 6))
        # Zeitzone (aus der oberen Leiste hierher, damit sie ins Fenster passt)
        self._tz_var = tk.StringVar(value=self._tz_current_choice())
        tz = ttk.Combobox(
            bottom, textvariable=self._tz_var, width=10, state="readonly",
            values=["System", "UTC", "UTC+1", "UTC+2", "UTC+3", "UTC+4", "UTC-1", "UTC-2"],
        )
        tz.pack(side="right", padx=2)
        tz.bind("<<ComboboxSelected>>", lambda _e: self._on_tz_change())
        ttk.Label(bottom, text=t("Zeitzone:")).pack(side="right", padx=(14, 2))

    # --- Sprache ------------------------------------------------------------

    def _build_language_menu(self, parent_menu: tk.Menu) -> None:
        """Untermenü „Sprache / Language" mit allen verfügbaren Katalogen."""
        parent_menu.add_separator()
        lang_menu = tk.Menu(parent_menu, tearoff=0)
        self._lang_var = tk.StringVar(value=current_language())
        for code, name in available_languages().items():
            lang_menu.add_radiobutton(
                label=name, value=code, variable=self._lang_var,
                command=lambda c=code: self._on_change_language(c),
            )
        parent_menu.add_cascade(label=t("Sprache / Language"), menu=lang_menu)

    def _on_change_language(self, code: str) -> None:
        """Sprache umschalten (wird beim nächsten Start wirksam)."""
        self._lang_var.set(code)
        if code == self._config.language:
            return
        self._config.language = code
        self._config.save()
        set_language(code)
        messagebox.showinfo(
            t("Sprache / Language"),
            t("Die Sprache wird beim nächsten Start von SailLog übernommen."),
        )

    # --- Bedingungs-Panel (dauerhafte Maskenwerte) -------------------------

    def _build_conditions(self, parent: ttk.LabelFrame) -> None:
        self._cond_vars: Dict[str, tk.Variable] = {}
        # In zwei Spalten anordnen (je 3 Zeilen), damit die Maske flach bleibt
        # und das Fenster auf einen Laptop-Bildschirm passt.
        self._row = 0
        per_col = 3

        def add(label, widget):
            i = self._row
            r = i % per_col
            base = (i // per_col) * 2
            ttk.Label(parent, text=label).grid(
                row=r, column=base, sticky="e", padx=(6, 3), pady=1
            )
            widget.grid(row=r, column=base + 1, sticky="w", padx=(0, 8), pady=1)
            self._row += 1

        self._cond_vars["logevent"] = tk.StringVar(value=self._logevents[0])
        self._logevent_combo = ttk.Combobox(
            parent, textvariable=self._cond_vars["logevent"], width=18,
            values=self._logevents,
        )
        add(t("Anlass:"), self._logevent_combo)
        # Auswahlwerte: Anzeige übersetzt, gespeichert wird der deutsche Code.
        self._cond_vars["cloud"] = tk.StringVar(value=t("wolkenlos"))
        add(t("Bewölkung:"), ttk.Combobox(
            parent, textvariable=self._cond_vars["cloud"], width=18,
            state="readonly", values=fields.tr_labels(CLOUD_COVER_LABELS),
        ))
        self._cond_vars["precip"] = tk.StringVar(value=t("kein"))
        add(t("Niederschlag:"), ttk.Combobox(
            parent, textvariable=self._cond_vars["precip"], width=18,
            state="readonly", values=fields.tr_labels(PRECIPITATION),
        ))
        self._cond_vars["visibility"] = tk.StringVar(value=t("gut"))
        add(t("Sicht:"), ttk.Combobox(
            parent, textvariable=self._cond_vars["visibility"], width=18,
            state="readonly", values=fields.tr_labels(VISIBILITY_LABELS),
        ))
        self._cond_vars["wave"] = tk.StringVar()
        add(t("Seegang (m):"), ttk.Entry(
            parent, textvariable=self._cond_vars["wave"], width=10,
        ))
        self._cond_vars["note"] = tk.StringVar()
        add(t("Bemerkung:"), ttk.Entry(
            parent, textvariable=self._cond_vars["note"], width=20,
        ))

        # Segel/Antrieb: passt sich an die Ausrüstung des aktiven Schiffs an
        # (Festsegel = an/aus, Rollsegel = 0–100 %, Bindereff = Reff-Stufen;
        # Motorboot = keine Segel). Wird bei Schiffswechsel neu aufgebaut.
        self._sail_frame = ttk.LabelFrame(parent, text=t("Segel / Antrieb"))
        self._sail_frame.grid(row=per_col, column=0, columnspan=4,
                              sticky="we", pady=(8, 0))
        self._sail_vars: Dict[str, tk.Variable] = {}
        self._sail_controls: list = []
        self._sail_mode = "classic"
        self._rig = rig.RigSpec()

        # Änderungen der festen Felder sofort in den Cache übernehmen
        for var in self._cond_vars.values():
            var.trace_add("write", lambda *_: self._sync_conditions())
        self._rebuild_sail_controls()
        self._logbook.conditions_provider = lambda: dict(self._condition_values)

    def _active_rig(self) -> "rig.RigSpec":
        sid = self._config.active_ship_id
        items = self._store.ship_equipment(sid) if sid else []
        return rig.rig_from_equipment(items)

    def _entry_rig(self, entry) -> "rig.RigSpec":
        """Rig für einen Eintrag: Schiff seines Törns, sonst aktives Schiff."""
        sid = None
        if getattr(entry, "trip_id", None):
            trip = self._store.get_trip(entry.trip_id)
            sid = trip.ship_id if trip else None
        sid = sid or self._config.active_ship_id
        items = self._store.ship_equipment(sid) if sid else []
        return rig.rig_from_equipment(items)

    def _rebuild_sail_controls(self) -> None:
        """Baut die Antriebs-Bedienelemente (Motor(en) + Segel) nach dem Schiff auf."""
        for w in self._sail_frame.winfo_children():
            w.destroy()
        self._sail_vars = {}
        self._sail_controls = []
        self._motor_vars = {}
        self._rig = self._active_rig()
        f = self._sail_frame

        def bind(var):
            var.trace_add("write", lambda *_: self._sync_conditions())
            return var

        # --- Motor(en) ---
        row = 0
        if len(self._rig.motors) >= 2:
            self._motor_mode = "multi"
            ttk.Label(f, text=t("Motoren:")).grid(row=row, column=0, sticky="ne", padx=(6, 3), pady=2)
            mbox = ttk.Frame(f)
            mbox.grid(row=row, column=1, columnspan=2, sticky="w")
            for name in self._rig.motors:
                var = bind(tk.BooleanVar(value=False))
                ttk.Checkbutton(mbox, text=t("{name} läuft", name=name), variable=var).pack(anchor="w")
                self._motor_vars[name] = var
        else:
            self._motor_mode = "single"
            self._cond_vars["engine_mode"] = bind(tk.StringVar(value=t("automatisch")))
            ttk.Label(f, text=t("Motor:")).grid(row=row, column=0, sticky="e", padx=(6, 3), pady=2)
            ttk.Combobox(f, textvariable=self._cond_vars["engine_mode"], width=16,
                         state="readonly",
                         values=fields.tr_labels(["automatisch", "ein", "aus"])).grid(
                row=row, column=1, sticky="w")
        row += 1

        # --- Segel ---
        if not self._rig.configured:
            # Fallback: klassische Segelfelder (ohne konfigurierte Ausrüstung)
            self._sail_mode = "classic"
            self._cond_vars["mainsail"] = bind(tk.StringVar(value=t("Geborgen")))
            ttk.Label(f, text=t("Großsegel:")).grid(row=row, column=0, sticky="e", padx=(6, 3), pady=2)
            ttk.Combobox(f, textvariable=self._cond_vars["mainsail"], width=16,
                         state="readonly", values=fields.tr_labels(MAINSAIL_OPTIONS)).grid(
                row=row, column=1, sticky="w")
            self._cond_vars["genoa"] = bind(tk.StringVar(value="0"))
            ttk.Label(f, text=t("Genua %:")).grid(row=row + 1, column=0, sticky="e", padx=(6, 3), pady=2)
            ttk.Spinbox(f, from_=0, to=100, textvariable=self._cond_vars["genoa"],
                        width=8).grid(row=row + 1, column=1, sticky="w")
            self._cond_vars["spinnaker"] = bind(tk.BooleanVar(value=False))
            ttk.Label(f, text=t("Spinnaker:")).grid(row=row + 2, column=0, sticky="e", padx=(6, 3), pady=2)
            ttk.Checkbutton(f, text=t("gesetzt"), variable=self._cond_vars["spinnaker"]).grid(
                row=row + 2, column=1, sticky="w")
            ttk.Label(f, text=t("(Tipp: unter Stammdaten die Schiffs-Ausrüstung "
                      "pflegen — dann passt sich die Eingabe an.)"),
                      foreground="#999").grid(row=row + 3, column=0, columnspan=3, sticky="w", padx=6)
        elif self._rig.is_motorboat:
            self._sail_mode = "motor"
            ttk.Label(f, text=t("🛥 Motorboot — keine Segel"), foreground="#555").grid(
                row=row, column=0, columnspan=3, sticky="w", padx=6, pady=(2, 6))
        else:
            self._sail_mode = "adaptive"
            for i, sail in enumerate(self._rig.sails):
                rr = row + i
                ttk.Label(f, text=sail.name + ":").grid(
                    row=rr, column=0, sticky="e", padx=(6, 3), pady=2)
                if sail.control == rig.CONTROL_ROLLER:
                    var = bind(tk.IntVar(value=0))
                    tk.Scale(f, from_=0, to=100, orient="horizontal", length=170,
                             variable=var, showvalue=1).grid(row=rr, column=1, sticky="w")
                    ttk.Label(f, text="%").grid(row=rr, column=2, sticky="w")
                elif sail.control == rig.CONTROL_SLAB:
                    var = bind(tk.StringVar(value=t("nicht gesetzt")))
                    ttk.Combobox(f, textvariable=var, width=14, state="readonly",
                                 values=fields.tr_labels(rig.SLAB_STATES)).grid(
                        row=rr, column=1, sticky="w")
                else:
                    var = bind(tk.BooleanVar(value=False))
                    ttk.Checkbutton(f, text=t("gesetzt"), variable=var).grid(
                        row=rr, column=1, sticky="w")
                self._sail_vars[sail.name] = var
                self._sail_controls.append((sail, var))
        self._sync_conditions()

    def _sync_conditions(self) -> None:
        v = self._cond_vars
        # Angezeigtes (übersetztes) Label -> kanonischer deutscher Code
        cloud = fields.code_from_label(CLOUD_COVER_LABELS, v["cloud"].get())
        precip = fields.code_from_label(PRECIPITATION, v["precip"].get())
        vis = fields.code_from_label(VISIBILITY_LABELS, v["visibility"].get())
        cv = {
            "wave_height_m": _parse_float(v["wave"].get()),
            "cloud_cover": cloud if cloud != "—" else "",
            "precipitation": precip if precip != "kein" else "",
            "visibility": vis if vis != "—" else "",
            "logevent": v["logevent"].get().strip(),
            "note": v["note"].get().strip(),
        }
        # Motor(en)
        if getattr(self, "_motor_mode", "single") == "multi":
            mstates = {name: (1 if var.get() else 0) for name, var in self._motor_vars.items()}
            cv["engine_mode"] = "ein" if any(mstates.values()) else "aus"
            cv["motors_json"] = json.dumps(mstates, ensure_ascii=False)
        else:
            cv["engine_mode"] = fields.code_from_label(
                ["automatisch", "ein", "aus"], self._cond_vars["engine_mode"].get())
            cv["motors_json"] = ""
        mode = getattr(self, "_sail_mode", "classic")
        if mode == "adaptive":
            states: Dict[str, object] = {}
            for sail, var in self._sail_controls:
                if isinstance(var, tk.BooleanVar):
                    val = "gesetzt" if var.get() else "nicht gesetzt"
                elif isinstance(var, tk.IntVar):
                    val = var.get()                                  # Rollsegel: Prozent
                else:  # SLAB: übersetztes Label -> Code
                    val = fields.code_from_label(rig.SLAB_STATES, var.get())
                states[sail.name] = val
            cv["mainsail"] = rig.summarize(states, self._rig)   # Kurzfassung (Tabelle/Bericht)
            cv["genoa_percent"] = None
            cv["spinnaker"] = None
            cv["sails_json"] = json.dumps(states, ensure_ascii=False)
        elif mode == "motor":
            cv["mainsail"] = ""
            cv["genoa_percent"] = None
            cv["spinnaker"] = None
            cv["sails_json"] = ""
        else:  # classic
            main = fields.code_from_label(MAINSAIL_OPTIONS, v["mainsail"].get())
            cv["mainsail"] = main if main != "—" else ""
            cv["genoa_percent"] = _parse_float(v["genoa"].get())
            cv["spinnaker"] = 1 if v["spinnaker"].get() else 0
            cv["sails_json"] = ""
        self._condition_values = cv

    def _on_save_entry(self) -> None:
        self._sync_conditions()
        entry = self._logbook.add_current(
            conditions=self._condition_values,
            note=self._condition_values.get("note", ""),
            trip_id=self._logbook.current_trip_id,
        )
        self._refresh_logbook()
        # Bildquelle für diesen Eintrag auswerten
        source = self._entry_img_src.get()
        if source == "Bild von Festplatte…":
            path = filedialog.askopenfilename(
                title=t("Bild für den Eintrag wählen"),
                filetypes=[(t("Bilder"), "*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp"),
                           (t("Alle Dateien"), "*.*")],
            )
            if path:
                self._attach_image_async(entry.id, disk_path=path)
        elif source == "Plotter-Screenshot":
            self._attach_image_async(entry.id, plotter=True)
        # Nach dem Speichern: Anlass zurück auf Standard, Bemerkung leeren
        self._cond_vars["logevent"].set(self._logevents[0])
        self._cond_vars["note"].set("")

    # --- Plotter-Screenshot (ADB vom Android-Tablet) -----------------------

    def _plotter_jpeg(self) -> Optional[bytes]:
        """Holt einen Plotter-Screenshot als JPEG (oder None). Läuft im Thread."""
        from saillog import android_screencap
        return android_screencap.capture_jpeg(
            self._config.plotter_adb_path,
            self._config.plotter_adb_serial,
            max_px=int(self._config.photo_max_px or 1600),
        )

    def _apply_plotter_autolog(self) -> None:
        """Setzt/entfernt den Screenshot-Provider für Auto-Einträge."""
        self._logbook.screenshot_provider = (
            self._plotter_jpeg if self._config.plotter_autolog else None
        )

    def _attach_image_async(self, entry_id, disk_path: str = "",
                            plotter: bool = False) -> None:
        import threading

        def work():
            if plotter:
                jpeg = self._plotter_jpeg()
                kind = "Plotter"
            else:
                jpeg = photos.resize_to_jpeg(
                    disk_path, int(self._config.photo_max_px or 1600))
                kind = "Datei"
            self._root.after(0, lambda: self._after_attach(entry_id, jpeg, kind))

        threading.Thread(target=work, daemon=True).start()

    def _after_attach(self, entry_id, jpeg, kind) -> None:
        if jpeg:
            self._store.set_image(entry_id, jpeg, "image/jpeg", created_dz=utc_now_iso())
            self._refresh_logbook()
        elif kind == "Plotter":
            messagebox.showerror(
                t("Plotter-Screenshot"),
                t("Kein Screenshot erhalten.\n\n"
                  "• adb-Pfad/Gerät prüfen (Menü Extras → Plotter-Screenshot…)\n"
                  "• Tablet per USB/WLAN gekoppelt und 'immer erlauben' bestätigt?"),
            )
        else:
            messagebox.showwarning(t("Bild"), t("Datei-Bild konnte nicht angehängt werden."))

    def _on_plotter_entry(self) -> None:
        """Sofort: Plotter-Screenshot holen und als Logbuch-Eintrag ablegen."""
        import threading
        self._sync_conditions()
        conditions = dict(self._condition_values)

        def work():
            jpeg = self._plotter_jpeg()
            self._root.after(0, lambda: self._plotter_entry_done(jpeg, conditions))

        threading.Thread(target=work, daemon=True).start()

    def _plotter_entry_done(self, jpeg, conditions) -> None:
        if not jpeg:
            messagebox.showerror(
                t("Plotter-Screenshot"),
                t("Kein Screenshot erhalten.\n\n"
                  "• adb-Pfad/Gerät prüfen (Menü Extras → Plotter-Screenshot…)\n"
                  "• Tablet gekoppelt und 'immer erlauben' bestätigt?"),
            )
            return
        entry = self._logbook.record_photo(
            trip_id=self._logbook.open_trip_id(), conditions=conditions, reason="Plotter"
        )
        if entry is not None:
            self._store.set_image(entry.id, jpeg, "image/jpeg", created_dz=utc_now_iso())
        self._refresh_logbook()

    def _on_plotter_settings(self) -> None:
        dialog = _PlotterDialog(self._root, self._config)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._config.plotter_adb_path = dialog.result["adb_path"]
        self._config.plotter_adb_serial = dialog.result["serial"]
        self._config.plotter_autolog = dialog.result["autolog"]
        self._config.save()
        self._apply_plotter_autolog()

    # --- AIS-Karte (Leaflet + OpenFreeMap) ---------------------------------

    def _make_ais_decoder(self):
        """Erzeugt einen AIS-Decoder je Quelle und merkt ihn sich."""
        decoder = AisDecoder(self._ais_targets)
        self._ais_decoders.append(decoder)
        return decoder.add_sentence

    def _ais_info(self) -> str:
        """Hinweistext für die Karte (z.B. aktive COG-Korrektur)."""
        if any(getattr(d, "cog_mode", "") == "whole" for d in self._ais_decoders):
            return t("COG-Korrektur aktiv: Feed liefert ganze Grad statt Zehntel.")
        return ""

    def _on_open_map(self) -> None:
        """Startet (falls nötig) den lokalen Kartenserver und öffnet den Browser."""
        # AIS-Karte zeigt alles (AIS-Ziele + alle Eintragstypen).
        self._map_show_ais = True
        self._map_entry_types = None
        self._open_map_server("AIS-Karte")

    def _on_open_log_map(self) -> None:
        """Logbuch-Karte: nur die Einträge (ohne AIS), Typen wählbar."""
        dialog = _LogMapDialog(self._root)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._map_show_ais = False
        self._map_entry_types = dialog.result          # Menge der gewählten Typen
        self._open_map_server("Logbuch-Karte")

    def _open_map_server(self, title: str) -> None:
        """Startet (falls nötig) den Kartenserver und öffnet ihn im Browser."""
        if self._map_server is None:
            try:
                self._map_server = MapServer(
                    own_provider=self._map_own,
                    targets_provider=self._map_targets,
                    track_provider=self._map_track,
                    entries_provider=self._map_entries,
                    info_provider=self._ais_info,
                    image_provider=self._store.get_image_by_id,
                )
                self._map_server.start()
            except OSError as exc:
                self._map_server = None
                messagebox.showerror(
                    title, t("Kartenserver konnte nicht starten:\n{error}", error=exc)
                )
                return
        webbrowser.open(self._map_server.url)

    def _map_targets(self) -> List[Dict]:
        """AIS-Ziele — leer, wenn die Karte ohne AIS geöffnet wurde."""
        return self._ais_targets.all() if self._map_show_ais else []

    def _map_own(self) -> Optional[Dict]:
        """Eigene Schiffsposition aus den Live-Daten für die Karte."""
        if not self._map_show_ais:
            return None                     # Logbuch-Karte: keine Live-Position
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
        # dichte Spur inkl. reiner Track-Punkte
        for entry in self._store.all(newest_first=False, trip_id=trip_id, limit=200000,
                                     include_track=True):
            if entry.lat is not None and entry.lon is not None:
                points.append([entry.lat, entry.lon])
        name = ""
        if trip_id is not None:
            trip = self._store.get_trip(trip_id)
            if trip is not None:
                name = trip.name or trip.start_location or f"Törn #{trip_id}"
        return {"name": name, "points": points}

    def _map_entries(self) -> List[Dict]:
        """Logbuch-Einträge des ausgewählten Törns als anklickbare Kartenpunkte."""
        trip_id = self._logbook.current_trip_id
        offset = self._tz_offset()
        rows = self._store.all(newest_first=False, trip_id=trip_id, limit=20000)
        types = self._map_entry_types      # None = alle Typen
        img_map = self._store.image_ids_map([e.id for e in rows])
        out: List[Dict] = []
        for e in rows:
            if e.lat is None or e.lon is None:
                continue
            if types is not None and e.entry_type not in types:
                continue
            wind = ""
            if e.tws_kn is not None:               # wahrer Wind
                if e.twd_deg is not None:
                    wind = f"{e.tws_kn:.0f} kn @ {e.twd_deg:.0f}°"
                else:
                    wind = f"{e.tws_kn:.0f} kn"
            sail_parts = []
            if e.mainsail and e.mainsail != "—":
                sail_parts.append(e.mainsail)
            if e.genoa_percent is not None:
                sail_parts.append(f"Genua {e.genoa_percent:.0f}%")
            if e.spinnaker:
                sail_parts.append("Spi")
            motor = "ein" if e.engine_on == 1 else ("aus" if e.engine_on == 0 else "")
            out.append({
                "lat": e.lat,
                "lon": e.lon,
                "time": timeutil.to_display(e.timestamp, offset),
                "type": e.entry_type,
                "anlass": e.logevent or "",
                "sog": None if e.sog_kn is None else round(e.sog_kn, 1),
                "depth": None if e.depth_m is None else round(e.depth_m, 1),
                "wind": wind,
                "motor": motor,
                "sails": ", ".join(sail_parts),
                "note": e.note or "",
                "images": img_map.get(e.id, []),
            })
        return out

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

    _DEMO_PORT = 2100     # lokaler Port des eingebetteten Demo-Simulators

    def _on_demo_bus(self) -> None:
        """Startet den eingebetteten NMEA-Simulator und verbindet darauf —
        ein Klick, damit ein Testuser ohne Boot/Gateway sofort Live-Werte sieht."""
        from saillog import simulator
        if getattr(self, "_demo_bus", None) is None:
            try:
                self._demo_bus = simulator.start_demo_bus(port=self._DEMO_PORT)
            except OSError as exc:
                messagebox.showerror(
                    t("Demo-Datenbus"),
                    t("Der Demo-Datenbus konnte nicht gestartet werden "
                      "(Port {port} belegt?).\n\n{error}", port=self._DEMO_PORT, error=exc))
                return
        if self._connected:                       # zuerst Bestehendes trennen
            self._on_connect_all()
        # nur im Speicher (nicht in die Konfiguration schreiben) -> beim
        # nächsten Start ist wieder alles normal.
        self._source_defs = [{"host": "127.0.0.1", "port": self._DEMO_PORT,
                              "protocol": "tcp"}]
        self._on_connect_all()
        messagebox.showinfo(
            t("Demo-Datenbus"),
            t("Demo-Datenbus läuft — die Messwerte kommen jetzt aus einem "
              "simulierten Boot (kein echtes Gateway). Zum Beenden auf "
              "„Trennen\" klicken oder das Programm neu starten."))

    def _autostart_connect(self) -> None:
        """Beim Programmstart automatisch mit den Datenquellen verbinden —
        aber nur, wenn der Nutzer wirklich eine Quelle eingetragen hat
        (``config.sources``). Ohne konfigurierte Quelle passiert nichts (kein
        Verbindungsversuch, keine Meldung) — sonst würde ein frisch installierter
        Testrechner vergeblich das Standard-Gateway anfunken."""
        if (self._config.sources and not self._connected
                and any(d.get("enabled", True) for d in self._config.sources)):
            self._on_connect_all()

    def _on_connect_all(self) -> None:
        if self._connected:
            for src in self._sources:
                src.stop()
            self._sources = []
            self._ais_decoders = []
            self._src_status = {}
            self._connect_btn.config(text=t("Verbinden"))
            self._update_sources_label()
            return
        if not self._source_defs:
            messagebox.showinfo(t("Quellen"), t("Bitte zuerst über 'Quellen…' eine Datenquelle anlegen."))
            return
        if not any(d.get("enabled", True) for d in self._source_defs):
            messagebox.showinfo(
                t("Quellen"),
                t("Alle Quellen sind ausgeschaltet — bitte über 'Quellen…' mindestens eine einschalten."))
            return
        self._ais_decoders = []
        for index, definition in enumerate(self._source_defs):
            if not definition.get("enabled", True):
                continue                     # ausgeschaltete Quelle überspringen
            try:
                port = int(definition["port"])
            except (ValueError, KeyError, TypeError):
                messagebox.showerror(t("Quelle"), t("Ungültiger Port bei Quelle {n}.", n=index + 1))
                continue
            proto = definition.get("protocol", "tcp")
            source_cls = SignalKSource if proto == "signalk" else NmeaSource
            source = source_cls(
                host=str(definition["host"]).strip(),
                port=port,
                live=self._live,
                protocol=proto,
                on_status=self._make_status_cb(index),
                on_raw=self._raw_buffer.append,
                # Eigener Decoder je Quelle (Mehrteiler werden pro Kanal
                # zusammengesetzt), gemeinsame Zielliste.
                on_ais=self._make_ais_decoder(),
                log_correction=self._active_log_correction(),
            )
            source.start()
            self._sources.append(source)
        self._connect_btn.config(text=t("Trennen"))
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
            proto = d.get("protocol", "tcp")
            if not d.get("enabled", True):       # ausgeschaltete Quelle
                parts.append(f"⊘ {proto} {d.get('host')}:{d.get('port')}")
                continue
            status = self._src_status.get(index, (STATUS_DISCONNECTED, ""))[0]
            mark = {
                STATUS_CONNECTED: "✓", STATUS_CONNECTING: "…",
                STATUS_ERROR: "✗", STATUS_DISCONNECTED: "·",
            }.get(status, "·")
            if status == STATUS_CONNECTED:
                connected += 1
            parts.append(f"{mark} {proto} {d.get('host')}:{d.get('port')}")
        self._sources_label.config(text="   ".join(parts) if parts else t("keine Quellen"))
        if not self._connected:
            self._status_label.config(text=t("getrennt"), fg="#888888")
        else:
            total = len(self._sources)
            color = "#1a8a1a" if connected == total else "#c08000"
            self._status_label.config(
                text=t("{connected}/{total} verbunden", connected=connected, total=total),
                fg=color)

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
                t("Quellen"), t("Geänderte Quellen werden beim nächsten 'Verbinden' aktiv.")
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
        """Strecke im aktiven Törn aus den geloggten GPS-Positionen.

        Robust gegenüber den (uneinheitlichen) Instrumenten-Logständen: die
        Strecke ist die Summe der Distanzen zwischen den geloggten Positionen,
        plus dem aktuellen Teilstück seit dem letzten Eintrag.
        """
        if self._logbook.current_trip_id is None:
            self._trip_dist_label.config(text="")
            return
        dist = getattr(self, "_trip_track_nm", 0.0)
        last = getattr(self, "_trip_last_pos", None)
        lat, lon = snapshot.get("lat"), snapshot.get("lon")
        if last is not None and lat is not None and lon is not None:
            dist += geo.haversine_nm(last[0], last[1], lat, lon)
        self._trip_dist_label.config(text=t("Strecke Törn: {dist} NM", dist=f"{dist:.1f}"))

    # --- Auto-Logging -------------------------------------------------------

    def _on_toggle_auto(self) -> None:
        if self._logbook.auto_running:
            self._logbook.stop_auto()
            self._auto_btn.config(text=t("Auto-Logging starten"))
            return
        self._logbook.start_auto(self._autolog_settings, on_entry=self._on_auto_entry)
        self._auto_btn.config(text=t("Auto-Logging stoppen"))

    def _autostart_logging(self) -> None:
        """AutoLogging direkt beim Programmstart mitstarten (per Knopf stoppbar)."""
        if not self._logbook.auto_running:
            self._logbook.start_auto(self._autolog_settings, on_entry=self._on_auto_entry)
            self._auto_btn.config(text=t("Auto-Logging stoppen"))

    # --- Fern-Erfassung (Handy/Tablet im Bordnetz) -------------------------

    @staticmethod
    def _lan_ip() -> str:
        """Beste lokale IP im Bordnetz ermitteln (ohne Internet zu brauchen)."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.168.1.1", 80))   # es fließen keine Daten, nur Routing
            ip = s.getsockname()[0]
        except Exception:  # noqa: BLE001
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def _remote_info(self) -> dict:
        """Aktuelle Werte für die Handy-Seite (läuft im Server-Thread)."""
        tid = self._logbook.current_trip_id
        trip = None
        if tid is not None:
            trip = next((d for d, i in self._trip_choices.items() if i == tid), None)
        spec = self._active_rig()
        return {
            "trip": trip,
            "measurements": self._live.snapshot(),
            "conditions": dict(getattr(self, "_condition_values", {}) or {}),
            "logevents": list(self._logevents),
            "rig": {
                "configured": spec.configured,
                "is_motorboat": spec.is_motorboat,
                "sails": [{"name": s.name, "control": s.control} for s in spec.sails],
                "motors": list(spec.motors),
            },
        }

    def _remote_submit(self, conditions: dict) -> dict:
        """Legt einen Eintrag vom Handy an (läuft im Server-Thread)."""
        entry = self._logbook.add_current(
            conditions=conditions,
            note=conditions.get("note", ""),
            trip_id=self._logbook.current_trip_id,
        )
        # Logbuch-Tabelle im GUI-Thread aktualisieren
        try:
            self._root.after(0, self._refresh_logbook)
        except Exception:  # noqa: BLE001
            pass
        return {
            "time": timeutil.to_display(entry.timestamp, self._tz_offset()),
            "lat": entry.lat,
            "lon": entry.lon,
            "logevent": entry.logevent or "",
        }

    def _maybe_start_remote(self) -> None:
        """Startet den Fern-Erfassungs-Server, falls aktiviert."""
        self._remote_server = None
        if not self._config.remote_enabled:
            return
        # PIN beim ersten Start erzeugen und speichern
        if not (self._config.remote_pin or "").strip():
            import random
            self._config.remote_pin = f"{random.randint(0, 9999):04d}"
            self._config.save()
        import base64
        try:
            icon = base64.b64decode(branding.ICON_PNG_B64)
        except Exception:  # noqa: BLE001
            icon = b""
        server = RemoteServer(
            info_provider=self._remote_info,
            submit=self._remote_submit,
            pin=self._config.remote_pin,
            host="0.0.0.0",
            port=int(self._config.remote_port or 8770),
            icon_png=icon,
        )
        try:
            server.start()
        except OSError as exc:
            messagebox.showwarning(
                t("Fern-Erfassung"),
                t("Der Handy-Zugang konnte nicht gestartet werden (Port "
                  "{port} belegt?).\n\n{error}", port=self._config.remote_port, error=exc),
            )
            return
        self._remote_server = server

    def _on_remote_settings(self) -> None:
        dialog = _RemoteDialog(self._root, self._config, self._remote_server,
                               self._lan_ip())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._config.remote_enabled = dialog.result["enabled"]
        self._config.remote_port = dialog.result["port"]
        self._config.remote_pin = dialog.result["pin"]
        self._config.save()
        # Server mit neuen Einstellungen neu aufsetzen
        if self._remote_server is not None:
            self._remote_server.stop()
            self._remote_server = None
        self._maybe_start_remote()

    def _on_edit_logevents(self) -> None:
        """Anlass-Auswahlliste anpassen (wirkt auf Maske, Dialoge und Handy)."""
        dialog = _LogeventsDialog(self._root, self._logevents)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._logevents = fields.logevents(dialog.result)
        # Kompakt speichern: None, wenn identisch mit der Standardliste
        self._config.logevents = (
            None if self._logevents == fields.DEFAULT_LOGEVENTS else self._logevents
        )
        self._config.save()
        self._logevent_combo["values"] = self._logevents
        if self._cond_vars["logevent"].get() not in self._logevents:
            self._cond_vars["logevent"].set(self._logevents[0])
        # Der Handy-/Tablet-Server liest die Liste live über den Info-Callback.

    def _on_autolog_settings(self) -> None:
        dialog = _AutoLogDialog(self._root, self._autolog_settings)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._autolog_settings = dialog.result
        self._config.autolog = dialog.result.to_dict()
        self._config.save()
        # Läuft das Auto-Logging schon, mit den neuen Auslösern neu starten
        if self._logbook.auto_running:
            self._logbook.start_auto(self._autolog_settings, on_entry=self._on_auto_entry)

    # --- Foto-Import --------------------------------------------------------

    def _on_photo_settings(self) -> None:
        dialog = _PhotoDialog(self._root, self._config)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._config.photo_folders = dialog.result["folders"]
        self._config.photo_folder = ""     # Alt-Einzelfeld geräumt (jetzt Liste)
        self._config.photo_recursive = dialog.result["recursive"]
        self._config.photo_group_seconds = dialog.result["group_seconds"]
        self._config.photo_import_enabled = dialog.result["enabled"]
        self._config.save()
        self._stop_photo_watcher()
        self._maybe_start_photo_watcher()

    def _maybe_start_photo_watcher(self) -> None:
        folders = self._config.photo_folder_list()
        if not (self._config.photo_import_enabled and folders):
            return
        if not photos.available():
            return  # ohne Pillow kein Foto-Import (Hinweis kommt im Dialog)
        recursive = bool(getattr(self._config, "photo_recursive", False))
        for folder in folders:
            watcher = photos.PhotoWatcher(
                folder,
                on_photo=self._on_photo_imported,
                max_px=int(self._config.photo_max_px or 1600),
                recursive=recursive,
            )
            watcher.start()
            self._photo_watchers.append(watcher)

    def _stop_photo_watcher(self) -> None:
        for watcher in self._photo_watchers:
            watcher.stop()
        self._photo_watchers = []

    def _on_photo_imported(self, jpeg: bytes, source_name: str) -> None:
        # Läuft im Watcher-Thread (evtl. mehrere gleichzeitig): Fotos, die kurz
        # nacheinander eintreffen, an denselben Eintrag hängen statt je einen
        # neuen anzulegen. Der PhotoGrouper serialisiert das thread-sicher.
        window = int(getattr(self._config, "photo_group_seconds", 0) or 0)

        def _create_entry() -> Optional[int]:
            conditions = dict(getattr(self, "_condition_values", {}) or {})
            entry = self._logbook.record_photo(
                trip_id=self._logbook.open_trip_id(), conditions=conditions
            )
            return entry.id if entry is not None else None

        entry_id = self._photo_grouper.resolve(time.monotonic(), window, _create_entry)
        if entry_id is not None:
            self._store.add_entry_image(entry_id, jpeg, "image/jpeg",
                                        created_dz=utc_now_iso())
        self._root.after(0, self._refresh_logbook)

    # --- Backup -------------------------------------------------------------

    def _on_backup(self) -> None:
        dialog = _BackupDialog(self._root, self._config, self._make_backup)
        self._root.wait_window(dialog.top)

    def _make_backup(self, folder: str) -> Optional[str]:
        """Erstellt ein Backup im Zielordner; gibt den Pfad zurück (oder None)."""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            path = backup.create_backup(
                self._config.db_path, str(CONFIG_PATH), folder, stamp
            )
            backup.prune_backups(folder, int(self._config.backup_keep or 5))
            return str(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(t("Backup"), t("Backup fehlgeschlagen:\n{error}", error=exc))
            return None

    def _on_auto_entry(self, entry) -> None:
        # Läuft im Auto-Log-Thread: die Tabelle im GUI-Thread aktualisieren.
        self._root.after(0, self._refresh_logbook)

    # --- Törns --------------------------------------------------------------

    def _refresh_trips(self) -> None:
        trips = self._store.all_trips(newest_first=True)
        self._trip_choices = {"— (kein Törn)": None}
        open_display = None
        for tr in trips:
            route = f"{tr.start_location or '?'} → {tr.end_location or '…'}"
            status = "offen" if tr.status == "open" else "abgeschlossen"
            disp = f"#{tr.id} {tr.name or route}  [{status}]"
            self._trip_choices[disp] = tr.id
            if tr.status == "open" and open_display is None:
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

    def _on_edit_trip(self) -> None:
        """Bearbeitet die Stammdaten eines bestehenden Törns (Name, Orte, Daten …)."""
        tid = self._logbook.current_trip_id
        trip = self._store.get_trip(tid) if tid else None
        if trip is None:
            messagebox.showinfo(
                t("Törn bearbeiten"), t("Bitte oben zuerst einen Törn auswählen."))
            return
        dialog = _TripEditDialog(self._root, trip, self._tz_offset(),
                                 ships=self._store.all_ships())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        for key, value in dialog.result.items():
            setattr(trip, key, value)
        self._store.update_trip(trip)
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

    # --- Berichte -----------------------------------------------------------

    def _on_report(self) -> None:
        has_trip = self._logbook.current_trip_id is not None
        dialog = _ReportDialog(self._root, has_trip, self._store.all_voyages())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        res = dialog.result
        offset = self._tz_offset()
        with_map = res.get("with_map", False)
        with_images = res.get("with_images", False)
        types = res.get("entry_types")     # gilt für Bericht-Einträge UND Karte

        # Kontext/Validierung auf dem Main-Thread; die eigentliche HTML-Erzeugung
        # passiert über eine Closure (für PDF im Hintergrund-Thread, damit die
        # Karten-Aufnahme die Oberfläche nicht blockiert).
        if res["kind"] == "fahrtenbuch":
            trips = self._store.all_trips(newest_first=False)
            if not trips:
                messagebox.showinfo(t("Bericht"), t("Noch keine Törns vorhanden."))
                return
            name = "fahrtenbuch.html"

            def make(mr, static):
                return reports.voyage_log_html(
                    self._store, self._config, trips, offset, "Fahrtenbuch",
                    with_map=with_map, map_types=types,
                    static_map=static, map_renderer=mr)
        elif res["kind"] == "voyage":
            voyage = self._store.get_voyage(res["voyage_id"])
            trips = self._store.trips_for_voyage(res["voyage_id"]) if voyage else []
            if not trips:
                messagebox.showinfo(
                    t("Bericht"), t("Diesem Törn sind noch keine Etappen zugeordnet "
                                    "(Extras → 'Törns/Etappen gruppieren…')."))
                return
            name = "toern_bericht.html"

            def make(mr, static):
                return reports.voyage_report_html(
                    self._store, self._config, voyage, trips, offset,
                    with_images=with_images, with_map=with_map,
                    map_types=types, entry_types=types,
                    static_map=static, map_renderer=mr)
        else:
            trip = self._store.get_trip(self._logbook.current_trip_id)
            if trip is None:
                messagebox.showinfo(t("Bericht"), t("Bitte oben eine Etappe auswählen."))
                return
            name = "etappen_bericht.html"

            def make(mr, static):
                return reports.trip_report_html(
                    self._store, self._config, trip, offset,
                    with_images=with_images, with_map=with_map,
                    map_types=types, entry_types=types,
                    static_map=static, map_renderer=mr)

        if res.get("as_pdf"):
            self._save_report_pdf(make, name, with_map)
        else:
            try:
                html = make(None, False)      # interaktive Leaflet-Karte
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(t("Bericht"), t("Bericht fehlgeschlagen:\n{error}", error=exc))
                return
            self._open_report_html(html, name)

    def _open_report_html(self, html: str, name: str) -> None:
        path = Path(self._config.db_path).parent / name
        try:
            path.write_text(html, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(t("Bericht"), t("Konnte den Bericht nicht speichern:\n{error}", error=exc))
            return
        webbrowser.open(path.as_uri())

    def _pdf_map_renderer(self, track, marks):
        """Fotografiert die Leaflet-Karte (mit OSM-Hintergrund) als PNG-Data-URI
        fürs PDF ab; None bei Fehler (dann greift der SVG-Fallback)."""
        import base64
        import os
        import tempfile
        from saillog import pdf
        fd, out = tempfile.mkstemp(suffix=".png", prefix="saillog_map_")
        os.close(fd)
        try:
            page = reports.map_page_html(track, marks, 1000, 640)
            ok = pdf.html_to_png(page, out, width=1000, height=640,
                                 browser=self._config.pdf_browser_path, wait_ms=8000)
            if not ok:
                return None
            with open(out, "rb") as fh:
                data = fh.read()
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    def _save_report_pdf(self, make_html, name: str, with_map: bool) -> None:
        """Erzeugt ein echtes PDF über den installierten Chromium-Browser.

        ``make_html(map_renderer, static_map)`` baut das HTML (mit statischer
        Karte, inkl. OSM-Hintergrund per Screenshot); läuft im Hintergrund."""
        from saillog import pdf
        if pdf.find_browser(self._config.pdf_browser_path) is None:
            if messagebox.askyesno(
                t("PDF-Export"),
                t("Kein Chromium-Browser (Edge/Chrome) gefunden, um direkt ein PDF "
                  "zu erzeugen.\n\nStattdessen den Bericht im Browser öffnen? Dort "
                  "über 'Drucken → Als PDF speichern' ausgeben.")):
                try:
                    self._open_report_html(make_html(None, False), name)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror(t("Bericht"), t("Bericht fehlgeschlagen:\n{error}", error=exc))
            return
        out = filedialog.asksaveasfilename(
            title=t("Bericht als PDF speichern"), defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")],
            initialfile=Path(name).with_suffix(".pdf").name,
        )
        if not out:
            return
        browser = self._config.pdf_browser_path
        renderer = self._pdf_map_renderer if with_map else None
        import threading

        def work():
            try:
                # static_map=True = SVG-Fallback, falls die Karten-Aufnahme scheitert
                html = make_html(renderer, True)
                ok = pdf.html_to_pdf(html, out, browser=browser, wait_ms=2500)
            except Exception:  # noqa: BLE001
                ok = False
            self._root.after(0, lambda: self._after_pdf(ok, out, make_html, name))

        threading.Thread(target=work, daemon=True).start()

    def _after_pdf(self, ok: bool, out: str, make_html, name: str) -> None:
        if ok:
            if messagebox.askyesno(t("PDF-Export"),
                                   t("PDF gespeichert:\n{path}\n\nJetzt öffnen?", path=out)):
                webbrowser.open(Path(out).as_uri())
        else:
            if messagebox.askyesno(
                t("PDF-Export"),
                t("Das PDF konnte nicht erzeugt werden.\n\nStattdessen den Bericht "
                  "im Browser öffnen (dort 'Als PDF speichern')?")):
                try:
                    self._open_report_html(make_html(None, False), name)
                except Exception as exc:  # noqa: BLE001
                    messagebox.showerror(t("Bericht"), t("Bericht fehlgeschlagen:\n{error}", error=exc))

    # --- Seemeilen-Nachweis -------------------------------------------------

    def _on_meilennachweis(self) -> None:
        trips_all = self._store.all_trips(newest_first=False)
        if not trips_all:
            messagebox.showinfo(t("Seemeilen-Nachweis"), t("Noch keine Törns vorhanden."))
            return
        dialog = _MeilenDialog(self._root, self._config)
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        r = dialog.result
        offset = self._tz_offset()
        # optionaler Zeitraum-Filter (nach Startdatum der Etappe)
        trips = [tr for tr in trips_all
                 if _in_date_range(tr.start_dz, r["von"], r["bis"], offset)]
        if not trips:
            messagebox.showinfo(t("Seemeilen-Nachweis"),
                                t("Im gewählten Zeitraum liegen keine Törns."))
            return

        name = "seemeilen_nachweis.html"

        def make(mr, static):     # (map_renderer, static) — hier ohne Karte
            return reports.meilennachweis_html(
                self._store, self._config, trips, offset,
                applicant=r["name"], role=r["role"], with_night=r["night"])

        if r["as_pdf"]:
            self._save_report_pdf(make, name, with_map=False)
        else:
            try:
                self._open_report_html(make(None, False), name)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror(t("Seemeilen-Nachweis"),
                                     t("Erstellung fehlgeschlagen:\n{error}", error=exc))

    def _on_manage_voyages(self) -> None:
        dialog = _VoyageDialog(self._root, self._store)
        self._root.wait_window(dialog.top)
        self._refresh_trips()

    # --- TripCon-Import -----------------------------------------------------

    def _on_import_tripcon(self) -> None:
        """Liest ein TripCon-Backup (.tcdb) ein — mit Vorschau und Bestätigung."""
        path = filedialog.askopenfilename(
            title=t("TripCon-Sicherung wählen"),
            filetypes=[(t("TripCon-Backup"), "*.tcdb"), (t("Alle Dateien"), "*.*")],
        )
        if not path:
            return
        import threading
        threading.Thread(
            target=self._tripcon_analyze_thread, args=(path,), daemon=True
        ).start()

    def _tripcon_analyze_thread(self, path: str) -> None:
        try:
            conn = tripcon.connect(path)
            try:
                info = tripcon.analyze_tcdb(conn)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._root.after(0, lambda: self._tripcon_failed(exc))
            return
        self._root.after(0, lambda: self._tripcon_confirm(path, info))

    def _tripcon_confirm(self, path: str, info: Dict) -> None:
        integ = str(info.get("integrity", "?"))
        if integ not in ("ok", "?"):
            if not messagebox.askyesno(
                t("TripCon-Import"),
                t("Achtung: Die Datei meldet Integritätsprobleme:\n{integ}\n\n"
                  "Trotzdem versuchen, so viel wie möglich zu importieren?", integ=integ)):
                return
        msg = t(
            "Datei: {name}\n\n"
            "Integrität: {integ}\n"
            "Törns: {trips}\n"
            "Log-Einträge: {log_entries}\n"
            "Plotter-Bilder: {plotter_images}\n"
            "Schiffe: {ships} · Personen: {persons}\n"
            "Zeitraum: {date_from} – {date_to}\n\n"
            "Jetzt in saillog importieren?\n"
            "(Ein früherer TripCon-Import wird dabei ersetzt; eigene Einträge "
            "bleiben unberührt.)",
            name=Path(path).name, integ=integ, trips=info.get('trips'),
            log_entries=info.get('log_entries'), plotter_images=info.get('plotter_images'),
            ships=info.get('ships'), persons=info.get('persons'),
            date_from=info.get('date_from', ''), date_to=info.get('date_to', ''),
        )
        if not messagebox.askyesno(t("TripCon-Import"), msg):
            return
        import threading
        threading.Thread(
            target=self._tripcon_import_thread, args=(path,), daemon=True
        ).start()

    def _tripcon_import_thread(self, path: str) -> None:
        try:
            conn = tripcon.connect(path)
            try:
                result = tripcon.import_into_saillog(
                    conn, self._config.db_path, replace=True,
                    max_px=int(self._config.photo_max_px or 1600),
                )
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            self._root.after(0, lambda: self._tripcon_failed(exc))
            return
        self._root.after(0, lambda: self._tripcon_done(result))

    def _tripcon_done(self, r: Dict) -> None:
        self._refresh_trips()
        self._refresh_logbook()
        messagebox.showinfo(
            t("TripCon-Import"),
            t("Import abgeschlossen.\n\n"
              "Einträge: {entries}\n"
              "Bilder: {images} (+{trip_images} törnweit, "
              "Methode: {method})\n"
              "Schiffe: {ships_created} neu, {ships_matched} erkannt\n"
              "Personen: {persons_created} neu, {persons_matched} erkannt",
              entries=r['entries'], images=r['images'],
              trip_images=r.get('trip_images', 0), method=r['image_method'],
              ships_created=r['ships_created'], ships_matched=r['ships_matched'],
              persons_created=r['persons_created'], persons_matched=r['persons_matched']),
        )

    def _tripcon_failed(self, exc: Exception) -> None:
        messagebox.showerror(
            t("TripCon-Import"), t("Import fehlgeschlagen:\n{error}", error=exc))

    # --- Stammdaten ---------------------------------------------------------

    def _on_manage_persons(self) -> None:
        dialog = _PersonManagerDialog(self._root, self._store)
        self._root.wait_window(dialog.top)

    def _on_manage_ships(self) -> None:
        dialog = _ShipManagerDialog(self._root, self._store, self._config)
        self._root.wait_window(dialog.top)
        # Loggeber-Korrektur des aktiven Schiffs auf laufende Quellen anwenden
        factor = self._active_log_correction()
        for src in self._sources:
            src.log_correction = factor
        # Eingabemaske an die (evtl. geänderte) Ausrüstung des Schiffs anpassen
        self._rebuild_sail_controls()

    def _active_log_correction(self) -> float:
        ship_id = self._config.active_ship_id
        if ship_id is None:
            return 1.0
        ship = self._store.get_ship(ship_id)
        return ship.log_correction if ship and ship.log_correction else 1.0

    # --- Tanken -------------------------------------------------------------

    def _on_fuel(self) -> None:
        dialog = _FuelDialog(
            self._root, self._store, self._live, self._tz_offset(),
            self._logbook.current_trip_id, self._config,
        )
        self._root.wait_window(dialog.top)

    # --- manuelle Einträge --------------------------------------------------

    def _on_new_entry(self) -> None:
        """Manueller Logbuch-Eintrag mit frei wählbarer Zeit und Position.

        Für nachträgliche Einträge (z.B. bei einem Unterbruch des Loggings),
        so wie man es in TripCon von Hand erfassen kann.
        """
        offset = self._tz_offset()
        trips = self._store.all_trips(newest_first=True)
        dialog = _NewEntryDialog(
            self._root, offset, trips, self._logbook.current_trip_id,
            self._live.snapshot(), logevents=self._logevents,
        )
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        r = dialog.result
        entry = LogEntry.from_snapshot(
            timestamp=r["timestamp"],
            entry_type="manual",
            measurements=r["measurements"],
            note=r["note"],
            crew=r["crew"],
            location=r["location"],
            trip_id=r["trip_id"],
            engine_on=r["engine_on"],
            mainsail=r["mainsail"],
            genoa_percent=r["genoa_percent"],
            spinnaker=r["spinnaker"],
            wave_height_m=r["wave_height_m"],
            cloud_cover=r["cloud_cover"],
            precipitation=r["precipitation"],
            visibility=r["visibility"],
            logevent=r["logevent"],
        )
        self._store.add(entry)
        self._refresh_logbook()

    def _on_manual_entry(self) -> None:
        dialog = _ManualEntryDialog(self._root, self._live.snapshot(),
                                    logevents=self._logevents)
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
        self._tree.heading("time", text=t("Zeit ({tz})", tz=timeutil.label(self._config.timezone_mode, self._config.timezone_offset_hours)))
        positions = []  # (lat, lon) der Log-Einträge — für die Strecke
        show_track = bool(getattr(self, "_show_track", None) and self._show_track.get())
        rows = self._store.all(limit=20000, newest_first=True, trip_id=trip_id,
                               include_track=show_track)
        img_counts = {
            eid: len(ids)
            for eid, ids in self._store.image_ids_map([e.id for e in rows]).items()
        }
        for entry in rows:
            pos = ""
            if entry.lat is not None and entry.lon is not None:
                pos = f"{entry.lat:.4f}, {entry.lon:.4f}"
                # Strecke aus den Log-Einträgen (wie gehabt) — Track-Punkte
                # zählen nicht doppelt hinein.
                if entry.entry_type != "track":
                    positions.append((entry.lat, entry.lon))
            wind = ""
            if entry.tws_kn is not None:           # wahrer Wind (für die Analyse)
                if entry.twd_deg is not None:
                    wind = f"{entry.tws_kn:.0f}kn @ {entry.twd_deg:.0f}°"
                else:
                    wind = f"{entry.tws_kn:.0f}kn"
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
                    str(img_counts[entry.id]) if entry.id in img_counts else "",
                    entry.note,
                ),
            )
        # Strecke im Törn aus der GPS-Spur (Einträge kommen neueste zuerst ->
        # für die aufsummierte Distanz chronologisch umdrehen).
        positions.reverse()
        self._trip_track_nm = geo.track_distance_nm(positions) if trip_id else 0.0
        self._trip_last_pos = positions[-1] if positions else None

        total = self._store.count(trip_id=trip_id)
        scope = t("im Törn") if trip_id else t("gesamt")
        self._count_label.config(text=t("{total} Einträge ({scope})", total=total, scope=scope))

    def _on_delete_entry(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        if not messagebox.askyesno(t("Löschen"), t("Ausgewählte Einträge löschen?")):
            return
        for iid in selection:
            self._store.delete(int(iid))
        self._refresh_logbook()

    def _on_view_image(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        data = self._store.get_image(int(selection[0]))
        if not data:
            messagebox.showinfo(t("Bild"), t("Zu diesem Eintrag ist kein Bild gespeichert."))
            return
        import tempfile
        try:
            fd, path = tempfile.mkstemp(suffix=".jpg", prefix="saillog_foto_")
            with __import__("os").fdopen(fd, "wb") as fh:
                fh.write(data)
        except OSError as exc:  # noqa: BLE001
            messagebox.showerror(t("Bild"), t("Konnte das Bild nicht öffnen:\n{error}", error=exc))
            return
        webbrowser.open(Path(path).as_uri())

    def _on_edit_entry(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        entry = self._store.get(int(selection[0]))
        if entry is None:
            return
        offset = self._tz_offset()
        ts_display = timeutil.to_display(entry.timestamp, offset)
        dialog = _EditEntryDialog(
            self._root, entry, ts_display,
            store=self._store, capture=self._plotter_jpeg,
            max_px=int(self._config.photo_max_px or 1600),
            logevents=self._logevents,
            rig_spec=self._entry_rig(entry),
        )
        self._root.wait_window(dialog.top)
        if dialog.result is not None:
            result = dict(dialog.result)
            new_ts = timeutil.from_display(result.pop("timestamp", ""), offset)
            if new_ts:
                entry.timestamp = new_ts
            for key, value in result.items():
                setattr(entry, key, value)
            entry.edited = 1
            entry.edited_dz = utc_now_iso()
            self._store.update(entry)
        # Bilder können im Dialog auch bei „Abbrechen" geändert worden sein
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
        messagebox.showinfo(t("Export"), t("{count} Einträge nach CSV exportiert.", count=count))

    def _on_export_gpx(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".gpx", filetypes=[("GPX", "*.gpx")],
            initialfile="toern.gpx",
        )
        if not path:
            return
        count = self._store.export_gpx(path, trip_id=self._logbook.current_trip_id)
        messagebox.showinfo(t("Export"), t("{count} Positionspunkte nach GPX exportiert.", count=count))

    # --- Schließen ----------------------------------------------------------

    def _on_close(self) -> None:
        self._logbook.stop_auto()
        self._stop_photo_watcher()
        for src in self._sources:
            src.stop()
        if self._map_server is not None:
            self._map_server.stop()
        if self._remote_server is not None:
            self._remote_server.stop()
        # Automatische Sicherung beim Beenden (best effort, blockiert nie)
        if self._config.backup_on_close and self._config.backup_folder:
            try:
                self._make_backup(self._config.backup_folder)
            except Exception:  # noqa: BLE001
                pass
        self._root.destroy()


def _parse_float(text: str) -> Optional[float]:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _in_date_range(iso_ts: str, von: str, bis: str, offset: float) -> bool:
    """True, wenn das (lokale) Datum von ``iso_ts`` in [von, bis] liegt.

    ``von``/``bis`` sind 'JJJJ-MM-TT' oder leer (= unbegrenzt)."""
    disp = timeutil.to_display(iso_ts, offset)
    date = disp[:10] if disp else ""
    if not date:
        return not (von or bis)
    if von and date < von.strip():
        return False
    if bis and date > bis.strip():
        return False
    return True


class _EditEntryDialog:
    """Dialog zum Bearbeiten eines bestehenden Logbuch-Eintrags.

    Auch die automatisch erfassten Messwerte (Position, SOG/COG, Tiefe, Wind)
    lassen sich korrigieren — z.B. eine falsche Koordinate, die die Tagesdistanz
    verfälscht. Zusätzlich die manuellen Felder, Zeit, Bilder und Notiz.
    """

    _ENGINE = {None: "—", 1: "ein", 0: "aus"}

    def __init__(self, parent: tk.Tk, entry, ts_display: str = "",
                 store=None, capture=None, max_px: int = 1600,
                 logevents: Optional[List[str]] = None,
                 rig_spec: "Optional[rig.RigSpec]" = None) -> None:
        self.result: Optional[Dict] = None
        self._logevents = logevents or fields.DEFAULT_LOGEVENTS
        self._rig = rig_spec
        self._sail_mode = "classic"
        self._sail_controls: list = []
        self._store = store
        self._entry_id = entry.id
        self._capture = capture
        self._max_px = max_px
        self._img_ids: List[int] = []
        self._img_index = 0
        self._thumb = None            # ImageTk-Referenz festhalten (sonst weg-GC)
        self.top = tk.Toplevel(parent)
        self.top.title(t("Eintrag bearbeiten (#{id})", id=entry.id))
        self.top.transient(parent)
        self.top.grab_set()

        # Knopfleiste unten FEST verankern — bleibt auch bei hohen (Hochkant-)
        # Bildern immer sichtbar. Der Inhalt darüber ist bei Bedarf scrollbar,
        # damit auf kleinen Laptop-Bildschirmen nichts abgeschnitten wird.
        btnbar = ttk.Frame(self.top, padding=(12, 8))
        btnbar.pack(side="bottom", fill="x")
        ttk.Button(btnbar, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(btnbar, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)
        ttk.Separator(self.top, orient="horizontal").pack(side="bottom", fill="x")

        # Scrollbarer Inhaltsbereich (Canvas + Frame)
        canvas = tk.Canvas(self.top, highlightthickness=0)
        vsb = ttk.Scrollbar(self.top, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._canvas = canvas
        frame = ttk.Frame(canvas, padding=12)
        win = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # innere Breite an die Canvas-Breite koppeln (nur vertikal scrollen)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.bind_all("<MouseWheel>", self._on_mousewheel)   # Windows/macOS
        canvas.bind_all("<Button-4>", self._on_mousewheel)     # Linux hoch
        canvas.bind_all("<Button-5>", self._on_mousewheel)     # Linux runter
        self.top.bind("<Destroy>", self._on_destroy)

        ttk.Label(frame, text=t("Typ: {type}", type=entry.entry_type),
                  foreground="#555").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))

        r = 1

        def lab(text, row, col=0):
            ttk.Label(frame, text=text).grid(row=row, column=col, sticky="e", padx=4, pady=2)

        # Automatisch erfasste Messwerte — korrigierbar (z.B. falsche Koordinate,
        # die die Tagesdistanz verfälscht).
        meas = ttk.LabelFrame(frame, text=t("Messwerte (automatisch erfasst — korrigierbar)"))
        meas.grid(row=r, column=0, columnspan=4, sticky="we", pady=(0, 8))

        def mlab(text, row, col):
            ttk.Label(meas, text=text).grid(row=row, column=col, sticky="e", padx=(8, 3), pady=2)

        def mfield(value, fmt="{:g}"):
            var = tk.StringVar(value="" if value is None else fmt.format(value))
            return var

        mlab(t("Breite (°):"), 0, 0)
        self._lat = mfield(entry.lat, "{:.6f}")
        ttk.Entry(meas, textvariable=self._lat, width=14).grid(row=0, column=1, sticky="w", padx=(0, 8))
        mlab(t("Länge (°):"), 0, 2)
        self._lon = mfield(entry.lon, "{:.6f}")
        ttk.Entry(meas, textvariable=self._lon, width=14).grid(row=0, column=3, sticky="w", padx=(0, 8))

        mlab(t("SOG (kn):"), 1, 0)
        self._sog = mfield(entry.sog_kn)
        ttk.Entry(meas, textvariable=self._sog, width=14).grid(row=1, column=1, sticky="w")
        mlab(t("COG (°):"), 1, 2)
        self._cog = mfield(entry.cog_deg)
        ttk.Entry(meas, textvariable=self._cog, width=14).grid(row=1, column=3, sticky="w")

        mlab(t("Tiefe (m):"), 2, 0)
        self._depth = mfield(entry.depth_m)
        ttk.Entry(meas, textvariable=self._depth, width=14).grid(row=2, column=1, sticky="w")
        mlab(t("Wind wahr:"), 2, 2)
        windbox = ttk.Frame(meas)
        windbox.grid(row=2, column=3, sticky="w")
        self._tws = mfield(entry.tws_kn)
        ttk.Entry(windbox, textvariable=self._tws, width=6).pack(side="left")
        ttk.Label(windbox, text=" kn @ ").pack(side="left")
        self._twd = mfield(entry.twd_deg)
        ttk.Entry(windbox, textvariable=self._twd, width=6).pack(side="left")
        ttk.Label(windbox, text=" °").pack(side="left")
        r += 1

        lab(t("Zeit (lokal):"), r)
        self._ts = tk.StringVar(value=ts_display or entry.timestamp)
        ttk.Entry(frame, textvariable=self._ts, width=24).grid(row=r, column=1, sticky="w")
        lab(t("Anlass:"), r, 2)
        self._logevent = tk.StringVar(value=entry.logevent)
        ttk.Combobox(frame, textvariable=self._logevent, width=18,
                     values=self._logevents).grid(row=r, column=3, sticky="w")
        r += 1

        self._build_edit_motor(frame, r, entry)
        r += 1
        # Segel/Antrieb adaptiv (nach Ausrüstung des Schiffs), aus sails_json vorbelegt
        self._build_edit_sails(frame, r, entry)
        r += 1

        lab(t("Bewölkung:"), r)
        self._cloud = tk.StringVar(value=fields.label_from_code(entry.cloud_cover or "—"))
        ttk.Combobox(frame, textvariable=self._cloud, width=18, state="readonly",
                     values=fields.tr_labels(CLOUD_COVER_LABELS)).grid(row=r, column=1, sticky="w")
        lab(t("Niederschlag:"), r, 2)
        self._precip = tk.StringVar(value=fields.label_from_code(entry.precipitation or "kein"))
        ttk.Combobox(frame, textvariable=self._precip, width=18, state="readonly",
                     values=fields.tr_labels(PRECIPITATION)).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Sicht:"), r)
        self._visibility = tk.StringVar(value=fields.label_from_code(entry.visibility or "—"))
        ttk.Combobox(frame, textvariable=self._visibility, width=18, state="readonly",
                     values=fields.tr_labels(VISIBILITY_LABELS)).grid(row=r, column=1, sticky="w")
        lab(t("Seegang (m):"), r, 2)
        self._wave = tk.StringVar(value="" if entry.wave_height_m is None else f"{entry.wave_height_m:g}")
        ttk.Entry(frame, textvariable=self._wave, width=10).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Ort / Hafen:"), r)
        self._location = tk.StringVar(value=entry.location)
        ttk.Entry(frame, textvariable=self._location, width=24).grid(row=r, column=1, sticky="w")
        lab(t("Crew:"), r, 2)
        self._crew = tk.StringVar(value=entry.crew)
        ttk.Entry(frame, textvariable=self._crew, width=18).grid(row=r, column=3, sticky="w")
        r += 1

        ttk.Label(frame, text=t("Notiz:")).grid(row=r, column=0, sticky="ne", padx=4, pady=2)
        self._note = tk.Text(frame, width=52, height=4)
        self._note.insert("1.0", entry.note or "")
        self._note.grid(row=r, column=1, columnspan=3, sticky="w", pady=2)
        r += 1

        if self._store is not None:
            self._build_image_panel(frame, r)
            r += 1

        # Fenstergröße an den Inhalt anpassen — aber nie höher/breiter als der
        # Bildschirm. Ist der Inhalt höher, greift die Scrollleiste; die
        # Speichern-Zeile unten bleibt dabei immer sichtbar.
        self.top.update_idletasks()
        content_w = frame.winfo_reqwidth() + vsb.winfo_reqwidth() + 4
        content_h = frame.winfo_reqheight() + btnbar.winfo_reqheight() + 12
        scr_w, scr_h = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        w = min(content_w, int(scr_w * 0.95))
        h = min(content_h, int(scr_h * 0.90))
        x = max(0, (scr_w - w) // 2)
        y = max(0, (scr_h - h) // 3)
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        self.top.minsize(min(content_w, 480), 320)

    def _on_mousewheel(self, event) -> None:
        canvas = getattr(self, "_canvas", None)
        if canvas is None:
            return
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(1, "units")
        elif getattr(event, "delta", 0):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_destroy(self, event) -> None:
        # globale Mausrad-Bindungen wieder lösen, sobald der Dialog schließt
        if event.widget is self.top:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                try:
                    self.top.unbind_all(seq)
                except Exception:  # noqa: BLE001
                    pass
            self._canvas = None

    # --- Bilder (mehrere je Eintrag: ansehen, blättern, +/−) ---------------

    def _build_image_panel(self, frame, row) -> None:
        imgf = ttk.LabelFrame(frame, text=t("Bilder"))
        imgf.grid(row=row, column=0, columnspan=4, sticky="we", pady=(8, 0))
        self._img_label = ttk.Label(imgf, text=t("(kein Bild)"), anchor="center")
        self._img_label.grid(row=0, column=0, columnspan=6, pady=4)
        self._img_caption = ttk.Label(imgf, text="", foreground="#555")
        self._img_caption.grid(row=1, column=0, columnspan=6)
        self._prev_btn = ttk.Button(imgf, text="◀", width=3, command=self._img_prev)
        self._prev_btn.grid(row=2, column=0, padx=2, pady=4)
        self._next_btn = ttk.Button(imgf, text="▶", width=3, command=self._img_next)
        self._next_btn.grid(row=2, column=1, padx=2)
        ttk.Button(imgf, text=t("+ Festplatte"), command=self._img_add_disk).grid(
            row=2, column=2, padx=(10, 2))
        self._plotter_add_btn = ttk.Button(imgf, text=t("+ Plotter"),
                                           command=self._img_add_plotter)
        self._plotter_add_btn.grid(row=2, column=3, padx=2)
        ttk.Button(imgf, text=t("Öffnen"), command=self._img_open).grid(row=2, column=4, padx=2)
        self._del_btn = ttk.Button(imgf, text=t("Löschen"), command=self._img_delete)
        self._del_btn.grid(row=2, column=5, padx=(2, 2))
        self._reload_images()

    def _reload_images(self, select_last: bool = False) -> None:
        self._img_ids = self._store.image_ids(self._entry_id)
        if select_last and self._img_ids:
            self._img_index = len(self._img_ids) - 1
        self._img_index = max(0, min(self._img_index, max(0, len(self._img_ids) - 1)))
        self._render_thumb()

    def _render_thumb(self) -> None:
        n = len(self._img_ids)
        has = n > 0
        for btn in (self._prev_btn, self._next_btn, self._del_btn):
            btn.config(state="normal" if has else "disabled")
        if not has:
            self._img_label.config(image="", text=t("(kein Bild — über + hinzufügen)"))
            self._img_caption.config(text="")
            self._thumb = None
            return
        rec = self._store.get_image_by_id(self._img_ids[self._img_index])
        self._img_caption.config(text=t("Bild {n}/{total}", n=self._img_index + 1, total=n))
        try:
            import io
            from PIL import Image, ImageTk
            im = Image.open(io.BytesIO(rec[0]))
            # Höhe stärker begrenzen als Breite, damit ein Hochformat-Bild den
            # Dialog nicht unnötig in die Höhe treibt (Rest löst die Scrollleiste).
            im.thumbnail((380, 300))
            self._thumb = ImageTk.PhotoImage(im)
            self._img_label.config(image=self._thumb, text="")
        except Exception:  # noqa: BLE001  (kein Pillow → Text-Fallback)
            self._thumb = None
            self._img_label.config(
                image="", text=t("(Vorschau braucht Pillow — 'Öffnen' zum Ansehen)"))

    def _img_prev(self) -> None:
        if self._img_ids:
            self._img_index = (self._img_index - 1) % len(self._img_ids)
            self._render_thumb()

    def _img_next(self) -> None:
        if self._img_ids:
            self._img_index = (self._img_index + 1) % len(self._img_ids)
            self._render_thumb()

    def _img_add_disk(self) -> None:
        path = filedialog.askopenfilename(
            title=t("Bild hinzufügen"),
            filetypes=[(t("Bilder"), "*.jpg *.jpeg *.png *.bmp *.gif *.tif *.tiff *.webp"),
                       (t("Alle Dateien"), "*.*")],
        )
        if not path:
            return
        jpeg = photos.resize_to_jpeg(path, self._max_px)
        if not jpeg:
            messagebox.showwarning(t("Bild"), t("Bild konnte nicht gelesen/verkleinert werden "
                                                "(Pillow nötig)."))
            return
        self._store.add_entry_image(self._entry_id, jpeg, "image/jpeg", utc_now_iso())
        self._reload_images(select_last=True)

    def _img_add_plotter(self) -> None:
        if self._capture is None:
            return
        self._img_caption.config(text=t("Plotter-Screenshot …"))
        self.top.update_idletasks()
        jpeg = self._capture()
        if not jpeg:
            messagebox.showerror(
                t("Plotter-Screenshot"),
                t("Kein Screenshot erhalten (adb-Pfad/Gerät prüfen, Extras → "
                  "Plotter-Screenshot…)."))
            self._render_thumb()
            return
        self._store.add_entry_image(self._entry_id, jpeg, "image/jpeg", utc_now_iso())
        self._reload_images(select_last=True)

    def _img_delete(self) -> None:
        if not self._img_ids:
            return
        if not messagebox.askyesno(t("Bild löschen"), t("Dieses Bild wirklich löschen?")):
            return
        self._store.delete_entry_image(self._img_ids[self._img_index])
        self._reload_images()

    def _img_open(self) -> None:
        if not self._img_ids:
            return
        rec = self._store.get_image_by_id(self._img_ids[self._img_index])
        if not rec:
            return
        import os
        import tempfile
        ext = ".jpg" if rec[1] and "jpeg" in rec[1] else ".png"
        try:
            fd, path = tempfile.mkstemp(suffix=ext, prefix="saillog_foto_")
            with os.fdopen(fd, "wb") as fh:
                fh.write(rec[0])
        except OSError as exc:  # noqa: BLE001
            messagebox.showerror(t("Bild"), t("Konnte das Bild nicht öffnen:\n{error}", error=exc))
            return
        webbrowser.open(Path(path).as_uri())

    def _build_edit_motor(self, frame, row, entry) -> None:
        self._motor_vars = {}
        motors = list(self._rig.motors) if self._rig is not None else []
        try:
            saved = json.loads(entry.motors_json) if entry.motors_json else {}
        except Exception:  # noqa: BLE001
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        for n in saved:
            if n not in motors:
                motors.append(n)
        if len(motors) >= 2:
            self._motor_mode = "multi"
            ttk.Label(frame, text=t("Motoren:")).grid(row=row, column=0, sticky="ne", padx=4, pady=2)
            box = ttk.Frame(frame)
            box.grid(row=row, column=1, columnspan=3, sticky="w")
            for n in motors:
                var = tk.BooleanVar(value=bool(saved.get(n)))
                ttk.Checkbutton(box, text=t("{name} läuft", name=n), variable=var).pack(anchor="w")
                self._motor_vars[n] = var
        else:
            self._motor_mode = "single"
            ttk.Label(frame, text=t("Motor:")).grid(row=row, column=0, sticky="e", padx=4, pady=2)
            self._engine = tk.StringVar(
                value=fields.label_from_code(self._ENGINE.get(entry.engine_on, "—")))
            ttk.Combobox(frame, textvariable=self._engine, width=18, state="readonly",
                         values=fields.tr_labels(["—", "ein", "aus"])).grid(row=row, column=1, sticky="w")

    def _motor_result(self) -> Dict:
        if getattr(self, "_motor_mode", "single") == "multi":
            m = {n: (1 if var.get() else 0) for n, var in self._motor_vars.items()}
            return {"engine_on": 1 if any(m.values()) else 0,
                    "motors_json": json.dumps(m, ensure_ascii=False)}
        engine_map = {"—": None, "ein": 1, "aus": 0}
        eng = fields.code_from_label(["—", "ein", "aus"], self._engine.get())
        return {"engine_on": engine_map.get(eng), "motors_json": ""}

    @staticmethod
    def _infer_control(value) -> str:
        try:
            float(value)
            return rig.CONTROL_ROLLER
        except (TypeError, ValueError):
            pass
        if str(value) in ("Reff 1", "Reff 2", "Reff 3"):
            return rig.CONTROL_SLAB
        return rig.CONTROL_FIXED

    def _build_edit_sails(self, frame, row, entry) -> None:
        box = ttk.LabelFrame(frame, text=t("Segel / Antrieb"))
        box.grid(row=row, column=0, columnspan=4, sticky="we", pady=(2, 4))
        spec = self._rig
        try:
            saved = json.loads(entry.sails_json) if entry.sails_json else {}
        except Exception:  # noqa: BLE001
            saved = {}
        if not isinstance(saved, dict):
            saved = {}

        def build_adaptive(sails):
            self._sail_mode = "adaptive"
            for i, sail in enumerate(sails):
                ttk.Label(box, text=sail.name + ":").grid(
                    row=i, column=0, sticky="e", padx=(6, 3), pady=2)
                init = saved.get(sail.name)
                if sail.control == rig.CONTROL_ROLLER:
                    try:
                        start = int(float(init))
                    except (TypeError, ValueError):
                        start = 0
                    var = tk.IntVar(value=start)
                    tk.Scale(box, from_=0, to=100, orient="horizontal", length=170,
                             variable=var, showvalue=1).grid(row=i, column=1, sticky="w")
                    ttk.Label(box, text="%").grid(row=i, column=2, sticky="w")
                elif sail.control == rig.CONTROL_SLAB:
                    code = init if init in rig.SLAB_STATES else "nicht gesetzt"
                    var = tk.StringVar(value=fields.label_from_code(code))
                    ttk.Combobox(box, textvariable=var, width=14, state="readonly",
                                 values=fields.tr_labels(rig.SLAB_STATES)).grid(
                        row=i, column=1, sticky="w")
                else:
                    var = tk.BooleanVar(value=(init == "gesetzt"))
                    ttk.Checkbutton(box, text=t("gesetzt"), variable=var).grid(
                        row=i, column=1, sticky="w")
                self._sail_controls.append((sail, var))

        if saved:
            # Segel aus dem Rig, plus gespeicherte Namen, die (noch) nicht im Rig sind
            sails = list(spec.sails) if spec is not None else []
            known = {s.name for s in sails}
            for name in saved:
                if name not in known:
                    sails.append(rig.SailControl(name=name, category="",
                                                 control=self._infer_control(saved[name])))
            build_adaptive(sails)
        elif spec is not None and spec.is_motorboat:
            self._sail_mode = "motor"
            txt = t("🛥 Motorboot — keine Segel")
            if spec.motors:
                txt += "   (" + ", ".join(spec.motors) + ")"
            ttk.Label(box, text=txt, foreground="#555").grid(
                row=0, column=0, columnspan=3, sticky="w", padx=6, pady=4)
        elif spec is not None and spec.configured:
            build_adaptive(list(spec.sails))
        else:
            # klassisch (kein Schiff/keine Ausrüstung, kein sails_json)
            self._sail_mode = "classic"
            ttk.Label(box, text=t("Großsegel:")).grid(row=0, column=0, sticky="e", padx=(6, 3), pady=2)
            self._mainsail = tk.StringVar(value=fields.label_from_code(entry.mainsail or "—"))
            ttk.Combobox(box, textvariable=self._mainsail, width=16, state="readonly",
                         values=fields.tr_labels(MAINSAIL_OPTIONS)).grid(row=0, column=1, sticky="w")
            ttk.Label(box, text=t("Genua %:")).grid(row=1, column=0, sticky="e", padx=(6, 3), pady=2)
            self._genoa = tk.StringVar(
                value="" if entry.genoa_percent is None else f"{entry.genoa_percent:g}")
            ttk.Spinbox(box, from_=0, to=100, textvariable=self._genoa, width=8).grid(
                row=1, column=1, sticky="w")
            ttk.Label(box, text=t("Spinnaker:")).grid(row=2, column=0, sticky="e", padx=(6, 3), pady=2)
            self._spinnaker = tk.BooleanVar(value=bool(entry.spinnaker))
            ttk.Checkbutton(box, text=t("gesetzt"), variable=self._spinnaker).grid(
                row=2, column=1, sticky="w")

    def _sail_result(self) -> Dict:
        """Segel-Felder fürs Speichern (adaptiv -> sails_json + Kurzfassung)."""
        if self._sail_mode == "adaptive":
            states: Dict[str, object] = {}
            for sail, var in self._sail_controls:
                if isinstance(var, tk.BooleanVar):
                    val = "gesetzt" if var.get() else "nicht gesetzt"
                elif isinstance(var, tk.IntVar):
                    val = var.get()
                else:  # SLAB: übersetztes Label -> Code
                    val = fields.code_from_label(rig.SLAB_STATES, var.get())
                states[sail.name] = val
            spec = rig.RigSpec(sails=[s for s, _ in self._sail_controls])
            return {
                "mainsail": rig.summarize(states, spec),
                "genoa_percent": None,
                "spinnaker": None,
                "sails_json": json.dumps(states, ensure_ascii=False),
            }
        if self._sail_mode == "motor":
            return {"mainsail": "", "genoa_percent": None, "spinnaker": None, "sails_json": ""}
        main = fields.code_from_label(MAINSAIL_OPTIONS, self._mainsail.get())
        return {
            "mainsail": main if main != "—" else "",
            "genoa_percent": _parse_float(self._genoa.get()),
            "spinnaker": 1 if self._spinnaker.get() else 0,
            "sails_json": "",
        }

    def _on_save(self) -> None:
        cloud = fields.code_from_label(CLOUD_COVER_LABELS, self._cloud.get())
        precip = fields.code_from_label(PRECIPITATION, self._precip.get())
        vis = fields.code_from_label(VISIBILITY_LABELS, self._visibility.get())
        self.result = {
            "timestamp": self._ts.get().strip(),
            "lat": _parse_float(self._lat.get()),
            "lon": _parse_float(self._lon.get()),
            "sog_kn": _parse_float(self._sog.get()),
            "cog_deg": _parse_float(self._cog.get()),
            "depth_m": _parse_float(self._depth.get()),
            "tws_kn": _parse_float(self._tws.get()),
            "twd_deg": _parse_float(self._twd.get()),
            "logevent": self._logevent.get().strip(),
            "cloud_cover": cloud if cloud != "—" else "",
            "precipitation": precip if precip != "kein" else "",
            "visibility": vis if vis != "—" else "",
            "wave_height_m": _parse_float(self._wave.get()),
            "location": self._location.get().strip(),
            "crew": self._crew.get().strip(),
            "note": self._note.get("1.0", "end").strip(),
        }
        self.result.update(self._sail_result())
        self.result.update(self._motor_result())
        self.top.destroy()


class _ManualEntryDialog:
    """Dialog für einen manuellen Logbuch-Eintrag mit Auto-Fill."""

    def __init__(self, parent: tk.Tk, snapshot: Dict[str, float],
                 logevents: Optional[List[str]] = None) -> None:
        self.result: Optional[Dict] = None
        self._logevents = logevents or fields.DEFAULT_LOGEVENTS
        self.top = tk.Toplevel(parent)
        self.top.title(t("Manueller Eintrag"))
        self.top.transient(parent)
        self.top.grab_set()

        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        parts = []
        if "lat" in snapshot and "lon" in snapshot:
            parts.append(t("Pos {lat}, {lon}", lat=f"{snapshot['lat']:.4f}", lon=f"{snapshot['lon']:.4f}"))
        if "sog_kn" in snapshot:
            parts.append(t("SOG {sog}kn", sog=f"{snapshot['sog_kn']:.1f}"))
        if "aws_kn" in snapshot:
            parts.append(t("Wind {wind}kn", wind=f"{snapshot['aws_kn']:.0f}"))
        if snapshot.get("engine_rpm") is not None:
            parts.append(t("Motor {rpm} U/min", rpm=f"{snapshot['engine_rpm']:.0f}"))
        info = " · ".join(parts) if parts else t("Keine Live-Messwerte (nur Text wird gespeichert).")
        ttk.Label(frame, text=info, foreground="#555").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )

        row = 1

        def add_label(text, r, c=0):
            ttk.Label(frame, text=text).grid(row=r, column=c, sticky="e", padx=4, pady=3)

        # Ort / Crew
        add_label(t("Ort / Hafen:"), row)
        self._location = tk.StringVar()
        ttk.Entry(frame, textvariable=self._location, width=28).grid(row=row, column=1, pady=3, sticky="w")
        add_label(t("Crew:"), row, 2)
        self._crew = tk.StringVar()
        ttk.Entry(frame, textvariable=self._crew, width=22).grid(row=row, column=3, pady=3, sticky="w")
        row += 1

        # Motor
        add_label(t("Motor:"), row)
        auto = snapshot.get("engine_rpm")
        auto_hint = ""
        if auto is not None:
            auto_hint = t(" (erkannt: {state})", state=t("ein") if auto > 0 else t("aus"))
        self._engine = tk.StringVar(value=t("automatisch"))
        ttk.Combobox(
            frame, textvariable=self._engine, width=25, state="readonly",
            values=fields.tr_labels(["automatisch", "ein", "aus"]),
        ).grid(row=row, column=1, pady=3, sticky="w")
        ttk.Label(frame, text=auto_hint, foreground="#888").grid(row=row, column=2, columnspan=2, sticky="w")
        row += 1

        # Großsegel / Genua / Spinnaker
        add_label(t("Großsegel:"), row)
        self._mainsail = tk.StringVar(value="—")
        ttk.Combobox(
            frame, textvariable=self._mainsail, width=25, state="readonly",
            values=fields.tr_labels(MAINSAIL_OPTIONS),
        ).grid(row=row, column=1, pady=3, sticky="w")
        add_label(t("Genua %:"), row, 2)
        self._genoa = tk.StringVar()
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._genoa, width=8).grid(
            row=row, column=3, pady=3, sticky="w"
        )
        row += 1

        add_label(t("Spinnaker:"), row)
        self._spinnaker = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=t("gesetzt"), variable=self._spinnaker).grid(
            row=row, column=1, sticky="w", pady=3
        )
        row += 1

        # Wetter
        add_label(t("Wellenhöhe (m):"), row)
        self._wave = tk.StringVar()
        ttk.Entry(frame, textvariable=self._wave, width=10).grid(row=row, column=1, pady=3, sticky="w")
        row += 1

        add_label(t("Bewölkung:"), row)
        self._cloud = tk.StringVar(value="—")
        self._cloud_combo = ttk.Combobox(
            frame, textvariable=self._cloud, width=25, state="readonly",
            values=fields.tr_labels(CLOUD_COVER_LABELS),
        )
        self._cloud_combo.grid(row=row, column=1, pady=3, sticky="w")
        self._cloud_hint = ttk.Label(frame, text="", foreground="#888")
        self._cloud_hint.grid(row=row, column=2, columnspan=2, sticky="w")
        self._cloud_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._cloud_hint.config(
                text=cloud_hint(fields.code_from_label(CLOUD_COVER_LABELS, self._cloud.get()))),
        )
        row += 1

        add_label(t("Niederschlag:"), row)
        self._precip = tk.StringVar(value=t("kein"))
        ttk.Combobox(
            frame, textvariable=self._precip, width=25, state="readonly",
            values=fields.tr_labels(PRECIPITATION),
        ).grid(row=row, column=1, pady=3, sticky="w")
        row += 1

        add_label(t("Sicht:"), row)
        self._visibility = tk.StringVar(value="—")
        self._vis_combo = ttk.Combobox(
            frame, textvariable=self._visibility, width=25, state="readonly",
            values=fields.tr_labels(VISIBILITY_LABELS),
        )
        self._vis_combo.grid(row=row, column=1, pady=3, sticky="w")
        self._vis_hint = ttk.Label(frame, text="", foreground="#888")
        self._vis_hint.grid(row=row, column=2, columnspan=2, sticky="w")
        self._vis_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e: self._vis_hint.config(
                text=visibility_hint(fields.code_from_label(VISIBILITY_LABELS, self._visibility.get()))),
        )
        row += 1

        # Notiz
        ttk.Label(frame, text=t("Notiz:")).grid(row=row, column=0, sticky="ne", padx=4, pady=3)
        self._note = tk.Text(frame, width=52, height=5)
        self._note.grid(row=row, column=1, columnspan=3, pady=3, sticky="w")
        row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=row, column=0, columnspan=4, pady=(10, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

        self._note.focus_set()

    def _on_save(self) -> None:
        engine_map = {"automatisch": None, "ein": 1, "aus": 0}
        eng = fields.code_from_label(["automatisch", "ein", "aus"], self._engine.get())
        main = fields.code_from_label(MAINSAIL_OPTIONS, self._mainsail.get())
        cloud = fields.code_from_label(CLOUD_COVER_LABELS, self._cloud.get())
        precip = fields.code_from_label(PRECIPITATION, self._precip.get())
        vis = fields.code_from_label(VISIBILITY_LABELS, self._visibility.get())
        genoa = _parse_float(self._genoa.get())
        self.result = {
            "note": self._note.get("1.0", "end").strip(),
            "crew": self._crew.get().strip(),
            "location": self._location.get().strip(),
            "engine_on": engine_map.get(eng),
            "mainsail": main if main != "—" else "",
            "genoa_percent": genoa,
            "spinnaker": 1 if self._spinnaker.get() else 0,
            "wave_height_m": _parse_float(self._wave.get()),
            "cloud_cover": cloud if cloud != "—" else "",
            "precipitation": precip if precip != "kein" else "",
            "visibility": vis if vis != "—" else "",
        }
        self.top.destroy()


class _TripEditDialog:
    """Bearbeitet die Stammdaten eines bestehenden Törns.

    Für Tippfehler-Korrekturen bei von Hand erfassten (älteren) Törns:
    Name, Start-/Zielort, Start-/Endzeit sowie die Kennwerte (Wasser, Diesel,
    Motorstunden, Log-Stand) und die Notiz lassen sich ändern.
    """

    def __init__(self, parent: tk.Tk, trip: Trip, offset: float = 0.0,
                 ships: Optional[List["Ship"]] = None) -> None:
        self.result: Optional[Dict] = None
        self._offset = offset
        self.top = tk.Toplevel(parent)
        self.top.title(t("Törn bearbeiten (#{id})", id=trip.id))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._vars: Dict[str, tk.StringVar] = {}
        # (Schlüssel, Beschriftung, Vorgabewert) — Zeiten in lokaler Anzeige
        rows = [
            ("name", t("Törn-Name:"), trip.name or ""),
            ("start_location", t("Startort:"), trip.start_location or ""),
            ("start_dz", t("Start (lokal):"), timeutil.to_display(trip.start_dz, offset)),
            ("end_location", t("Zielort:"), trip.end_location or ""),
            ("end_dz", t("Ende (lokal):"), timeutil.to_display(trip.end_dz, offset)),
            ("start_water_l", t("Wasser Start (l):"), _fmt_opt(trip.start_water_l)),
            ("start_diesel_l", t("Diesel Start (l):"), _fmt_opt(trip.start_diesel_l)),
            ("start_engine_hours", t("Motorstd. Start:"), _fmt_opt(trip.start_engine_hours)),
            ("start_log_nm", t("Log Start (Nm):"), _fmt_opt(trip.start_log_nm)),
            ("end_water_l", t("Wasser Ende (l):"), _fmt_opt(trip.end_water_l)),
            ("end_diesel_l", t("Diesel Ende (l):"), _fmt_opt(trip.end_diesel_l)),
            ("end_engine_hours", t("Motorstd. Ende:"), _fmt_opt(trip.end_engine_hours)),
            ("end_log_nm", t("Log Ende (Nm):"), _fmt_opt(trip.end_log_nm)),
            ("distance_nm", t("Seemeilen (manuell):"), _fmt_opt(trip.distance_nm)),
        ]
        for i, (key, label, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=4, pady=3)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=32).grid(
                row=i, column=1, pady=3, sticky="w")
            self._vars[key] = var

        r = len(rows)
        # Schiff des Törns (fest eintragbar — für den Meilennachweis, wenn
        # Törns auf verschiedenen Schiffen gefahren wurden)
        ttk.Label(frame, text=t("Schiff:")).grid(row=r, column=0, sticky="e", padx=4, pady=3)
        self._ship_choices: Dict[str, Optional[int]] = {"— (aktives Schiff)": None}
        for s in (ships or []):
            self._ship_choices[f"#{s.id} {s.name or '(ohne Name)'}"] = s.id
        self._ship_var = tk.StringVar()
        cur_ship = next((d for d, i in self._ship_choices.items()
                         if i == trip.ship_id), "— (aktives Schiff)")
        self._ship_var.set(cur_ship)
        ttk.Combobox(frame, textvariable=self._ship_var, width=30, state="readonly",
                     values=list(self._ship_choices.keys())).grid(
            row=r, column=1, pady=3, sticky="w")
        r += 1

        ttk.Label(frame, text=t("Notiz:")).grid(row=r, column=0, sticky="ne", padx=4, pady=3)
        self._note = tk.Text(frame, width=40, height=4)
        self._note.insert("1.0", trip.note or "")
        self._note.grid(row=r, column=1, pady=3, sticky="w")
        r += 1

        ttk.Label(frame, text=t("(Zeiten in der eingestellten Zeitzone, "
                              "Format JJJJ-MM-TT HH:MM)"), foreground="#888").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(2, 0))
        r += 1
        ttk.Label(frame, text=t("Seemeilen (manuell): leer = aus GPS-Spur berechnen; "
                              "gesetzt = überschreibt den Meilennachweis."),
                  foreground="#888").grid(
            row=r, column=0, columnspan=2, sticky="w", pady=(0, 6))
        r += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=r, column=0, columnspan=2, pady=(6, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        v = self._vars
        self.result = {
            "name": v["name"].get().strip(),
            "start_location": v["start_location"].get().strip(),
            "start_dz": timeutil.from_display(v["start_dz"].get().strip(), self._offset),
            "end_location": v["end_location"].get().strip(),
            "end_dz": timeutil.from_display(v["end_dz"].get().strip(), self._offset),
            "start_water_l": _parse_float(v["start_water_l"].get()),
            "start_diesel_l": _parse_float(v["start_diesel_l"].get()),
            "start_engine_hours": _parse_float(v["start_engine_hours"].get()),
            "start_log_nm": _parse_float(v["start_log_nm"].get()),
            "end_water_l": _parse_float(v["end_water_l"].get()),
            "end_diesel_l": _parse_float(v["end_diesel_l"].get()),
            "end_engine_hours": _parse_float(v["end_engine_hours"].get()),
            "end_log_nm": _parse_float(v["end_log_nm"].get()),
            "distance_nm": _parse_float(v["distance_nm"].get()),
            "ship_id": self._ship_choices.get(self._ship_var.get()),
            "note": self._note.get("1.0", "end").strip(),
        }
        self.top.destroy()


class _NewEntryDialog:
    """Manueller Logbuch-Eintrag mit frei wählbarer Zeit und Position.

    Für nachträglich erfasste Einträge (z.B. bei einem Unterbruch des
    Loggings), so wie man sie in TripCon von Hand einträgt.
    """

    _ENGINE = {None: "—", 1: "ein", 0: "aus"}

    def __init__(self, parent: tk.Tk, offset: float, trips: List[Trip],
                 current_trip_id: Optional[int],
                 snapshot: Optional[Dict[str, float]] = None,
                 logevents: Optional[List[str]] = None) -> None:
        self.result: Optional[Dict] = None
        self._offset = offset
        self._logevents = logevents or fields.DEFAULT_LOGEVENTS
        self.top = tk.Toplevel(parent)
        self.top.title(t("Neuer Eintrag (manuell)"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        r = 0

        def lab(text, row, col=0):
            ttk.Label(frame, text=text).grid(row=row, column=col, sticky="e", padx=4, pady=2)

        # Zeit + Törn
        lab(t("Zeit (lokal):"), r)
        now_local = timeutil.to_display(utc_now_iso(), offset)
        self._ts = tk.StringVar(value=now_local)
        ttk.Entry(frame, textvariable=self._ts, width=22).grid(row=r, column=1, sticky="w")
        lab(t("Törn:"), r, 2)
        self._trip_choices: Dict[str, Optional[int]] = {"— (kein Törn)": None}
        for tr in trips:
            route = f"{tr.start_location or '?'} → {tr.end_location or '…'}"
            self._trip_choices[f"#{tr.id} {tr.name or route}"] = tr.id
        self._trip_var = tk.StringVar()
        cur = next((d for d, idx in self._trip_choices.items() if idx == current_trip_id),
                   "— (kein Törn)")
        self._trip_var.set(cur)
        ttk.Combobox(frame, textvariable=self._trip_var, width=26, state="readonly",
                     values=list(self._trip_choices.keys())).grid(
            row=r, column=3, sticky="w")
        r += 1

        lab(t("Anlass:"), r)
        self._logevent = tk.StringVar(value=self._logevents[0])
        ttk.Combobox(frame, textvariable=self._logevent, width=19,
                     values=self._logevents).grid(row=r, column=1, sticky="w")
        lab(t("Ort / Hafen:"), r, 2)
        self._location = tk.StringVar()
        ttk.Entry(frame, textvariable=self._location, width=28).grid(
            row=r, column=3, sticky="w")
        r += 1

        # Position + Bewegung
        lab(t("Breite (°):"), r)
        self._lat = tk.StringVar()
        ttk.Entry(frame, textvariable=self._lat, width=22).grid(row=r, column=1, sticky="w")
        lab(t("Länge (°):"), r, 2)
        self._lon = tk.StringVar()
        ttk.Entry(frame, textvariable=self._lon, width=28).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("SOG (kn):"), r)
        self._sog = tk.StringVar()
        ttk.Entry(frame, textvariable=self._sog, width=22).grid(row=r, column=1, sticky="w")
        lab(t("COG (°):"), r, 2)
        self._cog = tk.StringVar()
        ttk.Entry(frame, textvariable=self._cog, width=28).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Tiefe (m):"), r)
        self._depth = tk.StringVar()
        ttk.Entry(frame, textvariable=self._depth, width=22).grid(row=r, column=1, sticky="w")
        lab(t("Wind wahr (kn / °):"), r, 2)
        wind = ttk.Frame(frame)
        wind.grid(row=r, column=3, sticky="w")
        self._tws = tk.StringVar()
        ttk.Entry(wind, textvariable=self._tws, width=8).pack(side="left")
        ttk.Label(wind, text=" kn @ ").pack(side="left")
        self._twd = tk.StringVar()
        ttk.Entry(wind, textvariable=self._twd, width=8).pack(side="left")
        ttk.Label(wind, text=" °").pack(side="left")
        r += 1

        # Position/Bewegung aus den Live-Daten vorbelegen — bei angeschlossener
        # GPS-Maus (oder Instrumentennetz) muss man Position, SOG und COG so
        # nicht von Hand eintippen. Alle Felder bleiben editierbar.
        snap = snapshot or {}

        def _pre(var, key, dec):
            v = snap.get(key)
            if v is not None:
                var.set(f"{v:.{dec}f}".replace(".", ","))

        _pre(self._lat, "lat", 5)
        _pre(self._lon, "lon", 5)
        _pre(self._sog, "sog_kn", 1)
        _pre(self._cog, "cog_deg", 0)
        _pre(self._depth, "depth_m", 1)
        _pre(self._tws, "tws_kn", 1)
        _pre(self._twd, "twd_deg", 0)

        # Motor / Segel
        lab(t("Motor:"), r)
        self._engine = tk.StringVar(value="—")
        ttk.Combobox(frame, textvariable=self._engine, width=19, state="readonly",
                     values=fields.tr_labels(["—", "ein", "aus"])).grid(row=r, column=1, sticky="w")
        lab(t("Großsegel:"), r, 2)
        self._mainsail = tk.StringVar(value="—")
        ttk.Combobox(frame, textvariable=self._mainsail, width=26, state="readonly",
                     values=fields.tr_labels(MAINSAIL_OPTIONS)).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Genua %:"), r)
        self._genoa = tk.StringVar()
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._genoa, width=8).grid(
            row=r, column=1, sticky="w")
        lab(t("Spinnaker:"), r, 2)
        self._spinnaker = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text=t("gesetzt"), variable=self._spinnaker).grid(
            row=r, column=3, sticky="w")
        r += 1

        # Wetter
        lab(t("Bewölkung:"), r)
        self._cloud = tk.StringVar(value="—")
        ttk.Combobox(frame, textvariable=self._cloud, width=19, state="readonly",
                     values=fields.tr_labels(CLOUD_COVER_LABELS)).grid(row=r, column=1, sticky="w")
        lab(t("Niederschlag:"), r, 2)
        self._precip = tk.StringVar(value=t("kein"))
        ttk.Combobox(frame, textvariable=self._precip, width=26, state="readonly",
                     values=fields.tr_labels(PRECIPITATION)).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Sicht:"), r)
        self._visibility = tk.StringVar(value="—")
        ttk.Combobox(frame, textvariable=self._visibility, width=19, state="readonly",
                     values=fields.tr_labels(VISIBILITY_LABELS)).grid(row=r, column=1, sticky="w")
        lab(t("Seegang (m):"), r, 2)
        self._wave = tk.StringVar()
        ttk.Entry(frame, textvariable=self._wave, width=28).grid(row=r, column=3, sticky="w")
        r += 1

        lab(t("Crew:"), r)
        self._crew = tk.StringVar()
        ttk.Entry(frame, textvariable=self._crew, width=22).grid(row=r, column=1, sticky="w")
        r += 1

        ttk.Label(frame, text=t("Notiz:")).grid(row=r, column=0, sticky="ne", padx=4, pady=2)
        self._note = tk.Text(frame, width=52, height=4)
        self._note.grid(row=r, column=1, columnspan=3, sticky="w", pady=2)
        r += 1

        ttk.Label(frame, text=t("(Position in Dezimalgrad, z.B. 54,806 / 9,451 — "
                                "S/W als Minuszeichen)"), foreground="#888").grid(
            row=r, column=0, columnspan=4, sticky="w", pady=(2, 6))
        r += 1

        buttons = ttk.Frame(frame)
        buttons.grid(row=r, column=0, columnspan=4, pady=(6, 0))
        ttk.Button(buttons, text=t("Eintrag speichern"), command=self._on_save).pack(
            side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(
            side="left", padx=4)

    def _on_save(self) -> None:
        engine_map = {"—": None, "ein": 1, "aus": 0}
        eng = fields.code_from_label(["—", "ein", "aus"], self._engine.get())
        main = fields.code_from_label(MAINSAIL_OPTIONS, self._mainsail.get())
        cloud = fields.code_from_label(CLOUD_COVER_LABELS, self._cloud.get())
        precip = fields.code_from_label(PRECIPITATION, self._precip.get())
        vis = fields.code_from_label(VISIBILITY_LABELS, self._visibility.get())
        measurements: Dict[str, float] = {}
        for key, var in (("lat", self._lat), ("lon", self._lon), ("sog_kn", self._sog),
                         ("cog_deg", self._cog), ("depth_m", self._depth),
                         ("tws_kn", self._tws), ("twd_deg", self._twd)):
            val = _parse_float(var.get())
            if val is not None:
                measurements[key] = val
        self.result = {
            "timestamp": timeutil.from_display(self._ts.get().strip(), self._offset)
            or utc_now_iso(),
            "trip_id": self._trip_choices.get(self._trip_var.get()),
            "measurements": measurements,
            "logevent": self._logevent.get().strip(),
            "location": self._location.get().strip(),
            "crew": self._crew.get().strip(),
            "engine_on": engine_map.get(eng),
            "mainsail": main if main != "—" else "",
            "genoa_percent": _parse_float(self._genoa.get()),
            "spinnaker": 1 if self._spinnaker.get() else 0,
            "wave_height_m": _parse_float(self._wave.get()),
            "cloud_cover": cloud if cloud != "—" else "",
            "precipitation": precip if precip != "kein" else "",
            "visibility": vis if vis != "—" else "",
            "note": self._note.get("1.0", "end").strip(),
        }
        self.top.destroy()


class _LogMapDialog:
    """Auswahl für die Logbuch-Karte (ohne AIS): welche Eintragstypen zeigen?"""

    def __init__(self, parent: tk.Tk) -> None:
        self.result: Optional[set] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Logbuch-Karte"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("Karte ohne AIS — welche Einträge sollen angezeigt "
                              "werden?")).grid(row=0, column=0, sticky="w", pady=(0, 8))
        self._auto = tk.BooleanVar(value=True)
        self._manual = tk.BooleanVar(value=True)
        self._tripcon = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text=t("Autolog-Einträge"), variable=self._auto).grid(
            row=1, column=0, sticky="w")
        ttk.Checkbutton(frame, text=t("Manuelle Einträge"), variable=self._manual).grid(
            row=2, column=0, sticky="w")
        ttk.Checkbutton(frame, text=t("Importierte Einträge (TripCon)"),
                        variable=self._tripcon).grid(row=3, column=0, sticky="w")

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, pady=(12, 0), sticky="w")
        ttk.Button(buttons, text=t("Karte öffnen"), command=self._on_ok).pack(
            side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(
            side="left", padx=4)

    def _on_ok(self) -> None:
        types = set()
        if self._auto.get():
            types.add("auto")
        if self._manual.get():
            types.add("manual")
        if self._tripcon.get():
            types.add("tripcon")
        if not types:
            messagebox.showinfo(t("Logbuch-Karte"), t("Bitte mindestens einen Eintragstyp "
                                                 "auswählen."))
            return
        self.result = types
        self.top.destroy()


def _fmt_opt(value: Optional[float]) -> str:
    """Optionalen Zahlenwert als Eingabetext (leer bei None)."""
    return "" if value is None else f"{value:g}"


def _fmt_live(snapshot: Dict[str, float], key: str) -> str:
    value = (snapshot or {}).get(key)
    return "" if value is None else f"{value:.1f}"


class _AutoLogDialog:
    """Einstellungen der AutoLog-Auslöser (nach dem Vorbild von TripCon)."""

    _INTERVALS = [
        ("5 Minuten", 300), ("10 Minuten", 600), ("15 Minuten", 900),
        ("30 Minuten", 1800), ("volle Stunde", 3600), ("2 Stunden", 7200),
    ]
    _AVG = ["30 s", "60 s", "120 s", "300 s"]
    _DECEL = ["2 kn/s", "3 kn/s", "5 kn/s", "8 kn/s"]
    _TRACK_IV = [("20 s", 20), ("30 s", 30), ("60 s", 60), ("120 s", 120),
                 ("300 s", 300)]

    def __init__(self, parent, settings: AutoLogSettings) -> None:
        self.result: Optional[AutoLogSettings] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("AutoLog-Auslöser"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._enabled = tk.BooleanVar(value=settings.enabled)
        ttk.Checkbutton(frame, text=t("AutoLog aktivieren"), variable=self._enabled).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        box = ttk.LabelFrame(frame, text=t("Logbucheintrag auslösen:"))
        box.grid(row=1, column=0, columnspan=3, sticky="we")
        r = 0

        self._interval_on = tk.BooleanVar(value=settings.interval_enabled)
        ttk.Checkbutton(box, text=t("zu jeder"), variable=self._interval_on).grid(
            row=r, column=0, sticky="w", padx=6, pady=3)
        self._interval = tk.StringVar(value=self._interval_label(settings.interval_seconds))
        ttk.Combobox(box, textvariable=self._interval, width=14, state="readonly",
                     values=[label for label, _ in self._INTERVALS]).grid(
            row=r, column=1, columnspan=2, sticky="w")
        r += 1

        self._sog_on = tk.BooleanVar(value=settings.sog_enabled)
        ttk.Checkbutton(box, text=t("wenn Fahrt über Grund ≥ (1–100)"),
                        variable=self._sog_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._sog = tk.StringVar(value=f"{settings.sog_threshold:g}")
        ttk.Entry(box, textvariable=self._sog, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(box, text="kn").grid(row=r, column=2, sticky="w")
        r += 1

        self._stw_on = tk.BooleanVar(value=settings.stw_enabled)
        ttk.Checkbutton(box, text=t("wenn Fahrt durchs Wasser ≥ (1–100)"),
                        variable=self._stw_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._stw = tk.StringVar(value=f"{settings.stw_threshold:g}")
        ttk.Entry(box, textvariable=self._stw, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(box, text="kn").grid(row=r, column=2, sticky="w")
        r += 1

        self._course_on = tk.BooleanVar(value=settings.course_enabled)
        ttk.Checkbutton(box, text=t("wenn Kurswechsel ≥ (20–170)"),
                        variable=self._course_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._course = tk.StringVar(value=f"{settings.course_threshold:g}")
        ttk.Entry(box, textvariable=self._course, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(box, text="°").grid(row=r, column=2, sticky="w")
        r += 1
        ttk.Label(box, text=t("   Mindestabstand zwischen Kurswechseln")).grid(
            row=r, column=0, sticky="w", padx=6)
        self._avg = tk.StringVar(value=f"{int(settings.course_cooldown_seconds)} s")
        ttk.Combobox(box, textvariable=self._avg, width=8, state="readonly",
                     values=self._AVG).grid(row=r, column=1, sticky="w")
        r += 1
        self._course_skip_motor = tk.BooleanVar(value=settings.course_skip_motor)
        ttk.Checkbutton(box, text=t("   bei Motor ein keinen Kurswechsel auslösen"),
                        variable=self._course_skip_motor).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=6)
        r += 1

        self._depth_on = tk.BooleanVar(value=settings.depth_enabled)
        ttk.Checkbutton(box, text=t("wenn Wassertiefe ≤ (0,5–25)"),
                        variable=self._depth_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._depth = tk.StringVar(value=f"{settings.depth_threshold:g}")
        ttk.Entry(box, textvariable=self._depth, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(box, text="m").grid(row=r, column=2, sticky="w")
        r += 1

        self._decel_on = tk.BooleanVar(value=settings.decel_enabled)
        ttk.Checkbutton(box, text=t("bei abrupter Fahrtreduzierung"),
                        variable=self._decel_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._decel = tk.StringVar(value=f"{settings.decel_threshold:g} kn/s")
        ttk.Combobox(box, textvariable=self._decel, width=8, state="readonly",
                     values=self._DECEL).grid(row=r, column=1, sticky="w")
        r += 1

        self._dist_on = tk.BooleanVar(value=settings.distance_enabled)
        ttk.Checkbutton(box, text=t("wenn Entfernung zum letzten Eintrag ≥ (0,1–2)"),
                        variable=self._dist_on).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        self._dist = tk.StringVar(value=f"{settings.distance_threshold:g}")
        ttk.Entry(box, textvariable=self._dist, width=8).grid(row=r, column=1, sticky="w")
        ttk.Label(box, text="NM").grid(row=r, column=2, sticky="w")
        r += 1

        # Trackaufzeichnung (dichte Kartenspur, getrennt von den Log-Einträgen)
        tbox = ttk.LabelFrame(frame, text=t("Trackaufzeichnung (nur Karte):"))
        tbox.grid(row=2, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self._track_on = tk.BooleanVar(value=settings.track_enabled)
        ttk.Checkbutton(tbox, text=t("dichte Positionsspur aufzeichnen"),
                        variable=self._track_on).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=6, pady=3)
        ttk.Label(tbox, text=t("   bei Kurswechsel ≥")).grid(row=1, column=0, sticky="w", padx=6)
        self._track_course = tk.StringVar(value=f"{settings.track_course_threshold:g}")
        ttk.Entry(tbox, textvariable=self._track_course, width=8).grid(
            row=1, column=1, sticky="w")
        ttk.Label(tbox, text="°").grid(row=1, column=2, sticky="w")
        ttk.Label(tbox, text=t("   sonst spätestens alle")).grid(row=2, column=0, sticky="w", padx=6)
        self._track_iv = tk.StringVar(
            value=self._track_iv_label(settings.track_interval_seconds))
        ttk.Combobox(tbox, textvariable=self._track_iv, width=8, state="readonly",
                     values=[label for label, _ in self._TRACK_IV]).grid(
            row=2, column=1, sticky="w")

        ttk.Label(frame, foreground="#777", wraplength=420,
                  text=t("Der Auslösegrund wird als Anlass im Eintrag gespeichert. Reine "
                       "Track-Punkte (nur Zeit + Position) bilden die dichte Kartenspur "
                       "und sind in der Liste ausblendbar.")).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=4, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(btns, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _interval_label(self, seconds: int) -> str:
        for label, sec in self._INTERVALS:
            if sec == seconds:
                return label
        return "volle Stunde"

    def _interval_seconds(self, label: str) -> int:
        for lab, sec in self._INTERVALS:
            if lab == label:
                return sec
        return 3600

    def _track_iv_label(self, seconds: int) -> str:
        for label, sec in self._TRACK_IV:
            if sec == seconds:
                return label
        return "60 s"

    def _track_iv_seconds(self, label: str) -> int:
        for lab, sec in self._TRACK_IV:
            if lab == label:
                return sec
        return 60

    def _on_ok(self) -> None:
        self.result = AutoLogSettings(
            enabled=self._enabled.get(),
            interval_enabled=self._interval_on.get(),
            interval_seconds=self._interval_seconds(self._interval.get()),
            align_boundary=True,
            sog_enabled=self._sog_on.get(),
            sog_threshold=_parse_float(self._sog.get()) or 8.0,
            stw_enabled=self._stw_on.get(),
            stw_threshold=_parse_float(self._stw.get()) or 4.0,
            course_enabled=self._course_on.get(),
            course_threshold=_parse_float(self._course.get()) or 40.0,
            course_cooldown_seconds=int(self._avg.get().split()[0]),
            course_skip_motor=self._course_skip_motor.get(),
            depth_enabled=self._depth_on.get(),
            depth_threshold=_parse_float(self._depth.get()) or 2.0,
            decel_enabled=self._decel_on.get(),
            decel_threshold=float(self._decel.get().split()[0]),
            distance_enabled=self._dist_on.get(),
            distance_threshold=_parse_float(self._dist.get()) or 0.5,
            track_enabled=self._track_on.get(),
            track_course_threshold=_parse_float(self._track_course.get()) or 10.0,
            track_interval_seconds=self._track_iv_seconds(self._track_iv.get()),
        )
        self.top.destroy()


class _LogeventsDialog:
    """Anlass-Auswahlliste bearbeiten (eine Zeile = ein Anlass)."""

    def __init__(self, parent, logevents: List[str]) -> None:
        self.result: Optional[List[str]] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Anlass-Liste bearbeiten"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("Ein Anlass pro Zeile. Reihenfolge = Auswahl-"
                  "Reihenfolge; der erste ist der Standard."),
                  foreground="#333").pack(anchor="w", pady=(0, 8))
        self._text = tk.Text(frame, width=34, height=16)
        self._text.pack(fill="both", expand=True)
        self._text.insert("1.0", "\n".join(logevents))

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Standard wiederherstellen"),
                   command=self._on_default).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_default(self) -> None:
        self._text.delete("1.0", "end")
        self._text.insert("1.0", "\n".join(fields.DEFAULT_LOGEVENTS))

    def _on_save(self) -> None:
        raw = self._text.get("1.0", "end")
        items = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not items:
            messagebox.showwarning(t("Anlass-Liste"), t("Bitte mindestens einen Anlass angeben."))
            return
        # Duplikate entfernen, Reihenfolge erhalten
        seen, uniq = set(), []
        for it in items:
            if it not in seen:
                seen.add(it)
                uniq.append(it)
        self.result = uniq
        self.top.destroy()


class _RemoteDialog:
    """Einstellungen + Zugangsdaten der Handy-/Tablet-Fern-Erfassung."""

    def __init__(self, parent, config, server, lan_ip: str) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Fern-Erfassung (Handy/Tablet)"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=14)
        frame.pack(fill="both", expand=True)

        running = server is not None and server.running
        port = int(config.remote_port or 8770)
        url = f"http://{lan_ip}:{port}/"

        ttk.Label(frame, text=t("Logbuch-Einträge vom Handy/Tablet im Bordnetz erfassen."),
                  foreground="#333").grid(row=0, column=0, columnspan=2, sticky="w",
                                          pady=(0, 8))

        self._enabled = tk.BooleanVar(value=config.remote_enabled)
        ttk.Checkbutton(frame, text=t("Fern-Erfassung aktiv (beim Start automatisch)"),
                        variable=self._enabled).grid(row=1, column=0, columnspan=2,
                                                     sticky="w", pady=2)

        # Zugangsdaten prominent anzeigen
        box = ttk.LabelFrame(frame, text=t("So verbindest du dich"))
        box.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 8))
        ttk.Label(box, text=t("1. Handy/Tablet ins selbe WLAN wie der Laptop.")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        ttk.Label(box, text=t("2. Im Browser diese Adresse öffnen:")).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=2)
        addr = ttk.Entry(box, width=30, font=("TkDefaultFont", 11, "bold"))
        addr.insert(0, url)
        addr.configure(state="readonly")
        addr.grid(row=2, column=0, sticky="w", padx=8, pady=2)
        ttk.Label(box, text=t("3. Mit der PIN anmelden.")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 2))
        # QR-Code inkl. PIN (?pin=…) — Scannen öffnet die Seite UND meldet an
        self._qr_canvas = tk.Canvas(box, highlightthickness=0, bg="white")
        self._qr_canvas.grid(row=0, column=2, rowspan=4, padx=(6, 10), pady=8)
        ttk.Label(box, text=t("↑ scannen = direkt angemeldet"), foreground="#777").grid(
            row=4, column=2, pady=(0, 6))
        qr_url = url + (f"?pin={config.remote_pin}" if config.remote_pin else "")
        self._draw_qr(qr_url)

        ttk.Label(frame, text=t("Port:")).grid(row=3, column=0, sticky="e", padx=4, pady=3)
        self._port = tk.StringVar(value=str(port))
        ttk.Entry(frame, textvariable=self._port, width=10).grid(
            row=3, column=1, sticky="w")
        ttk.Label(frame, text=t("PIN:")).grid(row=4, column=0, sticky="e", padx=4, pady=3)
        self._pin = tk.StringVar(value=config.remote_pin or "")
        pinrow = ttk.Frame(frame)
        pinrow.grid(row=4, column=1, sticky="w")
        ttk.Entry(pinrow, textvariable=self._pin, width=10).pack(side="left")
        ttk.Button(pinrow, text=t("Neue PIN"), command=self._new_pin).pack(side="left", padx=6)

        status = t("läuft") if running else t("aus")
        ttk.Label(frame, text=t("Status: {status}", status=status), foreground="#555").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(frame,
                  text=t("Tipp: Seite am Handy „zum Home-Bildschirm hinzufügen“ —\n"
                       "dann startet sie wie eine App."),
                  foreground="#777").grid(row=6, column=0, columnspan=2, sticky="w",
                                          pady=(2, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=0, columnspan=2, pady=(12, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Im Browser öffnen"),
                   command=lambda: webbrowser.open(url)).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _draw_qr(self, url: str) -> None:
        """Zeichnet den QR-Code der Adresse auf den Canvas (Standardbibliothek)."""
        try:
            matrix = qrcode.encode(url, error="M")
        except Exception:  # noqa: BLE001
            return
        n = len(matrix)
        quiet, scale = 2, 4
        dim = (n + 2 * quiet) * scale
        self._qr_canvas.configure(width=dim, height=dim)
        self._qr_canvas.delete("all")
        for r in range(n):
            for c in range(n):
                if matrix[r][c]:
                    x, y = (c + quiet) * scale, (r + quiet) * scale
                    self._qr_canvas.create_rectangle(
                        x, y, x + scale, y + scale, fill="black", outline="")

    def _new_pin(self) -> None:
        import random
        self._pin.set(f"{random.randint(0, 9999):04d}")

    def _on_save(self) -> None:
        pin = (self._pin.get() or "").strip()
        if self._enabled.get() and not pin:
            messagebox.showwarning(t("Fern-Erfassung"),
                                   t("Bitte eine PIN vergeben (oder „Neue PIN“)."))
            return
        try:
            port = int(self._port.get())
        except ValueError:
            messagebox.showwarning(t("Fern-Erfassung"), t("Ungültiger Port."))
            return
        self.result = {"enabled": self._enabled.get(), "port": port, "pin": pin}
        self.top.destroy()


class _PhotoDialog:
    """Einstellungen für den Foto-Import (Ordner überwachen)."""

    def __init__(self, parent, config) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Foto-Import"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=470, foreground="#555",
            text=t("Bilder in einen der überwachten Ordner legen → saillog erzeugt "
                 "automatisch einen Logbuch-Eintrag mit dem (verkleinerten) Bild und "
                 "den aktuellen NMEA-Daten. Verarbeitete Originale wandern in den "
                 "Unterordner „verarbeitet\". Mehrere Ordner sind möglich — z.B. wenn "
                 "PhotoSync je Gerät (Handy A, Handy B …) einen eigenen Unterordner "
                 "anlegt."),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self._enabled = tk.BooleanVar(value=config.photo_import_enabled)
        ttk.Checkbutton(frame, text=t("Foto-Import aktivieren"), variable=self._enabled).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=2)

        ttk.Label(frame, text=t("Überwachte Ordner:")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self._listbox = tk.Listbox(frame, width=52, height=4)
        for folder in config.photo_folder_list():
            self._listbox.insert("end", folder)
        self._listbox.grid(row=3, column=0, columnspan=2, sticky="w")
        side = ttk.Frame(frame)
        side.grid(row=3, column=2, sticky="n", padx=4)
        ttk.Button(side, text=t("+ Ordner…"), command=self._add).pack(fill="x", pady=(0, 2))
        ttk.Button(side, text=t("Entfernen"), command=self._remove).pack(fill="x")

        self._recursive = tk.BooleanVar(value=bool(getattr(config, "photo_recursive", False)))
        ttk.Checkbutton(
            frame, variable=self._recursive,
            text=t("Unterordner einbeziehen (z.B. auf den PhotoSync-Hauptordner zeigen — "
                 "erfasst automatisch jedes Geräte-Unterverzeichnis)"),
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # Bündelung: mehrere kurz nacheinander eintreffende Fotos = ein Eintrag
        grp = ttk.Frame(frame)
        grp.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(grp, text=t("Fotos bündeln, wenn sie innerhalb von")).pack(side="left")
        self._group_seconds = tk.StringVar(
            value=str(int(getattr(config, "photo_group_seconds", 90) or 0)))
        ttk.Spinbox(grp, from_=0, to=3600, width=5,
                    textvariable=self._group_seconds).pack(side="left", padx=4)
        ttk.Label(grp, text=t("Sekunden eintreffen (0 = jedes Foto einzeln)")).pack(side="left")

        ttk.Label(
            frame, foreground="#777", wraplength=470,
            text=t("Bilder werden auf max. {px} px "
                 "verkleinert und als JPEG gespeichert.", px=int(config.photo_max_px or 1600)),
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        if not photos.available():
            ttk.Label(
                frame, foreground="#b25000", wraplength=470,
                text=t("Hinweis: Für den Foto-Import wird Pillow benötigt — "
                     "installieren mit:  pip install pillow"),
            ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=8, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _add(self) -> None:
        directory = filedialog.askdirectory(title=t("Foto-Import-Ordner hinzufügen"))
        if directory and directory not in self._listbox.get(0, "end"):
            self._listbox.insert("end", directory)

    def _remove(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            self._listbox.delete(sel[0])

    def _on_ok(self) -> None:
        try:
            group = max(0, int(float(self._group_seconds.get())))
        except (TypeError, ValueError):
            group = 0
        self.result = {
            "folders": [f for f in self._listbox.get(0, "end") if f.strip()],
            "recursive": self._recursive.get(),
            "group_seconds": group,
            "enabled": self._enabled.get(),
        }
        self.top.destroy()


class _PlotterDialog:
    """Einstellungen für den Plotter-Screenshot per ADB (Android-Tablet)."""

    def __init__(self, parent, config) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Plotter-Screenshot (ADB)"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=480, foreground="#555",
            text=t("Holt den Bildschirm des Android-Tablets (Orca-/Plotter-Anzeige) per "
                 "adb ins Logbuch. Voraussetzung: adb installiert und Tablet gekoppelt "
                 "(Entwickleroptionen → USB-/Drahtlos-Debugging, 'immer erlauben')."),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frame, text=t("adb-Pfad:")).grid(row=1, column=0, sticky="e", padx=(0, 4), pady=4)
        self._adb = tk.StringVar(value=config.plotter_adb_path or "adb")
        ttk.Entry(frame, textvariable=self._adb, width=44).grid(row=1, column=1, sticky="w")
        ttk.Button(frame, text=t("Wählen…"), command=self._choose).grid(row=1, column=2, padx=4)

        ttk.Label(frame, text=t("Gerät (Serial / IP:Port):")).grid(
            row=2, column=0, sticky="e", padx=(0, 4), pady=4)
        self._serial = tk.StringVar(value=config.plotter_adb_serial)
        ttk.Entry(frame, textvariable=self._serial, width=30).grid(row=2, column=1, sticky="w")
        ttk.Button(frame, text=t("Geräte suchen"), command=self._find).grid(row=2, column=2, padx=4)
        ttk.Label(frame, foreground="#777",
                  text=t("USB: leer lassen (ein Gerät). WLAN: <Tablet-IP>:5555 eintragen.")).grid(
            row=3, column=1, columnspan=2, sticky="w")

        # WLAN-ADB-Steuerung
        wlan = ttk.Frame(frame)
        wlan.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 2))
        ttk.Label(wlan, text=t("WLAN:"), foreground="#555").pack(side="left")
        ttk.Button(wlan, text=t("Per USB für WLAN aktivieren"),
                   command=self._wlan_prepare).pack(side="left", padx=3)
        ttk.Button(wlan, text=t("Drahtlos verbinden"),
                   command=self._wlan_connect).pack(side="left", padx=3)

        self._autolog = tk.BooleanVar(value=config.plotter_autolog)
        ttk.Checkbutton(
            frame, text=t("Bei jedem Auto-Eintrag einen Plotter-Screenshot mitspeichern"),
            variable=self._autolog,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 2))

        self._status = ttk.Label(frame, text="", foreground="#555", wraplength=480)
        self._status.grid(row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=7, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text=t("Test"), command=self._test).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _choose(self) -> None:
        path = filedialog.askopenfilename(
            title=t("adb(.exe) wählen"),
            filetypes=[("adb", "adb.exe adb"), (t("Alle Dateien"), "*.*")],
        )
        if path:
            self._adb.set(path)

    def _find(self) -> None:
        from saillog import android_screencap
        devs = android_screencap.devices(self._adb.get().strip() or "adb")
        online = [s for s, st in devs if st == "device"]
        if not devs:
            self._status.config(
                text=t("Keine Geräte / adb nicht gefunden. Pfad prüfen und Tablet koppeln."),
                foreground="#b25000")
            return
        if len(online) == 1:
            self._serial.set(online[0])
        self._status.config(
            text=t("Gefunden: {list}", list=", ".join(f"{s} [{st}]" for s, st in devs)),
            foreground="#227722")

    def _wlan_prepare(self) -> None:
        """Per USB: tcpip aktivieren, Tablet-IP holen, WLAN-Adresse setzen + verbinden."""
        from saillog import android_screencap as scr
        adb = self._adb.get().strip() or "adb"
        self._status.config(text=t("Aktiviere WLAN-ADB über USB …"), foreground="#555")
        self.top.update_idletasks()
        ok, msg = scr.enable_tcpip(adb)
        if not ok:
            self._status.config(
                text=t("tcpip fehlgeschlagen: {msg}\nIst das Tablet per USB verbunden?", msg=msg),
                foreground="#b25000")
            return
        ip = scr.wlan_ip(adb)
        # Auto-Erkennung scheitert bei manchen Tablets → auf die im Feld
        # eingetragene Adresse zurückfallen.
        address = f"{ip}:5555" if ip else self._serial.get().strip()
        if ":" not in address:
            self._status.config(
                text=t("WLAN-ADB aktiv, aber Tablet-IP nicht gefunden. Bitte "
                     "<Tablet-IP>:5555 im Feld 'Gerät' eintragen und 'Drahtlos "
                     "verbinden'."),
                foreground="#b25000")
            return
        self._serial.set(address)
        cok, cmsg = scr.connect(address, adb)
        self._status.config(
            text=(t("WLAN-ADB aktiv, verbunden mit {address}. USB kann jetzt ab.", address=address)
                  if cok else t("tcpip ok, aber connect fehlte: {msg}", msg=cmsg)),
            foreground="#227722" if cok else "#b25000")

    def _wlan_connect(self) -> None:
        from saillog import android_screencap as scr
        address = self._serial.get().strip()
        if ":" not in address:
            self._status.config(
                text=t("Bitte erst <Tablet-IP>:5555 im Feld 'Gerät' eintragen."),
                foreground="#b25000")
            return
        ok, msg = scr.connect(address, self._adb.get().strip() or "adb")
        self._status.config(text=msg or t("(keine Antwort)"),
                            foreground="#227722" if ok else "#b25000")

    def _test(self) -> None:
        from saillog import android_screencap
        self._status.config(text=t("Teste Screenshot …"), foreground="#555")
        self.top.update_idletasks()
        png = android_screencap.capture_png(
            self._adb.get().strip() or "adb", self._serial.get().strip())
        if png:
            self._status.config(
                text=t("OK — Screenshot erhalten ({kb} kB).", kb=len(png) // 1024),
                foreground="#227722")
        else:
            self._status.config(
                text=t("Kein Screenshot. adb-Pfad/Gerät prüfen; Tablet gekoppelt und "
                     "'immer erlauben' bestätigt? (Ein schwarzes Bild = App sperrt "
                     "Screenshots.)"),
                foreground="#b25000")

    def _on_ok(self) -> None:
        self.result = {
            "adb_path": self._adb.get().strip() or "adb",
            "serial": self._serial.get().strip(),
            "autolog": self._autolog.get(),
        }
        self.top.destroy()


class _BackupDialog:
    """Datensicherung: Zielordner, Auto-beim-Beenden, „Jetzt sichern"."""

    def __init__(self, parent, config, make_backup) -> None:
        self._config = config
        self._make_backup = make_backup
        self.top = tk.Toplevel(parent)
        self.top.title(t("Backup"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=470, foreground="#555",
            text=t("Sichert die Logbuch-Datenbank (inkl. Fotos) und die Einstellungen "
                 "als ZIP — eine Datei zum Kopieren, z.B. auf einen USB-Stick."),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frame, text=t("Zielordner:")).grid(row=1, column=0, sticky="e", padx=(0, 4), pady=4)
        self._folder = tk.StringVar(value=config.backup_folder)
        ttk.Entry(frame, textvariable=self._folder, width=44).grid(row=1, column=1, sticky="w")
        ttk.Button(frame, text=t("Wählen…"), command=self._choose).grid(row=1, column=2, padx=4)

        self._auto = tk.BooleanVar(value=config.backup_on_close)
        ttk.Checkbutton(frame, text=t("beim Beenden automatisch sichern"),
                        variable=self._auto).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)

        keeprow = ttk.Frame(frame)
        keeprow.grid(row=3, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(keeprow, text=t("letzte behalten:")).pack(side="left")
        self._keep = tk.StringVar(value=str(int(config.backup_keep or 5)))
        ttk.Spinbox(keeprow, from_=1, to=99, textvariable=self._keep, width=5).pack(
            side="left", padx=4)

        self._status = ttk.Label(frame, foreground="#1a7a3a", wraplength=470)
        self._status.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._refresh_status()

        btns = ttk.Frame(frame)
        btns.grid(row=5, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btns, text=t("Jetzt sichern"), command=self._on_now).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Schließen"), command=self.top.destroy).pack(side="left", padx=4)

    def _choose(self) -> None:
        directory = filedialog.askdirectory(title=t("Backup-Zielordner"))
        if directory:
            self._folder.set(directory)
            self._refresh_status()

    def _refresh_status(self) -> None:
        folder = self._folder.get().strip()
        n = len(backup.list_backups(folder)) if folder else 0
        self._status.config(text=t("{n} Backup(s) im Zielordner.", n=n) if folder else "")

    def _save(self) -> None:
        self._config.backup_folder = self._folder.get().strip()
        self._config.backup_on_close = self._auto.get()
        try:
            self._config.backup_keep = max(1, int(self._keep.get()))
        except ValueError:
            self._config.backup_keep = 5
        self._config.save()

    def _on_now(self) -> None:
        folder = self._folder.get().strip()
        if not folder:
            messagebox.showinfo(t("Backup"), t("Bitte zuerst einen Zielordner wählen."))
            return
        self._save()
        path = self._make_backup(folder)
        if path:
            import os
            n = len(backup.list_backups(folder))
            self._status.config(
                text=t("Gesichert: {file}  ({n} Backup(s))", file=os.path.basename(path), n=n))

    def _on_ok(self) -> None:
        self._save()
        self.top.destroy()


class _PersonManagerDialog:
    """Personen-Stammdaten verwalten (Neu / Ändern / Löschen)."""

    def __init__(self, parent, store) -> None:
        self._store = store
        self.top = tk.Toplevel(parent)
        self.top.title(t("Personen verwalten"))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("420x360")
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("Gespeicherte Personen:")).pack(anchor="w")
        list_row = ttk.Frame(frame)
        list_row.pack(fill="both", expand=True, pady=6)
        self._list = tk.Listbox(list_row, height=12)
        self._list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_row, orient="vertical", command=self._list.yview)
        self._list.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._list.bind("<Double-1>", lambda _e: self._on_edit())

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text=t("Neu…"), command=self._on_new).pack(side="left")
        ttk.Button(btns, text=t("Ändern…"), command=self._on_edit).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Löschen"), command=self._on_delete).pack(side="left")
        ttk.Button(btns, text=t("Schließen"), command=self.top.destroy).pack(side="right")

        self._persons = []
        self._refresh()

    def _refresh(self) -> None:
        self._persons = self._store.all_persons()
        self._list.delete(0, "end")
        for p in self._persons:
            label = f"{p.last_name}, {p.first_name}".strip(", ")
            self._list.insert("end", label or f"Person #{p.id}")

    def _selected(self):
        sel = self._list.curselection()
        return self._persons[sel[0]] if sel else None

    def _on_new(self) -> None:
        dlg = _PersonEditDialog(self.top, self._store, Person())
        self.top.wait_window(dlg.top)
        if dlg.saved:
            self._refresh()

    def _on_edit(self) -> None:
        person = self._selected()
        if person is None:
            return
        dlg = _PersonEditDialog(self.top, self._store, person)
        self.top.wait_window(dlg.top)
        if dlg.saved:
            self._refresh()

    def _on_delete(self) -> None:
        person = self._selected()
        if person is None:
            return
        name = f"{person.last_name}, {person.first_name}".strip(", ")
        if messagebox.askyesno(t("Löschen"), t('Person „{name}" löschen?', name=name)):
            self._store.delete_person(person.id)
            self._refresh()


class _PersonEditDialog:
    """Personendaten erfassen/bearbeiten (nach TripCon-Vorlage) inkl. Foto."""

    _FIELDS = [
        ("last_name", "Name:"),
        ("first_name", "Vorname:"),
        ("email", "E-Mail:"),
        ("nationality", "Nationalität:"),
        ("passport_no", "Pass Nr.:"),
        ("street", "Straße / Nr.:"),
        ("birth_place", "Geburtsort:"),
        ("birth_date", "Geburtsdatum:"),
    ]

    def __init__(self, parent, store, person: Person) -> None:
        self.saved = False
        self._store = store
        self._person = person
        self._photo_action = None   # None=unverändert, ("set", jpeg), ("remove",)
        self.top = tk.Toplevel(parent)
        self.top.title(t("Personendaten erfassen"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._vars: Dict[str, tk.StringVar] = {}
        r = 0
        for attr, label in self._FIELDS:
            # „Name:" hier = Nachname (Kontext „person"), nicht der generische Name
            lbl = t(label, _ctx="person") if label == "Name:" else t(label)
            ttk.Label(frame, text=lbl).grid(row=r, column=0, sticky="e", padx=4, pady=3)
            var = tk.StringVar(value=getattr(person, attr) or "")
            ttk.Entry(frame, textvariable=var, width=30).grid(
                row=r, column=1, columnspan=2, sticky="w", pady=3)
            self._vars[attr] = var
            r += 1

        # PLZ / Stadt in einer Zeile
        ttk.Label(frame, text=t("PLZ / Stadt:")).grid(row=r, column=0, sticky="e", padx=4, pady=3)
        self._vars["zip_code"] = tk.StringVar(value=person.zip_code or "")
        ttk.Entry(frame, textvariable=self._vars["zip_code"], width=8).grid(
            row=r, column=1, sticky="w", pady=3)
        self._vars["city"] = tk.StringVar(value=person.city or "")
        ttk.Entry(frame, textvariable=self._vars["city"], width=20).grid(
            row=r, column=2, sticky="w", pady=3)
        r += 1

        # Foto
        photo = ttk.LabelFrame(frame, text=t("Foto"))
        photo.grid(row=0, column=3, rowspan=6, padx=(16, 0), sticky="n")
        self._photo_status = ttk.Label(photo, text="", foreground="#555")
        self._photo_status.pack(padx=8, pady=(8, 4))
        ttk.Button(photo, text=t("Hinzufügen…"), command=self._on_add_photo).pack(fill="x", padx=8, pady=2)
        ttk.Button(photo, text=t("Ansehen"), command=self._on_view_photo).pack(fill="x", padx=8, pady=2)
        ttk.Button(photo, text=t("Entfernen"), command=self._on_remove_photo).pack(fill="x", padx=8, pady=2)
        if not photos.available():
            ttk.Label(photo, text=t("(Foto braucht Pillow)"), foreground="#b25000",
                      wraplength=120).pack(padx=8, pady=(4, 8))
        self._update_photo_status()

        buttons = ttk.Frame(frame)
        buttons.grid(row=r, column=0, columnspan=4, pady=(12, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _has_photo(self) -> bool:
        if self._photo_action is not None:
            return self._photo_action[0] == "set"
        return self._person.id is not None and \
            self._store.get_person_photo(self._person.id) is not None

    def _update_photo_status(self) -> None:
        self._photo_status.config(
            text=t("✓ Foto vorhanden") if self._has_photo() else t("(kein Foto)"))

    def _on_add_photo(self) -> None:
        path = filedialog.askopenfilename(
            title=t("Personenfoto wählen"),
            filetypes=[(t("Bilder"), "*.jpg *.jpeg *.png *.bmp *.gif"), (t("Alle"), "*.*")])
        if not path:
            return
        jpeg = photos.resize_to_jpeg(path, max_px=400)
        if not jpeg:
            messagebox.showerror(
                t("Foto"), t("Konnte das Bild nicht verarbeiten.\nBraucht Pillow "
                        "(pip install pillow) und ein lesbares Bildformat."))
            return
        self._photo_action = ("set", jpeg)
        self._update_photo_status()

    def _on_remove_photo(self) -> None:
        self._photo_action = ("remove",)
        self._update_photo_status()

    def _on_view_photo(self) -> None:
        data = None
        if self._photo_action is not None and self._photo_action[0] == "set":
            data = self._photo_action[1]
        elif self._person.id is not None:
            data = self._store.get_person_photo(self._person.id)
        if not data:
            messagebox.showinfo(t("Foto"), t("Kein Foto vorhanden."))
            return
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="saillog_person_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        webbrowser.open(Path(path).as_uri())

    def _on_save(self) -> None:
        p = self._person
        for attr, var in self._vars.items():
            setattr(p, attr, var.get().strip())
        if p.id is None:
            self._store.add_person(p)
        else:
            self._store.update_person(p)
        # Foto anwenden
        if self._photo_action is not None:
            if self._photo_action[0] == "set":
                self._store.set_person_photo(p.id, self._photo_action[1])
            else:
                self._store.delete_person_photo(p.id)
        self.saved = True
        self.top.destroy()


class _ShipManagerDialog:
    """Schiffe verwalten: Auswahl (aktives Schiff), Neu/Ändern/Löschen."""

    def __init__(self, parent, store, config) -> None:
        self._store = store
        self._config = config
        self.top = tk.Toplevel(parent)
        self.top.title(t("Schiffe verwalten"))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("560x440")
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        sel = ttk.LabelFrame(frame, text=t("Auswahl (aktives Schiff)"))
        sel.pack(fill="x")
        self._combo_var = tk.StringVar()
        self._combo = ttk.Combobox(sel, textvariable=self._combo_var, width=30,
                                   state="readonly")
        self._combo.grid(row=0, column=0, padx=6, pady=6, sticky="w")
        self._combo.bind("<<ComboboxSelected>>", lambda _e: self._on_select())
        ttk.Button(sel, text=t("Neu…"), command=self._on_new).grid(row=0, column=1, padx=3)
        ttk.Button(sel, text=t("Ändern…"), command=self._on_edit).grid(row=0, column=2, padx=3)
        ttk.Button(sel, text=t("Löschen"), command=self._on_delete).grid(row=0, column=3, padx=3)

        self._summary = tk.Text(frame, height=15, wrap="word", state="disabled",
                                font=("TkDefaultFont", 9))
        self._summary.pack(fill="both", expand=True, pady=8)
        ttk.Button(frame, text=t("Schließen"), command=self.top.destroy).pack(side="right")

        self._ships = []
        self._refresh()

    def _refresh(self) -> None:
        self._ships = self._store.all_ships()
        self._combo["values"] = [s.name or f"Schiff #{s.id}" for s in self._ships]
        active = self._config.active_ship_id
        idx = next((i for i, s in enumerate(self._ships) if s.id == active), None)
        if idx is None and self._ships:
            idx = 0
            # sichtbar gewähltes Schiff auch als aktiv übernehmen
            self._config.active_ship_id = self._ships[0].id
            self._config.save()
        if idx is not None:
            self._combo.current(idx)
        else:
            self._combo_var.set("")
        self._show_summary()

    def _selected(self):
        i = self._combo.current()
        return self._ships[i] if 0 <= i < len(self._ships) else None

    def _equip_summary(self, ship_id) -> str:
        """Antrieb (Groß-/Vorsegel, Motor) des Schiffs als kurze Zeile."""
        if not ship_id:
            return "—"
        parts = []
        for key, label in EQUIP_CATEGORIES.items():
            names = [e.name for e in self._store.ship_equipment(ship_id, key)]
            if names:
                parts.append(f"{label}: {', '.join(names)}")
        return "  |  ".join(parts) if parts else "—"

    def _on_select(self) -> None:
        s = self._selected()
        self._config.active_ship_id = s.id if s else None
        self._config.save()
        self._show_summary()

    def _show_summary(self) -> None:
        s = self._selected()
        self._summary.config(state="normal")
        self._summary.delete("1.0", "end")
        if s:
            def v(x, u=""):
                return f"{x:g} {u}".strip() if x is not None else "—"
            self._summary.insert("1.0", "\n".join([
                f"Schiffstyp: {s.ship_type or '—'}    Kielart: {s.keel_type or '—'}",
                f"Schiffsnummer: {s.ship_number or '—'}",
                f"Länge: {v(s.length_m, 'm')}   Breite: {v(s.beam_m, 'm')}   "
                f"Tiefgang: {v(s.max_draft_m, 'm')}",
                f"Verdrängung: {v(s.displacement_t, 't')}   "
                f"Durchfahrtshöhe: {v(s.clearance_height_m, 'm')}",
                f"Flagge: {s.flag or '—'}   Heimathafen: {s.home_port or '—'}",
                f"Rufzeichen: {s.call_sign or '—'}   MMSI: {s.mmsi or '—'}",
                f"Echolot-Einbautiefe: {v(s.echo_depth_m, 'm')}   "
                f"Loggeber-Korrektur: {s.log_correction:g}",
                f"Tanks: Wasser {v(s.water_tank_l, 'l')}, "
                f"Treibstoff {v(s.fuel_tank_l, 'l')}",
                f"Antrieb: {self._equip_summary(s.id)}",
                f"Ausstattung: {s.equipment or '—'}",
                f"Stromversorgung: {s.power_source or '—'}",
            ]))
        else:
            self._summary.insert("1.0", 'Noch kein Schiff angelegt. „Neu…" anklicken.')
        self._summary.config(state="disabled")

    def _on_new(self) -> None:
        dlg = _ShipEditDialog(self.top, self._store, Ship())
        self.top.wait_window(dlg.top)
        if dlg.saved:
            if self._config.active_ship_id is None and dlg.ship_id:
                self._config.active_ship_id = dlg.ship_id
                self._config.save()
            self._refresh()

    def _on_edit(self) -> None:
        s = self._selected()
        if s is None:
            return
        dlg = _ShipEditDialog(self.top, self._store, s)
        self.top.wait_window(dlg.top)
        if dlg.saved:
            self._refresh()

    def _on_delete(self) -> None:
        s = self._selected()
        if s is None:
            return
        if messagebox.askyesno(t("Löschen"), t('Schiff „{name}" löschen?', name=s.name)):
            self._store.delete_ship(s.id)
            if self._config.active_ship_id == s.id:
                self._config.active_ship_id = None
                self._config.save()
            self._refresh()


class _MotorParamDialog:
    """Neuen Motor in die Parameter-Datenbank aufnehmen."""

    def __init__(self, parent) -> None:
        self.result: Optional[EquipmentParam] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Motor hinzufügen"))
        self.top.transient(parent)
        self.top.grab_set()
        f = ttk.Frame(self.top, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text=t("Bezeichnung")).grid(row=0, column=0, columnspan=2, sticky="w")
        self._name = tk.StringVar()
        ttk.Entry(f, textvariable=self._name, width=32).grid(
            row=1, column=0, columnspan=4, sticky="we", pady=(0, 8))

        ttk.Label(f, text=t("Öldruck  maximal")).grid(row=2, column=0, sticky="w")
        ttk.Label(f, text=t("Schrittweite")).grid(row=2, column=2, sticky="w", padx=(10, 0))
        self._oil_max = tk.StringVar(value="2")
        self._oil_step = tk.StringVar(value="0.1")
        ttk.Entry(f, textvariable=self._oil_max, width=8).grid(row=3, column=0, sticky="w")
        ttk.Label(f, text="Bar").grid(row=3, column=1, sticky="w")
        ttk.Entry(f, textvariable=self._oil_step, width=8).grid(row=3, column=2, sticky="w", padx=(10, 0))
        ttk.Label(f, text="Bar").grid(row=3, column=3, sticky="w")

        ttk.Label(f, text=t("Drehzahl  maximal")).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(f, text=t("Schrittweite")).grid(row=4, column=2, sticky="w", padx=(10, 0), pady=(8, 0))
        self._rpm_max = tk.StringVar(value="6000")
        self._rpm_step = tk.StringVar(value="100")
        ttk.Entry(f, textvariable=self._rpm_max, width=8).grid(row=5, column=0, sticky="w")
        ttk.Label(f, text=t("U/min")).grid(row=5, column=1, sticky="w")
        ttk.Entry(f, textvariable=self._rpm_step, width=8).grid(row=5, column=2, sticky="w", padx=(10, 0))
        ttk.Label(f, text=t("U/min")).grid(row=5, column=3, sticky="w")

        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, columnspan=4, pady=(14, 0))
        ttk.Button(btns, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        name = self._name.get().strip()
        if not name:
            messagebox.showwarning(t("Motor"), t("Bitte eine Bezeichnung angeben."))
            return
        self.result = EquipmentParam(category="motor", name=name, attrs={
            "oil_max": _parse_float(self._oil_max.get()),
            "oil_step": _parse_float(self._oil_step.get()),
            "rpm_max": _parse_float(self._rpm_max.get()),
            "rpm_step": _parse_float(self._rpm_step.get()),
        })
        self.top.destroy()


class _SailParamDialog:
    """Neues Segel (Groß-/Vorsegel) in die Parameter-Datenbank aufnehmen."""

    def __init__(self, parent, category: str, category_label: str) -> None:
        self.result: Optional[EquipmentParam] = None
        self._category = category
        self.top = tk.Toplevel(parent)
        self.top.title(t("{category} hinzufügen", category=category_label))
        self.top.transient(parent)
        self.top.grab_set()
        f = ttk.Frame(self.top, padding=14)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text=t("Bezeichnung")).grid(row=0, column=0, sticky="w")
        self._name = tk.StringVar()
        ttk.Entry(f, textvariable=self._name, width=28).grid(
            row=1, column=0, sticky="we", pady=(0, 8))
        ttk.Label(f, text=t("Art des Reffs")).grid(row=2, column=0, sticky="w")
        self._reef = tk.StringVar(value=REEF_TYPES[0])
        ttk.Combobox(f, textvariable=self._reef, state="readonly", width=20,
                     values=REEF_TYPES).grid(row=3, column=0, sticky="w")

        btns = ttk.Frame(f)
        btns.grid(row=4, column=0, pady=(14, 0))
        ttk.Button(btns, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_save(self) -> None:
        name = self._name.get().strip()
        if not name:
            messagebox.showwarning(t("Segel"), t("Bitte eine Bezeichnung angeben."))
            return
        self.result = EquipmentParam(category=self._category, name=name,
                                     attrs={"reef": self._reef.get()})
        self.top.destroy()


class _ShipEditDialog:
    """Schiff bearbeiten: Kennwerte + Tanks + Ausrüstung + Foto."""

    _FIELDS = [
        ("name", "Schiffsname:", "text"),
        ("ship_type", "Schiffstyp:", "text"),
        ("keel_type", "Kielart:", "text"),
        ("ship_number", "Schiffsnummer:", "text"),
        ("length_m", "Länge (m):", "num"),
        ("beam_m", "Breite (m):", "num"),
        ("max_draft_m", "max. Tiefgang (m):", "num"),
        ("displacement_t", "Verdrängung (t):", "num"),
        ("clearance_height_m", "Durchfahrtshöhe (m):", "num"),
        ("flag", "Flagge:", "text"),
        ("home_port", "Heimathafen:", "text"),
        ("call_sign", "Rufzeichen:", "text"),
        ("mmsi", "MMSI:", "text"),
        ("echo_depth_m", "Einbautiefe Echolot (m):", "num"),
        ("log_correction", "Korrekturfaktor Loggeber:", "num"),
        ("water_tank_l", "Wassertank (l):", "num"),
        ("fuel_tank_l", "Treibstofftank (l):", "num"),
        ("sails", "Segel/Antrieb:", "text"),
        ("equipment", "Ausstattung:", "text"),
        ("power_source", "Stromversorgung:", "text"),
    ]

    def __init__(self, parent, store, ship: Ship) -> None:
        self.saved = False
        self.ship_id = ship.id
        self._store = store
        self._ship = ship
        self._photo_action = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Schiff bearbeiten"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._vars: Dict[str, tk.StringVar] = {}
        per_col = (len(self._FIELDS) + 1) // 2
        for i, (attr, label, kind) in enumerate(self._FIELDS):
            r = i % per_col
            base = (i // per_col) * 2
            ttk.Label(frame, text=t(label)).grid(row=r, column=base, sticky="e", padx=(6, 3), pady=2)
            cur = getattr(ship, attr)
            if kind == "num":
                text = "" if cur is None else f"{cur:g}"
            else:
                text = cur or ""
            var = tk.StringVar(value=text)
            ttk.Entry(frame, textvariable=var, width=18).grid(
                row=r, column=base + 1, sticky="w", pady=2)
            self._vars[attr] = var

        # Foto rechts
        photo = ttk.LabelFrame(frame, text=t("Schiffsfoto"))
        photo.grid(row=0, column=4, rowspan=6, padx=(16, 0), sticky="n")
        self._photo_status = ttk.Label(photo, text="", foreground="#555")
        self._photo_status.pack(padx=8, pady=(8, 4))
        ttk.Button(photo, text=t("Hinzufügen…"), command=self._on_add_photo).pack(fill="x", padx=8, pady=2)
        ttk.Button(photo, text=t("Ansehen"), command=self._on_view_photo).pack(fill="x", padx=8, pady=2)
        ttk.Button(photo, text=t("Entfernen"), command=self._on_remove_photo).pack(fill="x", padx=8, pady=2)
        if not photos.available():
            ttk.Label(photo, text=t("(Foto braucht Pillow)"), foreground="#b25000",
                      wraplength=120).pack(padx=8, pady=(4, 8))
        self._update_photo_status()

        # Ausrüstung — Antrieb (Parameter-Datenbank <-> dieses Schiff)
        self._equip: Dict[str, list] = {}
        for key in EQUIP_CATEGORIES:
            self._equip[key] = (self._store.ship_equipment(ship.id, key)
                                if ship.id else [])
        self._build_equipment(frame, per_col)

        buttons = ttk.Frame(frame)
        buttons.grid(row=per_col + 1, column=0, columnspan=5, pady=(12, 0))
        ttk.Button(buttons, text=t("Speichern"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    # --- Ausrüstung -------------------------------------------------------
    def _build_equipment(self, frame, row) -> None:
        box = ttk.LabelFrame(frame, text=t("Ausrüstung — Antrieb"))
        box.grid(row=row, column=0, columnspan=5, sticky="we", pady=(12, 0))
        ttk.Label(box, text=t("Antrieb:")).grid(row=0, column=0, sticky="e", padx=6, pady=4)
        self._equip_cat = tk.StringVar(value=EQUIP_CATEGORIES["mainsail"])
        cat = ttk.Combobox(box, textvariable=self._equip_cat, state="readonly",
                           width=14, values=list(EQUIP_CATEGORIES.values()))
        cat.grid(row=0, column=1, sticky="w")
        cat.bind("<<ComboboxSelected>>", lambda _e: self._refresh_equip())
        ttk.Button(box, text=t("＋ Neu…"), command=self._on_add_param).grid(
            row=0, column=2, padx=6, sticky="w")

        ttk.Label(box, text=t("Parameter-Datenbank"), foreground="#555").grid(
            row=1, column=0, columnspan=2, pady=(4, 0))
        ttk.Label(box, text=t("dieses Schiff"), foreground="#555").grid(
            row=1, column=3, columnspan=2, pady=(4, 0))
        self._db_list = tk.Listbox(box, width=28, height=6, exportselection=False)
        self._db_list.grid(row=2, column=0, columnspan=2, padx=6, pady=(0, 8), sticky="we")
        mid = ttk.Frame(box)
        mid.grid(row=2, column=2)
        ttk.Button(mid, text="→", width=3, command=self._equip_add).pack(pady=3)
        ttk.Button(mid, text="←", width=3, command=self._equip_remove).pack(pady=3)
        self._ship_list = tk.Listbox(box, width=28, height=6, exportselection=False)
        self._ship_list.grid(row=2, column=3, columnspan=2, padx=6, pady=(0, 8), sticky="we")
        self._refresh_equip()

    def _cat_key(self) -> str:
        name = self._equip_cat.get()
        return next((k for k, v in EQUIP_CATEGORIES.items() if v == name), "mainsail")

    @staticmethod
    def _equip_label(item) -> str:
        reef = (item.attrs or {}).get("reef")
        if reef and reef != "kein Reff":
            return f"{item.name}  ·  {reef}"
        return item.name

    def _refresh_equip(self) -> None:
        key = self._cat_key()
        self._db_params = self._store.equipment_params(key)
        self._db_list.delete(0, "end")
        for p in self._db_params:
            self._db_list.insert("end", self._equip_label(p))
        self._ship_list.delete(0, "end")
        for e in self._equip[key]:
            self._ship_list.insert("end", self._equip_label(e))

    def _equip_add(self) -> None:
        sel = self._db_list.curselection()
        if not sel:
            return
        p = self._db_params[sel[0]]
        key = self._cat_key()
        self._equip[key].append(ShipEquipment(
            ship_id=self._ship.id, category=key, name=p.name,
            attrs=dict(p.attrs or {}), param_id=p.id))
        self._refresh_equip()

    def _equip_remove(self) -> None:
        sel = self._ship_list.curselection()
        if not sel:
            return
        del self._equip[self._cat_key()][sel[0]]
        self._refresh_equip()

    def _on_add_param(self) -> None:
        key = self._cat_key()
        if key == "motor":
            dlg = _MotorParamDialog(self.top)
        else:
            dlg = _SailParamDialog(self.top, key, EQUIP_CATEGORIES[key])
        self.top.wait_window(dlg.top)
        if dlg.result is None:
            return
        self._store.add_equipment_param(dlg.result)
        self._refresh_equip()

    def _has_photo(self) -> bool:
        if self._photo_action is not None:
            return self._photo_action[0] == "set"
        return self._ship.id is not None and \
            self._store.get_ship_photo(self._ship.id) is not None

    def _update_photo_status(self) -> None:
        self._photo_status.config(
            text=t("✓ Foto vorhanden") if self._has_photo() else t("(kein Foto)"))

    def _on_add_photo(self) -> None:
        path = filedialog.askopenfilename(
            title=t("Schiffsfoto wählen"),
            filetypes=[(t("Bilder"), "*.jpg *.jpeg *.png *.bmp *.gif"), (t("Alle"), "*.*")])
        if not path:
            return
        jpeg = photos.resize_to_jpeg(path, max_px=800)
        if not jpeg:
            messagebox.showerror(
                t("Foto"), t("Konnte das Bild nicht verarbeiten (braucht Pillow)."))
            return
        self._photo_action = ("set", jpeg)
        self._update_photo_status()

    def _on_remove_photo(self) -> None:
        self._photo_action = ("remove",)
        self._update_photo_status()

    def _on_view_photo(self) -> None:
        data = None
        if self._photo_action is not None and self._photo_action[0] == "set":
            data = self._photo_action[1]
        elif self._ship.id is not None:
            data = self._store.get_ship_photo(self._ship.id)
        if not data:
            messagebox.showinfo(t("Foto"), t("Kein Foto vorhanden."))
            return
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="saillog_ship_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        webbrowser.open(Path(path).as_uri())

    def _on_save(self) -> None:
        s = self._ship
        for attr, _label, kind in self._FIELDS:
            raw = self._vars[attr].get().strip()
            if kind == "num":
                value = _parse_float(raw)
                if attr == "log_correction":
                    value = value if value else 1.0
                setattr(s, attr, value)
            else:
                setattr(s, attr, raw)
        if s.id is None:
            self._store.add_ship(s)
        else:
            self._store.update_ship(s)
        self.ship_id = s.id
        # Ausrüstung (Antrieb) je Kategorie speichern
        for key in EQUIP_CATEGORIES:
            for it in self._equip.get(key, []):
                it.ship_id = s.id
            self._store.set_ship_equipment(s.id, self._equip.get(key, []), category=key)
        if self._photo_action is not None:
            if self._photo_action[0] == "set":
                self._store.set_ship_photo(s.id, self._photo_action[1])
            else:
                self._store.delete_ship_photo(s.id)
        self.saved = True
        self.top.destroy()


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
        self.top.title(t("Crewliste") + (t(" — Törn #{id}", id=trip.id) if trip else ""))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("740x580")
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        # Bootsangaben (aus der Konfiguration vorbelegt, werden gespeichert)
        boat = ttk.LabelFrame(frame, text=t("Bootsangaben (werden gespeichert)"))
        boat.pack(fill="x")
        self._boat_vars: Dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(self._BOAT_FIELDS):
            r, c = i % 4, (i // 4) * 2
            ttk.Label(boat, text=t(label)).grid(row=r, column=c, sticky="e", padx=(8, 3), pady=3)
            var = tk.StringVar(value=str(getattr(config, key, "") or ""))
            ttk.Entry(boat, textvariable=var, width=26).grid(row=r, column=c + 1, sticky="w", pady=3)
            self._boat_vars[key] = var

        # Ort/Datum für den Ausdruck
        pd = ttk.Frame(frame)
        pd.pack(fill="x", pady=(8, 0))
        ttk.Label(pd, text=t("Ort (Ausklarierung):")).pack(side="left", padx=(4, 3))
        # Zuletzt gespeicherten Ort/Datum bevorzugen, sonst Törn-Ort / heute.
        default_place = getattr(config, "clearance_place", "") or ""
        if not default_place and trip:
            default_place = trip.end_location or trip.start_location or ""
        self._place = tk.StringVar(value=default_place)
        ttk.Entry(pd, textvariable=self._place, width=22).pack(side="left")
        ttk.Label(pd, text=t("Datum:")).pack(side="left", padx=(12, 3))
        default_date = getattr(config, "clearance_date", "") or \
            datetime.date.today().strftime("%d.%m.%Y")
        self._date = tk.StringVar(value=default_date)
        ttk.Entry(pd, textvariable=self._date, width=12).pack(side="left")

        # Crew-Tabelle
        title = t("Crew") if trip else t("Crew (kein Törn gewählt — allgemeine Liste)")
        crew = ttk.LabelFrame(frame, text=title)
        crew.pack(fill="both", expand=True, pady=(8, 0))
        cols = ("pos", "name", "first", "birth", "place", "nat", "pass")
        headers = {
            "pos": t("Funktion"), "name": t("Name"), "first": t("Vorname"),
            "birth": t("Geburtsdatum"), "place": t("Geburtsort"),
            "nat": t("Staatsang."), "pass": t("Pass-Nr."),
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
        ttk.Button(cbtn, text=t("Person hinzufügen…"), command=self._on_add).pack(side="left")
        ttk.Button(cbtn, text=t("Bearbeiten…"), command=self._on_edit).pack(side="left", padx=4)
        ttk.Button(cbtn, text=t("Entfernen"), command=self._on_remove).pack(side="left")

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(10, 0))
        ttk.Button(bottom, text=t("Crewliste drucken…"), command=self._on_print).pack(side="left")
        ttk.Button(bottom, text=t("Speichern & schließen"), command=self._on_close).pack(
            side="right"
        )
        ttk.Button(bottom, text=t("Schließen"), command=self.top.destroy).pack(
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
        if messagebox.askyesno(t("Entfernen"), t("Person aus der Crewliste entfernen?")):
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
            messagebox.showerror(t("Crewliste"), t("Konnte Datei nicht schreiben:\n{error}", error=exc))
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
        self.top.title(t("Crew-Mitglied"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        row0 = 0
        # Auswahl aus gespeicherten Personen (falls vorhanden)
        if self._persons:
            ttk.Label(frame, text=t("Gespeicherte Person:")).grid(
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
            lbl = t(label, _ctx="person") if label == "Name:" else t(label)
            ttk.Label(frame, text=lbl).grid(row=r, column=0, sticky="e", padx=4, pady=3)
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
        ttk.Button(btns, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

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


class _FuelDialog:
    """Tank-Logbuch mit Verbrauchsberechnung (l/h) über „voll getankt"."""

    def __init__(self, parent, store, live, offset, trip_id, config) -> None:
        self._store = store
        self._live = live
        self._offset = offset
        self._trip_id = trip_id
        self._config = config
        self.top = tk.Toplevel(parent)
        self.top.title(t("Tanken & Verbrauch"))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.geometry("680x510")
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._summary = ttk.Label(
            frame, text="", font=("TkDefaultFont", 11, "bold"), foreground="#1a5a8a"
        )
        self._summary.pack(anchor="w")
        self._remaining = ttk.Label(
            frame, text="", font=("TkDefaultFont", 11, "bold"), foreground="#1a7a3a"
        )
        self._remaining.pack(anchor="w", pady=(2, 0))
        ttk.Label(
            frame, foreground="#777",
            text=t("Verbrauch = getankte Menge zwischen zwei „voll getankt\"-Einträgen, "
                   "geteilt durch die Motorstunden dazwischen."),
        ).pack(anchor="w", pady=(2, 6))

        # Tankgröße: bevorzugt aus den Schiffsdaten des aktiven Schiffs
        # (Treibstofftank), sonst aus der Konfiguration (Abwärtskompatibilität).
        ship = store.get_ship(config.active_ship_id) if config.active_ship_id else None
        ship_cap = ship.fuel_tank_l if ship and ship.fuel_tank_l else None
        default_cap = ship_cap if ship_cap else (config.tank_capacity_l or None)
        tankrow = ttk.Frame(frame)
        tankrow.pack(fill="x", pady=(0, 6))
        ttk.Label(tankrow, text=t("Tankgröße (L):")).pack(side="left")
        self._tank = tk.StringVar(
            value="" if not default_cap else f"{default_cap:g}")
        ent = ttk.Entry(tankrow, textvariable=self._tank, width=8)
        ent.pack(side="left", padx=(4, 0))
        ent.bind("<FocusOut>", lambda _e: self._save_tank())
        ent.bind("<Return>", lambda _e: self._save_tank())
        if ship_cap:
            ttk.Label(tankrow, foreground="#777",
                      text=t("(aus Schiffsdaten)")).pack(side="left", padx=(6, 0))

        cols = ("time", "liters", "loc", "full", "hours")
        headers = {"time": t("Zeit"), "liters": t("Liter"), "loc": t("Ort"),
                   "full": t("Voll"), "hours": t("Motorstd.")}
        widths = {"time": 150, "liters": 70, "loc": 160, "full": 46, "hours": 90}
        self._tree = ttk.Treeview(frame, columns=cols, show="headings", height=11)
        for c in cols:
            self._tree.heading(c, text=headers[c])
            self._tree.column(c, width=widths[c],
                              anchor="e" if c in ("liters", "hours") else "w")
        self._tree.pack(fill="both", expand=True, pady=6)
        self._tree.bind("<Double-1>", lambda _e: self._on_edit())

        btns = ttk.Frame(frame)
        btns.pack(fill="x")
        ttk.Button(btns, text=t("Tankung hinzufügen…"), command=self._on_add).pack(side="left")
        ttk.Button(btns, text=t("Bearbeiten…"), command=self._on_edit).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Entfernen"), command=self._on_remove).pack(side="left")
        ttk.Button(btns, text=t("Schließen"), command=self.top.destroy).pack(side="right")

        self._refresh()

    def _refresh(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        entries = self._store.all_fuel(newest_first=False)
        for e in reversed(entries):  # neueste oben
            self._tree.insert(
                "", "end", iid=str(e.id),
                values=(
                    timeutil.to_display(e.timestamp, self._offset),
                    "" if e.liters is None else f"{e.liters:.1f}",
                    e.location,
                    "✓" if e.full_tank else "",
                    "" if e.engine_hours is None else f"{e.engine_hours:.1f}",
                ),
            )
        stats = fuel.consumption_stats(entries)
        self._show_summary(stats)
        self._show_remaining(entries, stats)

    def _save_tank(self) -> None:
        self._config.tank_capacity_l = _parse_float(self._tank.get()) or 0.0
        self._config.save()
        self._refresh()

    def _show_summary(self, stats) -> None:
        if stats["last_rate"] is None:
            self._summary.config(
                text=t("Verbrauch: noch nicht berechenbar — mind. zwei „voll getankt\"-"
                       "Einträge mit Motorstunden nötig.")
            )
            return
        txt = t("Verbrauch (letztes Intervall): {rate} l/h",
                rate=f"{stats['last_rate']:.1f}")
        if stats["avg_rate"] is not None:
            txt += t("      Ø {rate} l/h ({liters} l / {hours} h)",
                     rate=f"{stats['avg_rate']:.1f}",
                     liters=f"{stats['total_liters']:.0f}",
                     hours=f"{stats['total_hours']:.1f}")
        self._summary.config(text=txt)

    def _show_remaining(self, entries, stats) -> None:
        rate = stats["avg_rate"] if stats["avg_rate"] is not None else stats["last_rate"]
        capacity = _parse_float(self._tank.get())
        hours = self._live.snapshot().get("engine_hours")
        est = fuel.remaining_estimate(entries, capacity, hours, rate)
        if est is None:
            hint = ""
            if capacity and rate and hours is None:
                hint = t(" (keine Motorstunden aus dem NMEA-Netz)")
            self._remaining.config(text=t("Restfüllstand: —") + hint)
            return
        self._remaining.config(
            text=t("Restfüllstand (geschätzt): {rem} L von {cap} L  ·  "
                   "Reichweite ~{h} h Motorlaufzeit",
                   rem=f"{est['remaining_l']:.0f}", cap=f"{est['capacity_l']:.0f}",
                   h=f"{est['remaining_hours']:.1f}")
        )

    def _selected(self) -> Optional[int]:
        sel = self._tree.selection()
        return int(sel[0]) if sel else None

    def _entry(self, fid: int):
        return next((e for e in self._store.all_fuel() if e.id == fid), None)

    def _on_add(self) -> None:
        entry = FuelEntry(
            trip_id=self._trip_id,
            timestamp=utc_now_iso(),
            full_tank=1,
            engine_hours=self._live.snapshot().get("engine_hours"),
        )
        dlg = _FuelEntryDialog(self.top, entry, self._offset)
        self.top.wait_window(dlg.top)
        if dlg.result is not None:
            self._store.add_fuel(dlg.result)
            self._refresh()

    def _on_edit(self) -> None:
        fid = self._selected()
        if fid is None:
            return
        entry = self._entry(fid)
        if entry is None:
            return
        dlg = _FuelEntryDialog(self.top, entry, self._offset)
        self.top.wait_window(dlg.top)
        if dlg.result is not None:
            self._store.update_fuel(dlg.result)
            self._refresh()

    def _on_remove(self) -> None:
        fid = self._selected()
        if fid is None:
            return
        if messagebox.askyesno(t("Entfernen"), t("Tankung entfernen?")):
            self._store.delete_fuel(fid)
            self._refresh()


class _FuelEntryDialog:
    """Dialog für einen einzelnen Tank-Vorgang."""

    def __init__(self, parent, entry: FuelEntry, offset: float) -> None:
        self.result: Optional[FuelEntry] = None
        self._entry = entry
        self._offset = offset
        self.top = tk.Toplevel(parent)
        self.top.title(t("Tankung"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        def lab(text, r):
            ttk.Label(frame, text=text).grid(row=r, column=0, sticky="e", padx=4, pady=3)

        lab(t("Zeit (lokal):"), 0)
        ts = timeutil.to_display(entry.timestamp, offset) if entry.timestamp else ""
        self._ts = tk.StringVar(value=ts)
        ttk.Entry(frame, textvariable=self._ts, width=24).grid(row=0, column=1, sticky="w")

        lab(t("Liter:"), 1)
        self._liters = tk.StringVar(
            value="" if entry.liters is None else f"{entry.liters:g}")
        ttk.Entry(frame, textvariable=self._liters, width=12).grid(row=1, column=1, sticky="w")

        lab(t("Ort:"), 2)
        self._loc = tk.StringVar(value=entry.location)
        ttk.Entry(frame, textvariable=self._loc, width=24).grid(row=2, column=1, sticky="w")

        lab(t("Motorstunden:"), 3)
        self._hours = tk.StringVar(
            value="" if entry.engine_hours is None else f"{entry.engine_hours:g}")
        ttk.Entry(frame, textvariable=self._hours, width=12).grid(row=3, column=1, sticky="w")
        ttk.Label(frame, text=t("(aus NMEA vorbelegt)"), foreground="#888").grid(
            row=3, column=2, sticky="w")

        self._full = tk.BooleanVar(value=bool(entry.full_tank))
        ttk.Checkbutton(frame, text=t("voll getankt"), variable=self._full).grid(
            row=4, column=1, sticky="w", pady=3)

        lab(t("Notiz:"), 5)
        self._note = tk.StringVar(value=entry.note)
        ttk.Entry(frame, textvariable=self._note, width=30).grid(
            row=5, column=1, columnspan=2, sticky="w")

        btns = ttk.Frame(frame)
        btns.grid(row=6, column=0, columnspan=3, pady=(10, 0))
        ttk.Button(btns, text=t("Speichern"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_ok(self) -> None:
        e = self._entry
        new_ts = timeutil.from_display(self._ts.get().strip(), self._offset)
        if new_ts:
            e.timestamp = new_ts
        e.liters = _parse_float(self._liters.get())
        e.location = self._loc.get().strip()
        e.engine_hours = _parse_float(self._hours.get())
        e.full_tank = 1 if self._full.get() else 0
        e.note = self._note.get().strip()
        self.result = e
        self.top.destroy()


class _TripStartDialog:
    """Dialog zum Beginnen eines Törns (Start-Kennwerte wie in TripCon).

    Log-Stand und Motorenstunden werden — falls im NMEA-Netz vorhanden —
    aus den Live-Werten vorbelegt.
    """

    def __init__(self, parent: tk.Tk, snapshot: Optional[Dict[str, float]] = None) -> None:
        self.result: Optional[Dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Neuen Törn beginnen"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        self._vars: Dict[str, tk.StringVar] = {}
        rows = [
            ("name", t("Törn-Name:"), ""),
            ("start_location", t("Startort:"), ""),
            ("start_water_l", t("Wasser (Liter):"), ""),
            ("start_diesel_l", t("Diesel (Liter):"), ""),
            ("start_engine_hours", t("Motorenstunden:"), _fmt_live(snapshot, "engine_hours")),
            ("start_log_nm", t("Log-Stand (Nm):"), _fmt_live(snapshot, "log_total_nm")),
        ]
        for i, (key, label, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=4, pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=30).grid(row=i, column=1, pady=4)
            self._vars[key] = var
        if snapshot and (snapshot.get("engine_hours") or snapshot.get("log_total_nm")):
            ttk.Label(
                frame, text=t("(Motorstunden/Log aus NMEA vorbelegt)"), foreground="#888"
            ).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(2, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text=t("Törn beginnen"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

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
        self.top.title(t("Törn abschließen: {name}", name=trip.name or trip.start_location))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        # Endwerte bevorzugt aus NMEA (Log/Motorstunden), sonst Startwert
        live_hours = _fmt_live(snapshot, "engine_hours")
        live_log = _fmt_live(snapshot, "log_total_nm")
        self._vars: Dict[str, tk.StringVar] = {}
        rows = [
            ("end_location", t("Zielort:"), ""),
            ("end_water_l", t("Wasser (Liter):"), ""),
            ("end_diesel_l", t("Diesel (Liter):"), ""),
            ("end_engine_hours", t("Motorenstunden:"),
             live_hours or ("" if trip.start_engine_hours is None else str(trip.start_engine_hours))),
            ("end_log_nm", t("Log-Stand (Nm):"),
             live_log or ("" if trip.start_log_nm is None else str(trip.start_log_nm))),
        ]
        for i, (key, label, default) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="e", padx=4, pady=4)
            var = tk.StringVar(value=default)
            ttk.Entry(frame, textvariable=var, width=30).grid(row=i, column=1, pady=4)
            self._vars[key] = var

        ttk.Label(frame, text=t("Abschluss-Notiz:")).grid(
            row=len(rows), column=0, sticky="ne", padx=4, pady=4
        )
        self._note = tk.Text(frame, width=32, height=4)
        self._note.grid(row=len(rows), column=1, pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text=t("Törn abschließen"), command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

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


class _VoyageDialog:
    """Törns anlegen und Etappen (Trips) zuordnen."""

    def __init__(self, parent: tk.Tk, store) -> None:
        self._store = store
        self.top = tk.Toplevel(parent)
        self.top.title(t("Törns / Etappen gruppieren"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=560, foreground="#555",
            text=t("Fasse mehrere Etappen zu einem Törn zusammen: oben einen Törn "
                 "wählen oder 'Neu…' anlegen, unten die Etappen markieren und "
                 "'→ zuordnen'."),
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))

        ttk.Label(frame, text=t("Törn:")).grid(row=1, column=0, sticky="e")
        self._voy_var = tk.StringVar()
        self._voy_combo = ttk.Combobox(frame, textvariable=self._voy_var,
                                       state="readonly", width=30)
        self._voy_combo.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Button(frame, text=t("Neu…"), command=self._new).grid(row=1, column=2, padx=2)
        ttk.Button(frame, text=t("Umbenennen…"), command=self._rename).grid(row=1, column=3, padx=2)
        ttk.Button(frame, text=t("Löschen"), command=self._delete).grid(row=1, column=4, padx=2)

        ttk.Label(frame, text=t("Etappen (mehrere markierbar):")).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(10, 2))
        self._list = tk.Listbox(frame, width=74, height=12, selectmode="extended")
        self._list.grid(row=3, column=0, columnspan=5, sticky="we")

        btns = ttk.Frame(frame)
        btns.grid(row=4, column=0, columnspan=5, pady=10)
        ttk.Button(btns, text=t("→ dem gewählten Törn zuordnen"),
                   command=self._assign).pack(side="left", padx=4)
        ttk.Button(btns, text=t("aus Törn entfernen"),
                   command=self._remove).pack(side="left", padx=4)
        ttk.Button(btns, text=t("Schließen"), command=self.top.destroy).pack(side="left", padx=16)

        self._voy_ids: Dict[str, int] = {}
        self._trip_ids: List[int] = []
        self._refresh_voyages()
        self._refresh_trips()

    def _refresh_voyages(self) -> None:
        voyages = self._store.all_voyages()
        self._voy_ids = {(v.name or f"Törn #{v.id}"): v.id for v in voyages}
        self._voy_combo["values"] = list(self._voy_ids.keys())
        if self._voy_ids and self._voy_var.get() not in self._voy_ids:
            self._voy_var.set(next(iter(self._voy_ids)))
        elif not self._voy_ids:
            self._voy_var.set("")

    def _refresh_trips(self) -> None:
        self._list.delete(0, "end")
        self._trip_ids = []
        names = {v.id: (v.name or f"Törn #{v.id}") for v in self._store.all_voyages()}
        for tr in self._store.all_trips(newest_first=False):
            route = f"{tr.start_location or '?'} → {tr.end_location or '…'}"
            vn = names.get(tr.voyage_id, "—") if tr.voyage_id else "—"
            self._list.insert("end", f"#{tr.id}  {tr.name or route}   [{route}]   → {vn}")
            self._trip_ids.append(tr.id)

    def _cur_voyage_id(self) -> Optional[int]:
        return self._voy_ids.get(self._voy_var.get())

    def _selected_trip_ids(self) -> List[int]:
        return [self._trip_ids[i] for i in self._list.curselection()]

    def _new(self) -> None:
        from tkinter import simpledialog
        name = simpledialog.askstring(t("Neuer Törn"), t("Name des Törns:"), parent=self.top)
        if not name or not name.strip():
            return
        revier = simpledialog.askstring(t("Neuer Törn"), t("Revier (optional):"),
                                        parent=self.top) or ""
        vid = self._store.add_voyage(Voyage(name=name.strip(), revier=revier.strip()))
        self._refresh_voyages()
        self._voy_var.set(name.strip())
        self._refresh_trips()

    def _rename(self) -> None:
        vid = self._cur_voyage_id()
        if vid is None:
            return
        from tkinter import simpledialog
        v = self._store.get_voyage(vid)
        name = simpledialog.askstring(t("Umbenennen"), t("Name:"), initialvalue=v.name,
                                      parent=self.top)
        if name is None:
            return
        revier = simpledialog.askstring(t("Umbenennen"), t("Revier:"), initialvalue=v.revier,
                                        parent=self.top)
        v.name = name.strip()
        if revier is not None:
            v.revier = revier.strip()
        self._store.update_voyage(v)
        self._refresh_voyages()
        self._voy_var.set(v.name or f"Törn #{v.id}")

    def _delete(self) -> None:
        vid = self._cur_voyage_id()
        if vid is None:
            return
        if not messagebox.askyesno(
                t("Törn löschen"), t("Diesen Törn löschen?\n(Die Etappen bleiben erhalten.)")):
            return
        self._store.delete_voyage(vid)
        self._refresh_voyages()
        self._refresh_trips()

    def _assign(self) -> None:
        vid = self._cur_voyage_id()
        if vid is None:
            messagebox.showinfo(t("Törn"), t("Bitte oben einen Törn wählen oder 'Neu…'."))
            return
        ids = self._selected_trip_ids()
        if not ids:
            messagebox.showinfo(t("Etappen"), t("Bitte unten eine oder mehrere Etappen markieren."))
            return
        for tid in ids:
            self._store.set_trip_voyage(tid, vid)
        self._refresh_trips()

    def _remove(self) -> None:
        for tid in self._selected_trip_ids():
            self._store.set_trip_voyage(tid, None)
        self._refresh_trips()


class _ReportDialog:
    """Auswahl des Bericht-Typs (öffnet druckbares HTML im Browser)."""

    def __init__(self, parent: tk.Tk, has_trip: bool, voyages: list) -> None:
        self.result: Optional[dict] = None
        self._voyages = voyages
        self.top = tk.Toplevel(parent)
        self.top.title(t("Bericht erzeugen"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=480, foreground="#555",
            text=t("Ausgabe wählen: direkt als PDF speichern (nutzt den "
                   "installierten Edge/Chrome) oder als HTML im Browser öffnen "
                   "und dort drucken."),
        ).pack(anchor="w", pady=(0, 8))

        # Ausgabeformat
        og = ttk.LabelFrame(frame, text=t("Ausgabe"), padding=8)
        og.pack(fill="x", pady=(0, 8))
        self._output = tk.StringVar(value="pdf")
        ttk.Radiobutton(og, text=t("Als PDF speichern"), variable=self._output,
                        value="pdf").pack(side="left", padx=(2, 12))
        ttk.Radiobutton(og, text=t("Im Browser öffnen (HTML)"), variable=self._output,
                        value="html").pack(side="left")

        # Eintragsarten-Filter + Karte (gelten für jeden Bericht)
        mg = ttk.LabelFrame(frame, text=t("Eintragsarten & Karte"), padding=8)
        mg.pack(fill="x", pady=(0, 8))
        ttk.Label(mg, text=t("Angezeigte Eintragsarten (Bericht + Kartenmarkierung):"),
                  foreground="#556").grid(row=0, column=0, columnspan=4, sticky="w")
        self._map_auto = tk.BooleanVar(value=True)
        self._map_manual = tk.BooleanVar(value=True)
        self._map_tripcon = tk.BooleanVar(value=True)
        ttk.Checkbutton(mg, text="Autolog", variable=self._map_auto).grid(
            row=1, column=1, sticky="w", padx=(6, 0), pady=(2, 0))
        ttk.Checkbutton(mg, text=t("Manuell"), variable=self._map_manual).grid(
            row=1, column=2, sticky="w", padx=(6, 0), pady=(2, 0))
        ttk.Checkbutton(mg, text=t("Import"), variable=self._map_tripcon).grid(
            row=1, column=3, sticky="w", padx=(6, 0), pady=(2, 0))
        ttk.Separator(mg, orient="horizontal").grid(
            row=2, column=0, columnspan=4, sticky="we", pady=6)
        self._with_images = tk.BooleanVar(value=True)
        ttk.Checkbutton(mg, text=t("Fotos der Einträge einbetten"),
                        variable=self._with_images).grid(
            row=3, column=0, columnspan=4, sticky="w")
        self._with_map = tk.BooleanVar(value=True)
        ttk.Checkbutton(mg, text=t("Karte einbetten (ohne AIS)"),
                        variable=self._with_map).grid(row=4, column=0, columnspan=4,
                                                      sticky="w")

        # 1) Ganzer Törn (mehrere Etappen)
        vg = ttk.LabelFrame(frame, text=t("Ganzer Törn (mehrere Etappen)"), padding=8)
        vg.pack(fill="x", pady=4)
        self._voy_map = {f"{v.name or (t('Törn #') + str(v.id))}": v.id for v in voyages}
        self._voy_var = tk.StringVar()
        if self._voy_map:
            self._voy_var.set(next(iter(self._voy_map)))
        row = ttk.Frame(vg); row.pack(fill="x")
        ttk.Label(row, text=t("Törn:")).pack(side="left")
        combo = ttk.Combobox(row, textvariable=self._voy_var, state="readonly",
                             width=34, values=list(self._voy_map.keys()))
        combo.pack(side="left", padx=6)
        bv1 = ttk.Button(vg, text=t("Törn-Bericht erstellen"), width=42,
                         command=lambda: self._pick("voyage"))
        bv1.pack(fill="x", pady=(6, 2))
        if not self._voy_map:
            combo.config(state="disabled"); bv1.config(state="disabled")
            ttk.Label(vg, foreground="#b25000",
                      text=t("(Noch keine Törns — unter Extras → 'Törns/Etappen "
                             "gruppieren…' anlegen.)"), wraplength=380).pack(anchor="w")

        # 2) Einzelne Etappe (aktueller Törn)
        eg = ttk.LabelFrame(frame, text=t("Einzelne Etappe (aktueller Törn)"), padding=8)
        eg.pack(fill="x", pady=4)
        be1 = ttk.Button(eg, text=t("Etappen-Bericht erstellen"), width=42,
                         command=lambda: self._pick("trip"))
        be1.pack(fill="x", pady=2)
        if not has_trip:
            be1.config(state="disabled")
            ttk.Label(eg, foreground="#b25000",
                      text=t("(Oben keine Etappe ausgewählt.)")).pack(anchor="w")

        # 3) Fahrtenbuch
        ttk.Button(frame, text=t("Fahrtenbuch (alle Törns)"), width=42,
                   command=lambda: self._pick("fahrtenbuch")).pack(
            fill="x", pady=(8, 2))
        ttk.Button(frame, text=t("Abbrechen"), command=self.top.destroy).pack(pady=(10, 0))

    def _pick(self, kind: str) -> None:
        voyage_id = self._voy_map.get(self._voy_var.get()) if kind == "voyage" else None
        types = set()
        if self._map_auto.get():
            types.add("auto")
        if self._map_manual.get():
            types.add("manual")
        if self._map_tripcon.get():
            types.add("tripcon")
        # alle drei (oder keine) gewählt = keine Einschränkung (None); sonst
        # gilt der Filter für die Einträge im Bericht UND die Kartenmarkierung.
        types = None if len(types) in (0, 3) else types
        self.result = {
            "kind": kind, "with_images": self._with_images.get(),
            "voyage_id": voyage_id,
            "with_map": self._with_map.get(),
            "entry_types": types,
            "as_pdf": self._output.get() == "pdf",
        }
        self.top.destroy()


class _MeilenDialog:
    """Eingaben für den Seemeilen-Nachweis (Segelscheine DE/AT/CH)."""

    _ROLES = ["Skipper", "Co-Skipper", "Wachführer", "verantw. Rudergänger",
              "Crew", "Mitsegler"]

    def __init__(self, parent: tk.Tk, config) -> None:
        self.result: Optional[dict] = None
        self.top = tk.Toplevel(parent)
        self.top.title(t("Seemeilen-Nachweis"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, wraplength=470, foreground="#555",
            text=t("Erzeugt eine druckbare Meilen-Zusammenstellung aus deinen "
                   "Törns — mit Übersicht der Anforderungen für SKS/SSS/SHS (DE), "
                   "FB3/FB4 (AT) und Hochseeschein (CH). Jede Etappe hat eine "
                   "Unterschriftsspalte für den Skipper."),
        ).pack(anchor="w", pady=(0, 10))

        grid = ttk.Frame(frame)
        grid.pack(fill="x")

        def row(label, r):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="e", padx=4, pady=4)

        row(t("Antragsteller/in:"), 0)
        self._name = tk.StringVar(value=getattr(config, "skipper_name", "") or "")
        ttk.Entry(grid, textvariable=self._name, width=32).grid(
            row=0, column=1, columnspan=3, sticky="w", pady=4)

        row(t("Funktion (Standard):"), 1)
        self._role = tk.StringVar(value="Skipper")
        ttk.Combobox(grid, textvariable=self._role, width=22, values=self._ROLES).grid(
            row=1, column=1, sticky="w")
        ttk.Label(grid, text=t("(je Törn aus der Crewliste, falls hinterlegt)"),
                  foreground="#888").grid(row=1, column=2, columnspan=2, sticky="w")

        row(t("Zeitraum von:"), 2)
        self._von = tk.StringVar()
        ttk.Entry(grid, textvariable=self._von, width=14).grid(row=2, column=1, sticky="w")
        ttk.Label(grid, text=t("bis:")).grid(row=2, column=2, sticky="e")
        self._bis = tk.StringVar()
        ttk.Entry(grid, textvariable=self._bis, width=14).grid(row=2, column=3, sticky="w")
        ttk.Label(grid, text=t("(JJJJ-MM-TT, leer = alle)"), foreground="#888").grid(
            row=3, column=1, columnspan=3, sticky="w")

        self._night = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text=t("Nachtmeilen berechnen (aus Sonnenstand)"),
                        variable=self._night).pack(anchor="w", pady=(8, 0))

        og = ttk.LabelFrame(frame, text=t("Ausgabe"), padding=8)
        og.pack(fill="x", pady=(8, 0))
        self._output = tk.StringVar(value="pdf")
        ttk.Radiobutton(og, text=t("Als PDF speichern"), variable=self._output,
                        value="pdf").pack(side="left", padx=(2, 12))
        ttk.Radiobutton(og, text=t("Im Browser öffnen (HTML)"), variable=self._output,
                        value="html").pack(side="left")

        buttons = ttk.Frame(frame)
        buttons.pack(pady=(12, 0))
        ttk.Button(buttons, text=t("Nachweis erstellen"), command=self._on_ok).pack(
            side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(
            side="left", padx=4)

    def _on_ok(self) -> None:
        self.result = {
            "name": self._name.get().strip(),
            "role": self._role.get().strip() or "Skipper",
            "von": self._von.get().strip(),
            "bis": self._bis.get().strip(),
            "night": self._night.get(),
            "as_pdf": self._output.get() == "pdf",
        }
        self.top.destroy()


class _SourcesDialog:
    """Verwaltet die Liste der Datenquellen (mehrere gleichzeitig möglich)."""

    def __init__(self, parent: tk.Tk, defs: list) -> None:
        self.result: Optional[list] = None
        self._defs = [dict(d) for d in defs]
        self.top = tk.Toplevel(parent)
        self.top.title(t("Datenquellen"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=t("Aktive Quellen (Häkchen = wird gelesen, Doppelklick schaltet um):")).grid(
            row=0, column=0, columnspan=6, sticky="w"
        )
        self._listbox = tk.Listbox(frame, width=52, height=5)
        self._listbox.grid(row=1, column=0, columnspan=5, pady=6, sticky="w")
        # Doppelklick auf eine Quelle schaltet sie ein/aus (zum Testen, welche
        # Quelle welche Werte liefert — ohne sie löschen und neu eintragen zu müssen).
        self._listbox.bind("<Double-Button-1>", lambda _e: self._on_toggle())
        side = ttk.Frame(frame)
        side.grid(row=1, column=5, sticky="n", padx=4)
        ttk.Button(side, text=t("Ein/Aus"), command=self._on_toggle).pack(fill="x", pady=(0, 4))
        ttk.Button(side, text=t("Entfernen"), command=self._on_remove).pack(fill="x")
        self._refresh_list()

        # Eingabezeile zum Hinzufügen
        ttk.Label(frame, text=t("Protokoll:")).grid(row=2, column=0, sticky="e", pady=6)
        self._proto = tk.StringVar(value="tcp")
        proto = ttk.Combobox(
            frame, textvariable=self._proto,
            values=["tcp", "udp", "serial", "signalk"],
            width=8, state="readonly",
        )
        proto.grid(row=2, column=1, sticky="w")
        proto.bind("<<ComboboxSelected>>", lambda _e: self._update_hint())
        ttk.Label(frame, text=t("Host / COM:")).grid(row=2, column=2, sticky="e")
        self._host = tk.StringVar()
        ttk.Entry(frame, textvariable=self._host, width=16).grid(row=2, column=3, sticky="w")
        ttk.Label(frame, text=t("Port / Baud:")).grid(row=2, column=4, sticky="e")
        self._port = tk.StringVar()
        ttk.Entry(frame, textvariable=self._port, width=8).grid(row=2, column=5, sticky="w")

        ttk.Button(frame, text=t("+ Quelle hinzufügen"), command=self._on_add).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=6
        )
        self._hint = ttk.Label(frame, text="", foreground="#888")
        self._hint.grid(row=3, column=2, columnspan=4, sticky="w")
        self._update_hint()

        # Vorlagen
        tmpl = ttk.Frame(frame)
        tmpl.grid(row=4, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(tmpl, text=t("Vorlagen:"), foreground="#555").pack(side="left")
        ttk.Button(tmpl, text=t("GPS-Maus (USB)"), command=self._tmpl_gpsmaus).pack(side="left", padx=3)
        ttk.Button(tmpl, text="B&G (TCP 10110)", command=self._tmpl_bg).pack(side="left", padx=3)
        ttk.Button(tmpl, text="PredictWind DataHub", command=self._tmpl_datahub).pack(side="left", padx=3)
        ttk.Button(tmpl, text="Maretron (COM)", command=self._tmpl_maretron).pack(side="left", padx=3)
        ttk.Button(tmpl, text="Signal K", command=self._tmpl_signalk).pack(side="left", padx=3)
        ttk.Button(tmpl, text=t("🔍 Ports…"), command=self._on_pick_port).pack(side="left", padx=3)
        self._gofree_btn = ttk.Button(
            tmpl, text=t("🔍 GoFree suchen"), command=self._on_gofree_search
        )
        self._gofree_btn.pack(side="left", padx=(12, 3))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=6, pady=(12, 0))
        ttk.Button(buttons, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _refresh_list(self) -> None:
        self._listbox.delete(0, "end")
        for i, d in enumerate(self._defs):
            port = d.get("port")
            baud = "auto" if str(port).strip() in ("0", "") else port
            on = d.get("enabled", True)
            mark = "☑" if on else "☐"
            text = f"{mark}  {d.get('protocol', 'tcp')}   {d.get('host')} : {baud}"
            if not on:
                text += t("   (aus)")
            self._listbox.insert("end", text)
            if not on:                       # ausgeschaltete Quellen abblenden
                self._listbox.itemconfig(i, foreground="#999")

    def _update_hint(self) -> None:
        if self._proto.get() == "serial":
            self._hint.config(text=t("seriell: Host = COM-Port (COM13), "
                                     "Port = Baud (0 = automatisch erkennen)"))
        elif self._proto.get() == "signalk":
            self._hint.config(text=t("Signal K: Host = Server-IP, "
                                     "Port = 3000 (REST-Abfrage im Sekundentakt)"))
        else:
            self._hint.config(text="")

    def _tmpl_gpsmaus(self) -> None:
        # GPS-Maus (USB): NMEA über einen COM-Port. Baud 0 = automatisch
        # erkennen. Port wird – wenn möglich – automatisch geraten, sonst
        # über „🔍 Ports…" auswählen.
        from saillog import serialports
        self._proto.set("serial")
        guess = serialports.guess_gps_port()
        self._host.set(guess or "COM13")
        self._port.set("0")
        self._update_hint()

    def _on_pick_port(self) -> None:
        """Verfügbare COM-Ports auflisten und einen auswählen (für die GPS-Maus)."""
        from saillog import serialports
        ports = serialports.gps_first(serialports.available_ports())
        if not ports:
            messagebox.showinfo(
                t("COM-Ports"),
                t("Keine seriellen Ports gefunden.\n\n"
                  "• GPS-Maus eingesteckt? Im Windows-Gerätemanager unter\n"
                  "  „Anschlüsse (COM & LPT)“ erscheint sie als COMx.\n"
                  "• Fehlt pyserial? Dann 'pip install pyserial'."))
            return
        chooser = _PortChooser(self.top, ports)
        self.top.wait_window(chooser.top)
        if chooser.result:
            self._proto.set("serial")
            self._host.set(chooser.result)
            if not self._port.get().strip():
                self._port.set("0")
            self._update_hint()

    def _on_add(self) -> None:
        host = self._host.get().strip()
        port = self._port.get().strip()
        if not host or not port:
            return
        self._defs.append({"host": host, "port": port,
                           "protocol": self._proto.get(), "enabled": True})
        self._host.set("")
        self._port.set("")
        self._refresh_list()

    def _on_remove(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            del self._defs[sel[0]]
            self._refresh_list()

    def _on_toggle(self) -> None:
        """Schaltet die ausgewählte Quelle ein/aus, ohne sie zu löschen —
        praktisch zum Testen, welche Quelle welche Werte liefert."""
        sel = self._listbox.curselection()
        if not sel:
            return
        i = sel[0]
        self._defs[i]["enabled"] = not self._defs[i].get("enabled", True)
        self._refresh_list()
        self._listbox.selection_set(i)      # Auswahl beibehalten

    def _on_gofree_search(self) -> None:
        """Lauscht kurz auf GoFree-Ankündigungen und trägt die NMEA-Quelle ein."""
        import threading
        from saillog import discover

        self._gofree_btn.config(state="disabled")
        self._hint.config(text=t("Suche GoFree-Geräte (bis 6 s) …"))

        def work():
            try:
                devices = discover.listen_gofree(seconds=6.0)
            except Exception:  # noqa: BLE001
                devices = []
            self.top.after(0, lambda: self._gofree_done(devices))

        threading.Thread(target=work, daemon=True).start()

    def _gofree_done(self, devices) -> None:
        from saillog import discover

        self._gofree_btn.config(state="normal")
        self._update_hint()
        if not devices:
            messagebox.showinfo(
                "GoFree",
                t("Kein GoFree-Gerät gehört.\n\n"
                  "• Ist am MFD GoFree/Drahtlos aktiv und der Laptop im Plotter-Netz?\n"
                  "• Windows-Firewall für UDP 2052 freigeben und das Netzprofil auf\n"
                  "  'Privat' stellen (Details: find_sources.py --gofree --raw)."),
            )
            return

        existing = {
            (str(d.get("host")), str(d.get("port"))) for d in self._defs
        }
        added, names = 0, []
        for dev in devices:
            names.append(dev.get("model") or dev.get("name") or dev.get("ip"))
            for hint in discover.gofree_source_hints(dev):
                _, _, addr = hint.partition(" ")          # "TCP ip:port"
                host, _, port = addr.rpartition(":")
                if host and port and (host, port) not in existing:
                    self._defs.append({"protocol": "tcp", "host": host, "port": port})
                    existing.add((host, port))
                    added += 1
        self._refresh_list()
        msg = t("Gefunden: ") + ", ".join(str(n) for n in names)
        msg += (t("\n{count} NMEA-Quelle(n) hinzugefügt.", count=added)
                if added else t("\nQuelle(n) waren bereits vorhanden."))
        messagebox.showinfo("GoFree", msg)

    def _tmpl_bg(self) -> None:
        # NMEA-0183-Standardport 10110 (TCP). Die Plotter-IP ist netzabhängig —
        # am einfachsten über „🔍 GoFree suchen" automatisch eintragen lassen.
        self._proto.set("tcp"); self._host.set(""); self._port.set("10110")
        self._update_hint()

    def _tmpl_datahub(self) -> None:
        # PredictWind DataHub: wandelt NMEA2000 -> NMEA0183 und sendet per WLAN/
        # LAN über TCP/UDP. Auf dem eigenen WLAN des DataHub ist er unter
        # 10.10.10.1 erreichbar; NMEA-Ausgabe standardmäßig TCP 11102 (UDP
        # 11101). Ports ggf. unter 10.10.10.1 -> NMEA -> Settings prüfen. Hängt
        # er am Boots-Router, IP per „🔍 Ports…"/discover.py ermitteln.
        self._proto.set("tcp"); self._host.set("10.10.10.1"); self._port.set("11102")
        self._update_hint()

    def _tmpl_maretron(self) -> None:
        # COM-Port ist rechnerabhängig (im Gerätemanager nachsehen); COM3 als
        # üblicher Vorgabewert.
        self._proto.set("serial"); self._host.set("COM3"); self._port.set("115200")
        self._update_hint()

    def _tmpl_signalk(self) -> None:
        # Signal-K-Server: REST-Abfrage über HTTP, Standardport 3000. Die
        # Server-IP ist netzabhängig (oft ein Raspberry Pi im Bordnetz).
        self._proto.set("signalk"); self._host.set(""); self._port.set("3000")
        self._update_hint()

    def _on_ok(self) -> None:
        self.result = self._defs
        self.top.destroy()


class _PortChooser:
    """Kleiner Auswahldialog für einen seriellen Port (GPS-Maus)."""

    def __init__(self, parent, ports) -> None:
        self.result: Optional[str] = None
        self._devices = [dev for dev, _desc in ports]
        self.top = tk.Toplevel(parent)
        self.top.title(t("COM-Port wählen"))
        self.top.transient(parent)
        self.top.grab_set()
        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t("Gefundene serielle Ports "
                                "(oben stehen wahrscheinliche GPS-Empfänger):")).pack(
            anchor="w")
        self._listbox = tk.Listbox(frame, width=52, height=min(8, max(3, len(ports))))
        for dev, desc in ports:
            self._listbox.insert("end", f"{dev} — {desc}")
        self._listbox.selection_set(0)
        self._listbox.pack(pady=8, fill="x")
        self._listbox.bind("<Double-Button-1>", lambda _e: self._on_ok())
        buttons = ttk.Frame(frame)
        buttons.pack()
        ttk.Button(buttons, text=t("Übernehmen"), command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(buttons, text=t("Abbrechen"), command=self.top.destroy).pack(side="left", padx=4)

    def _on_ok(self) -> None:
        sel = self._listbox.curselection()
        if sel:
            self.result = self._devices[sel[0]]
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
        self.top.title(t("NMEA-Rohdaten"))
        self.top.geometry("640x420")

        toolbar = ttk.Frame(self.top, padding=6)
        toolbar.pack(fill="x")
        self._pause_btn = ttk.Button(toolbar, text=t("Pause"), command=self._toggle_pause)
        self._pause_btn.pack(side="left")
        ttk.Button(toolbar, text=t("Leeren"), command=self._clear).pack(side="left", padx=6)
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
        self._pause_btn.config(text=t("Weiter") if self._paused else t("Pause"))

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
            self._info.config(text=t("{count} Sätze gepuffert", count=len(self._buffer)))
        self.top.after(700, self._refresh)

    def _close(self) -> None:
        self._alive = False
        self.top.destroy()
