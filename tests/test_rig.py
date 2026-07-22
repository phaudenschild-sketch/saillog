"""Tests für die Ableitung Ausrüstung -> Eingabe-Elemente (rig.py)."""

import unittest

from saillog import rig
from saillog.storage import ShipEquipment


class RigTest(unittest.TestCase):
    def test_control_for_reef(self):
        self.assertEqual(rig.control_for_reef("kein Reff"), rig.CONTROL_FIXED)
        self.assertEqual(rig.control_for_reef("Rollreff"), rig.CONTROL_ROLLER)
        self.assertEqual(rig.control_for_reef("Bindereff"), rig.CONTROL_SLAB)
        self.assertEqual(rig.control_for_reef(None), rig.CONTROL_FIXED)

    def test_sailboat_controls(self):
        items = [
            ShipEquipment(category="mainsail", name="Großsegel", attrs={"reef": "Bindereff"}),
            ShipEquipment(category="headsail", name="Genua", attrs={"reef": "Rollreff"}),
            ShipEquipment(category="headsail", name="Sturmfock", attrs={"reef": "kein Reff"}),
        ]
        spec = rig.rig_from_equipment(items)
        self.assertTrue(spec.has_sails)
        self.assertFalse(spec.is_motorboat)
        ctrls = {s.name: s.control for s in spec.sails}
        self.assertEqual(ctrls["Großsegel"], rig.CONTROL_SLAB)
        self.assertEqual(ctrls["Genua"], rig.CONTROL_ROLLER)
        self.assertEqual(ctrls["Sturmfock"], rig.CONTROL_FIXED)
        # Bindereff-Stufen
        gs = next(s for s in spec.sails if s.name == "Großsegel")
        self.assertEqual(gs.states(), ["nicht gesetzt", "gesetzt", "Reff 1", "Reff 2", "Reff 3"])

    def test_motorboat(self):
        items = [
            ShipEquipment(category="motor", name="Volvo Penta 40PS"),
            ShipEquipment(category="motor", name="Yanmar 40 PS"),
        ]
        spec = rig.rig_from_equipment(items)
        self.assertTrue(spec.is_motorboat)
        self.assertFalse(spec.has_sails)
        self.assertEqual(spec.motors, ["Volvo Penta 40PS", "Yanmar 40 PS"])

    def test_unconfigured(self):
        spec = rig.rig_from_equipment([])
        self.assertFalse(spec.configured)
        self.assertFalse(spec.is_motorboat)

    def test_summarize(self):
        spec = rig.rig_from_equipment([
            ShipEquipment(category="mainsail", name="Groß", attrs={"reef": "Bindereff"}),
            ShipEquipment(category="headsail", name="Genua", attrs={"reef": "Rollreff"}),
        ])
        s = rig.summarize({"Groß": "Reff 1", "Genua": 60}, spec)
        self.assertIn("Groß Reff 1", s)
        self.assertIn("Genua 60%", s)
        # nichts gesetzt (Segelschiff) -> „geborgen"
        self.assertEqual(rig.summarize({"Groß": "nicht gesetzt", "Genua": 0}, spec), "geborgen")
        # ohne Segel (Motorboot) -> leer
        self.assertEqual(rig.summarize({}, rig.rig_from_equipment([])), "")


if __name__ == "__main__":
    unittest.main()
