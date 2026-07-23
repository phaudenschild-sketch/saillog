"""Tests für die Signal-K-Quelle (Mapping + REST-Polling)."""

import json
import math
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from saillog.livedata import LiveData
from saillog.signalk import SignalKSource, signalk_to_snapshot


def _leaf(v):
    return {"value": v}


# Repräsentativer vessels/self-Baum eines Signal-K-Servers (SI-Einheiten).
SAMPLE = {
    "navigation": {
        "position": _leaf({"latitude": 43.5, "longitude": 16.4}),
        "speedOverGround": _leaf(3.0),                 # m/s
        "speedThroughWater": _leaf(2.5),               # m/s
        "courseOverGroundTrue": _leaf(math.radians(120)),
        "headingMagnetic": _leaf(math.radians(115)),
        "log": _leaf(1852000.0),                       # m -> 1000 NM
        "attitude": _leaf({"roll": math.radians(10), "pitch": math.radians(-2)}),
        "datetime": _leaf("2026-07-23T14:35:09.000Z"),
    },
    "environment": {
        "wind": {
            "speedApparent": _leaf(5.0),               # m/s
            "angleApparent": _leaf(math.radians(-45)), # -> 315°
            "speedTrue": _leaf(6.0),
            "directionTrue": _leaf(math.radians(200)),
        },
        "depth": {"belowTransducer": _leaf(12.3)},
        "water": {"temperature": _leaf(291.15)},       # 18 °C
        "outside": {
            "temperature": _leaf(298.15),              # 25 °C
            "pressure": _leaf(101300.0),               # 1013 mbar
        },
    },
    "propulsion": {
        "0": {
            "revolutions": _leaf(30.0),                # Hz -> 1800 rpm
            "temperature": _leaf(353.15),              # 80 °C
            "oilPressure": _leaf(300000.0),            # 3 bar
            "runTime": _leaf(3600 * 1200),             # 1200 h
        },
    },
    "electrical": {"batteries": {"house": {"voltage": _leaf(13.4)}}},
    "steering": {"rudderAngle": _leaf(math.radians(-5))},
    "tanks": {"fuel": {"0": {
        "currentLevel": _leaf(0.75),                   # -> 75 %
        "currentVolume": _leaf(0.120),                 # m³ -> 120 L
    }}},
}


class MappingTest(unittest.TestCase):
    def test_full_mapping_with_conversions(self):
        s = signalk_to_snapshot(SAMPLE)
        self.assertAlmostEqual(s["lat"], 43.5)
        self.assertAlmostEqual(s["lon"], 16.4)
        self.assertAlmostEqual(s["sog_kn"], 3.0 * 1.9438444924406, places=3)
        self.assertAlmostEqual(s["stw_kn"], 2.5 * 1.9438444924406, places=3)
        self.assertAlmostEqual(s["cog_deg"], 120.0, places=3)
        self.assertAlmostEqual(s["hdg_mag_deg"], 115.0, places=3)
        self.assertAlmostEqual(s["log_total_nm"], 1000.0, places=3)
        self.assertAlmostEqual(s["heel_deg"], 10.0, places=3)
        self.assertAlmostEqual(s["trim_deg"], -2.0, places=3)
        self.assertEqual(s["utc_time"], "143509")
        self.assertAlmostEqual(s["aws_kn"], 5.0 * 1.9438444924406, places=3)
        self.assertAlmostEqual(s["awa_deg"], 315.0, places=3)
        self.assertAlmostEqual(s["tws_kn"], 6.0 * 1.9438444924406, places=3)
        self.assertAlmostEqual(s["twd_deg"], 200.0, places=3)
        self.assertAlmostEqual(s["depth_m"], 12.3, places=3)
        self.assertAlmostEqual(s["water_temp_c"], 18.0, places=2)
        self.assertAlmostEqual(s["air_temp_c"], 25.0, places=2)
        self.assertAlmostEqual(s["baro_mbar"], 1013.0, places=2)
        self.assertAlmostEqual(s["engine_rpm"], 1800.0, places=1)
        self.assertAlmostEqual(s["engine_temp_c"], 80.0, places=2)
        self.assertAlmostEqual(s["oil_pressure_bar"], 3.0, places=3)
        self.assertAlmostEqual(s["engine_hours"], 1200.0, places=1)
        self.assertAlmostEqual(s["alternator_v"], 13.4, places=3)
        self.assertAlmostEqual(s["rudder_deg"], -5.0, places=3)
        self.assertAlmostEqual(s["fuel_pct"], 75.0, places=3)
        self.assertAlmostEqual(s["fuel_l"], 120.0, places=3)

    def test_only_present_keys(self):
        # Nur vorhandene Werte werden gesetzt (kein None, das belegte Felder
        # in LiveData überschreiben würde).
        s = signalk_to_snapshot(
            {"navigation": {"position": _leaf({"latitude": 1.0, "longitude": 2.0})}})
        self.assertEqual(set(s), {"lat", "lon"})

    def test_position_needs_both_lat_and_lon(self):
        s = signalk_to_snapshot(
            {"navigation": {"position": _leaf({"latitude": 1.0})}})
        self.assertNotIn("lat", s)

    def test_multi_instance_picks_first_available(self):
        # Erste Motor-Instanz ohne Drehzahl, zweite mit -> zweite gewinnt.
        tree = {"propulsion": {
            "port": {"temperature": _leaf(300.0)},
            "stbd": {"revolutions": _leaf(25.0)},
        }}
        s = signalk_to_snapshot(tree)
        self.assertAlmostEqual(s["engine_rpm"], 1500.0, places=1)

    def test_empty_and_garbage_input(self):
        self.assertEqual(signalk_to_snapshot({}), {})
        self.assertEqual(signalk_to_snapshot(None), {})
        self.assertEqual(signalk_to_snapshot({"navigation": 42}), {})

    def test_bool_is_not_a_measurement(self):
        # Ein bool darf nicht als Zahl durchrutschen.
        s = signalk_to_snapshot(
            {"navigation": {"speedOverGround": _leaf(True)}})
        self.assertNotIn("sog_kn", s)


class _Handler(BaseHTTPRequestHandler):
    payload = json.dumps(SAMPLE).encode("utf-8")

    def do_GET(self):  # noqa: N802
        if "signalk" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(self.payload)))
            self.end_headers()
            self.wfile.write(self.payload)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):  # Test-Server still halten
        pass


class LivePollTest(unittest.TestCase):
    def test_source_polls_and_feeds_livedata(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        live = LiveData()
        src = SignalKSource(host="127.0.0.1", port=port, live=live,
                            poll_interval=0.05)
        src.start()
        self.addCleanup(src.stop)

        deadline = time.time() + 3.0
        while live.get("sog_kn") is None and time.time() < deadline:
            time.sleep(0.05)

        self.assertIsNotNone(live.get("sog_kn"), "SignalKSource hat keine Werte geliefert")
        self.assertAlmostEqual(live.get("cog_deg"), 120.0, places=1)
        self.assertAlmostEqual(live.get("depth_m"), 12.3, places=1)

    def test_default_port_when_zero(self):
        live = LiveData()
        src = SignalKSource(host="10.0.0.1", port=0, live=live)
        self.assertIn(":3000/signalk", src._url)


if __name__ == "__main__":
    unittest.main()
