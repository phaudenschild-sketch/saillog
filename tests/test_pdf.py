"""Tests für den PDF-Export (HTML -> Chromium headless -> PDF)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from saillog import pdf


class FindBrowserTest(unittest.TestCase):
    def test_explicit_existing_wins(self):
        with tempfile.NamedTemporaryFile(suffix=".exe") as f:
            self.assertEqual(pdf.find_browser(f.name), f.name)

    def test_explicit_missing_falls_through_to_none(self):
        # Kein Browser im PATH/Standardort (Testumgebung) -> None
        with mock.patch.object(pdf, "shutil") as sh, \
             mock.patch.object(pdf, "_PATH_NAMES", []):
            sh.which.return_value = None
            with mock.patch.object(pdf.os, "name", "posix"), \
                 mock.patch.object(pdf, "_macos_candidates", return_value=[]):
                self.assertIsNone(pdf.find_browser("/does/not/exist"))

    def test_env_override(self):
        with tempfile.NamedTemporaryFile(suffix=".exe") as f:
            with mock.patch.dict(os.environ, {"TRIPLOG_BROWSER": f.name}):
                self.assertEqual(pdf.find_browser(), f.name)


class PdfHelpersTest(unittest.TestCase):
    def test_looks_like_pdf(self):
        d = tempfile.mkdtemp()
        good = os.path.join(d, "a.pdf")
        Path(good).write_bytes(b"%PDF-1.4\n...")
        bad = os.path.join(d, "b.pdf")
        Path(bad).write_bytes(b"<html>")
        self.assertTrue(pdf._looks_like_pdf(good))
        self.assertFalse(pdf._looks_like_pdf(bad))
        self.assertFalse(pdf._looks_like_pdf(os.path.join(d, "missing.pdf")))

    def test_html_to_pdf_without_browser_returns_false(self):
        out = os.path.join(tempfile.mkdtemp(), "x.pdf")
        with mock.patch.object(pdf, "find_browser", return_value=None):
            self.assertFalse(pdf.html_to_pdf("<html></html>", out))
        self.assertFalse(os.path.exists(out))

    def test_html_to_png_without_browser_returns_false(self):
        out = os.path.join(tempfile.mkdtemp(), "x.png")
        with mock.patch.object(pdf, "find_browser", return_value=None):
            self.assertFalse(pdf.html_to_png("<html></html>", out))
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
