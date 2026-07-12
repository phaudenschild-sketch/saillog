#!/usr/bin/env python3
"""Orca-Core-Prober: schaut sich die HTTP-/WebSocket-Dienste an.

Der Orca Core bietet keinen NMEA0183-Server, sondern proprietäre HTTP-/
WebSocket-Dienste (für die Orca-App). Dieses Werkzeug fragt die HTTP-Ports ab
und schneidet die ersten WebSocket-Nachrichten mit, damit sichtbar wird, ob
dort brauchbare Daten (JSON) fließen.

    python orca_probe.py                 # Standard-Host 192.168.9.100
    python orca_probe.py 192.168.9.100
    python orca_probe.py 192.168.9.100 --ws-port 9000 --ws-path /
"""

import argparse
import base64
import os
import socket
import sys

HTTP_PORTS = [8080, 8085, 8088, 8090, 9001, 9081]
WS_PORTS = [9000, 8089, 9089]
WS_PATHS = ["/", "/ws", "/data", "/stream", "/api"]


def _printable(data: bytes, limit: int = 600) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else ("\n" if b in (10, 13) else ".")
                    for b in data[:limit])
    return text + ("…" if len(data) > limit else "")


def http_get(host: str, port: int, path: str = "/", timeout: float = 3.0,
             limit: int = 65536) -> bytes:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: saillog-probe\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            try:
                while len(data) < limit:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            return data
    except OSError as exc:
        return f"(Fehler: {exc})".encode()


# Gängige Nav-/Daten-Endpunkte, die eine Bord-API anbieten könnte
API_PORTS = [8080, 9001, 8085]
API_PATHS = [
    "/", "/version", "/status", "/health", "/info", "/state",
    # Navigation
    "/nav", "/navigation", "/navdata", "/gps", "/position", "/location",
    "/fix", "/data", "/instruments", "/instrument", "/sensors", "/sensor",
    "/wind", "/depth", "/speed", "/sog", "/cog", "/hdg",
    "/vessel", "/boat", "/telemetry", "/values", "/measurements",
    # IMU / Lage / Heading (Orca-Kern)
    "/imu", "/attitude", "/heading", "/orientation", "/euler", "/quaternion",
    "/roll", "/pitch", "/yaw", "/rot", "/motion", "/ahrs", "/compass",
    "/calibration", "/raw", "/imu/data", "/imu/raw", "/orientation/euler",
    # NMEA-2000 / CAN
    "/nmea", "/nmea2000", "/n2k", "/can", "/can0", "/pgn", "/pgns", "/bus",
    "/devices", "/sources", "/environment", "/engine", "/battery",
    "/ais", "/targets", "/autopilot", "/pilot", "/ap", "/route", "/waypoints",
    # Streams / Sammelendpunkte
    "/stream", "/live", "/realtime", "/all", "/log", "/logs", "/dump",
    # API-Präfixe
    "/api", "/api/v1", "/api/nav", "/api/data", "/api/status", "/api/gps",
    "/api/imu", "/api/attitude", "/api/heading", "/api/nmea", "/api/n2k",
    "/signalk", "/signalk/v1/api/vessels/self",
]


def _http_status_body(raw: bytes):
    """(Statuscode:int, Body:bytes) aus einer HTTP-Antwort."""
    head, _, body = raw.partition(b"\r\n\r\n")
    first = head.split(b"\r\n", 1)[0]
    parts = first.split(b" ")
    code = 0
    if len(parts) >= 2 and parts[1].isdigit():
        code = int(parts[1])
    return code, body


def scan_api(host: str) -> None:
    """Probiert gängige API-Pfade auf den HTTP-Ports und zeigt 200er-Antworten."""
    for port in API_PORTS:
        print(f"\n== API-Scan {host}:{port} ==")
        found = 0
        for path in API_PATHS:
            raw = http_get(host, port, path)
            if raw.startswith(b"(Fehler"):
                print(f"  {path}  → {raw.decode('utf-8','replace')}")
                break
            code, body = _http_status_body(raw)
            if code == 200:
                found += 1
                preview = _printable(body.strip(), 400).replace("\n", " ")
                print(f"  200  {path}\n       {preview}")
            elif code and code != 404:
                print(f"  {code}  {path}")
        if not found:
            print("  (keine 200-Antworten außer evtl. Fehlern)")


class _Buffered:
    """Gepufferter Socket-Leser (behält bereits gelesene Bytes)."""

    def __init__(self, sock, initial: bytes = b""):
        self._sock = sock
        self._buf = initial

    def read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def _ws_send_text(sock, text: str) -> None:
    """Sendet einen maskierten Text-Frame (Client->Server MUSS maskiert sein)."""
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    header = bytes([0x81])
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    sock.sendall(header + mask + masked)


# Kandidaten für ein 'subscribe'-Kommando (der Orca sendet {"event":...})
_SUBSCRIBE_TRIES = [
    '{"event":"subscribe"}',
    '{"event":"subscribe","data":"all"}',
    '{"subscribe":"all"}',
    '{"cmd":"subscribe"}',
    '{"type":"subscribe"}',
    '{"event":"start"}',
    '{"action":"subscribe","topics":["*"]}',
]


def _ws_read_frame(reader: _Buffered) -> bytes:
    """Liest einen (unmaskierten Server-)WebSocket-Frame; gibt Nutzdaten zurück."""
    _op, payload = _ws_frame(reader)
    return payload


def _ws_frame(reader: _Buffered):
    """Liest einen WebSocket-Frame und gibt (opcode, payload) zurück.

    Opcodes: 0x1=Text, 0x2=Binär, 0x8=Close, 0x9=Ping, 0xA=Pong.
    Server->Client-Frames sind normalerweise unmaskiert; wir demaskieren
    sicherheitshalber trotzdem."""
    hdr = reader.read(2)
    if len(hdr) < 2:
        return None, b""
    opcode = hdr[0] & 0x0F
    masked = bool(hdr[1] & 0x80)
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(reader.read(2), "big")
    elif length == 127:
        length = int.from_bytes(reader.read(8), "big")
    mask = reader.read(4) if masked else b""
    payload = reader.read(length)
    if masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _ws_send_op(sock, opcode: int, payload: bytes = b"") -> None:
    """Sendet einen (maskierten) Client-Frame mit beliebigem Opcode."""
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    header = bytes([0x80 | opcode])
    if n < 126:
        header += bytes([0x80 | n])
    elif n < 65536:
        header += bytes([0x80 | 126]) + n.to_bytes(2, "big")
    else:
        header += bytes([0x80 | 127]) + n.to_bytes(8, "big")
    sock.sendall(header + mask + masked)


def _ws_connect(host: str, port: int, path: str, timeout: float):
    """WebSocket-Handshake. Gibt (sock, reader) oder (None, statustext)."""
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        sock = socket.create_connection((host, port), timeout=4.0)
    except OSError as exc:
        return None, f"Verbindung fehlgeschlagen: {exc}"
    sock.sendall(req.encode())
    sock.settimeout(timeout)
    resp = b""
    try:
        while b"\r\n\r\n" not in resp and len(resp) < 4096:
            chunk = sock.recv(256)
            if not chunk:
                break
            resp += chunk
    except socket.timeout:
        pass
    head, _, leftover = resp.partition(b"\r\n\r\n")
    first = head.split(b"\r\n", 1)[0].decode("ascii", "ignore")
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        sock.close()
        return None, f"kein WebSocket-Upgrade ({first})"
    return sock, _Buffered(sock, leftover)


def ws_capture(host: str, port: int, path: str, seconds: float = 6.0,
               max_msgs: int = 8) -> None:
    sock, reader = _ws_connect(host, port, path, seconds)
    if sock is None:
        print(f"    {reader}")
        return
    try:
        print(f"    verbunden — lausche {int(seconds)} s …")
        got = 0
        try:
            while got < max_msgs:
                payload = _ws_read_frame(reader)
                if not payload:
                    break
                print(f"    ◀ {payload.decode('utf-8', 'replace')[:400]}")
                got += 1
        except (socket.timeout, OSError):
            pass
        if got == 0:
            print("    verbunden, aber keine Nachrichten (evtl. 'subscribe' nötig).")
    finally:
        sock.close()


_OPNAME = {0x0: "cont", 0x1: "text", 0x2: "bin", 0x8: "close", 0x9: "ping", 0xA: "pong"}


def ws_deep(host: str, port: int, path: str, seconds: float = 30.0,
            subscribe: bool = True) -> None:
    """Lauscht am WebSocket; hält per Pong die Verbindung offen.

    subscribe=True: schickt vorher 'subscribe'-Kandidaten.
    subscribe=False: sendet NICHTS (reines Mitlauschen — falls der Orca von
    selbst streamt und unsere Kommandos ihn nur stören).
    Behandelt WebSocket-Opcodes korrekt (Ping->Pong), zeigt Text als Text und
    Binär als Hex."""
    import time as _time
    sock, reader = _ws_connect(host, port, path, 2.0)
    if sock is None:
        print(f"    {reader}")
        return
    mode = f"sende {len(_SUBSCRIBE_TRIES)} subscribe-Versuche und " if subscribe else "lausche NUR (kein subscribe) — "
    print(f"    verbunden — {mode}{int(seconds)} s (mit Pong-Keepalive) …")
    if subscribe:
        for cmd in _SUBSCRIBE_TRIES:
            try:
                _ws_send_text(sock, cmd)
                print(f"    ▶ {cmd}")
            except OSError:
                break

    sock.settimeout(2.0)
    deadline = _time.monotonic() + seconds
    shown = 0
    text_kinds = {}
    bin_sizes = {}
    n_text = n_bin = n_ping = 0
    try:
        while _time.monotonic() < deadline:
            try:
                opcode, payload = _ws_frame(reader)
            except socket.timeout:
                continue
            if opcode is None:
                print("    (Verbindung vom Server geschlossen)")
                break
            if opcode == 0x9:                      # Ping -> Pong (Keepalive!)
                n_ping += 1
                try:
                    _ws_send_op(sock, 0xA, payload)
                except OSError:
                    break
                continue
            if opcode == 0xA:                      # Pong
                continue
            if opcode == 0x8:                      # Close
                print("    (Server hat die Verbindung geschlossen — Close-Frame)")
                break
            if opcode == 0x1:                      # Text
                n_text += 1
                text = payload.decode("utf-8", "replace")
                text_kinds[text[:60]] = text_kinds.get(text[:60], 0) + 1
                if shown < 60:
                    print(f"    ◀ TEXT: {text[:300]}")
                    shown += 1
            elif opcode == 0x2:                    # Binär
                n_bin += 1
                bin_sizes[len(payload)] = bin_sizes.get(len(payload), 0) + 1
                if shown < 60:
                    print(f"    ◀ BIN {len(payload):>4} B: {payload[:48].hex(' ')}")
                    shown += 1
    except OSError:
        pass
    finally:
        try:
            _ws_send_op(sock, 0x8)               # sauber schließen
        except OSError:
            pass
        sock.close()

    print(f"\n    → {n_text} Text, {n_bin} Binär, {n_ping} Ping empfangen.")
    if text_kinds:
        print("    Text-Ereignisse:")
        for tag, n in sorted(text_kinds.items(), key=lambda x: -x[1])[:10]:
            print(f"        {n:>4}×  {tag}")
    if bin_sizes:
        print("    Binär-Framegrößen (Bytes → Anzahl):")
        for size, n in sorted(bin_sizes.items()):
            print(f"        {size:>4} B  ×{n}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Orca-Core HTTP/WebSocket-Prober")
    parser.add_argument("host", nargs="?", default="192.168.9.100")
    parser.add_argument("--ws-port", type=int, default=None, help="nur diesen WS-Port")
    parser.add_argument("--ws-path", default=None, help="nur diesen WS-Pfad")
    parser.add_argument(
        "--deep", action="store_true",
        help="WebSocket 9000: subscribe-Kommandos senden und länger lauschen",
    )
    parser.add_argument(
        "--api", action="store_true",
        help="gängige REST-API-Pfade auf 8080/9001/8085 durchprobieren",
    )
    parser.add_argument(
        "--listen", action="store_true",
        help="WebSocket 9000: NUR lauschen (kein subscribe), lange, mit Keepalive",
    )
    parser.add_argument(
        "--seconds", type=float, default=30.0, help="Lauschdauer (Standard 30 s)",
    )
    parser.add_argument(
        "--fetch", metavar="PORT/PATH",
        help="einen einzelnen Pfad holen, z.B. 8080//nav  oder  9001//api/data",
    )
    args = parser.parse_args(argv)

    if args.fetch:
        port_str, _, path = args.fetch.partition("/")
        path = "/" + path.lstrip("/")
        print(f"== GET http://{args.host}:{port_str}{path} ==")
        raw = http_get(args.host, int(port_str), path)
        print(_printable(raw, 8000))
        return 0

    if args.api:
        print(f"== API-Endpunkt-Scan an {args.host} ==")
        scan_api(args.host)
        print("\nFertig. Ausgabe kopieren und schicken.")
        return 0

    if args.deep or args.listen:
        port = args.ws_port or 9000
        path = args.ws_path or "/"
        kind = "Nur-Lauschen" if args.listen else "Tiefen-Probe"
        print(f"== {kind} ws://{args.host}:{port}{path} ==")
        ws_deep(args.host, port, path, seconds=args.seconds,
                subscribe=not args.listen)
        print("\nFertig. Ausgabe kopieren und schicken.")
        return 0

    print(f"== HTTP-Dienste an {args.host} ==")
    for port in HTTP_PORTS:
        body = http_get(args.host, port)
        print(f"\n-- Port {port} GET / --")
        print("  " + _printable(body).replace("\n", "\n  "))

    print("\n== WebSocket-Dienste ==")
    ws_ports = [args.ws_port] if args.ws_port else WS_PORTS
    ws_paths = [args.ws_path] if args.ws_path else WS_PATHS
    for port in ws_ports:
        for path in ws_paths:
            print(f"\n-- ws://{args.host}:{port}{path} --")
            ws_capture(args.host, port, path)

    print("\nFertig. Ausgabe kopieren und an saillog/Claude schicken.")
    print("Tipp: 'python orca_probe.py 192.168.9.100 --deep' probiert subscribe-Kommandos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
