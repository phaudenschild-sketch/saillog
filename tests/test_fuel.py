"""Tests für Tank-Logbuch und Verbrauchsberechnung."""

import os
import tempfile
import unittest

from saillog import fuel
from saillog.storage import FuelEntry, LogbookStore


def _f(ts, liters, full, hours):
    return FuelEntry(id=None, timestamp=ts, liters=liters, full_tank=full,
                     engine_hours=hours)


class ConsumptionTest(unittest.TestCase):
    def test_two_full_tanks(self):
        # voll bei 100 h, dann 60 l nachgetankt bei 110 h -> 6 l/h
        entries = [
            _f("2026-07-01T08:00:00Z", 40.0, 1, 100.0),
            _f("2026-07-05T08:00:00Z", 60.0, 1, 110.0),
        ]
        s = fuel.consumption_stats(entries)
        self.assertAlmostEqual(s["last_rate"], 6.0, places=3)
        self.assertAlmostEqual(s["avg_rate"], 6.0, places=3)
        self.assertEqual(len(s["intervals"]), 1)

    def test_partial_fills_between_full(self):
        # voll@100h; Teiltankung 20 l; voll@120h mit 40 l -> 60 l / 20 h = 3 l/h
        entries = [
            _f("2026-07-01T00:00:00Z", 30.0, 1, 100.0),
            _f("2026-07-02T00:00:00Z", 20.0, 0, 108.0),   # Teiltankung zählt mit
            _f("2026-07-03T00:00:00Z", 40.0, 1, 120.0),
        ]
        s = fuel.consumption_stats(entries)
        self.assertAlmostEqual(s["last_rate"], 3.0, places=3)  # 60 l / 20 h

    def test_multiple_intervals_average(self):
        entries = [
            _f("2026-07-01T00:00:00Z", 50.0, 1, 100.0),
            _f("2026-07-02T00:00:00Z", 60.0, 1, 110.0),   # 60 l / 10 h = 6.0
            _f("2026-07-03T00:00:00Z", 80.0, 1, 130.0),   # 80 l / 20 h = 4.0
        ]
        s = fuel.consumption_stats(entries)
        self.assertAlmostEqual(s["last_rate"], 4.0, places=3)
        # Ø = (60+80) / (10+20) = 140/30
        self.assertAlmostEqual(s["avg_rate"], 140.0 / 30.0, places=3)

    def test_not_computable_without_two_full(self):
        s = fuel.consumption_stats([_f("2026-07-01T00:00:00Z", 50.0, 1, 100.0)])
        self.assertIsNone(s["last_rate"])
        self.assertIsNone(s["avg_rate"])

    def test_ignores_full_without_engine_hours(self):
        entries = [
            _f("2026-07-01T00:00:00Z", 50.0, 1, None),    # keine Motorstunden
            _f("2026-07-02T00:00:00Z", 60.0, 1, 110.0),
        ]
        s = fuel.consumption_stats(entries)
        self.assertIsNone(s["last_rate"])  # nur ein brauchbarer Bezugspunkt

    def test_zero_or_negative_hours_skipped(self):
        entries = [
            _f("2026-07-01T00:00:00Z", 50.0, 1, 100.0),
            _f("2026-07-02T00:00:00Z", 60.0, 1, 100.0),   # gleiche Stunden -> übersprungen
        ]
        s = fuel.consumption_stats(entries)
        self.assertIsNone(s["last_rate"])


class RemainingEstimateTest(unittest.TestCase):
    def test_basic_remaining(self):
        # voll@100h, 160 L Tank, 6 l/h, jetzt 110h -> 60 l weg -> 100 l Rest
        entries = [_f("2026-07-01T00:00:00Z", 160.0, 1, 100.0)]
        est = fuel.remaining_estimate(entries, 160.0, 110.0, 6.0)
        self.assertAlmostEqual(est["remaining_l"], 100.0, places=3)
        self.assertAlmostEqual(est["remaining_hours"], 100.0 / 6.0, places=3)

    def test_partial_refuel_after_full_adds_back(self):
        entries = [
            _f("2026-07-01T00:00:00Z", 160.0, 1, 100.0),
            _f("2026-07-02T00:00:00Z", 30.0, 0, 110.0),   # Teiltankung danach
        ]
        # jetzt 120h, rate 5 -> verbraucht 100 l, +30 nachgetankt -> 90 l Rest
        est = fuel.remaining_estimate(entries, 160.0, 120.0, 5.0)
        self.assertAlmostEqual(est["remaining_l"], 90.0, places=3)

    def test_capped_at_capacity(self):
        entries = [
            _f("2026-07-01T00:00:00Z", 160.0, 1, 100.0),
            _f("2026-07-02T00:00:00Z", 200.0, 0, 101.0),  # unrealistisch viel
        ]
        est = fuel.remaining_estimate(entries, 160.0, 101.0, 5.0)
        self.assertLessEqual(est["remaining_l"], 160.0)

    def test_missing_inputs_return_none(self):
        e = [_f("2026-07-01T00:00:00Z", 160.0, 1, 100.0)]
        self.assertIsNone(fuel.remaining_estimate(e, None, 110.0, 6.0))     # keine Kapazität
        self.assertIsNone(fuel.remaining_estimate(e, 160.0, None, 6.0))     # keine Motorstunden
        self.assertIsNone(fuel.remaining_estimate(e, 160.0, 110.0, None))   # keine Rate
        self.assertIsNone(fuel.remaining_estimate([], 160.0, 110.0, 6.0))   # keine Tankung

    def test_negative_hours_since_returns_none(self):
        e = [_f("2026-07-01T00:00:00Z", 160.0, 1, 120.0)]
        self.assertIsNone(fuel.remaining_estimate(e, 160.0, 110.0, 6.0))  # jetzt < voll


class FuelStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.store = LogbookStore(self._path)

    def tearDown(self):
        os.unlink(self._path)

    def test_crud_and_order(self):
        self.store.add_fuel(_f("2026-07-05T00:00:00Z", 60.0, 1, 110.0))
        self.store.add_fuel(_f("2026-07-01T00:00:00Z", 40.0, 1, 100.0))
        entries = self.store.all_fuel(newest_first=False)
        self.assertEqual([e.liters for e in entries], [40.0, 60.0])  # chronologisch
        first = entries[0]
        first.location = "Rogoznica"
        first.liters = 42.0
        self.store.update_fuel(first)
        self.assertEqual(self.store.all_fuel()[0].location, "Rogoznica")
        self.assertEqual(self.store.all_fuel()[0].liters, 42.0)
        self.store.delete_fuel(first.id)
        self.assertEqual(len(self.store.all_fuel()), 1)


if __name__ == "__main__":
    unittest.main()
