"""Tests für den NMEA0183-Parser."""

import unittest

from masarasi import nmea
from masarasi.nmea import NmeaParser, valid_checksum


class ChecksumTest(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(
            valid_checksum("$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A")
        )

    def test_invalid(self):
        self.assertFalse(valid_checksum("$GPRMC,123519,A*00"))

    def test_missing_is_tolerated(self):
        self.assertTrue(valid_checksum("$GPRMC,123519,A"))


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = NmeaParser()

    def test_rmc_position_and_speed(self):
        result = self.parser.parse(
            "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        )
        self.assertAlmostEqual(result[nmea.LAT], 48.1173, places=3)
        self.assertAlmostEqual(result[nmea.LON], 11.5167, places=3)
        self.assertAlmostEqual(result[nmea.SOG], 22.4)
        self.assertAlmostEqual(result[nmea.COG], 84.4)

    def test_southern_western_hemisphere(self):
        result = self.parser.parse("$GPGLL,4750.00,S,00930.00,W,120000,A*checksum")
        # Prüfsumme absichtlich falsch -> aber Format testen wir separat
        # Deshalb hier ohne Prüfsumme:
        result = self.parser.parse("$GPGLL,4750.00,S,00930.00,W,120000,A")
        self.assertLess(result[nmea.LAT], 0)
        self.assertLess(result[nmea.LON], 0)
        self.assertAlmostEqual(result[nmea.LAT], -47.8333, places=3)

    def test_mwv_apparent_wind(self):
        result = self.parser.parse("$IIMWV,042.0,R,12.5,N,A")
        self.assertAlmostEqual(result[nmea.AWA], 42.0)
        self.assertAlmostEqual(result[nmea.AWS], 12.5)

    def test_mwv_true_wind(self):
        result = self.parser.parse("$IIMWV,030.0,T,10.0,N,A")
        self.assertAlmostEqual(result[nmea.TWA], 30.0)
        self.assertAlmostEqual(result[nmea.TWS], 10.0)

    def test_mwv_speed_unit_kmh(self):
        result = self.parser.parse("$IIMWV,042.0,R,18.52,K,A")
        self.assertAlmostEqual(result[nmea.AWS], 10.0, places=2)

    def test_mwv_invalid_status_ignored(self):
        self.assertEqual(self.parser.parse("$IIMWV,042.0,R,12.5,N,V"), {})

    def test_dpt_with_offset(self):
        result = self.parser.parse("$SDDPT,12.3,0.5")
        self.assertAlmostEqual(result[nmea.DEPTH], 12.8)

    def test_dbt_meters(self):
        result = self.parser.parse("$SDDBT,036.5,f,011.1,M,006.0,F")
        self.assertAlmostEqual(result[nmea.DEPTH], 11.1)

    def test_mtw_water_temp(self):
        result = self.parser.parse("$IIMTW,19.2,C")
        self.assertAlmostEqual(result[nmea.WATER_TEMP], 19.2)

    def test_vhw_speed_through_water(self):
        result = self.parser.parse("$IIVHW,45.0,T,42.0,M,5.2,N,9.6,K")
        self.assertAlmostEqual(result[nmea.STW], 5.2)
        self.assertAlmostEqual(result[nmea.HDG_TRUE], 45.0)

    def test_hdg_true_from_variation(self):
        result = self.parser.parse("$IIHDG,100.0,,,2.0,E")
        self.assertAlmostEqual(result[nmea.HDG_MAG], 100.0)
        self.assertAlmostEqual(result[nmea.HDG_TRUE], 102.0)

    def test_vtg(self):
        result = self.parser.parse("$GPVTG,84.4,T,,M,22.4,N,41.5,K")
        self.assertAlmostEqual(result[nmea.COG], 84.4)
        self.assertAlmostEqual(result[nmea.SOG], 22.4)

    def test_unknown_sentence(self):
        self.assertEqual(self.parser.parse("$GPGSA,A,3,04,05,,09"), {})

    def test_garbage(self):
        self.assertEqual(self.parser.parse("hello world"), {})
        self.assertEqual(self.parser.parse(""), {})
        self.assertEqual(self.parser.parse("$"), {})

    def test_bad_checksum_rejected(self):
        self.assertEqual(self.parser.parse("$GPRMC,123519,A,4807.038,N*00"), {})


if __name__ == "__main__":
    unittest.main()
