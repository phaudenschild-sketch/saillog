"""Tests für den ADB-Plotter-Screenshot (ohne echtes adb/Tablet)."""

import subprocess
import unittest

from masarasi import android_screencap as scr

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class DevicesTest(unittest.TestCase):
    def test_parses_device_list(self):
        out = "List of devices attached\nZX10108TA12E00234\tdevice\nemulator-5554\toffline\n"

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        orig = subprocess.run
        subprocess.run = fake_run
        try:
            devs = scr.devices()
        finally:
            subprocess.run = orig
        self.assertEqual(devs, [("ZX10108TA12E00234", "device"),
                                ("emulator-5554", "offline")])

    def test_devices_empty_on_error(self):
        def boom(cmd, **kw):
            raise OSError("adb not found")

        orig = subprocess.run
        subprocess.run = boom
        try:
            self.assertEqual(scr.devices(), [])
        finally:
            subprocess.run = orig


class CaptureTest(unittest.TestCase):
    def test_capture_returns_png_bytes(self):
        def fake_run(cmd, **kw):
            self.assertIn("screencap", cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=_PNG, stderr=b"")

        orig = subprocess.run
        subprocess.run = fake_run
        try:
            data = scr.capture_png()
        finally:
            subprocess.run = orig
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_capture_rejects_non_png(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=b"error: no devices", stderr=b"")

        orig = subprocess.run
        subprocess.run = fake_run
        try:
            self.assertIsNone(scr.capture_png())
        finally:
            subprocess.run = orig

    def test_capture_serial_in_command(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=_PNG, stderr=b"")

        orig = subprocess.run
        subprocess.run = fake_run
        try:
            scr.capture_png(adb_path="adb", serial="ZX10108TA12E00234")
        finally:
            subprocess.run = orig
        self.assertIn("-s", seen["cmd"])
        self.assertIn("ZX10108TA12E00234", seen["cmd"])


class WlanTest(unittest.TestCase):
    def _patch(self, fn):
        orig = subprocess.run
        subprocess.run = fn
        self.addCleanup(lambda: setattr(subprocess, "run", orig))

    def test_connect_success(self):
        self._patch(lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="connected to 192.168.9.215:5555\n", stderr=""))
        ok, msg = scr.connect("192.168.9.215:5555")
        self.assertTrue(ok)
        self.assertIn("connected to", msg)

    def test_connect_already(self):
        self._patch(lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="already connected to 192.168.9.215:5555\n", stderr=""))
        ok, _ = scr.connect("192.168.9.215:5555")
        self.assertTrue(ok)

    def test_connect_failure(self):
        self._patch(lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="cannot connect: connection refused"))
        ok, _ = scr.connect("192.168.9.215:5555")
        self.assertFalse(ok)

    def test_wlan_ip_parsed(self):
        out = "2: wlan0    inet 192.168.9.215/24 brd 192.168.9.255 scope global wlan0\n"
        self._patch(lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=""))
        self.assertEqual(scr.wlan_ip(), "192.168.9.215")

    def test_capture_autoconnects_for_network_serial(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "connect" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="connected to x", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=_PNG, stderr=b"")

        self._patch(fake_run)
        scr.capture_png(serial="192.168.9.215:5555")
        self.assertTrue(any("connect" in c for c in calls),
                        "network serial should trigger adb connect first")


if __name__ == "__main__":
    unittest.main()
