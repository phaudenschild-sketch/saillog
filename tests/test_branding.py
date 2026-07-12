"""Tests für die Marke (Logo/Copyright) und die Konfig-Migration ~/.masarasi -> ~/.triplog."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from triplog import branding, config, crewlist, reports
from triplog.storage import LogbookStore, LogEntry, Ship, Trip


class BrandingTest(unittest.TestCase):
    def test_logo_and_copyright(self):
        self.assertEqual(branding.APP_NAME, "TripLog")
        self.assertIn("Peter Haudenschild", branding.COPYRIGHT)
        html = branding.logo_html(48)
        self.assertIn("<svg", html)
        self.assertIn('height="48"', html)

    def test_report_carries_logo_and_copyright(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = os.path.join(tmp.name, "l.sqlite3")
        store = LogbookStore(db)
        store.add_ship(Ship(name="SY MASARASI"))
        trip = Trip(name="T", status="closed", start_location="A", end_location="B",
                    start_dz="2024-07-25T06:00:00Z", end_dz="2024-07-25T10:00:00Z")
        store.add_trip(trip)
        store.add(LogEntry.from_snapshot(
            timestamp="2024-07-25T06:00:00Z", entry_type="manual",
            measurements={"lat": 43.0, "lon": 16.0}, trip_id=trip.id, logevent="Start"))

        class C:
            active_ship_id = None
            db_path = db
        html = reports.trip_report_html(store, C(), trip, 0.0)
        self.assertIn("<svg", html)                 # Logo eingebettet
        self.assertIn("TripLog", html)              # Marke
        self.assertIn("Peter Haudenschild", html)   # Copyright im Fuß
        self.assertIn("SY MASARASI", html)          # Schiffsname bleibt erhalten

    def test_crewlist_carries_logo_and_copyright(self):
        html = crewlist.build_html({"ship_name": "SY MASARASI"}, [])
        self.assertIn("<svg", html)
        self.assertIn("TripLog", html)
        self.assertIn("Peter Haudenschild", html)
        self.assertIn("SY MASARASI", html)


class ConfigMigrationTest(unittest.TestCase):
    def test_app_dir_migrates_legacy_masarasi(self):
        with tempfile.TemporaryDirectory() as home:
            legacy = Path(home) / ".masarasi"
            legacy.mkdir()
            (legacy / "logbook.sqlite3").write_text("db")
            (legacy / "config.json").write_text("{}")
            with mock.patch.object(config.Path, "home", return_value=Path(home)):
                new = config._app_dir()
            self.assertEqual(new, Path(home) / ".triplog")
            self.assertTrue((new / "logbook.sqlite3").exists())
            self.assertFalse(legacy.exists())        # umgezogen, nicht dupliziert

    def test_load_rewrites_legacy_db_path(self):
        with tempfile.TemporaryDirectory() as home:
            newdir = Path(home) / ".triplog"
            newdir.mkdir()
            db = newdir / "logbook.sqlite3"
            db.write_text("db")
            cfgfile = newdir / "config.json"
            # config.json enthält noch den ALTEN Pfad
            cfgfile.write_text(json.dumps(
                {"db_path": str(Path(home) / ".masarasi" / "logbook.sqlite3")}))
            cfg = config.Config.load(cfgfile)
            self.assertEqual(cfg.db_path, str(db))   # auf neuen Ort umgebogen


if __name__ == "__main__":
    unittest.main()
