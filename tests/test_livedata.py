"""Tests für den Live-Daten-Speicher."""

import unittest

from saillog.livedata import LiveData


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

    def test_slow_engine_values_kept_longer(self):
        # Motor-Dynamikdaten kommen selten -> längeres Frische-Fenster (60 s).
        live = LiveData(stale_after=10.0)
        live.update({"engine_temp_c": 92.8, "alternator_v": 14.6,
                     "engine_hours": 240.0, "sog_kn": 5.0}, now=100.0)
        # nach 30 s: SOG (schnell) veraltet, Motorwerte bleiben
        snap = live.snapshot(now=130.0)
        self.assertNotIn("sog_kn", snap)
        self.assertEqual(snap["engine_temp_c"], 92.8)
        self.assertEqual(snap["alternator_v"], 14.6)
        self.assertEqual(snap["engine_hours"], 240.0)
        # nach 70 s: auch die Motorwerte veraltet
        self.assertEqual(live.snapshot(now=170.0), {})

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

    # --- Prioritäten mehrerer Quellen --------------------------------------

    def test_higher_priority_wins_over_lower(self):
        live = LiveData(stale_after=10.0)
        # Niedrig-priorisierte Quelle liefert zuerst …
        live.update({"sog_kn": 5.0}, now=1.0, priority=1)
        # … die höher priorisierte Quelle überschreibt.
        live.update({"sog_kn": 6.0}, now=2.0, priority=3)
        self.assertEqual(live.get("sog_kn", now=2.5), 6.0)

    def test_lower_priority_does_not_override_fresh(self):
        live = LiveData(stale_after=10.0)
        live.update({"sog_kn": 6.0}, now=1.0, priority=3)   # bevorzugt
        live.update({"sog_kn": 5.0}, now=2.0, priority=1)   # schwächer, frisch
        self.assertEqual(live.get("sog_kn", now=2.5), 6.0)  # bevorzugt bleibt

    def test_lower_priority_fills_in_when_preferred_stale(self):
        live = LiveData(stale_after=10.0)
        live.update({"sog_kn": 6.0}, now=1.0, priority=3)   # bevorzugt
        # 20 s später ist die bevorzugte Quelle veraltet -> schwächere springt ein
        live.update({"sog_kn": 5.0}, now=21.0, priority=1)
        self.assertEqual(live.get("sog_kn", now=21.5), 5.0)

    def test_preferred_source_recovers(self):
        live = LiveData(stale_after=10.0)
        live.update({"sog_kn": 6.0}, now=1.0, priority=3)
        live.update({"sog_kn": 5.0}, now=21.0, priority=1)  # Einspringer
        live.update({"sog_kn": 7.0}, now=22.0, priority=3)  # bevorzugt zurück
        self.assertEqual(live.get("sog_kn", now=22.5), 7.0)

    def test_equal_priority_last_wins(self):
        live = LiveData(stale_after=10.0)
        live.update({"sog_kn": 5.0}, now=1.0, priority=2)
        live.update({"sog_kn": 6.0}, now=2.0, priority=2)
        self.assertEqual(live.get("sog_kn", now=2.5), 6.0)


if __name__ == "__main__":
    unittest.main()
