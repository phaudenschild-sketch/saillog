"""Erzeugt eine druckbare Crewliste (Ein-/Ausklarieren) als HTML.

Zweisprachig Deutsch/Englisch nach dem üblichen Muster (blauwasser.de):
Bootsangaben oben, nummerierte Crew-Tabelle, Unterschrift des Skippers.
Die Seite ist auf A4 ausgelegt und enthält einen „Drucken"-Knopf; der
Nutzer druckt aus dem Browser (oder speichert als PDF).

Reine Standardbibliothek.
"""

from __future__ import annotations

from html import escape
from typing import Dict, List, Optional

# Mindestanzahl Zeilen in der Tabelle (leere Zeilen zum handschriftlichen
# Ergänzen an Bord / beim Amt).
_MIN_ROWS = 8

# Bootsangaben-Felder: (config-Schlüssel, Label DE, Label EN)
_BOAT_FIELDS = [
    ("ship_name", "Schiffsname", "Name of yacht"),
    ("ship_type", "Bootstyp", "Type of boat"),
    ("ship_flag", "Flagge", "Flag"),
    ("home_port", "Heimathafen", "Port of registry"),
    ("call_sign", "Rufzeichen", "Call sign"),
    ("ship_mmsi", "MMSI", "MMSI"),
    ("registration_no", "Registriernummer", "Registration No."),
    ("ship_length", "Länge über alles", "Length overall"),
]

# Crew-Tabellenspalten: (Attribut, Label DE, Label EN)
_CREW_COLUMNS = [
    ("position", "Funktion", "Position"),
    ("last_name", "Name", "Surname"),
    ("first_name", "Vorname", "First name"),
    ("birth_date", "Geburtsdatum", "Date of birth"),
    ("birth_place", "Geburtsort", "Place of birth"),
    ("nationality", "Staatsangehörigkeit", "Nationality"),
    ("passport_no", "Reisepass-Nr.", "Passport No."),
]


def build_html(
    boat: Dict[str, str],
    crew: List,
    place: str = "",
    date_str: str = "",
) -> str:
    """Baut die Crewliste als vollständiges HTML-Dokument."""
    boat_rows = []
    for key, de, en in _BOAT_FIELDS:
        value = escape(str(boat.get(key, "") or ""))
        boat_rows.append(
            f'<div class="bf"><span class="k">{de}<br><i>{en}</i></span>'
            f'<span class="v">{value}</span></div>'
        )

    head_cells = ['<th class="nr">Nr.<br><i>No.</i></th>']
    for _attr, de, en in _CREW_COLUMNS:
        head_cells.append(f"<th>{de}<br><i>{en}</i></th>")

    body_rows = []
    n = max(len(crew), _MIN_ROWS)
    for i in range(n):
        member = crew[i] if i < len(crew) else None
        cells = [f'<td class="nr">{i + 1}</td>']
        for attr, _de, _en in _CREW_COLUMNS:
            val = escape(str(getattr(member, attr, "") or "")) if member else ""
            cells.append(f"<td>{val}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    place_date = escape(", ".join(p for p in (place or "", date_str or "") if p))

    return _TEMPLATE.format(
        boat_rows="\n".join(boat_rows),
        head="".join(head_cells),
        body="\n".join(body_rows),
        place_date=place_date,
    )


_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Crewliste / Crew List</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: Arial, Helvetica, sans-serif; color: #000; margin: 24px;
          font-size: 12px; }}
  h1 {{ font-size: 20px; margin: 0 0 2px; }}
  .sub {{ color: #444; margin: 0 0 16px; font-size: 12px; }}
  .boat {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px 24px;
           border: 1px solid #000; padding: 10px 12px; margin-bottom: 16px; }}
  .bf {{ display: flex; justify-content: space-between; align-items: baseline;
         border-bottom: 1px dotted #999; padding: 3px 0; gap: 10px; }}
  .bf .k {{ color: #333; white-space: nowrap; }}
  .bf .k i {{ color: #777; font-weight: normal; }}
  .bf .v {{ font-weight: bold; text-align: right; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ border: 1px solid #000; padding: 5px 6px; text-align: left;
            vertical-align: middle; }}
  th {{ background: #eee; font-size: 11px; }}
  th i, td i {{ color: #777; font-weight: normal; }}
  td {{ height: 30px; }}
  .nr {{ width: 34px; text-align: center; }}
  .sign {{ display: flex; justify-content: space-between; margin-top: 34px;
           gap: 40px; }}
  .sign div {{ flex: 1; border-top: 1px solid #000; padding-top: 4px;
               color: #333; }}
  .place {{ margin-top: 20px; }}
  .toolbar {{ margin-bottom: 16px; }}
  button {{ font-size: 14px; padding: 6px 14px; cursor: pointer; }}
  @media print {{ .toolbar {{ display: none; }} body {{ margin: 0; }} }}
  @page {{ size: A4; margin: 14mm; }}
</style>
</head>
<body>
<div class="toolbar"><button onclick="window.print()">Drucken / Print</button></div>

<h1>Crewliste / Crew List</h1>
<p class="sub">Angaben zum Schiff und zur Besatzung /
   Details of vessel and crew</p>

<div class="boat">
{boat_rows}
</div>

<table>
  <thead><tr>{head}</tr></thead>
  <tbody>
{body}
  </tbody>
</table>

<div class="place">Ort, Datum / Place, date:
   <b>{place_date}</b></div>

<div class="sign">
  <div>Unterschrift Skipper / Signature of skipper</div>
  <div>Stempel / Stamp</div>
</div>
</body>
</html>
"""
