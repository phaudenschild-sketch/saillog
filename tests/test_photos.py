"""Tests für den Foto-Import (Ordner-Watcher + Verarbeitung)."""

import os
import tempfile
import unittest
from pathlib import Path

from masarasi import photos


class ResizeAvailabilityTest(unittest.TestCase):
    def test_available_is_bool(self):
        self.assertIsInstance(photos.available(), bool)

    def test_resize_missing_file_returns_none(self):
        # Fehlende Datei / kein Pillow -> None, kein Absturz.
        self.assertIsNone(photos.resize_to_jpeg("/nicht/vorhanden.jpg"))


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._got = []
        # resize durch einen Stub ersetzen (kein Pillow im Test nötig)
        self._orig_resize = photos.resize_to_jpeg
        photos.resize_to_jpeg = lambda path, max_px=1600, quality=82: b"JPEGDATA"

    def tearDown(self):
        photos.resize_to_jpeg = self._orig_resize
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def _watcher(self):
        return photos.PhotoWatcher(self._dir, on_photo=lambda b, n: self._got.append((b, n)))

    def _write(self, name, data=b"x"):
        Path(self._dir, name).write_bytes(data)

    def test_stable_file_processed_on_second_scan(self):
        w = self._watcher()
        self._write("foto1.jpg", b"abc")
        # 1. Scan: nur registriert (Stabilitätsprüfung), noch nicht verarbeitet
        self.assertEqual(w.scan_once(), 0)
        self.assertEqual(self._got, [])
        # 2. Scan: unveränderte Größe -> verarbeitet, Original verschoben
        self.assertEqual(w.scan_once(), 1)
        self.assertEqual(self._got, [(b"JPEGDATA", "foto1.jpg")])
        self.assertFalse(Path(self._dir, "foto1.jpg").exists())
        self.assertTrue(Path(self._dir, "verarbeitet", "foto1.jpg").exists())

    def test_growing_file_waits_until_stable(self):
        w = self._watcher()
        self._write("big.jpg", b"a")
        self.assertEqual(w.scan_once(), 0)      # registriert
        self._write("big.jpg", b"aaaa")         # noch am Kopieren (größer)
        self.assertEqual(w.scan_once(), 0)      # Größe geändert -> warten
        self.assertEqual(w.scan_once(), 1)      # jetzt stabil -> verarbeitet

    def test_non_image_ignored(self):
        w = self._watcher()
        self._write("notiz.txt", b"hallo")
        w.scan_once()
        self.assertEqual(w.scan_once(), 0)
        self.assertEqual(self._got, [])

    def test_no_duplicate_after_processing(self):
        w = self._watcher()
        self._write("a.png")
        w.scan_once()
        w.scan_once()
        # Original ist weg -> weitere Scans lösen nichts mehr aus
        self.assertEqual(w.scan_once(), 0)
        self.assertEqual(len(self._got), 1)


if __name__ == "__main__":
    unittest.main()
