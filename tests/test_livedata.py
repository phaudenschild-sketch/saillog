"""Tests für den Live-Daten-Speicher."""

import unittest

from masarasi.livedata import LiveData


class LiveDataTest(unittest.TestCase):
    def test_update_and_snapshot(self):
        live = LiveData()
        live.update({"sog_kn": 5.2, "cog_deg": 90.0}, now=100.0)
        snap = live.snapshot(now=101.0)
        self.assertEqual(snap["sog_kn"], 5.2)
        self.assertEqual(snap["cog_deg"], 90.0)

    def test_stale_values_dropped(self):
        live = LiveData(stale_after=10.0)
        live.update({"sog_kn": 5.2}, now=100.0)
        # 20 Sekunden später -> veraltet
        self.assertEqual(live.snapshot(now=120.0), {})
        self.assertIsNone(live.get("sog_kn", now=120.0))

    def test_get_single(self):
        live = LiveData()
        live.update({"depth_m": 12.0}, now=50.0)
        self.assertEqual(live.get("depth_m", now=51.0), 12.0)
        self.assertIsNone(live.get("missing", now=51.0))

    def test_age(self):
        live = LiveData()
        self.assertIsNone(live.age(now=10.0))
        live.update({"sog_kn": 1.0}, now=10.0)
        self.assertAlmostEqual(live.age(now=15.0), 5.0)

    def test_overwrite_keeps_latest(self):
        live = LiveData()
        live.update({"sog_kn": 5.0}, now=1.0)
        live.update({"sog_kn": 6.0}, now=2.0)
        self.assertEqual(live.get("sog_kn", now=2.5), 6.0)

    def test_log_total_is_monotonic_max(self):
        # Gesamtlog: kleinere Ausreißer/Reset-Werte werden ignoriert
        live = LiveData()
        live.update({"log_total_nm": 4029.3}, now=1.0)
        live.update({"log_total_nm": 2.055}, now=1.5)   # zweite Quelle / Reset
        live.update({"log_total_nm": 1434.0}, now=2.0)  # Grunddistanz
        self.assertAlmostEqual(live.get("log_total_nm", now=2.5), 4029.3)
        live.update({"log_total_nm": 4030.1}, now=3.0)  # wachsend -> übernommen
        self.assertAlmostEqual(live.get("log_total_nm", now=3.5), 4030.1)

    def test_log_total_resets_after_stale(self):
        live = LiveData(stale_after=10.0)
        live.update({"log_total_nm": 4029.3}, now=1.0)
        live.update({"log_total_nm": 5.0}, now=100.0)   # nach Veralten neu
        self.assertAlmostEqual(live.get("log_total_nm", now=100.5), 5.0)


if __name__ == "__main__":
    unittest.main()
