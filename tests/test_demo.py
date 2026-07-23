"""Tests für die Demo-Daten (Beispiel-Törn beim ersten Start)."""

import os
import tempfile
import unittest

from saillog import demo
from saillog.storage import LogbookStore


class DemoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "logbook.sqlite3")
        self.store = LogbookStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_creates_trip_and_entries(self):
        n = demo.seed_demo_data(self.store)
        self.assertGreaterEqual(n, 6)
        trips = self.store.all_trips()
        self.assertEqual(len(trips), 1)
        self.assertIn("Demo", trips[0].name)
        self.assertEqual(trips[0].status, "closed")
        entries = self.store.all(trip_id=trips[0].id)
        self.assertEqual(len(entries), n)
        # Alle Einträge hängen am Demo-Törn und haben eine Position
        self.assertTrue(all(e.trip_id == trips[0].id for e in entries))
        self.assertTrue(all(e.lat is not None and e.lon is not None for e in entries))
        # gespeicherte Auswahlwerte sind kanonisch deutsch
        clouds = {e.cloud_cover for e in entries}
        self.assertTrue(clouds <= {"heiter", "wolkig", "wolkenlos"})

    def test_seed_is_additive_only(self):
        # Zweiter Aufruf legt einen weiteren Törn an — die Freshness-Prüfung
        # (Datei existiert schon) passiert im Aufrufer, nicht hier.
        demo.seed_demo_data(self.store)
        demo.seed_demo_data(self.store)
        self.assertEqual(len(self.store.all_trips()), 2)


if __name__ == "__main__":
    unittest.main()
