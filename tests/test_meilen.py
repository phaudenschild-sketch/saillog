"""Tests für Seemeilen-Nachweis, Nachtmeilen (Sonnenstand) und PDF-Kartenbild."""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from saillog import reports, sun
from saillog.storage import LogbookStore, LogEntry, Ship, Trip


class SunTest(unittest.TestCase):
    def test_day_and_night(self):
        # Berlin: Sommer-Mittag ist Tag, Mitternacht ist Nacht
        noon = datetime(2024, 6, 21, 12, tzinfo=timezone.utc)
        midnight = datetime(2024, 6, 21, 0, tzinfo=timezone.utc)
        self.assertGreater(sun.altitude_deg(52.5, 13.4, noon), 40)
        self.assertFalse(sun.is_night(52.5, 13.4, noon))
        self.assertTrue(sun.is_night(52.5, 13.4, midnight))

    def test_is_night_missing_data(self):
        self.assertFalse(sun.is_night(None, 10.0, datetime.now(timezone.utc)))


class NightMilesTest(unittest.TestCase):
    def test_night_segments_counted(self):
        # Fahrt über Mitternacht (UTC), Adria -> ein Teil Nachtmeilen
        ents = []
        for h in (22, 23):
            ents.append(LogEntry.from_snapshot(
                f"2024-07-10T{h:02d}:00:00Z", "auto",
                {"lat": 43.0 + (h - 22) * 0.1, "lon": 16.0}))
        for h in (0, 1, 2):
            ents.append(LogEntry.from_snapshot(
                f"2024-07-11T{h:02d}:00:00Z", "auto",
                {"lat": 43.2 + h * 0.1, "lon": 16.0}))
        night = reports.leg_night_nm(ents)
        total = reports.leg_stats(ents)["total"]
        self.assertGreater(night, 0)
        self.assertLessEqual(night, total + 1e-6)

    def test_daytime_has_no_night_miles(self):
        ents = [LogEntry.from_snapshot(f"2024-07-01T{h:02d}:00:00Z", "auto",
                                       {"lat": 43.0 + h * 0.05, "lon": 16.0})
                for h in (9, 10, 11, 12)]
        self.assertEqual(round(reports.leg_night_nm(ents), 3), 0.0)


class MeilennachweisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = os.path.join(self.tmp.name, "l.sqlite3")
        self.store = LogbookStore(db)
        self.store.add_ship(Ship(name="SY MASARASI", ship_type="Dufour 385",
                                 length_m=11.6))

        class C:
            active_ship_id = None
            db_path = db
        self.cfg = C()
        self.trip = Trip(name="T1", status="closed", start_location="Kiel",
                         end_location="Fehmarn", start_dz="2024-07-01T05:00:00Z",
                         end_dz="2024-07-01T18:00:00Z")
        self.store.add_trip(self.trip)
        for h in range(6, 16):
            self.store.add(LogEntry.from_snapshot(
                f"2024-07-01T{h:02d}:00:00Z", "auto",
                {"lat": 54.0 + (h - 6) * 0.1, "lon": 10.0}, trip_id=self.trip.id))

    def test_report_structure(self):
        html = reports.meilennachweis_html(
            self.store, self.cfg, [self.trip], 0.0,
            applicant="Peter Haudenschild", role="Skipper", with_night=True)
        for key in ("Seemeilen-Nachweis", "Peter Haudenschild", "SY MASARASI",
                    "SKS", "SSS", "SHS", "FB4", "Hochseeschein",
                    "davon Nacht", "Unterschrift Skipper", "Prüfungsträger"):
            self.assertIn(key, html)
        self.assertTrue(html.lstrip().startswith("<!doctype html>"))

    def test_totals_and_status(self):
        html = reports.meilennachweis_html(
            self.store, self.cfg, [self.trip], 0.0, applicant="X")
        total = reports.leg_stats(
            self.store.all(trip_id=self.trip.id))["total"]
        self.assertGreater(total, 50)                 # ~54 sm
        self.assertIn("noch", html)                   # 300 sm für SKS nicht erreicht

    def test_without_night_hides_column(self):
        html = reports.meilennachweis_html(
            self.store, self.cfg, [self.trip], 0.0, with_night=False)
        self.assertNotIn("davon Nacht", html)


class MapImageTest(unittest.TestCase):
    def test_map_page_html_is_leaflet(self):
        html = reports.map_page_html([[43.0, 16.0], [43.1, 16.1]], [])
        self.assertIn("leaflet.js", html)
        self.assertIn("L.polyline", html)
        self.assertIn('id="map"', html)

    def test_map_renderer_produces_image(self):
        e = LogEntry.from_snapshot("2024-07-01T06:00:00Z", "manual",
                                   {"lat": 43.0, "lon": 16.0})
        rendered = reports.map_block(
            [e], 0.0, None, "Karte", track=[[43.0, 16.0], [43.1, 16.1]],
            static=True, map_renderer=lambda t, m: "data:image/png;base64,AAAA")
        self.assertIn('class="trackmap" src="data:image/png', rendered)
        self.assertNotIn("leaflet", rendered)

    def test_renderer_none_falls_back_to_svg(self):
        e = LogEntry.from_snapshot("2024-07-01T06:00:00Z", "manual",
                                   {"lat": 43.0, "lon": 16.0})
        rendered = reports.map_block(
            [e], 0.0, None, "Karte", track=[[43.0, 16.0], [43.1, 16.1]],
            static=True, map_renderer=lambda t, m: None)
        self.assertIn("<svg", rendered)               # SVG-Fallback


if __name__ == "__main__":
    unittest.main()
