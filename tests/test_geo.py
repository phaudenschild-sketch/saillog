"""Tests für die geografischen Distanzfunktionen."""

import unittest

from masarasi import geo


class HaversineTest(unittest.TestCase):
    def test_one_degree_latitude_is_about_60nm(self):
        # 1° Breite entspricht per Definition ~60 Seemeilen.
        d = geo.haversine_nm(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(d, 60.0, delta=0.1)

    def test_zero_distance(self):
        self.assertAlmostEqual(geo.haversine_nm(43.5, 16.0, 43.5, 16.0), 0.0, places=6)

    def test_known_leg(self):
        # Split (43.50N,16.44E) -> Vis (43.06N,16.18E): ~28 NM Luftlinie
        d = geo.haversine_nm(43.50, 16.44, 43.06, 16.18)
        self.assertTrue(26 < d < 30, d)


class TrackDistanceTest(unittest.TestCase):
    def test_sums_consecutive_legs(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 0.0)]  # 60 NM + 0
        self.assertAlmostEqual(geo.track_distance_nm(pts), 60.0, delta=0.1)

    def test_skips_missing_points(self):
        pts = [(0.0, 0.0), (None, None), None, (1.0, 0.0)]
        self.assertAlmostEqual(geo.track_distance_nm(pts), 60.0, delta=0.1)

    def test_empty_and_single(self):
        self.assertEqual(geo.track_distance_nm([]), 0.0)
        self.assertEqual(geo.track_distance_nm([(43.0, 16.0)]), 0.0)


if __name__ == "__main__":
    unittest.main()
