"""Tests für den SQLite-Speicher und die Exporte."""

import os
import tempfile
import unittest

from triplog.storage import LogbookStore, LogEntry


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.sqlite3")
        self.store = LogbookStore(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _sample(self, ts, note=""):
        return LogEntry.from_snapshot(
            timestamp=ts,
            entry_type="auto",
            measurements={"lat": 47.5, "lon": 9.4, "sog_kn": 5.2, "depth_m": 12.0},
            note=note,
        )

    def test_add_and_count(self):
        self.assertEqual(self.store.count(), 0)
        self.store.add(self._sample("2026-07-05T09:00:00Z"))
        self.assertEqual(self.store.count(), 1)

    def test_from_snapshot_maps_measurements(self):
        entry = self._sample("2026-07-05T09:00:00Z")
        self.assertEqual(entry.lat, 47.5)
        self.assertEqual(entry.sog_kn, 5.2)
        self.assertEqual(entry.depth_m, 12.0)

    def test_ordering_newest_first(self):
        self.store.add(self._sample("2026-07-05T09:00:00Z", note="alt"))
        self.store.add(self._sample("2026-07-05T10:00:00Z", note="neu"))
        entries = self.store.all(newest_first=True)
        self.assertEqual(entries[0].note, "neu")
        self.assertEqual(entries[1].note, "alt")

    def test_delete(self):
        entry = self._sample("2026-07-05T09:00:00Z")
        entry_id = self.store.add(entry)
        self.store.delete(entry_id)
        self.assertEqual(self.store.count(), 0)

    def test_manual_entry_without_measurements(self):
        entry = LogEntry.from_snapshot(
            timestamp="2026-07-05T09:00:00Z",
            entry_type="manual",
            measurements={},
            note="Ankunft im Hafen",
            crew="Peter, Anna",
            location="Romanshorn",
        )
        self.store.add(entry)
        loaded = self.store.all()[0]
        self.assertEqual(loaded.entry_type, "manual")
        self.assertEqual(loaded.crew, "Peter, Anna")
        self.assertIsNone(loaded.lat)

    def test_export_csv(self):
        self.store.add(self._sample("2026-07-05T09:00:00Z"))
        self.store.add(self._sample("2026-07-05T10:00:00Z"))
        out = os.path.join(self.tmpdir.name, "out.csv")
        count = self.store.export_csv(out)
        self.assertEqual(count, 2)
        with open(out, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("timestamp", content)
        self.assertIn("47.5", content)

    def test_export_gpx(self):
        self.store.add(self._sample("2026-07-05T09:00:00Z"))
        # Eintrag ohne Position wird im GPX ignoriert
        self.store.add(
            LogEntry.from_snapshot("2026-07-05T09:05:00Z", "manual", {}, note="x")
        )
        out = os.path.join(self.tmpdir.name, "out.gpx")
        count = self.store.export_gpx(out)
        self.assertEqual(count, 1)
        with open(out, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("<trkpt", content)
        self.assertIn('lat="47.500000"', content)
        self.assertIn("</gpx>", content)


if __name__ == "__main__":
    unittest.main()
