"""Tests für die GPS-Maus-Anbindung: Port-Auswahl, Auto-Baud, NMEA-Erkennung."""

import unittest

from saillog import serialports
from saillog.livedata import LiveData
from saillog.source import NmeaSource, _SERIAL_BAUDS


class SerialPortsTest(unittest.TestCase):
    def test_gps_first_orders_receivers_ahead(self):
        ports = [("COM3", "Standard-Seriell"), ("COM13", "u-blox GNSS Receiver"),
                 ("COM5", "Prolific USB-to-Serial")]
        ordered = serialports.gps_first(ports)
        # u-blox und Prolific (USB-Seriell) vor dem Standard-Port
        self.assertEqual(ordered[0][0], "COM13")
        self.assertIn(ordered[1][0], ("COM5",))
        self.assertEqual(ordered[-1][0], "COM3")

    def test_guess_picks_gps_like_port(self):
        ports = [("COM3", "Standard-Seriell"), ("COM13", "u-blox 7 GPS/GLONASS")]
        self.assertEqual(serialports.guess_gps_port(ports), "COM13")

    def test_guess_single_port_even_without_hint(self):
        self.assertEqual(serialports.guess_gps_port([("COM4", "irgendwas")]), "COM4")

    def test_guess_none_when_ambiguous(self):
        ports = [("COM3", "Foo"), ("COM4", "Bar")]
        self.assertIsNone(serialports.guess_gps_port(ports))


class _FakeSerial:
    """Minimaler pyserial-Ersatz: liefert vorgegebene Bytes häppchenweise."""

    def __init__(self, port, baudrate, timeout=1.0, data=b"", good_baud=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.closed = False
        # Nur bei „richtiger" Baudrate sinnvolle Daten liefern
        self._buf = data if (good_baud is None or baudrate == good_baud) else b"\x00\xff\x12"

    def read(self, n=1):
        if not self._buf:
            return b""
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk

    def close(self):
        self.closed = True


def _fake_serial_module(good_baud, data):
    class _Mod:
        @staticmethod
        def Serial(port, baudrate, timeout=1.0):
            return _FakeSerial(port, baudrate, timeout, data=data, good_baud=good_baud)
    return _Mod


_RMC = (b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n")


class AutoBaudTest(unittest.TestCase):
    def _source(self):
        return NmeaSource("COM13", 0, LiveData(), protocol="serial")

    def test_probe_detects_gps_sentence(self):
        src = self._source()
        ser = _FakeSerial("COM13", 9600, data=_RMC * 3)
        self.assertTrue(src._probe_gps(ser, seconds=1.0))

    def test_probe_rejects_garbage(self):
        src = self._source()
        ser = _FakeSerial("COM13", 9600, data=b"\x00\xff\x10\x20noise\r\n")
        self.assertFalse(src._probe_gps(ser, seconds=0.3))

    def test_probe_rejects_bad_checksum(self):
        src = self._source()
        bad = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*00\r\n"
        ser = _FakeSerial("COM13", 9600, data=bad)
        self.assertFalse(src._probe_gps(ser, seconds=0.3))

    def test_open_autobaud_locks_onto_working_baud(self):
        src = self._source()
        # Nur bei 4800 kommen gültige Sätze -> diese Baudrate muss gewählt werden
        mod = _fake_serial_module(good_baud=4800, data=_RMC * 3)
        ser = src._open_autobaud(mod, probe_seconds=0.3)
        self.assertIsNotNone(ser)
        self.assertEqual(ser.baudrate, 4800)

    def test_open_autobaud_none_when_no_nmea(self):
        src = self._source()
        mod = _fake_serial_module(good_baud=-1, data=b"")   # nie gültig
        self.assertIsNone(src._open_autobaud(mod, probe_seconds=0.2))

    def test_common_bauds_include_gmouse_defaults(self):
        for b in (4800, 9600, 38400):
            self.assertIn(b, _SERIAL_BAUDS)


class PortParsingTest(unittest.TestCase):
    def test_auto_baud_port_string(self):
        # „auto"/leer -> Baud 0 (Auto-Erkennung), kein Absturz
        src = NmeaSource("COM13", "auto", LiveData(), protocol="serial")
        self.assertEqual(src._port, 0)

    def test_numeric_baud_string(self):
        src = NmeaSource("COM13", "4800", LiveData(), protocol="serial")
        self.assertEqual(src._port, 4800)


if __name__ == "__main__":
    unittest.main()
