"""Tests für Schiffs-Stammdaten + Loggeber-Korrektur an der Quelle."""

import os
import tempfile
import unittest

from masarasi.livedata import LiveData
from masarasi.source import NmeaSource
from masarasi.storage import LogbookStore, Ship


class ShipStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.store = LogbookStore(self._path)

    def tearDown(self):
        os.unlink(self._path)

    def test_crud_and_fields(self):
        sid = self.store.add_ship(Ship(
            name="SY Tymanfaya", ship_type="Sailing Vessel", length_m=20.62,
            beam_m=5.36, max_draft_m=2.50, displacement_t=38.0, flag="Schweiz",
            home_port="Basel", call_sign="HBY4380", mmsi="269101720",
            log_correction=1.07, water_tank_l=2000.0, fuel_tank_l=1300.0))
        s = self.store.get_ship(sid)
        self.assertEqual(s.name, "SY Tymanfaya")
        self.assertEqual(s.length_m, 20.62)
        self.assertEqual(s.log_correction, 1.07)
        self.assertEqual(s.fuel_tank_l, 1300.0)
        s.home_port = "Kastela"
        self.store.update_ship(s)
        self.assertEqual(self.store.get_ship(sid).home_port, "Kastela")
        self.assertEqual(len(self.store.all_ships()), 1)
        self.store.delete_ship(sid)
        self.assertEqual(self.store.all_ships(), [])

    def test_ship_photo(self):
        sid = self.store.add_ship(Ship(name="X"))
        self.store.set_ship_photo(sid, b"JPEGDATA")
        self.assertEqual(self.store.get_ship_photo(sid), b"JPEGDATA")
        self.store.delete_ship(sid)
        self.assertIsNone(self.store.get_ship_photo(sid))


def _nmea(body: str) -> bytes:
    """Baut einen NMEA-Satz mit korrekter Prüfsumme."""
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}".encode("ascii")


class LogCorrectionTest(unittest.TestCase):
    def test_correction_applied_to_stw_and_log(self):
        live = LiveData()
        src = NmeaSource("x", 1, live, log_correction=1.10)
        src._handle_line(_nmea("IIVHW,100.0,T,095.0,M,5.00,N,9.26,K"))  # STW
        src._handle_line(_nmea("IIVLW,100.0,N,100.0,N,,N,,N"))          # Log
        snap = live.snapshot()
        self.assertAlmostEqual(snap["stw_kn"], 5.00 * 1.10, places=3)
        self.assertAlmostEqual(snap["log_total_nm"], 100.0 * 1.10, places=3)

    def test_no_correction_by_default(self):
        live = LiveData()
        src = NmeaSource("x", 1, live)  # log_correction default 1.0
        src._handle_line(_nmea("IIVHW,100.0,T,095.0,M,5.00,N,9.26,K"))
        self.assertAlmostEqual(live.snapshot()["stw_kn"], 5.00, places=3)


if __name__ == "__main__":
    unittest.main()
