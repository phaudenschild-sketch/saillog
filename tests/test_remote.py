"""Tests für die Handy-/Tablet-Fern-Erfassung (HTTP-Server)."""

import unittest
import urllib.request
import urllib.error
import urllib.parse
from http.cookiejar import CookieJar

from saillog.remote import RemoteServer


class RemoteServerTest(unittest.TestCase):
    def setUp(self):
        self.submitted = []
        self.info = {
            "trip": "Sommer 2026",
            "measurements": {"lat": 43.02, "lon": 16.71, "sog_kn": 5.6,
                             "cog_deg": 230.0, "tws_kn": 8.2, "depth_m": 12.3},
            "conditions": {"engine_mode": "aus", "mainsail": "Geborgen",
                           "genoa_percent": 0, "cloud_cover": "wolkenlos",
                           "precipitation": "", "visibility": "gut"},
        }

        self.info["logevents"] = ["Ablegen", "Anlegen", "Wende", "Halse"]

        def submit(conditions):
            self.submitted.append(conditions)
            return {"time": "2026-07-21 08:00", "lat": 43.02, "lon": 16.71,
                    "logevent": conditions.get("logevent", "")}

        self.server = RemoteServer(lambda: self.info, submit, pin="1234",
                                   host="127.0.0.1", port=0, icon_png=b"\x89PNG_test")
        self.server.start()
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def _opener(self):
        return urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar()),
            urllib.request.HTTPRedirectHandler(),
        )

    def _get(self, opener, path="/"):
        with opener.open(self.base + path) as r:
            return r.status, r.read().decode("utf-8")

    def _post(self, opener, path, data):
        body = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in data.items())
        req = urllib.request.Request(self.base + path, data=body.encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        with opener.open(req) as r:
            return r.status, r.read().decode("utf-8")

    def test_requires_pin(self):
        op = self._opener()
        _, html = self._get(op, "/")
        self.assertIn("PIN", html)
        self.assertNotIn("Neuer Eintrag", html)

    def test_wrong_pin_rejected(self):
        op = self._opener()
        try:
            self._post(op, "/login", {"pin": "0000"})
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)
        else:
            self.fail("falsche PIN hätte 401 geben müssen")

    def test_login_then_form_and_submit(self):
        op = self._opener()
        # richtige PIN -> danach Formular erreichbar
        self._post(op, "/login", {"pin": "1234"})
        _, form = self._get(op, "/")
        self.assertIn("Neuer Eintrag", form)
        self.assertIn("Sommer 2026", form)          # Törn im Live-Kopf
        self.assertIn("Geborgen", form)             # Bedingung vorbelegt
        # Eintrag absenden
        _, res = self._post(op, "/entry",
                            {"logevent": "Manöver", "note": "Test", "engine_mode": "aus",
                             "mainsail": "Geborgen", "genoa": "0", "cloud": "wolkenlos",
                             "precip": "kein", "visibility": "gut", "wave": "0.3"})
        self.assertIn("gespeichert", res)
        self.assertEqual(len(self.submitted), 1)
        c = self.submitted[0]
        self.assertEqual(c["logevent"], "Manöver")
        self.assertEqual(c["note"], "Test")
        self.assertEqual(c["genoa_percent"], 0.0)
        self.assertEqual(c["wave_height_m"], 0.3)

    def test_custom_logevents_in_form(self):
        op = self._opener()
        self._post(op, "/login", {"pin": "1234"})
        _, form = self._get(op, "/")
        self.assertIn("Ablegen", form)
        self.assertIn("Halse", form)
        self.assertNotIn(">Besonderes<", form)   # alte Liste nicht mehr da

    def test_manifest_and_icon_without_login(self):
        op = self._opener()
        # ohne Login erreichbar (Browser lädt sie vor der Anmeldung)
        _, manifest = self._get(op, "/manifest.webmanifest")
        self.assertIn('"standalone"', manifest)
        self.assertIn('SailLog', manifest)
        with op.open(self.base + "/icon.png") as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get("Content-Type"), "image/png")
            self.assertTrue(r.read().startswith(b"\x89PNG"))

    def test_entry_without_login_blocked(self):
        op = self._opener()
        # ohne Login POST /entry -> Redirect auf Login, submit NICHT aufgerufen
        self._post(op, "/entry", {"logevent": "Manöver"})
        self.assertEqual(len(self.submitted), 0)


if __name__ == "__main__":
    import urllib.parse  # noqa
    unittest.main()
