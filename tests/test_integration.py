"""End-to-End-Test: Simulator -> TCP-Quelle -> LiveData -> Logbuch."""

import os
import socket
import tempfile
import threading
import time
import unittest

from masarasi.livedata import LiveData
from masarasi.logbook import LogbookService
from masarasi.simulator import build_burst
from masarasi.source import NmeaSource
from masarasi.storage import LogbookStore


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _MiniServer:
    """Sendet ein paar Simulator-Bursts an den ersten Client."""

    def __init__(self, port: int) -> None:
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))
        self._srv.listen(1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        step = 0
        with conn:
            while not self._stop.is_set():
                try:
                    conn.sendall(build_burst(step).encode("ascii"))
                except OSError:
                    break
                step += 1
                time.sleep(0.05)

    def stop(self):
        self._stop.set()
        self._srv.close()


class IntegrationTest(unittest.TestCase):
    def test_stream_to_logbook(self):
        port = _free_port()
        server = _MiniServer(port)
        server.start()

        live = LiveData()
        source = NmeaSource("127.0.0.1", port, live, protocol="tcp")
        source.start()

        # Auf die ersten Live-Werte warten
        deadline = time.time() + 5.0
        while time.time() < deadline and not live.snapshot():
            time.sleep(0.05)

        snap = live.snapshot()
        self.assertIn("lat", snap)
        self.assertIn("sog_kn", snap)
        self.assertIn("aws_kn", snap)

        # Automatischen Eintrag schreiben
        tmpdir = tempfile.TemporaryDirectory()
        try:
            store = LogbookStore(os.path.join(tmpdir.name, "log.sqlite3"))
            service = LogbookService(store, live)
            entry = service.record_auto()
            self.assertIsNotNone(entry)
            self.assertEqual(store.count(), 1)
            self.assertIsNotNone(entry.lat)
        finally:
            source.stop()
            server.stop()
            tmpdir.cleanup()


if __name__ == "__main__":
    unittest.main()
