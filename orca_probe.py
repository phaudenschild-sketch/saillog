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


def ws_capture(host: str, port: int, path: str, seconds: float = 6.0,
               max_msgs: int = 8) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    try:
        sock = socket.create_connection((host, port), timeout=4.0)
    except OSError as exc:
        print(f"    Verbindung fehlgeschlagen: {exc}")
        return
    try:
        sock.sendall(req.encode())
        sock.settimeout(seconds)
        resp = b""
        while b"\r\n\r\n" not in resp and len(resp) < 4096:
            chunk = sock.recv(256)
            if not chunk:
                break
            resp += chunk
        head, _, leftover = resp.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("ascii", "ignore")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            print(f"    kein WebSocket-Upgrade ({status})")
            return
        print(f"    verbunden ({status}) — lausche {int(seconds)} s …")
        reader = _Buffered(sock, leftover)  # evtl. schon mitgelesene Frame-Bytes
        got = 0
        try:
            while got < max_msgs:
                payload = _ws_read_frame(reader)
                if not payload:
                    break
                text = payload.decode("utf-8", "replace")
                print(f"    ◀ {text[:400]}")
                got += 1
        except socket.timeout:
            pass
        if got == 0:
            print("    verbunden, aber keine Nachrichten — evtl. erst nach einem")
            print("    'subscribe'-Kommando (App-Protokoll).")
    finally:
        sock.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Orca-Core HTTP/WebSocket-Prober")
    parser.add_argument("host", nargs="?", default="192.168.9.100")
    parser.add_argument("--ws-port", type=int, default=None, help="nur diesen WS-Port")
    parser.add_argument("--ws-path", default=None, help="nur diesen WS-Pfad")
    args = parser.parse_args(argv)

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
