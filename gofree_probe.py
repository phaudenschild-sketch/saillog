#!/usr/bin/env python3
"""GoFree-Datendienst erkunden: WebSocket ``navico-nav-ws`` (Port 2053).

Der Zeus kündigt neben NMEA-0183 (Port 10110) auch ``navico-nav-ws`` an —
die WebSocket-Daten-API von GoFree. Darüber fließt (potenziell) der volle
NMEA-2000-Satz inkl. Motordaten direkt vom Plotter. Das genaue Protokoll ist
nicht dokumentiert, darum verbindet dieses Werkzeug, lauscht zunächst
unaufgefordert und schickt danach mehrere Anfrage-Varianten — und zeigt alles,
was zurückkommt. Aus der Ausgabe leiten wir das echte Protokoll ab.

    python gofree_probe.py                       # Standard-Host 192.168.9.224
    python gofree_probe.py 192.168.9.224
    python gofree_probe.py 192.168.9.224 --http  # zusätzlich die HTTP-API (Port 80)

Voraussetzung: im Bordnetz, ggf. am MFD unter „Fernsteuerberechtigungen" den
Laptop freigeben (der WS-Dienst kann eine Freigabe verlangen).
"""

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# schlanken stdlib-WebSocket-Client aus dem Orca-Prober wiederverwenden
from orca_probe import (  # noqa: E402
    _ws_connect, _ws_read_frame, _ws_send_text, http_get, _printable,
)

DEFAULT_HOST = "192.168.9.224"
NAV_WS_PORT = 2053
WS_PATHS = ["/", "/ws", "/navico", "/nav-ws", "/data", "/websocket"]

# Anfrage-Kandidaten (JSON). Das GoFree-Toolkit spricht JSON; die genaue Form
# ist unbekannt, darum mehrere Varianten. Wir beobachten, worauf der Zeus
# antwortet.
REQUESTS = [
    '{"Version":1}',
    '{"DataListReq":{"group":0}}',
    '{"DataListReq":{}}',
    '{"InfoReq":{}}',
    '{"ReqData":{"list":true}}',
    '{"DataReq":[{"id":0,"repeat":false}]}',
    '{"MsgListReq":{}}',
]

HTTP_PATHS = ["/", "/Navico", "/valueList", "/api", "/gofree", "/dataList"]


def probe_http(host: str) -> None:
    print(f"== GoFree-HTTP-API an {host}:80 ==")
    for path in HTTP_PATHS:
        body = http_get(host, 80, path)
        print(f"\n-- GET http://{host}:80{path} --")
        print("  " + _printable(body, 800).replace("\n", "\n  "))


def probe_ws(host: str, port: int, seconds: float) -> bool:
    """Verbindet, lauscht und sendet Anfragen. True, wenn Daten kamen."""
    for path in WS_PATHS:
        print(f"\n== ws://{host}:{port}{path} ==")
        sock, reader = _ws_connect(host, port, path, 3.0)
        if sock is None:
            print(f"   {reader}")
            continue
        print("   verbunden.")
        try:
            # 1) unaufgefordert lauschen (manche Dienste senden sofort)
            sock.settimeout(2.0)
            spontaneous = 0
            try:
                for _ in range(4):
                    payload = _ws_read_frame(reader)
                    if not payload:
                        break
                    spontaneous += 1
                    print(f"   ◀(spontan) {payload.decode('utf-8', 'replace')[:300]}")
            except (socket.timeout, OSError):
                pass

            # 2) Anfrage-Varianten senden
            for req in REQUESTS:
                try:
                    _ws_send_text(sock, req)
                    print(f"   ▶ {req}")
                except OSError:
                    break

            # 3) Antworten sammeln
            sock.settimeout(seconds)
            count = 0
            kinds = {}
            try:
                while count < 80:
                    payload = _ws_read_frame(reader)
                    if not payload:
                        break
                    count += 1
                    text = payload.decode("utf-8", "replace")
                    kinds[text[:50]] = kinds.get(text[:50], 0) + 1
                    if count <= 30:
                        print(f"   ◀ {text[:300]}")
            except (socket.timeout, OSError):
                pass
        finally:
            sock.close()

        print(f"   → {count} Antwort(en)"
              + (f", {spontaneous} spontan" if spontaneous else "") + ".")
        for tag, n in sorted(kinds.items(), key=lambda x: -x[1])[:10]:
            print(f"       {n:>3}×  {tag}")
        if count or spontaneous:
            return True   # funktionierender Pfad gefunden — reicht
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Erkundet den GoFree-WebSocket-Datendienst (navico-nav-ws)."
    )
    parser.add_argument("host", nargs="?", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=NAV_WS_PORT)
    parser.add_argument("--seconds", type=float, default=8.0,
                        help="Lauschdauer nach den Anfragen (Standard 8)")
    parser.add_argument("--http", action="store_true",
                        help="zusätzlich die HTTP-API (Port 80) abfragen")
    args = parser.parse_args(argv)

    if args.http:
        probe_http(args.host)

    print(f"\n== GoFree nav-ws an {args.host}:{args.port} ==")
    ok = probe_ws(args.host, args.port, args.seconds)
    if not ok:
        print("\n  Keine Antwort auf keinem Pfad. Mögliche Gründe: Dienst verlangt")
        print("  eine Freigabe (MFD → Drahtlos → Fernsteuerberechtigungen) oder ein")
        print("  bestimmtes WebSocket-Subprotokoll. Bitte Ausgabe schicken.")
    print("\nFertig. Bitte die komplette Ausgabe kopieren und schicken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
