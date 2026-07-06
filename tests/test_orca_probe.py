"""Test für den WebSocket-Frame-Parser des Orca-Probers."""

import os
import sys
import unittest

# orca_probe.py liegt im Projekt-Wurzelverzeichnis
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import orca_probe  # noqa: E402


class _FakeSock:
    def __init__(self, data: bytes):
        self._data = data

    def recv(self, n: int) -> bytes:
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk


class WsFrameTest(unittest.TestCase):
    def test_short_text_frame(self):
        payload = b'{"a":1}'
        frame = bytes([0x81, len(payload)]) + payload
        reader = orca_probe._Buffered(_FakeSock(frame))
        self.assertEqual(orca_probe._ws_read_frame(reader), payload)

    def test_extended_length_frame(self):
        payload = b"x" * 300  # > 125 -> 16-bit Länge
        frame = bytes([0x81, 126]) + (300).to_bytes(2, "big") + payload
        reader = orca_probe._Buffered(_FakeSock(frame))
        self.assertEqual(orca_probe._ws_read_frame(reader), payload)

    def test_two_frames_with_leftover(self):
        p1, p2 = b"eins", b"zwei"
        data = bytes([0x81, 4]) + p1 + bytes([0x81, 4]) + p2
        reader = orca_probe._Buffered(_FakeSock(data))
        self.assertEqual(orca_probe._ws_read_frame(reader), p1)
        self.assertEqual(orca_probe._ws_read_frame(reader), p2)

    def test_empty_on_close(self):
        reader = orca_probe._Buffered(_FakeSock(b""))
        self.assertEqual(orca_probe._ws_read_frame(reader), b"")


if __name__ == "__main__":
    unittest.main()
