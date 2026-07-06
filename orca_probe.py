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


def http_get(host: str, port: int, path: str = "/", timeout: float = 3.0) -> bytes:
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                "User-Agent: masarasi-probe\r\nConnection: close\r\n\r\n".encode()
            )
            data = b""
            try:
                while len(data) < 4096:
                    chunk = sock.recv(4096 - len(data))
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            return data
    except OSError as exc:
        return f"(Fehler: {exc})".encode()


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
    hdr = reader.read(2)
    if len(hdr) < 2:
        return b""
    length = hdr[1] & 0x7F
    if length == 126:
        length = int.from_bytes(reader.read(2), "big")
    elif length == 127:
        length = int.from_bytes(reader.read(8), "big")
    return reader.read(length)


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


def ws_deep(host: str, port: int, path: str, seconds: float = 20.0) -> None:
    """Sendet 'subscribe'-Kandidaten und lauscht länger; zeigt alle Frames."""
    import time as _time
    sock, reader = _ws_connect(host, port, path, 2.0)
    if sock is None:
        print(f"    {reader}")
        return
    print(f"    verbunden — sende {len(_SUBSCRIBE_TRIES)} subscribe-Versuche und "
          f"lausche {int(seconds)} s …")
    for cmd in _SUBSCRIBE_TRIES:
        try:
            _ws_send_text(sock, cmd)
            print(f"    ▶ {cmd}")
        except OSError:
            break
    sock.settimeout(seconds)
    count = 0
    kinds = {}
    try:
        while True:
            payload = _ws_read_frame(reader)
            if not payload:
                break
            count += 1
            text = payload.decode("utf-8", "replace")
            # Ereignistyp merken (falls JSON {"event":...})
            tag = text[:60]
            kinds[tag] = kinds.get(tag, 0) + 1
            if count <= 40:
                print(f"    ◀ {text[:300]}")
    except (socket.timeout, OSError):
        pass
    finally:
        sock.close()
    print(f"    → {count} Nachricht(en) gesamt. Häufigste:")
    for tag, n in sorted(kinds.items(), key=lambda x: -x[1])[:10]:
        print(f"        {n:>4}×  {tag}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Orca-Core HTTP/WebSocket-Prober")
    parser.add_argument("host", nargs="?", default="192.168.9.100")
    parser.add_argument("--ws-port", type=int, default=None, help="nur diesen WS-Port")
    parser.add_argument("--ws-path", default=None, help="nur diesen WS-Pfad")
    parser.add_argument(
        "--deep", action="store_true",
        help="WebSocket 9000: subscribe-Kommandos senden und länger lauschen",
    )
    args = parser.parse_args(argv)

    if args.deep:
        port = args.ws_port or 9000
        path = args.ws_path or "/"
        print(f"== Tiefen-Probe ws://{args.host}:{port}{path} ==")
        ws_deep(args.host, port, path)
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

    print("\nFertig. Ausgabe kopieren und an masarasi/Claude schicken.")
    print("Tipp: 'python orca_probe.py 192.168.9.100 --deep' probiert subscribe-Kommandos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
