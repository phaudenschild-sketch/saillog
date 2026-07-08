"""Tests für den lokalen Kartenserver (Datenaufbereitung + HTTP)."""

import json
import unittest
import urllib.request

from masarasi.webmap import MapServer


def _make(own=None, targets=None, track=None):
    return MapServer(
        own_provider=lambda: own,
        targets_provider=lambda: list(targets or []),
        track_provider=lambda: track or {"name": "", "points": []},
    )


class DataAssemblyTest(unittest.TestCase):
    def test_targets_without_position_are_dropped(self):
        srv = _make(targets=[
            {"mmsi": 1, "lat": 43.5, "lon": 16.0, "cog": 90.0, "name": "A"},
            {"mmsi": 2, "name": "nur Name, keine Position"},
        ])
        data = srv._data()
        self.assertEqual(len(data["targets"]), 1)
        self.assertEqual(data["targets"][0]["mmsi"], 1)
        self.assertEqual(data["targets"][0]["name"], "A")

    def test_own_and_track_passed_through(self):
        own = {"lat": 43.5, "lon": 16.0, "cog": 12.0, "heading": None, "sog": 5.0}
        track = {"name": "Split→Vis", "points": [[43.5, 16.0], [43.4, 16.1]]}
        srv = _make(own=own, track=track)
        data = srv._data()
        self.assertEqual(data["own"], own)
        self.assertEqual(data["track"]["name"], "Split→Vis")
        self.assertEqual(len(data["track"]["points"]), 2)


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.srv = _make(
            own={"lat": 43.5, "lon": 16.0, "cog": 10.0, "heading": 12.0, "sog": 6.0},
            targets=[{"mmsi": 42, "lat": 43.6, "lon": 15.9, "cog": 200.0, "name": "X"}],
            track={"name": "T", "points": [[43.5, 16.0], [43.6, 15.9]]},
        )
        self.srv.start()
        # keinen Proxy für localhost verwenden
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.srv.stop()

    def _get(self, path):
        with self._opener.open(self.srv.url.rstrip("/") + path, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")

    def test_index_page_served(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("masarasi", body)
        self.assertIn("openfreemap.org", body)  # OpenFreeMap eingebunden
        self.assertIn("leaflet", body.lower())

    def test_data_json(self):
        status, body = self._get("/data.json")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["own"]["sog"], 6.0)
        self.assertEqual(len(data["targets"]), 1)
        self.assertEqual(data["targets"][0]["mmsi"], 42)
        self.assertEqual(len(data["track"]["points"]), 2)

    def test_unknown_path_404(self):
        try:
            self._get("/nope")
            self.fail("erwartete HTTPError 404")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)


if __name__ == "__main__":
    unittest.main()
