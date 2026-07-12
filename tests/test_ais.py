"""Tests für den AIS-Decoder.

Die Vergleichswerte stammen aus der bekannten AIVDM/AIVDO-Referenz
(catb.org / gpsd) und sind unabhängig belegt.
"""

import unittest

from saillog.ais import AisDecoder, AisTargets, decode_payload


class DecodePayloadTest(unittest.TestCase):
    def test_type1_position(self):
        msg = decode_payload("13u?etPv2;0n:dDPwUM1U1Cb069D")
        self.assertEqual(msg["type"], 1)
        self.assertEqual(msg["mmsi"], 265547250)
        self.assertAlmostEqual(msg["lat"], 57.6603533, places=4)
        self.assertAlmostEqual(msg["lon"], 11.8329766, places=4)
        self.assertAlmostEqual(msg["sog"], 13.9, places=1)
        self.assertAlmostEqual(msg["cog"], 40.4, places=1)
        self.assertEqual(msg["heading"], 41)

    def test_type18_classb_position(self):
        msg = decode_payload("B6CdCm0t3`tba35RbUQ8QwUoP00")
        self.assertEqual(msg["type"], 18)
        self.assertEqual(msg["mmsi"], 423302100)
        self.assertAlmostEqual(msg["lat"], 38.73892, places=4)
        self.assertAlmostEqual(msg["lon"], 53.010996, places=4)
        self.assertAlmostEqual(msg["sog"], 1.4, places=1)
        self.assertAlmostEqual(msg["cog"], 116.0, places=1)

    def test_garbage_payload(self):
        self.assertIsNone(decode_payload(""))
        self.assertIsNone(decode_payload("x"))

    def test_speed_over_ground_sentinel(self):
        # SOG 1023 (=102.3) bedeutet „nicht verfügbar" -> None
        msg = decode_payload("13u?etPv2;0n:dDPwUM1U1Cb069D")
        self.assertIsNotNone(msg["sog"])  # dieser Satz hat gültige SOG


class DecoderTest(unittest.TestCase):
    def test_single_sentence_updates_targets(self):
        targets = AisTargets()
        decoder = AisDecoder(targets)
        decoder.add_sentence(
            "!AIVDM,1,1,,A,13u?etPv2;0n:dDPwUM1U1Cb069D,0*29", now=1000.0
        )
        rows = targets.all(now=1000.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mmsi"], 265547250)
        self.assertAlmostEqual(rows[0]["cog"], 40.4, places=1)

    def test_multipart_reassembly_type5(self):
        targets = AisTargets()
        decoder = AisDecoder(targets)
        self.assertIsNone(decoder.add_sentence(
            "!AIVDM,2,1,3,B,55P5TL01VIaAL@7WKO@mBplU@<PDhh000000001S;AJ::4A80?4i@E53,0*3E"
        ))
        msg = decoder.add_sentence("!AIVDM,2,2,3,B,1@0000000000000,2*55")
        self.assertEqual(msg["type"], 5)
        self.assertEqual(msg["mmsi"], 369190000)
        self.assertEqual(msg["name"], "MT.MITCHELL")

    def test_multipart_with_mismatched_sequence_ids(self):
        # Echte Wetherdock-easyTRX2-Sätze: die beiden Teile tragen
        # unterschiedliche Sequenz-IDs (hier 0 und 1) — die Zusammensetzung
        # muss trotzdem gelingen (Schlüssel = Funkkanal, nicht Sequenz-ID).
        targets = AisTargets()
        decoder = AisDecoder(targets)
        self.assertIsNone(decoder.add_sentence(
            "!AIVDM,2,1,0,,53SPsH000003T7G3G00mV1L5T00000000000000t00d800@P06ll5@000000,0*55"
        ))
        msg = decoder.add_sentence("!AIVDM,2,2,1,,00000000003,2*67")
        self.assertIsNotNone(msg)
        self.assertEqual(msg["type"], 5)
        self.assertEqual(msg["mmsi"], 238566240)
        self.assertEqual(msg["name"], "MY WAY")

    def test_name_and_position_merge_on_same_target(self):
        targets = AisTargets()
        decoder = AisDecoder(targets)
        # Positionsmeldung ...
        decoder.add_sentence(
            "!AIVDM,1,1,,A,13u?etPv2;0n:dDPwUM1U1Cb069D,0*29", now=1.0
        )
        # ... später käme für dieselbe MMSI ein Name (hier direkt gesetzt)
        targets.update(265547250, {"name": "TESTBOOT"}, now=2.0)
        row = next(r for r in targets.all(now=2.0) if r["mmsi"] == 265547250)
        self.assertEqual(row["name"], "TESTBOOT")
        self.assertIsNotNone(row["lat"])  # Position bleibt erhalten

    def test_stale_targets_expire(self):
        targets = AisTargets(max_age=600.0)
        decoder = AisDecoder(targets)
        decoder.add_sentence(
            "!AIVDM,1,1,,A,13u?etPv2;0n:dDPwUM1U1Cb069D,0*29", now=0.0
        )
        self.assertEqual(len(targets.all(now=100.0)), 1)
        self.assertEqual(len(targets.all(now=1000.0)), 0)  # > max_age

    def test_non_ais_line_ignored(self):
        targets = AisTargets()
        decoder = AisDecoder(targets)
        self.assertIsNone(decoder.add_sentence("$GPRMC,123519,A,4807.038,N", now=1.0))
        self.assertEqual(len(targets.all(now=1.0)), 0)


# Echte Sätze eines Bordfeeds, der COG fehlerhaft in ganzen Grad statt
# Zehntelgrad kodiert (B&G-Multiplexer). Rohes COG-Feld = Kurs in Grad.
_WHOLE_DEG_SENTENCES = [
    ("!AIVDM,1,1,,,13S9cO0P17Q>nG2HUc3PQgwD2000,0*2B", 238185340, 134),
    ("!AIVDM,1,1,,,13Sa`k01PwQ>FadHUPN0K0GD0000,0*1D", 238708940, 108),  # SAN SPIRITO
    ("!AIVDM,1,1,,,13S8M?P01@1>P?LHUHB10Pk@0000,0*2C", 238165310, 258),  # JADERA
    ("!AIVDM,1,1,,,13SBk50P1JQ>U<lHUVCPp?wD2000,0*7D", 238334740, 224),
]


class CogWholeDegreeTest(unittest.TestCase):
    def test_detects_and_corrects_whole_degree_feed(self):
        targets = AisTargets()
        decoder = AisDecoder(targets)
        for sentence, _mmsi, _deg in _WHOLE_DEG_SENTENCES:
            decoder.add_sentence(sentence, now=1.0)
        self.assertEqual(decoder.cog_mode, "whole")
        # Zweite Runde (wie im Livebetrieb): jetzt sind alle korrigiert.
        for sentence, _mmsi, _deg in _WHOLE_DEG_SENTENCES:
            decoder.add_sentence(sentence, now=2.0)
        by_mmsi = {r["mmsi"]: r for r in targets.all(now=2.0)}
        for _sentence, mmsi, deg in _WHOLE_DEG_SENTENCES:
            self.assertAlmostEqual(by_mmsi[mmsi]["cog"], float(deg), places=1)

    def test_standard_feed_stays_tenths(self):
        # Ein normkonformer Satz (COG 40,4°, Feldwert 404 ≥ 360) sperrt den
        # Decoder dauerhaft auf Zehntelgrad — kein fälschliches Hochskalieren.
        targets = AisTargets()
        decoder = AisDecoder(targets)
        decoder.add_sentence(
            "!AIVDM,1,1,,A,13u?etPv2;0n:dDPwUM1U1Cb069D,0*29", now=1.0
        )
        self.assertEqual(decoder.cog_mode, "tenths")
        # Selbst danach auftretende kleine COG-Werte werden NICHT hochskaliert.
        decoder.add_sentence("!AIVDM,1,1,,,13Sa`k01PwQ>FadHUPN0K0GD0000,0*1D", now=2.0)
        row = next(r for r in targets.all(now=2.0) if r["mmsi"] == 238708940)
        self.assertAlmostEqual(row["cog"], 10.8, places=1)  # bleibt Zehntelgrad


if __name__ == "__main__":
    unittest.main()
