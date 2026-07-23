"""Tests für die flexible Schiffsausrüstung (Parameter-DB + pro Schiff)."""

import tempfile
import unittest
from pathlib import Path

from saillog.storage import (
    LogbookStore, Ship, EquipmentParam, ShipEquipment, EQUIP_CATEGORIES,
)


class EquipmentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = LogbookStore(str(Path(self.dir.name) / "t.sqlite3"))

    def tearDown(self):
        self.dir.cleanup()

    def test_seeded_defaults(self):
        mains = self.store.equipment_params("mainsail")
        heads = self.store.equipment_params("headsail")
        self.assertIn("Großsegel", [p.name for p in mains])
        self.assertIn("Genua", [p.name for p in heads])
        # Reff-Typ im attrs erhalten
        genua = next(p for p in heads if p.name == "Genua")
        self.assertEqual(genua.attrs.get("reef"), "Rollreff")

    def test_add_motor_param_with_attrs(self):
        p = EquipmentParam(category="motor", name="Volvo Penta 40PS",
                           attrs={"oil_max": 2.0, "oil_step": 0.1,
                                  "rpm_max": 6000, "rpm_step": 100})
        pid = self.store.add_equipment_param(p)
        got = next(x for x in self.store.equipment_params("motor") if x.id == pid)
        self.assertEqual(got.name, "Volvo Penta 40PS")
        self.assertEqual(got.attrs["rpm_max"], 6000)

    def test_assign_and_replace_ship_equipment(self):
        ship = Ship(name="BEISPIEL")
        sid = self.store.add_ship(ship)
        heads = self.store.equipment_params("headsail")
        genua = next(p for p in heads if p.name == "Genua")
        self.store.set_ship_equipment(sid, [
            ShipEquipment(ship_id=sid, category="headsail", name="Genua",
                          attrs=genua.attrs, param_id=genua.id),
            ShipEquipment(ship_id=sid, category="headsail", name="Gennaker",
                          attrs={"reef": "kein Reff"}),
        ], category="headsail")
        got = self.store.ship_equipment(sid, "headsail")
        self.assertEqual([e.name for e in got], ["Genua", "Gennaker"])
        self.assertEqual(got[0].param_id, genua.id)
        # Kategorie-Ersatz betrifft andere Kategorien nicht
        self.store.set_ship_equipment(sid, [
            ShipEquipment(ship_id=sid, category="mainsail", name="Großsegel")],
            category="mainsail")
        self.assertEqual(len(self.store.ship_equipment(sid, "headsail")), 2)
        self.assertEqual(len(self.store.ship_equipment(sid, "mainsail")), 1)

    def test_delete_ship_removes_equipment(self):
        sid = self.store.add_ship(Ship(name="X"))
        self.store.set_ship_equipment(sid, [
            ShipEquipment(ship_id=sid, category="motor", name="Yanmar 40 PS")])
        self.assertEqual(len(self.store.ship_equipment(sid)), 1)
        self.store.delete_ship(sid)
        self.assertEqual(len(self.store.ship_equipment(sid)), 0)

    def test_categories(self):
        self.assertEqual(EQUIP_CATEGORIES["motor"], "Motor")


if __name__ == "__main__":
    unittest.main()
