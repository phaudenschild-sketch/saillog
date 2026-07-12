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
