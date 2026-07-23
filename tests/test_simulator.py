"""Test für den eingebetteten Demo-Datenbus (Simulator in der GUI)."""

import socket
import time
import unittest

from saillog import simulator
from saillog.nmea import NmeaParser


class DemoBusTest(unittest.TestCase):
    def test_start_demo_bus_streams_valid_nmea(self):
        server = simulator.start_demo_bus(host="127.0.0.1", port=2177, period=0.05)
        self.addCleanup(server.close)
        # kurz warten, bis der Accept-Thread lauscht
        time.sleep(0.1)
        client = socket.create_connection(("127.0.0.1", 2177), timeout=2.0)
        self.addCleanup(client.close)
        client.settimeout(2.0)
        data = b""
        deadline = time.time() + 2.0
        while b"\n" not in data and time.time() < deadline:
            data += client.recv(4096)
        text = data.decode("ascii", "replace")
        # Es kommen vollständige NMEA-Sätze an …
        self.assertIn("$", text)
        parser = NmeaParser()
        parsed = {}
        for line in text.splitlines():
            if line.startswith("$"):
                parsed.update(parser.parse(line))
        # … und sie ergeben plausible Messwerte (mind. Position oder SOG).
        self.assertTrue(
            any(k in parsed for k in ("lat", "lon", "sog_kn", "cog_deg", "depth_m")),
            f"keine verwertbaren Messwerte im Demo-Stream: {parsed}",
        )


if __name__ == "__main__":
    unittest.main()
