"""Einstiegspunkt: startet die triplog-GUI."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import messagebox


def main() -> int:
    """Startet die Anwendung."""
    root = tk.Tk()
    root.withdraw()  # verstecken bis die GUI aufgebaut ist

    try:
        from triplog.gui import Application
    except Exception as exc:  # noqa: BLE001 - beim Start alles abfangen
        messagebox.showerror(
            "Startfehler",
            f"triplog konnte nicht gestartet werden:\n{exc}",
        )
        return 1

    Application(root)
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
