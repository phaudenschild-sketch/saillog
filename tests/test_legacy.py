"""Tests für die Analyse alter Logbuch-Sicherungen."""

import os
import sqlite3
import struct
import tempfile
import unittest
import zlib
import zipfile

from saillog.legacy import (
    detect_type,
    extract_images,
    image_ext,
    inspect_path,
)


def _png() -> bytes:
    """Erzeugt ein minimales gültiges 1x1-PNG."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


class DetectTypeTest(unittest.TestCase):
    def test_image_ext(self):
        self.assertEqual(image_ext(_png()), "png")
        self.assertEqual(image_ext(b"\xff\xd8\xff\xe0rest"), "jpg")
        self.assertIsNone(image_ext(b"not an image"))

    def test_detect_types(self):
        self.assertEqual(detect_type(b"SQLite format 3\x00rest"), "sqlite")
        self.assertEqual(detect_type(b"PK\x03\x04rest"), "zip")
        self.assertEqual(detect_type(_png()), "image/png")
        self.assertEqual(detect_type(b'{"a": 1}'), "json")
        self.assertEqual(detect_type(b"<?xml version=\"1.0\"?>"), "xml")


class InspectSqliteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "logbuch.sl3")
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE trip_log (id INTEGER PRIMARY KEY, utc TEXT, lat REAL, "
            "lon REAL, note TEXT)"
        )
        conn.execute(
            "INSERT INTO trip_log (utc, lat, lon, note) VALUES (?,?,?,?)",
            ("2024-06-01T08:00:00Z", 47.5, 9.4, "Ablegen Romanshorn"),
        )
        conn.execute("CREATE TABLE screenshots (id INTEGER PRIMARY KEY, image BLOB)")
        conn.execute("INSERT INTO screenshots (image) VALUES (?)", (_png(),))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_inspect_reports_tables_and_text(self):
        report = inspect_path(self.db)
        self.assertIn("trip_log", report)
        self.assertIn("screenshots", report)
        self.assertIn("Ablegen Romanshorn", report)  # TEXT als Text, nicht BLOB
        self.assertIn("Bilder in Spalte(n): image", report)
        self.assertIn("BILD/png", report)

    def test_extract_images_from_sqlite(self):
        out = os.path.join(self.tmp.name, "bilder")
        count = extract_images(self.db, out)
        self.assertEqual(count, 1)
        files = os.listdir(out)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith(".png"))


class InspectZipTest(unittest.TestCase):
    def test_zip_with_image(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        zpath = os.path.join(tmp.name, "backup.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("plotter/screen1.png", _png())
            zf.writestr("data/log.csv", "utc,lat,lon\n2024-06-01,47.5,9.4\n")
        report = inspect_path(zpath)
        self.assertIn("ZIP mit 2 Einträgen", report)
        self.assertIn("screen1.png", report)
        out = os.path.join(tmp.name, "bilder")
        self.assertEqual(extract_images(zpath, out), 1)


class InspectDirTest(unittest.TestCase):
    def test_directory_summary(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "a.png"), "wb") as h:
            h.write(_png())
        with open(os.path.join(tmp.name, "log.txt"), "w") as h:
            h.write("hallo")
        report = inspect_path(tmp.name)
        self.assertIn("Ordner mit 2 Datei(en)", report)
        out = os.path.join(tmp.name, "out")
        self.assertEqual(extract_images(tmp.name, out), 1)


if __name__ == "__main__":
    unittest.main()
