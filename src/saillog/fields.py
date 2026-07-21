"""Zentrale Definitionen der manuellen Logbuch-Felder und ihrer Auswahllisten.

An einer Stelle definiert, damit GUI, Speicher und Import dieselben Werte
verwenden.
"""

from __future__ import annotations

# --- Anlass (Logevent) ------------------------------------------------------

# Standard-Auswahl für den „Anlass" eines Eintrags — angelehnt an TripCon.
# In der App unter Extras → „Anlass-Liste…" frei anpassbar (config.logevents).
DEFAULT_LOGEVENTS = [
    "Routineeintrag", "Anlegen", "Ablegen", "Segel setzen", "Segel einholen",
    "Segel wechseln", "Reffen", "Ausreffen", "Im Hafen", "Wende", "Halse",
    "Distanz", "Dienstwechsel", "Mast legen", "Schleusen",
]


def logevents(configured=None):
    """Liefert die Anlass-Liste: konfigurierte Werte oder die Standardliste."""
    items = [str(x).strip() for x in (configured or []) if str(x).strip()]
    return items or list(DEFAULT_LOGEVENTS)


# --- Segelkonfiguration -----------------------------------------------------

# Großsegel-Zustand
MAINSAIL_OPTIONS = ["—", "Voll", "Reff 1", "Reff 2", "Geborgen"]

# Spinnaker: Ja/Nein (als 0/1 gespeichert)

# --- Wetter / Bedingungen ---------------------------------------------------

# Bewölkung: (Bezeichnung, Prozent-Hinweis, Richtwert-Prozent für Auswertung)
CLOUD_COVER = [
    ("—", "", None),
    ("wolkenlos", "0–10 %", 5),
    ("heiter", "10–50 %", 30),
    ("wolkig", "50–80 %", 65),
    ("stark bewölkt", "80–90 %", 85),
    ("völlig bedeckt", "100 %", 100),
]

# Niederschlag
PRECIPITATION = ["kein", "Nieselregen", "Regen", "Gewitter", "Hagel", "Schnee"]

# Sicht: (Bezeichnung, Reichweiten-Hinweis, Richtwert Nm)
VISIBILITY = [
    ("—", "", None),
    ("gut", "10 Nm", 10.0),
    ("mässig", "5 Nm", 5.0),
    ("schlecht", "2 Nm", 2.0),
    ("Nebel", "< 1 Nm", 0.5),
]


def _labels(options):
    return [o[0] for o in options]


CLOUD_COVER_LABELS = _labels(CLOUD_COVER)
VISIBILITY_LABELS = _labels(VISIBILITY)


def cloud_hint(label: str) -> str:
    for name, hint, _pct in CLOUD_COVER:
        if name == label:
            return hint
    return ""


def visibility_hint(label: str) -> str:
    for name, hint, _nm in VISIBILITY:
        if name == label:
            return hint
    return ""
