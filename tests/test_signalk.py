"""Tests für die Signal-K-Anbindung (Mapping + HTTP-Polling-Quelle).

Der End-to-End-Test startet einen lokalen HTTP-Server, der einen
Signal-K-``vessels/self``-Baum liefert — so lässt sich die komplette Kette
(Abruf → Übersetzung → LiveData) **ohne echten Server/Boot** prüfen.
"""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from saillog import nmea, signalk
from saillog.livedata import LiveData


def _full_tree():
    """Ein repräsentativer Signal-K-Baum in SI-Einheiten."""
    return {
        "navigation": {
            "position": {"value": {"latitude": 43.51, "longitude": 16.44}},
            "speedOverGround": {"value": 3.086666},        # ~6 kn
            "courseOverGroundTrue": {"value": 1.5707963},  # 90°
            "speedThroughWater": {"value": 2.5722},        # ~5 kn
            "headingTrue": {"value": 3.1415926},           # 180°
            "headingMagnetic": {"value": 3.2288591},       # 185°
            "log": {"value": 185200.0},                    # 100 NM
            "attitude": {"value": {"roll": 0.1745329,      # 10°
                                    "pitch": -0.0523599}}, # -3°
        },
        "steering": {"rudderAngle": {"value": 0.0872665}},  # 5°
        "environment": {
            "depth": {"belowTransducer": {"value": 12.3}},
            "water": {"temperature": {"value": 290.15}},    # 17 °C
            "outside": {"temperature": {"value": 298.15},   # 25 °C
                        "pressure": {"value": 101300.0}},   # 1013 mbar
            "wind": {
                "speedApparent": {"value": 5.144},          # ~10 kn
                "angleApparent": {"value": -0.5235988},     # -30° -> 330°
                "speedTrue": {"value": 6.0},
                "directionTrue": {"value": 4.712389},       # 270°
                "angleTrueWater": {"value": 0.7853982},     # 45°
            },
        },
        "propulsion": {
            "0": {
                "revolutions": {"value": 30.0},        # 1800 U/min
                "temperature": {"value": 353.15},      # 80 °C
                "alternatorVoltage": {"value": 13.9},
                "runTime": {"value": 3600000.0},       # 1000 h
                "oilPressure": {"value": 250000.0},    # 2.5 bar
            }
        },
    }


class MapTest(unittest.TestCase):
    def setUp(self):
        self.v = signalk.map_values(_full_tree())

    def test_position(self):
        self.assertAlmostEqual(self.v[nmea.LAT], 43.51)
        self.assertAlmostEqual(self.v[nmea.LON], 16.44)

    def test_speeds_knots(self):
        self.assertAlmostEqual(self.v[nmea.SOG], 6.0, places=2)
        self.assertAlmostEqual(self.v[nmea.STW], 5.0, places=2)

    def test_angles_degrees(self):
        self.assertAlmostEqual(self.v[nmea.COG], 90.0, places=3)
        self.assertAlmostEqual(self.v[nmea.HDG_TRUE], 180.0, places=3)
        self.assertAlmostEqual(self.v[nmea.HDG_MAG], 185.0, places=2)

    def test_log_meters_to_nm(self):
        self.assertAlmostEqual(self.v[nmea.LOG_TOTAL], 100.0, places=3)

    def test_attitude_and_rudder(self):
        self.assertAlmostEqual(self.v[nmea.HEEL], 10.0, places=2)
        self.assertAlmostEqual(self.v[nmea.TRIM], -3.0, places=2)
        self.assertAlmostEqual(self.v[nmea.RUDDER], 5.0, places=2)

    def test_depth(self):
        self.assertAlmostEqual(self.v[nmea.DEPTH], 12.3)

    def test_temperatures_and_pressure(self):
        self.assertAlmostEqual(self.v[nmea.WATER_TEMP], 17.0, places=2)
        self.assertAlmostEqual(self.v[nmea.AIR_TEMP], 25.0, places=2)
        self.assertAlmostEqual(self.v[nmea.BARO], 1013.0, places=1)

    def test_wind(self):
        self.assertAlmostEqual(self.v[nmea.AWS], 10.0, places=1)
        self.assertAlmostEqual(self.v[nmea.AWA], 330.0, places=2)   # backbord
        self.assertAlmostEqual(self.v[nmea.TWD], 270.0, places=2)
        self.assertAlmostEqual(self.v[nmea.TWA], 45.0, places=2)

    def test_engine(self):
        self.assertAlmostEqual(self.v[nmea.ENGINE_RPM], 1800.0, places=1)
        self.assertAlmostEqual(self.v[nmea.ENGINE_TEMP], 80.0, places=2)
        self.assertAlmostEqual(self.v[nmea.ALT_VOLTAGE], 13.9, places=2)
        self.assertAlmostEqual(self.v[nmea.ENGINE_HOURS], 1000.0, places=1)
        self.assertAlmostEqual(self.v[nmea.OIL_PRESSURE], 2.5, places=2)


class RobustnessTest(unittest.TestCase):
    def test_non_dict_tree(self):
        self.assertEqual(signalk.map_values(None), {})
        self.assertEqual(signalk.map_values([]), {})

    def test_missing_and_null_skipped(self):
        tree = {"navigation": {"speedOverGround": {"value": None},
                               "courseOverGroundTrue": {"value": 0.0}}}
        v = signalk.map_values(tree)
        self.assertNotIn(nmea.SOG, v)
        self.assertIn(nmea.COG, v)          # 0.0 ist ein gültiger Wert

    def test_direct_unwrapped_values(self):
        # Manche Feeds liefern Blätter ohne {"value": …}-Hülle.
        tree = {"navigation": {"speedOverGround": 3.086666}}
        v = signalk.map_values(tree)
        self.assertAlmostEqual(v[nmea.SOG], 6.0, places=2)

    def test_depth_fallback_order(self):
        tree = {"environment": {"depth": {"belowSurface": {"value": 8.0}}}}
        self.assertAlmostEqual(signalk.map_values(tree)[nmea.DEPTH], 8.0)

    def test_bool_not_treated_as_number(self):
        tree = {"navigation": {"speedOverGround": {"value": True}}}
        self.assertNotIn(nmea.SOG, signalk.map_values(tree))


class EngineInstanceTest(unittest.TestCase):
    def test_single_instance_auto(self):
        tree = {"propulsion": {"0": {"revolutions": {"value": 25.0}}}}
        self.assertAlmostEqual(
            signalk.map_values(tree)[nmea.ENGINE_RPM], 1500.0, places=1)

    def test_explicit_instance(self):
        tree = {"propulsion": {
            "port": {"revolutions": {"value": 10.0}},
            "starboard": {"revolutions": {"value": 20.0}},
        }}
        v = signalk.map_values(tree, instance="starboard")
        self.assertAlmostEqual(v[nmea.ENGINE_RPM], 1200.0, places=1)

    def test_default_instance_is_deterministic(self):
        tree = {"propulsion": {
            "starboard": {"revolutions": {"value": 20.0}},
            "port": {"revolutions": {"value": 10.0}},
        }}
        # Ohne Angabe: alphabetisch erste Instanz ("port").
        v = signalk.map_values(tree)
        self.assertAlmostEqual(v[nmea.ENGINE_RPM], 600.0, places=1)


class _Handler(BaseHTTPRequestHandler):
    payload = b"{}"

    def do_GET(self):  # noqa: N802 - Signatur von BaseHTTPRequestHandler
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, *args):  # Test-Ausgabe ruhig halten
        pass


class SourceEndToEndTest(unittest.TestCase):
    """Kette Abruf -> Übersetzung -> LiveData gegen einen echten HTTP-Server."""

    def setUp(self):
        _Handler.payload = json.dumps(_full_tree()).encode("utf-8")
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.host, self.port = self.server.server_address
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_fetch_once(self):
        src = signalk.SignalKSource(self.host, port=self.port)
        values = src.fetch_once()
        self.assertAlmostEqual(values[nmea.SOG], 6.0, places=2)
        self.assertAlmostEqual(values[nmea.ENGINE_RPM], 1800.0, places=1)

    def test_start_feeds_livedata(self):
        live = LiveData()
        statuses = []
        src = signalk.SignalKSource(
            self.host, port=self.port, live=live, poll_interval=0.1,
            on_status=lambda s, m: statuses.append(s))
        src.start()
        try:
            deadline = time.time() + 5.0
            while time.time() < deadline and not live.snapshot():
                time.sleep(0.05)
        finally:
            src.stop()
        snap = live.snapshot()
        self.assertAlmostEqual(snap[nmea.SOG], 6.0, places=2)
        self.assertAlmostEqual(snap[nmea.WATER_TEMP], 17.0, places=2)
        self.assertIn(signalk.STATUS_CONNECTED, statuses)

    def test_url_built_correctly(self):
        src = signalk.SignalKSource(self.host, port=self.port)
        self.assertTrue(src.url.endswith("/signalk/v1/api/vessels/self"))
        self.assertIn(f"{self.host}:{self.port}", src.url)


class ErrorTest(unittest.TestCase):
    def test_unreachable_sets_error_status(self):
        statuses = []
        # Port 1 ist praktisch nie offen -> Verbindungsfehler erwartet.
        src = signalk.SignalKSource(
            "127.0.0.1", port=1, live=LiveData(), poll_interval=0.1,
            reconnect_delay=0.1, timeout=1.0,
            on_status=lambda s, m: statuses.append(s))
        src.start()
        try:
            deadline = time.time() + 4.0
            while time.time() < deadline and signalk.STATUS_ERROR not in statuses:
                time.sleep(0.05)
        finally:
            src.stop()
        self.assertIn(signalk.STATUS_ERROR, statuses)


if __name__ == "__main__":
    unittest.main()
