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

    def test_multiple_images_per_entry(self):
        entry = LogEntry(timestamp="2026-07-05T10:00:00Z")
        self.store.add(entry)
        id1 = self.store.add_entry_image(entry.id, _png() + b"1", "image/png")
        id2 = self.store.add_entry_image(entry.id, _png() + b"2", "image/png")
        id3 = self.store.add_entry_image(entry.id, _png() + b"3", "image/png")
        self.assertEqual(self.store.image_ids(entry.id), [id1, id2, id3])
        self.assertEqual(self.store.count_entry_images(entry.id), 3)
        # erstes Bild = get_image (Rückwärtskompatibilität)
        self.assertEqual(self.store.get_image(entry.id), _png() + b"1")
        # einzelnes Bild per id
        self.assertEqual(self.store.get_image_by_id(id2), (_png() + b"2", "image/png"))
        # mittleres löschen
        self.store.delete_entry_image(id2)
        self.assertEqual(self.store.image_ids(entry.id), [id1, id3])

    def test_image_ids_map_bulk(self):
        e1 = LogEntry(timestamp="2026-07-05T10:00:00Z"); self.store.add(e1)
        e2 = LogEntry(timestamp="2026-07-05T11:00:00Z"); self.store.add(e2)
        a = self.store.add_entry_image(e1.id, _png(), "image/png")
        b = self.store.add_entry_image(e1.id, _png(), "image/png")
        c = self.store.add_entry_image(e2.id, _png(), "image/png")
        m = self.store.image_ids_map([e1.id, e2.id])
        self.assertEqual(m[e1.id], [a, b])
        self.assertEqual(m[e2.id], [c])

    def test_migration_single_to_multi(self):
        import sqlite3
        path = os.path.join(self.tmp.name, "old.sqlite3")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "timestamp TEXT, entry_type TEXT)")
        conn.execute("CREATE TABLE entry_images (entry_id INTEGER PRIMARY KEY, "
                     "image BLOB NOT NULL, mime TEXT DEFAULT 'image/png', "
                     "created_dz TEXT DEFAULT '')")
        conn.execute("INSERT INTO log_entries (timestamp, entry_type) VALUES ('t','manual')")
        conn.execute("INSERT INTO entry_images (entry_id, image, mime, created_dz) "
                     "VALUES (1, ?, 'image/png', '')", (_png(),))
        conn.commit(); conn.close()
        store = LogbookStore(path)                 # löst die Migration aus
        self.assertEqual(store.get_image(1), _png())
        # jetzt lassen sich weitere Bilder anhängen (vorher entry_id = PK)
        store.add_entry_image(1, _png() + b"x", "image/png")
        self.assertEqual(store.count_entry_images(1), 2)


class CaptureModuleTest(unittest.TestCase):
    def test_available_is_bool(self):
        self.assertIsInstance(plotter_capture.available(), bool)

    def test_grab_invalid_region_returns_none(self):
        self.assertIsNone(plotter_capture.grab_png(None))
        self.assertIsNone(plotter_capture.grab_png([0, 0, 0, 0]))
        self.assertIsNone(plotter_capture.grab_png([10, 10, 12]))  # falsche Länge

    def test_load_png_passthrough(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "a.png")
        with open(path, "wb") as h:
            h.write(_png())
        # PNG geht immer (auch ohne Pillow)
        self.assertEqual(plotter_capture.load_image_as_png(path), _png())

    def test_load_jpg_without_pillow_returns_none(self):
        if plotter_capture.available():
            self.skipTest("Pillow installiert — JPG würde konvertiert")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "a.jpg")
        with open(path, "wb") as h:
            h.write(b"\xff\xd8\xff\xe0" + b"\x00" * 40)  # JPEG-Signatur
        self.assertIsNone(plotter_capture.load_image_as_png(path))


if __name__ == "__main__":
    unittest.main()
