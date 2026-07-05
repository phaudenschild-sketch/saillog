"""Tests für den Discovery-Scanner."""

import socket
import threading
import time
import unittest

from masarasi.discover import probe_tcp, probe_udp
from masarasi.simulator import build_burst


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class SentenceTypeTest(unittest.TestCase):
    def test_counts_types(self):
        from masarasi.discover import _sentence_types

        data = build_burst(0).encode("ascii")
        types = _sentence_types(data)
        self.assertIn("RMC", types)
        self.assertIn("MWV", types)
        self.assertIn("DPT", types)


class ProbeTcpTest(unittest.TestCase):
    def test_detects_nmea_source(self):
        port = _free_port()
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)

        def serve():
            try:
                conn, _ = srv.accept()
                with conn:
                    conn.sendall(build_burst(0).encode("ascii"))
                    time.sleep(0.2)
            except OSError:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            result = probe_tcp("127.0.0.1", port, listen_seconds=1.0)
            self.assertIsNotNone(result)
            self.assertIn("RMC", result)
        finally:
            srv.close()

    def test_closed_port_returns_none(self):
        port = _free_port()  # niemand lauscht hier
        self.assertIsNone(probe_tcp("127.0.0.1", port, listen_seconds=0.5))


class GoFreeAnnouncementTest(unittest.TestCase):
    def test_parses_services(self):
        from masarasi.discover import parse_gofree_announcement
        import json

        payload = json.dumps({
            "Name": "Zeus3-9inch",
            "Model": "Zeus3",
            "IP": "192.168.9.224",
            "Services": [
                {"Service": "nmea-0183", "Version": 1, "Port": 10110},
                {"Service": "websocket", "Version": 2, "Port": 443},
            ],
        }).encode("utf-8")
        ann = parse_gofree_announcement(payload)
        self.assertEqual(ann["model"], "Zeus3")
        self.assertEqual(ann["ip"], "192.168.9.224")
        names = {s["name"] for s in ann["services"]}
        self.assertIn("nmea-0183", names)
        ports = {s["port"] for s in ann["services"]}
        self.assertIn(10110, ports)
        self.assertIn(443, ports)

    def test_rejects_non_json(self):
        from masarasi.discover import parse_gofree_announcement
        self.assertIsNone(parse_gofree_announcement(b"$GPRMC,not json"))


class ProbeUdpTest(unittest.TestCase):
    def test_receives_broadcast(self):
        port = _free_port()
        stop = threading.Event()

        def send():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            while not stop.is_set():
                sock.sendto(build_burst(0).encode("ascii"), ("127.0.0.1", port))
                time.sleep(0.1)
            sock.close()

        thread = threading.Thread(target=send, daemon=True)
        thread.start()
        try:
            result = probe_udp(port, listen_seconds=1.0)
            self.assertIsNotNone(result)
            self.assertIn("RMC", result)
        finally:
            stop.set()


if __name__ == "__main__":
    unittest.main()
