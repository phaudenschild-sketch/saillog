"""Tests für die Mehrsprachigkeit (i18n)."""

import json
import tempfile
import unittest
from pathlib import Path

from saillog import i18n


class I18nTest(unittest.TestCase):
    def setUp(self):
        # Kontrollierten Katalog-Ordner unterschieben
        self._orig_dir = i18n._LANG_DIR
        self._tmp = tempfile.TemporaryDirectory()
        i18n._LANG_DIR = Path(self._tmp.name)
        (i18n._LANG_DIR / "en.json").write_text(json.dumps({
            "__language__": "English",
            "Speichern": "Save",
            "Motor {rpm} U/min": "Engine {rpm} rpm",
            "Länge": "Longitude",
            "ship\x04Länge": "Length",
        }), encoding="utf-8")
        (i18n._LANG_DIR / "de.json").write_text(json.dumps({
            "__language__": "Deutsch",
        }), encoding="utf-8")

    def tearDown(self):
        i18n._LANG_DIR = self._orig_dir
        i18n.set_language("de")
        self._tmp.cleanup()

    def test_default_is_german_original(self):
        i18n.set_language("de")
        self.assertEqual(i18n.t("Speichern"), "Speichern")

    def test_translation(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("Speichern"), "Save")

    def test_missing_key_falls_back_to_original(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("Unbekannter Text"), "Unbekannter Text")

    def test_format_placeholders(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("Motor {rpm} U/min", rpm=1785), "Engine 1785 rpm")
        i18n.set_language("de")
        self.assertEqual(i18n.t("Motor {rpm} U/min", rpm=1785), "Motor 1785 U/min")

    def test_available_languages(self):
        langs = i18n.available_languages()
        self.assertEqual(langs.get("en"), "English")
        self.assertIn("de", langs)

    def test_unknown_language_behaves_like_german(self):
        i18n.set_language("xx")
        self.assertEqual(i18n.t("Speichern"), "Speichern")

    def test_context_disambiguates_homographs(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("Länge"), "Longitude")              # Position
        self.assertEqual(i18n.t("Länge", _ctx="ship"), "Length")    # Bootslänge
        # Fehlender Kontext-Schlüssel -> deutscher Originaltext
        self.assertEqual(i18n.t("Breite", _ctx="ship"), "Breite")
        i18n.set_language("de")
        self.assertEqual(i18n.t("Länge", _ctx="ship"), "Länge")


if __name__ == "__main__":
    unittest.main()
