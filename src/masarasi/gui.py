"""tkinter-GUI für masarasi — das Segel-Logbuch."""

from __future__ import annotations

import tkinter as tk
from collections import deque
from tkinter import filedialog, messagebox, ttk
from typing import Deque, Dict, Optional

from masarasi.config import Config
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
from masarasi.storage import LogbookStore

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

        root.title("masarasi — Segel-Logbuch")
        root.geometry("880x680")
        root.minsize(720, 560)

        self._build_ui()
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
        ttk.Combobox(
            top, textvariable=self._proto_var, values=["tcp", "udp"],
            width=5, state="readonly",
        ).grid(row=0, column=5, padx=4)

        self._connect_btn = ttk.Button(top, text="Verbinden", command=self._on_connect)
        self._connect_btn.grid(row=0, column=6, padx=8)

        ttk.Button(top, text="Rohdaten…", command=self._on_show_raw).grid(
            row=0, column=7, padx=4
        )

        self._status_label = tk.Label(top, text="getrennt", fg="#888888")
        self._status_label.grid(row=0, column=8, padx=8)

        # Live-Daten-Dashboard
        dash = ttk.LabelFrame(self._root, text="Aktuelle Messwerte")
        dash.pack(fill="x", **pad)
        columns = 4
        for index, (key, label, unit) in enumerate(FIELD_LABELS):
            row, col = divmod(index, columns)
            cell = ttk.Frame(dash)
            cell.grid(row=row, column=col, sticky="w", padx=10, pady=6)
            ttk.Label(cell, text=label, foreground="#666").pack(anchor="w")
            value = tk.Label(cell, text="—", font=("TkDefaultFont", 13, "bold"))
            value.pack(anchor="w")
            ttk.Label(cell, text=unit, foreground="#999").pack(anchor="w")
            self._value_labels[key] = value

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
            controls, text="Manueller Eintrag…", command=self._on_manual_entry
        ).grid(row=0, column=3, padx=8)

        ttk.Button(controls, text="CSV exportieren", command=self._on_export_csv).grid(
            row=0, column=4, padx=4
        )
        ttk.Button(controls, text="GPX exportieren", command=self._on_export_gpx).grid(
            row=0, column=5, padx=4
        )

        # Logbuch-Tabelle
        table_frame = ttk.Frame(self._root)
        table_frame.pack(fill="both", expand=True, **pad)

        cols = ("time", "type", "pos", "sog", "cog", "wind", "depth", "note")
        headers = {
            "time": "Zeit (UTC)", "type": "Typ", "pos": "Position",
            "sog": "SOG", "cog": "COG", "wind": "Wind", "depth": "Tiefe",
            "note": "Notiz",
        }
        widths = {
            "time": 150, "type": 60, "pos": 160, "sog": 55, "cog": 55,
            "wind": 110, "depth": 60, "note": 200,
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

        bottom = ttk.Frame(self._root)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Eintrag löschen", command=self._on_delete_entry).pack(
            side="left"
        )
        self._count_label = ttk.Label(bottom, text="")
        self._count_label.pack(side="right")

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
        for key, label, _unit in FIELD_LABELS:
            value = snapshot.get(key)
            if value is None:
                label_widget = self._value_labels[key]
                label_widget.config(text="—", fg="#bbbbbb")
            else:
                if key in ("lat", "lon"):
                    text = f"{value:.5f}"
                else:
                    text = f"{value:.1f}"
                self._value_labels[key].config(text=text, fg="#111111")

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

    def _on_auto_entry(self, _entry) -> None:
        self._root.after(0, self._refresh_logbook)

    # --- manuelle Einträge --------------------------------------------------

    def _on_manual_entry(self) -> None:
        dialog = _ManualEntryDialog(self._root, self._live.snapshot())
        self._root.wait_window(dialog.top)
        if dialog.result is None:
            return
        self._logbook.add_manual(
            note=dialog.result["note"],
            crew=dialog.result["crew"],
            location=dialog.result["location"],
        )
        self._refresh_logbook()

    # --- Logbuch-Tabelle ----------------------------------------------------

    def _refresh_logbook(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        for entry in self._store.all(limit=5000, newest_first=True):
            pos = ""
            if entry.lat is not None and entry.lon is not None:
                pos = f"{entry.lat:.4f}, {entry.lon:.4f}"
            wind = ""
            if entry.aws_kn is not None:
                wind = f"{entry.aws_kn:.0f}kn @ {entry.awa_deg or 0:.0f}°"
            self._tree.insert(
                "", "end", iid=str(entry.id),
                values=(
                    entry.timestamp,
                    entry.entry_type,
                    pos,
                    "" if entry.sog_kn is None else f"{entry.sog_kn:.1f}",
                    "" if entry.cog_deg is None else f"{entry.cog_deg:.0f}",
                    wind,
                    "" if entry.depth_m is None else f"{entry.depth_m:.1f}",
                    entry.note,
                ),
            )
        self._count_label.config(text=f"{self._store.count()} Einträge")

    def _on_delete_entry(self) -> None:
        selection = self._tree.selection()
        if not selection:
            return
        if not messagebox.askyesno("Löschen", "Ausgewählte Einträge löschen?"):
            return
        for iid in selection:
            self._store.delete(int(iid))
        self._refresh_logbook()

    # --- Export -------------------------------------------------------------

    def _on_export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="logbuch.csv",
        )
        if not path:
            return
        count = self._store.export_csv(path)
        messagebox.showinfo("Export", f"{count} Einträge nach CSV exportiert.")

    def _on_export_gpx(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".gpx", filetypes=[("GPX", "*.gpx")],
            initialfile="toern.gpx",
        )
        if not path:
            return
        count = self._store.export_gpx(path)
        messagebox.showinfo("Export", f"{count} Positionspunkte nach GPX exportiert.")

    # --- Schließen ----------------------------------------------------------

    def _on_close(self) -> None:
        self._logbook.stop_auto()
        if self._source is not None:
            self._source.stop()
        self._root.destroy()


class _ManualEntryDialog:
    """Dialog für einen manuellen Logbuch-Eintrag mit Auto-Fill."""

    def __init__(self, parent: tk.Tk, snapshot: Dict[str, float]) -> None:
        self.result: Optional[Dict[str, str]] = None
        self.top = tk.Toplevel(parent)
        self.top.title("Manueller Eintrag")
        self.top.transient(parent)
        self.top.grab_set()

        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)

        info = "Aktuelle Messwerte werden automatisch übernommen."
        if snapshot:
            parts = []
            if "lat" in snapshot and "lon" in snapshot:
                parts.append(f"Pos {snapshot['lat']:.4f}, {snapshot['lon']:.4f}")
            if "sog_kn" in snapshot:
                parts.append(f"SOG {snapshot['sog_kn']:.1f}kn")
            if "aws_kn" in snapshot:
                parts.append(f"Wind {snapshot['aws_kn']:.0f}kn")
            if parts:
                info = " · ".join(parts)
        else:
            info = "Keine Live-Messwerte verfügbar (nur Text wird gespeichert)."
        ttk.Label(frame, text=info, foreground="#555").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        ttk.Label(frame, text="Ort / Hafen:").grid(row=1, column=0, sticky="e", pady=4)
        self._location = tk.StringVar()
        ttk.Entry(frame, textvariable=self._location, width=40).grid(row=1, column=1, pady=4)

        ttk.Label(frame, text="Crew:").grid(row=2, column=0, sticky="e", pady=4)
        self._crew = tk.StringVar()
        ttk.Entry(frame, textvariable=self._crew, width=40).grid(row=2, column=1, pady=4)

        ttk.Label(frame, text="Notiz:").grid(row=3, column=0, sticky="ne", pady=4)
        self._note = tk.Text(frame, width=40, height=6)
        self._note.grid(row=3, column=1, pady=4)

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text="Speichern", command=self._on_save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abbrechen", command=self.top.destroy).pack(side="left", padx=4)

        self._note.focus_set()

    def _on_save(self) -> None:
        self.result = {
            "note": self._note.get("1.0", "end").strip(),
            "crew": self._crew.get().strip(),
            "location": self._location.get().strip(),
        }
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
