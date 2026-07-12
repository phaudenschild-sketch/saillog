"""Tests für die Zeitzonen-Anzeige."""

import unittest

from triplog import timeutil


class TimeutilTest(unittest.TestCase):
    def test_to_display_with_offset(self):
        # UTC 10:00 -> lokal +2 -> 12:00
        self.assertEqual(
            timeutil.to_display("2026-07-06T10:00:00Z", 2.0),
            "2026-07-06 12:00:00",
        )

    def test_to_display_negative_offset(self):
        self.assertEqual(
            timeutil.to_display("2026-07-06T01:00:00Z", -2.0),
            "2026-07-05 23:00:00",
        )

    def test_from_display_roundtrip(self):
        utc = timeutil.from_display("2026-07-06 12:00:00", 2.0)
        self.assertEqual(utc, "2026-07-06T10:00:00Z")

    def test_roundtrip_stable(self):
        original = "2026-07-06T10:00:00Z"
        disp = timeutil.to_display(original, 3.0)
        back = timeutil.from_display(disp, 3.0)
        self.assertEqual(back, original)

    def test_handles_offset_timestamp(self):
        # Eingang bereits mit Versatz -> korrekt nach lokal
        self.assertEqual(
            timeutil.to_display("2026-07-06T12:00:00+02:00", 0.0),
            "2026-07-06 10:00:00",
        )

    def test_unparsable_from_display_kept(self):
        self.assertEqual(timeutil.from_display("keine zeit", 2.0), "keine zeit")

    def test_label(self):
        self.assertEqual(timeutil.label("fixed", 2.0), "UTC+2")
        self.assertEqual(timeutil.label("fixed", 0.0), "UTC+0")
        self.assertTrue(timeutil.label("system", 0.0).startswith("System"))


if __name__ == "__main__":
    unittest.main()
