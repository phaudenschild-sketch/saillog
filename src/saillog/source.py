"""Netzwerk-Quelle: liest den NMEA0183-Stream vom WLAN/LAN-Gateway.

Läuft in einem eigenen Daemon-Thread, verbindet sich per TCP oder UDP,
zerlegt eingehende Zeilen mit dem NmeaParser und schreibt die Messwerte
in den LiveData-Speicher. Bei Verbindungsabbruch wird automatisch neu
verbunden.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from saillog.livedata import LiveData
from saillog.nmea import NmeaParser, valid_checksum

# Übliche Baudraten von GPS-Mäusen/USB-NMEA-Adaptern, in Reihenfolge der
# Wahrscheinlichkeit — für die automatische Baudraten-Erkennung (Baud = 0/leer).
_SERIAL_BAUDS = (9600, 4800, 38400, 115200, 19200, 57600)
_GPS_ADDRESSES = ("RMC", "GGA", "GLL", "VTG")

# Status-Konstanten
STATUS_DISCONNECTED = "disconnected"
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_ERROR = "error"

StatusCallback = Callable[[str, str], None]  # (status, meldung)


class NmeaSource:
    """Verbindet sich mit dem Gateway und speist LiveData."""

    def __init__(
        self,
        host: str,
        port: int,
        live: LiveData,
        protocol: str = "tcp",
        on_status: Optional[StatusCallback] = None,
        on_raw: Optional[Callable[[str], None]] = None,
        on_ais: Optional[Callable[[str], None]] = None,
        reconnect_delay: float = 3.0,
        log_correction: float = 1.0,
        priority: int = 1,
    ) -> None:
        self._host = host
        # Priorität beim Zusammenführen mehrerer Quellen (je höher, desto
        # bevorzugter). Eine gestartete Quelle hat mindestens 1.
        self._priority = max(1, int(priority))
        try:
            self._port = int(port)          # Baud (seriell) bzw. Port (TCP/UDP)
        except (TypeError, ValueError):
            self._port = 0                  # 0/„auto" -> Baudrate selbst erkennen
        self._protocol = protocol.lower()
        self._live = live
        self._parser = NmeaParser()
        self._on_status = on_status
        self._on_raw = on_raw
        self._on_ais = on_ais
        self._reconnect_delay = reconnect_delay
        # Korrekturfaktor des Loggebers (Kalibrierung); wirkt auf Fahrt durchs
        # Wasser (STW) und den Gesamtlog. 1.0 = keine Korrektur.
        self.log_correction = float(log_correction) if log_correction else 1.0

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._status = STATUS_DISCONNECTED

    # --- öffentliche Steuerung ---------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    @property
    def status(self) -> str:
        return self._status

    # --- interne Logik ------------------------------------------------------

    def _set_status(self, status: str, message: str = "") -> None:
        self._status = status
        if self._on_status is not None:
            self._on_status(status, message)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._protocol == "udp":
                    self._run_udp()
                elif self._protocol == "serial":
                    self._run_serial()
                else:
                    self._run_tcp()
            except OSError as exc:
                self._set_status(STATUS_ERROR, str(exc))
            if self._stop.is_set():
                break
            # Vor dem Reconnect kurz warten
            self._stop.wait(self._reconnect_delay)
        self._set_status(STATUS_DISCONNECTED, "getrennt")

    def _run_serial(self) -> None:
        # Serieller COM-Port: GPS-Maus (USB) oder NMEA-Adapter (z.B. Maretron
        # USB100). Host = COM-Port, Port = Baud. Baud 0/leer -> automatisch
        # erkennen (praktisch für GPS-Mäuse, deren Baudrate man selten kennt).
        try:
            import serial  # pyserial
        except ImportError:
            self._set_status(
                STATUS_ERROR, "pyserial fehlt — 'pip install pyserial'"
            )
            self._stop.wait(5.0)
            return

        if self._port and self._port > 0:
            self._set_status(
                STATUS_CONNECTING, f"öffne {self._host} @ {self._port} Baud"
            )
            ser = serial.Serial(self._host, self._port, timeout=1.0)
        else:
            self._set_status(
                STATUS_CONNECTING, f"suche Baudrate an {self._host} …"
            )
            ser = self._open_autobaud(serial)
            if ser is None:
                self._set_status(
                    STATUS_ERROR, f"kein NMEA an {self._host} erkannt"
                )
                self._stop.wait(3.0)
                return

        self._sock = ser  # zum Schließen in stop()
        try:
            self._set_status(
                STATUS_CONNECTED,
                f"verbunden (seriell @ {ser.baudrate} Baud)"
            )
            buffer = b""
            while not self._stop.is_set():
                chunk = ser.read(256)
                if chunk:
                    buffer += chunk
                    buffer = self._consume(buffer)
        finally:
            self._sock = None
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    def _open_autobaud(self, serial_mod, probe_seconds: float = 2.5):
        """Probiert übliche Baudraten durch und liefert einen offenen Port, an
        dem gültige GPS-NMEA-Sätze ankommen (sonst None)."""
        for baud in _SERIAL_BAUDS:
            if self._stop.is_set():
                return None
            try:
                ser = serial_mod.Serial(self._host, baud, timeout=0.5)
            except Exception:  # noqa: BLE001 - Port nicht öffenbar -> abbrechen
                return None
            self._set_status(
                STATUS_CONNECTING, f"prüfe {self._host} @ {baud} Baud …"
            )
            if self._probe_gps(ser, seconds=probe_seconds):
                return ser
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass
        return None

    def _probe_gps(self, ser, seconds: float = 2.5) -> bool:
        """True, wenn innerhalb weniger Sekunden ein gültiger GPS-Satz
        (RMC/GGA/GLL/VTG mit korrekter Prüfsumme) eintrifft."""
        deadline = time.time() + seconds
        buffer = b""
        while time.time() < deadline and not self._stop.is_set():
            try:
                chunk = ser.read(128)
            except Exception:  # noqa: BLE001
                return False
            if not chunk:
                continue
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                text = line.decode("ascii", "ignore").strip()
                if len(text) < 7 or text[0] not in "$!" or "," not in text:
                    continue
                if not valid_checksum(text):
                    continue
                address = text[1:text.find(",")]
                if address[-3:] in _GPS_ADDRESSES:
                    return True
        return False

    def _run_tcp(self) -> None:
        self._set_status(STATUS_CONNECTING, f"verbinde mit {self._host}:{self._port}")
        with socket.create_connection((self._host, self._port), timeout=5.0) as sock:
            self._sock = sock
            sock.settimeout(5.0)
            self._set_status(STATUS_CONNECTED, "verbunden")
            buffer = b""
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    raise OSError("Verbindung vom Gateway geschlossen")
                buffer += chunk
                buffer = self._consume(buffer)
        self._sock = None

    def _run_udp(self) -> None:
        self._set_status(STATUS_CONNECTING, f"lausche auf UDP {self._port}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self._port))
            sock.settimeout(5.0)
            self._sock = sock
            self._set_status(STATUS_CONNECTED, "empfange UDP")
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                # Ein UDP-Datagramm enthält vollständige Sätze -> alle Zeilen
                for line in data.replace(b"\r", b"\n").split(b"\n"):
                    self._handle_line(line)
        finally:
            self._sock = None
            sock.close()

    def _consume(self, buffer: bytes) -> bytes:
        """Verarbeitet vollständige Zeilen; gibt den Rest-Puffer zurück."""
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            self._handle_line(line)
        return buffer

    def _handle_line(self, raw: bytes) -> None:
        try:
            text = raw.decode("ascii", errors="ignore").strip()
        except Exception:
            return
        if not text:
            return
        if self._on_raw is not None:
            self._on_raw(text)
        # AIS-Sätze getrennt behandeln (der NMEA-Parser kennt sie nicht).
        if self._on_ais is not None and (
            text.startswith("!AIVDM") or text.startswith("!AIVDO")
        ):
            try:
                self._on_ais(text)
            except Exception:  # noqa: BLE001 - eine kaputte Zeile darf nicht stören
                pass
            return
        values = self._parser.parse(text)
        if values:
            if self.log_correction != 1.0:
                for key in ("stw_kn", "log_total_nm"):
                    if values.get(key) is not None:
                        values[key] = values[key] * self.log_correction
            self._live.update(values, priority=self._priority)
