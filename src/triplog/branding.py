"""Markenzeichen für TripLog — Logo (Inline-SVG), Name und Copyright.

Eine Quelle für alle Ausgaben (Berichte, Crewliste, Karte). Das Logo ist als
skalierbares Inline-SVG hinterlegt und braucht keine externe Datei.
"""

APP_NAME = "TripLog"
APP_TAGLINE = "Segel-Logbuch"
COPYRIGHT = "\u00a9 Peter Haudenschild"

LOGO_SVG = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 260 72" role="img" aria-label="TripLog">
  <!-- Badge -->
  <rect x="4" y="4" width="64" height="64" rx="15" fill="#0d2a4a"/>
  <!-- gestrichelte Kurslinie (Nicken auf die Track-Aufzeichnung) -->
  <path d="M12 54 C 22 40, 30 40, 40 30 S 56 16, 62 12" fill="none"
        stroke="#e8820c" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="1.5 5"/>
  <!-- Mast -->
  <line x1="37" y1="13" x2="37" y2="45" stroke="#eaf2fb" stroke-width="2.2"/>
  <!-- Grosssegel (links) -->
  <path d="M35 15 L35 44 L18 44 Z" fill="#eaf2fb"/>
  <!-- Fock (rechts) -->
  <path d="M39 19 L39 44 L52 44 Z" fill="#9fd2f2"/>
  <!-- Rumpf -->
  <path d="M15 46 H57 L50 55 A6 6 0 0 1 45 57 H25 A10 10 0 0 1 15 46 Z" fill="#1c7fc0"/>
  <!-- Wellen -->
  <path d="M13 61 q 6 4 12 0 t 12 0 t 12 0" fill="none" stroke="#1c7fc0"
        stroke-width="2" stroke-linecap="round" opacity="0.7"/>
  <!-- Wortmarke -->
  <text x="82" y="47" font-family="Segoe UI, Helvetica, Arial, sans-serif"
        font-size="34" font-weight="700" letter-spacing="0.5">
    <tspan fill="#0d2a4a">Trip</tspan><tspan fill="#e8820c">Log</tspan>
  </text>
  <text x="83" y="62" font-family="Segoe UI, Helvetica, Arial, sans-serif"
        font-size="10.5" letter-spacing="3.4" fill="#5a6b7d">SEGEL-LOGBUCH</text>
</svg>"""


def logo_html(height: int = 52) -> str:
    """Logo als skalierbares Inline-SVG (Höhe in px) für HTML-Ausgaben."""
    return (
        f'<span style="display:inline-block;height:{height}px;line-height:0" '
        f'aria-label="{APP_NAME}">' + LOGO_SVG.replace(
            "<svg ", f'<svg height="{height}" ', 1) + "</span>"
    )


# 64x64-PNG des App-Icons (aus assets/icon.svg gerendert), base64 fuer das
# tkinter-Fenstericon zur Laufzeit — kein Dateizugriff noetig.
ICON_PNG_B64 = "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"


def set_window_icon(root) -> bool:
    """Setzt das App-Icon fuer ein tkinter-Fenster (best effort)."""
    try:
        import tkinter as tk
        img = tk.PhotoImage(data=ICON_PNG_B64)
        root.iconphoto(True, img)
        root._triplog_icon = img          # Referenz halten (sonst weg-GC)
        return True
    except Exception:                     # noqa: BLE001  (Icon ist optional)
        return False
