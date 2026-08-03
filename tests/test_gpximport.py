"""Tests für den GPX-Track-Import (z.B. Orca-Tageslogs)."""

import os
import tempfile
import unittest

from saillog import gpximport
from saillog.storage import LogbookStore, Trip


# Kleiner GPX-Track mit Default-Namespace (wie Orca) und drei Punkten.
SAMPLE_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx creator="Orca App" version="1.1"
     xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><time>2026-07-23T08:00:00Z</time></metadata>
  <trk>
    <name>Logbuch-MASARASI-23 Jul 2026</name>
    <trkseg>
      <trkpt lat="43.10000" lon="16.00000"><time>2026-07-23T08:00:00.500Z</time></trkpt>
      <trkpt lat="43.11000" lon="16.00000"><time>2026-07-23T08:06:00Z</time></trkpt>
      <trkpt lat="43.12000" lon="16.00000"><time>2026-07-23T08:12:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class ParseTest(unittest.TestCase):
    def test_name_and_points(self):
        tr = gpximport.parse_gpx(SAMPLE_GPX)
        self.assertEqual(tr.name, "Logbuch-MASARASI-23 Jul 2026")
        self.assertEqual(len(tr.points), 3)

    def test_timestamp_normalised_utc(self):
        tr = gpximport.parse_gpx(SAMPLE_GPX)
        # Millisekunden werden auf Sekunden gekürzt, 'Z' bleibt.
        self.assertEqual(tr.points[0][0], "2026-07-23T08:00:00Z")
        self.assertEqual(tr.points[0][1], 43.1)
        self.assertEqual(tr.points[0][2], 16.0)

    def test_invalid_points_skipped(self):
        gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
               '<trkpt lat="43.0" lon="16.0"><time>2026-07-23T08:00:00Z</time></trkpt>'
               '<trkpt lat="foo" lon="16.0"/>'      # ungültig -> übersprungen
               '</trkseg></trk></gpx>')
        tr = gpximport.parse_gpx(gpx)
        self.assertEqual(len(tr.points), 1)

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            gpximport.import_gpx(_Store(), "kein xml <<<", trip_id=None)


class MotionTest(unittest.TestCase):
    def test_sog_cog_computed(self):
        tr = gpximport.parse_gpx(SAMPLE_GPX)
        entries = gpximport.build_entries(tr, trip_id=None, source="x")
        # 0.01° Breite ≈ 0.6 NM in 6 min -> ~6 kn, Kurs Nord (0°)
        self.assertAlmostEqual(entries[0].cog_deg, 0.0, places=1)
        self.assertAlmostEqual(entries[0].sog_kn, 6.0, delta=0.2)

    def test_last_point_carries_motion(self):
        tr = gpximport.parse_gpx(SAMPLE_GPX)
        entries = gpximport.build_entries(tr, trip_id=None, source="x")
        self.assertIsNotNone(entries[-1].cog_deg)   # letzter erbt Bewegung
        self.assertEqual(entries[-1].entry_type, "track")

    def test_motion_off(self):
        tr = gpximport.parse_gpx(SAMPLE_GPX)
        entries = gpximport.build_entries(tr, trip_id=None, source="x", motion=False)
        self.assertTrue(all(e.sog_kn is None and e.cog_deg is None for e in entries))


def _Store():
    """Frischer LogbookStore auf einer temporären Datei."""
    path = tempfile.mktemp(suffix=".sqlite3")
    return LogbookStore(path)


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        self.store = LogbookStore(self.path)
        self.trip_id = self.store.add_trip(Trip(name="Test"))

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_import_creates_hidden_track_points(self):
        summary = gpximport.import_gpx(
            self.store, SAMPLE_GPX, trip_id=self.trip_id, source="tag1.gpx")
        self.assertEqual(summary["imported"], 3)
        self.assertEqual(summary["points"], 3)
        # In der Logbuch-Liste unsichtbar, auf der Karte (include_track) sichtbar.
        self.assertEqual(len(self.store.all()), 0)
        self.assertEqual(len(self.store.all(include_track=True)), 3)

    def test_points_attached_to_trip(self):
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag1.gpx")
        pts = self.store.all(include_track=True, trip_id=self.trip_id)
        self.assertEqual(len(pts), 3)
        self.assertTrue(all(e.entry_type == "track" for e in pts))
        self.assertTrue(all(e.logevent == "GPX" for e in pts))

    def test_summary_fields(self):
        s = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                 source="tag1.gpx")
        self.assertEqual(s["first"], "2026-07-23T08:00:00Z")
        self.assertEqual(s["last"], "2026-07-23T08:12:00Z")
        self.assertAlmostEqual(s["distance_nm"], 1.2, delta=0.1)

    def test_reimport_replaces_same_source(self):
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag1.gpx")
        s2 = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                  source="tag1.gpx")
        self.assertEqual(s2["replaced"], 3)
        self.assertEqual(len(self.store.all(include_track=True)), 3)  # keine Dubletten

    def test_different_sources_coexist(self):
        # gap_only=False: zwei Quellen mit gleichen Zeiten dürfen koexistieren
        # (die Idempotenz greift nur pro Quelle). Mit gap_only würde die zweite
        # als „schon abgedeckt" übersprungen — das prüft GapFillTest separat.
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag1.gpx", gap_only=False)
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag2.gpx", gap_only=False)
        self.assertEqual(len(self.store.all(include_track=True)), 6)

    def test_own_track_points_untouched_by_reimport(self):
        # Ein eigener (live) Track-Punkt ohne GPX-Marker darf beim GPX-Import
        # nicht gelöscht werden.
        from saillog.storage import LogEntry
        self.store.add(LogEntry(timestamp="2026-07-23T09:00:00Z",
                                entry_type="track", trip_id=self.trip_id,
                                lat=43.2, lon=16.0, logevent="Track"))
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag1.gpx")
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="tag1.gpx")
        # 3 GPX + 1 eigener = 4 (der eigene überlebt den Re-Import)
        self.assertEqual(len(self.store.all(include_track=True)), 4)

    def test_empty_gpx_raises(self):
        empty = '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk></trk></gpx>'
        with self.assertRaises(ValueError):
            gpximport.import_gpx(self.store, empty, trip_id=self.trip_id)


class GapFillTest(unittest.TestCase):
    """„Nur Lücken füllen": abgedeckte Zeiträume nicht doppelt zeichnen."""

    def setUp(self):
        self.path = tempfile.mktemp(suffix=".sqlite3")
        self.store = LogbookStore(self.path)
        self.trip_id = self.store.add_trip(Trip(name="Test"))

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def _own_track(self, iso):
        from saillog.storage import LogEntry
        self.store.add(LogEntry(timestamp=iso, entry_type="track",
                                trip_id=self.trip_id, lat=43.0, lon=16.0,
                                logevent="Track"))

    def test_gap_only_default_true(self):
        # Ohne eigene Spur wird nichts übersprungen (nichts abgedeckt).
        s = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                 source="a.gpx")
        self.assertEqual(s["skipped"], 0)
        self.assertEqual(s["imported"], 3)

    def test_covered_points_skipped(self):
        # Eigener Track-Punkt bei 08:06:00 deckt den ersten GPX-Punkt
        # (08:06:00) ab -> dieser wird übersprungen, die anderen bleiben.
        self._own_track("2026-07-23T08:06:00Z")
        s = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                 source="a.gpx", near_seconds=90)
        self.assertEqual(s["skipped"], 1)
        self.assertEqual(s["imported"], 2)

    def test_gap_only_off_imports_all(self):
        self._own_track("2026-07-23T08:06:00Z")
        s = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                 source="a.gpx", gap_only=False)
        self.assertEqual(s["skipped"], 0)
        self.assertEqual(s["imported"], 3)

    def test_own_track_not_counted_against_reimport(self):
        # Beim Re-Import derselben Quelle dürfen deren EIGENE (gelöschte) Punkte
        # nicht als „Abdeckung" zählen — sonst würde alles übersprungen.
        gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                             source="a.gpx")
        s2 = gpximport.import_gpx(self.store, SAMPLE_GPX, trip_id=self.trip_id,
                                  source="a.gpx")
        self.assertEqual(s2["imported"], 3)   # ersetzt, nicht übersprungen
        self.assertEqual(s2["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
