"""Tests für die HTML-Berichte aus den Logdaten."""

import os
import tempfile
import unittest

from triplog import reports
from triplog.storage import LogbookStore, LogEntry, Ship, Trip, Voyage


class _Cfg:
    def __init__(self, ship_id, db_path):
        self.active_ship_id = ship_id
        self.db_path = db_path


class FormatTest(unittest.TestCase):
    def test_latlon_dm(self):
        s = reports.latlon_dm(42.9706, 16.6680)
        self.assertIn("42°", s)
        self.assertIn("N", s)
        self.assertIn("016°", s)
        self.assertIn("E", s)
        self.assertIn(",", s)                      # Dezimalminuten mit Komma

    def test_latlon_dm_south_west(self):
        s = reports.latlon_dm(-33.9, -18.4)
        self.assertIn("S", s)
        self.assertIn("W", s)

    def test_compass(self):
        self.assertEqual(reports.compass(0), "N")
        self.assertEqual(reports.compass(90), "E")
        self.assertEqual(reports.compass(180), "S")
        self.assertEqual(reports.compass(270), "W")
        self.assertEqual(reports.compass(None), "")

    def test_wind_str(self):
        self.assertEqual(reports.wind_str(12, 90), "12 kn, E")
        self.assertEqual(reports.wind_str(None, 90), "---")

    def test_leg_stats_splits_motor_and_sail(self):
        e1 = LogEntry(timestamp="t1", lat=43.0, lon=16.0, engine_on=1)
        e2 = LogEntry(timestamp="t2", lat=43.1, lon=16.0, engine_on=1)   # Motor→Motor
        e3 = LogEntry(timestamp="t3", lat=43.2, lon=16.0, engine_on=0)   # Übergang
        e4 = LogEntry(timestamp="t4", lat=43.3, lon=16.0, engine_on=0)   # Segeln→Segeln
        stats = reports.leg_stats([e1, e2, e3, e4])
        self.assertGreater(stats["total"], 0)
        self.assertGreater(stats["motor"], 0)     # e1→e2 (+ Übergang e2→e3)
        self.assertGreater(stats["sailed"], 0)    # e3→e4
        self.assertAlmostEqual(stats["total"], stats["motor"] + stats["sailed"], places=6)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = os.path.join(self.tmp.name, "l.sqlite3")
        self.store = LogbookStore(db)
        self.ship = Ship(name="SY MASARASI", ship_type="Dufour 385", length_m=12.0)
        self.store.add_ship(self.ship)
        self.cfg = _Cfg(self.ship.id, db)
        self.trip = Trip(name="SY MASARASI", status="closed",
                         start_location="Vella Luka", end_location="Uvala Gradina",
                         start_dz="2024-07-25T06:24:00Z", end_dz="2024-07-25T17:03:00Z")
        self.store.add_trip(self.trip)
        e1 = LogEntry.from_snapshot(
            timestamp="2024-07-25T06:24:00Z", entry_type="manual",
            measurements={"lat": 42.97, "lon": 16.66, "sog_kn": 6.3, "cog_deg": 216,
                          "tws_kn": 10, "twd_deg": 20, "depth_m": 1.5,
                          "baro_mbar": 1016, "air_temp_c": 24},
            note="Ablegen", logevent="Ablegen", cloud_cover="wolkenlos",
            trip_id=self.trip.id)
        e1.engine_on = 1
        self.store.add(e1)
        e2 = LogEntry.from_snapshot(
            timestamp="2024-07-25T07:11:00Z", entry_type="auto",
            measurements={"lat": 42.91, "lon": 16.65, "sog_kn": 6.2, "tws_kn": 14},
            logevent="Intervall", trip_id=self.trip.id)
        e2.engine_on = 0
        self.store.add(e2)
        self.e1 = e1
        self.store.add_entry_image(e1.id, b"\xff\xd8\xffFAKEJPEG", "image/jpeg")

    def test_trip_report_without_images(self):
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0,
                                        with_images=False)
        self.assertIn("Törnbericht", html)
        self.assertIn("SY MASARASI", html)
        self.assertIn("Technische Daten", html)
        self.assertIn("Ablegen", html)
        self.assertIn("42°", html)                 # Position im DM-Format
        self.assertIn("Zusammenfassung", html)
        self.assertNotIn("data:image", html)        # ohne Bilder

    def test_trip_report_with_images_embeds_data_uri(self):
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0,
                                        with_images=True)
        self.assertIn("Etappenbericht", html)
        self.assertIn("data:image/jpeg;base64,", html)

    def test_voyage_log(self):
        html = reports.voyage_log_html(self.store, self.cfg,
                                       self.store.all_trips(newest_first=False),
                                       0.0, "Fahrtenbuch")
        self.assertIn("Fahrtenbuch", html)
        self.assertIn("Fahrtenübersicht", html)
        self.assertIn("Vella Luka", html)
        self.assertIn("Uvala Gradina", html)
        self.assertIn("NM", html)

    def test_report_is_wellformed_html(self):
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0)
        self.assertTrue(html.lstrip().startswith("<!doctype html>"))
        self.assertIn("</html>", html)

    def test_report_has_print_button(self):
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0)
        self.assertIn("window.print()", html)
        self.assertIn("noprint", html)

    def test_trip_report_with_map_embeds_leaflet(self):
        # ohne Karte: keine Leaflet-Assets
        plain = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0)
        self.assertNotIn("leaflet", plain)
        # mit Karte: Leaflet + Kartendiv + Positionsdaten
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0,
                                        with_map=True)
        self.assertIn("leaflet.js", html)
        self.assertIn('id="map"', html)
        self.assertIn("L.polyline", html)
        self.assertIn("L.circleMarker", html)

    def test_map_block_filters_entry_types(self):
        entries = self.store.all(newest_first=False, trip_id=self.trip.id)
        # Marker stecken als Daten im eingebetteten JSON (ein "fill" je Marker);
        # die Route zieht sich über alle Positionspunkte.
        # e1 = manual, e2 = auto -> nur manuelle markieren
        only_manual = reports.map_block(entries, 0.0, {"manual"})
        self.assertEqual(only_manual.count('"fill"'), 1)
        # beide Typen -> zwei Marker
        both = reports.map_block(entries, 0.0, {"manual", "auto"})
        self.assertEqual(both.count('"fill"'), 2)
        # None = alle Typen
        allt = reports.map_block(entries, 0.0, None)
        self.assertEqual(allt.count('"fill"'), 2)

    def test_trip_report_filters_entry_types(self):
        # e1 = manual ("Ablegen"), e2 = auto ("Intervall")
        full = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0)
        self.assertIn("Ablegen", full)
        self.assertIn("Intervall", full)
        self.assertIn("2 Einträge", full)
        # nur manuelle Einträge im Bericht
        only_manual = reports.trip_report_html(
            self.store, self.cfg, self.trip, 0.0, entry_types={"manual"})
        self.assertIn("Ablegen", only_manual)
        self.assertNotIn("Intervall", only_manual)
        self.assertIn("1 von 2 Einträgen", only_manual)
        # Distanz-Zusammenfassung bleibt über ALLE Einträge berechnet
        self.assertIn("Zusammenfassung", only_manual)

    def test_map_block_without_positions(self):
        e = LogEntry(timestamp="2024-07-25T06:24:00Z", entry_type="manual")
        html = reports.map_block([e], 0.0, None)
        self.assertIn("Keine Positionsdaten", html)

    def test_map_block_uses_dense_track_for_line(self):
        entries = self.store.all(newest_first=False, trip_id=self.trip.id)
        dense = [[42.97, 16.66], [42.95, 16.66], [42.93, 16.65], [42.91, 16.65]]
        html = reports.map_block(entries, 0.0, {"manual"}, "Karte", track=dense)
        # Linie kommt aus dem dichten Track (4 Punkte), Marker nur manuell (1)
        self.assertIn("42.93", html)          # Track-Zwischenpunkt (nicht in entries)
        self.assertEqual(html.count('"fill"'), 1)

    def test_trip_report_map_includes_track_points(self):
        # reiner Track-Punkt an die Etappe hängen
        tp = LogEntry.from_snapshot(
            timestamp="2024-07-25T06:40:00Z", entry_type="track",
            measurements={"lat": 42.95, "lon": 16.655}, trip_id=self.trip.id)
        self.store.add(tp)
        html = reports.trip_report_html(self.store, self.cfg, self.trip, 0.0,
                                        with_map=True)
        self.assertIn("16.655", html)         # Track-Punkt steckt in der Linie
        # aber NICHT in der Eintragsliste: weiterhin 2 Log-Einträge
        self.assertIn("2 Einträge", html)


    def test_voyage_report_multi_leg(self):
        # zweite Etappe + beide einem Törn zuordnen
        t2 = Trip(name="SY MASARASI", status="closed", start_location="Uvala Gradina",
                  end_location="Vela Luka", start_dz="2024-07-26T08:00:00Z",
                  end_dz="2024-07-26T14:00:00Z")
        self.store.add_trip(t2)
        e = LogEntry.from_snapshot(
            timestamp="2024-07-26T08:00:00Z", entry_type="manual",
            measurements={"lat": 42.9, "lon": 16.6, "tws_kn": 8}, logevent="Ablegen",
            trip_id=t2.id)
        self.store.add(e)
        v = Voyage(name="Schwerwetter Ausbildung", revier="Adria")
        self.store.add_voyage(v)
        self.store.set_trip_voyage(self.trip.id, v.id)
        self.store.set_trip_voyage(t2.id, v.id)
        trips = self.store.trips_for_voyage(v.id)
        self.assertEqual(len(trips), 2)
        html = reports.voyage_report_html(self.store, self.cfg, v, trips, 0.0)
        self.assertIn("Törnbericht", html)
        self.assertIn("Schwerwetter Ausbildung", html)
        self.assertIn("Adria", html)                    # Revier
        self.assertIn("Etappenübersicht", html)
        self.assertEqual(html.count("Etappe: "), 2)     # zwei Etappen-Detailteile
        # mit Bildern -> Data-URI (Bild hängt an self.trip)
        html_img = reports.voyage_report_html(self.store, self.cfg, v, trips, 0.0,
                                              with_images=True)
        self.assertIn("data:image/jpeg;base64,", html_img)


if __name__ == "__main__":
    unittest.main()
