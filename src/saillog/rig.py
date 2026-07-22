"""Takelage/Antrieb eines Schiffs -> passende Eingabe-Elemente.

Aus der Ausrüstung eines Schiffs (``storage.ShipEquipment``) wird abgeleitet,
welche Segel es gibt und wie ihr Zustand erfasst wird:

* **Festsegel** (Reff „kein Reff"): gesetzt / nicht gesetzt
* **Rollsegel** (Reff „Rollreff"): 0–100 %
* **Bindereff**: nicht gesetzt / gesetzt / Reff 1 / Reff 2 / Reff 3

Motorboote haben keine Segel, aber ggf. mehrere Motoren. Reine Logik, damit
GUI **und** Handy-Seite dieselben Elemente aufbauen können.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from saillog.i18n import t

# Bedienelement-Typen
CONTROL_FIXED = "fixed"      # gesetzt / nicht gesetzt (Checkbox)
CONTROL_ROLLER = "roller"    # 0–100 % (Schieberegler)
CONTROL_SLAB = "slab"        # nicht gesetzt / gesetzt / Reff 1–3 (Auswahl)

# Zustände für Bindereff-Segel
SLAB_STATES = ["nicht gesetzt", "gesetzt", "Reff 1", "Reff 2", "Reff 3"]
FIXED_STATES = ["nicht gesetzt", "gesetzt"]


def control_for_reef(reef: Optional[str]) -> str:
    """Bedienelement-Typ aus der Reff-Art ableiten."""
    r = (reef or "").lower()
    if "roll" in r:
        return CONTROL_ROLLER
    if "binde" in r:
        return CONTROL_SLAB
    return CONTROL_FIXED          # „kein Reff" / unbekannt -> Festsegel


def default_state(control: str):
    """Sinnvoller Startwert je Bedienelement."""
    if control == CONTROL_ROLLER:
        return 0
    return "nicht gesetzt"


@dataclass
class SailControl:
    """Ein Segel des Schiffs samt passendem Bedienelement."""

    name: str
    category: str                 # 'mainsail' | 'headsail'
    control: str                  # CONTROL_FIXED | _ROLLER | _SLAB

    def states(self) -> List[str]:
        if self.control == CONTROL_SLAB:
            return list(SLAB_STATES)
        if self.control == CONTROL_FIXED:
            return list(FIXED_STATES)
        return []                 # Roller: numerisch, keine feste Liste


@dataclass
class RigSpec:
    """Ableitung aus der Schiffsausrüstung: Segel (mit Bedienelement) + Motoren."""

    sails: List[SailControl] = field(default_factory=list)
    motors: List[str] = field(default_factory=list)

    @property
    def has_sails(self) -> bool:
        return bool(self.sails)

    @property
    def is_motorboat(self) -> bool:
        return not self.sails and bool(self.motors)

    @property
    def configured(self) -> bool:
        """True, wenn die Ausrüstung überhaupt Antrieb definiert."""
        return bool(self.sails or self.motors)


def rig_from_equipment(items) -> RigSpec:
    """Baut die RigSpec aus einer Liste von ShipEquipment-Objekten/Dicts."""
    sails: List[SailControl] = []
    motors: List[str] = []
    for e in items or []:
        category = getattr(e, "category", None) if not isinstance(e, dict) else e.get("category")
        name = getattr(e, "name", None) if not isinstance(e, dict) else e.get("name")
        attrs = getattr(e, "attrs", None) if not isinstance(e, dict) else e.get("attrs")
        attrs = attrs or {}
        if category in ("mainsail", "headsail"):
            sails.append(SailControl(name=name or "Segel", category=category,
                                     control=control_for_reef(attrs.get("reef"))))
        elif category == "motor":
            motors.append(name or "Motor")
    return RigSpec(sails=sails, motors=motors)


def summarize(states: dict, spec: RigSpec) -> str:
    """Kurzfassung der gesetzten Segel/Reffs für Tabellen/Berichte."""
    parts: List[str] = []
    for sail in spec.sails:
        val = states.get(sail.name)
        if sail.control == CONTROL_ROLLER:
            try:
                pct = float(val)
            except (TypeError, ValueError):
                pct = 0
            if pct > 0:
                parts.append(f"{sail.name} {pct:g}%")
        else:
            if val and val != "nicht gesetzt":
                # Reff-Stufe („Reff 1" …) für die Anzeige übersetzen; der Segelname
                # ist ein Eigenname und bleibt unverändert.
                label = sail.name if val == "gesetzt" else f"{sail.name} {t(val)}"
                parts.append(label)
    if parts:
        return ", ".join(parts)
    # Segelschiff, aber nichts gesetzt -> „geborgen" (statt leerer Spalte)
    return t("geborgen") if spec.sails else ""
