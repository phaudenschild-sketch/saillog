"""Tests für die Bildspeicherung pro Eintrag und die Aufnahme-Kapselung."""

import os
import struct
import tempfile
import unittest
import zlib

from masarasi import plotter_capture
from masarasi.storage import LogbookStore, LogEntry


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


class EntryImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LogbookStore(os.path.join(self.tmp.name, "log.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_get_image(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z", entry_type="manual")
        self.store.add(entry)
        self.store.set_image(entry.id, _png())
        self.assertEqual(self.store.get_image(entry.id), _png())
        self.assertIn(entry.id, self.store.entries_with_images())

    def test_replace_image(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z")
        self.store.add(entry)
        self.store.set_image(entry.id, b"\x89PNG\r\n\x1a\n" + b"a")
        self.store.set_image(entry.id, b"\x89PNG\r\n\x1a\n" + b"bb")
        self.assertEqual(len(self.store.entries_with_images()), 1)
        self.assertEqual(self.store.get_image(entry.id), b"\x89PNG\r\n\x1a\n" + b"bb")

    def test_delete_entry_removes_image(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z")
        self.store.add(entry)
        self.store.set_image(entry.id, _png())
        self.store.delete(entry.id)
        self.assertIsNone(self.store.get_image(entry.id))
        self.assertEqual(self.store.entries_with_images(), set())

    def test_delete_by_type_removes_images(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z", entry_type="tripcon")
        self.store.add(entry)
        self.store.set_image(entry.id, _png())
        self.store.delete_by_type("tripcon")
        self.assertEqual(self.store.entries_with_images(), set())

    def test_export_images(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z")
        self.store.add(entry)
        self.store.set_image(entry.id, _png())
        out = os.path.join(self.tmp.name, "bilder")
        count = self.store.export_entry_images(out)
        self.assertEqual(count, 1)
        files = os.listdir(out)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith(".png"))


class CaptureModuleTest(unittest.TestCase):
    def test_available_is_bool(self):
        self.assertIsInstance(plotter_capture.available(), bool)

    def test_grab_invalid_region_returns_none(self):
        self.assertIsNone(plotter_capture.grab_png(None))
        self.assertIsNone(plotter_capture.grab_png([0, 0, 0, 0]))
        self.assertIsNone(plotter_capture.grab_png([10, 10, 12]))  # falsche Länge


if __name__ == "__main__":
    unittest.main()
