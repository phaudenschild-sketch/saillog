"""Aufbau-Tests für GUI-Dialoge.

Diese Tests prüfen, dass die Dialoge vollständig aufgebaut werden (kein
Absturz mitten im Konstruktor, der das Fenster halb-fertig zurücklässt).
Sie überspringen sich selbst, wenn kein X-Display verfügbar ist — im
normalen Headless-Lauf also inaktiv, mit ``xvfb-run`` aktiv.
"""

import unittest

try:  # tkinter braucht ein Display; ohne wird der Test übersprungen
    import tkinter as tk
    _root = tk.Tk()
    _root.withdraw()
    _HAS_TK = True
except Exception:  # pragma: no cover - reine Umgebungserkennung
    _HAS_TK = False


@unittest.skipUnless(_HAS_TK, "kein Tk-Display verfügbar")
class NewEntryDialogTest(unittest.TestCase):
    def setUp(self):
        from saillog.storage import Trip
        self.Trip = Trip

    def test_builds_completely_with_default_logevents(self):
        # Regression: ohne übergebene logevents darf der Aufbau nicht an der
        # „Anlass"-Combobox abbrechen (fehlendes self._logevents).
        from saillog.gui import _NewEntryDialog
        dialog = _NewEntryDialog(
            _root, 0.0, [self.Trip(id=1, name="Sommer 2026")], 1,
            {"lat": 43.32, "lon": 16.37, "sog_kn": 4.6},
        )
        self.addCleanup(dialog.top.destroy)
        # Anlass ist vorbelegt und die Speichern-Logik erreichbar
        self.assertTrue(dialog._logevent.get())

    def test_uses_custom_logevents(self):
        from saillog.gui import _NewEntryDialog
        dialog = _NewEntryDialog(
            _root, 0.0, [], None, {}, logevents=["Ankern vor Insel", "Wende"],
        )
        self.addCleanup(dialog.top.destroy)
        self.assertEqual(dialog._logevent.get(), "Ankern vor Insel")


if __name__ == "__main__":
    unittest.main()
