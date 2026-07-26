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


@unittest.skipUnless(_HAS_TK, "kein Tk-Display verfügbar")
class SourcesDialogTest(unittest.TestCase):
    def test_priority_up_and_down_to_off(self):
        # Priorität hoch-/runterstufen; 0 = aus (Quelle bleibt gespeichert).
        from saillog.gui import _SourcesDialog, _source_priority
        dialog = _SourcesDialog(
            _root, [{"host": "192.168.4.1", "port": 2000, "protocol": "tcp"}]
        )
        self.addCleanup(dialog.top.destroy)
        # Fehlende Priorität gilt als 1 (Abwärtskompatibilität).
        self.assertEqual(_source_priority(dialog._defs[0]), 1)
        dialog._listbox.selection_set(0)
        dialog._on_prio(+1)
        self.assertEqual(dialog._defs[0]["priority"], 2)
        dialog._listbox.selection_set(0)
        dialog._on_prio(-1)
        dialog._listbox.selection_set(0)
        dialog._on_prio(-1)
        self.assertEqual(dialog._defs[0]["priority"], 0)     # 0 = aus
        # Nicht unter 0 fallen.
        dialog._listbox.selection_set(0)
        dialog._on_prio(-1)
        self.assertEqual(dialog._defs[0]["priority"], 0)

    def test_added_source_has_priority_one(self):
        from saillog.gui import _SourcesDialog
        dialog = _SourcesDialog(_root, [])
        self.addCleanup(dialog.top.destroy)
        dialog._host.set("10.0.0.1")
        dialog._port.set("10110")
        dialog._on_add()
        self.assertEqual(len(dialog._defs), 1)
        self.assertEqual(dialog._defs[0]["priority"], 1)

    def test_legacy_enabled_flag_maps_to_priority(self):
        # Alte Konfiguration mit enabled=False -> Priorität 0 (aus).
        from saillog.gui import _source_priority
        self.assertEqual(_source_priority({"enabled": True}), 1)
        self.assertEqual(_source_priority({"enabled": False}), 0)
        self.assertEqual(_source_priority({}), 1)
        self.assertEqual(_source_priority({"priority": 5}), 5)

    def test_off_source_survives_ok(self):
        # Ausgeschaltete Quelle (Prio 0) bleibt in der Liste.
        from saillog.gui import _SourcesDialog
        dialog = _SourcesDialog(
            _root,
            [
                {"host": "a", "port": 1, "protocol": "tcp", "priority": 3},
                {"host": "b", "port": 2, "protocol": "tcp", "priority": 0},
            ],
        )
        self.addCleanup(dialog.top.destroy)
        dialog._on_ok()
        self.assertEqual(len(dialog.result), 2)
        self.assertEqual(dialog.result[1]["priority"], 0)


@unittest.skipUnless(_HAS_TK, "kein Tk-Display verfügbar")
class BackupOnCloseTest(unittest.TestCase):
    """Backup-Abfrage beim Beenden (Vorgabe „Ja", Enter genügt)."""

    def _fake_app(self, folder="", auto=False):
        from types import SimpleNamespace
        from saillog.config import Config
        calls = []
        cfg = Config(backup_folder=folder, backup_on_close=auto)
        app = SimpleNamespace(_config=cfg, _make_backup=lambda f: calls.append(f))
        return app, calls

    def test_prompt_defaults_to_yes_and_backs_up(self):
        from saillog import gui
        app, calls = self._fake_app()
        captured = {}

        def fake_ask(title, message, **kw):
            captured.update(kw)
            return True                       # Nutzer bestätigt (Enter)

        orig = gui.messagebox.askyesno
        gui.messagebox.askyesno = fake_ask
        try:
            gui.Application._backup_on_close(app)
        finally:
            gui.messagebox.askyesno = orig
        self.assertEqual(len(calls), 1)                       # Backup ausgeführt
        self.assertEqual(captured.get("default"), gui.messagebox.YES)  # Vorgabe „Ja"

    def test_prompt_no_skips_backup(self):
        from saillog import gui
        app, calls = self._fake_app()
        orig = gui.messagebox.askyesno
        gui.messagebox.askyesno = lambda *a, **k: False
        try:
            gui.Application._backup_on_close(app)
        finally:
            gui.messagebox.askyesno = orig
        self.assertEqual(calls, [])                           # kein Backup

    def test_auto_backup_stays_silent(self):
        from saillog import gui
        app, calls = self._fake_app(folder="/tmp/saillog-test-x", auto=True)

        def boom(*a, **k):
            raise AssertionError("Auto-Backup darf nicht nachfragen")

        orig = gui.messagebox.askyesno
        gui.messagebox.askyesno = boom
        try:
            gui.Application._backup_on_close(app)
        finally:
            gui.messagebox.askyesno = orig
        self.assertEqual(calls, ["/tmp/saillog-test-x"])      # still gesichert


if __name__ == "__main__":
    unittest.main()
