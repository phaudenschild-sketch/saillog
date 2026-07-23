"""NMEA0183-Simulator.

Startet einen kleinen TCP-Server, der realistische NMEA0183-Sätze eines
segelnden Bootes sendet — zum Testen von saillog ohne echtes Gateway.

    python -m saillog.simulator            # Port 2000
    python -m saillog.simulator --port 2000

Dann in SailLog als Gateway  host=127.0.0.1  port=2000  (TCP) einstellen.
"""

from __future__ import annotations

import argparse
import math
import socket
import threading
import time


def _checksum(body: str) -> str:
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"{checksum:02X}"


def sentence(body: str) -> str:
    """Baut einen kompletten NMEA-Satz mit Prüfsumme."""
    return f"${body}*{_checksum(body)}\r\n"


def _fmt_lat(lat: float) -> str:
    hemi = "N" if lat >= 0 else "S"
    lat = abs(lat)
    degrees = int(lat)
    minutes = (lat - degrees) * 60
    return f"{degrees:02d}{minutes:07.4f},{hemi}"


def _fmt_lon(lon: float) -> str:
    hemi = "E" if lon >= 0 else "W"
    lon = abs(lon)
    degrees = int(lon)
    minutes = (lon - degrees) * 60
    return f"{degrees:03d}{minutes:07.4f},{hemi}"


def build_burst(step: int) -> str:
    """Erzeugt einen Schwung Sätze für einen Zeitschritt."""
    # Boot fährt langsam nach Nordost, Werte schwanken realistisch
    lat = 47.5000 + step * 0.0002
    lon = 9.4000 + step * 0.0003
    sog = 5.5 + math.sin(step / 10.0) * 0.8
    cog = 45.0 + math.sin(step / 20.0) * 5.0
    stw = sog - 0.3
    hdg = cog - 3.0
    awa = 42.0 + math.sin(step / 7.0) * 8.0
    aws = 12.0 + math.sin(step / 9.0) * 2.0
    tws = 9.5 + math.sin(step / 11.0) * 1.5
    twd = 20.0 + math.sin(step / 15.0) * 6.0
    depth = 18.0 + math.sin(step / 5.0) * 4.0
    temp = 19.2 + math.sin(step / 30.0) * 0.5
    t = time.gmtime()
    hhmmss = f"{t.tm_hour:02d}{t.tm_min:02d}{t.tm_sec:02d}"
    ddmmyy = f"{t.tm_mday:02d}{t.tm_mon:02d}{t.tm_year % 100:02d}"

    parts = [
        sentence(
            f"GPRMC,{hhmmss},A,{_fmt_lat(lat)},{_fmt_lon(lon)},"
            f"{sog:.1f},{cog:.1f},{ddmmyy},,"
        ),
        sentence(f"GPVTG,{cog:.1f},T,,M,{sog:.1f},N,,K"),
        sentence(f"IIVHW,{hdg:.1f},T,{hdg - 2:.1f},M,{stw:.1f},N,,K"),
        sentence(f"IIMWV,{awa:.1f},R,{aws:.1f},N,A"),
        sentence(f"IIMWD,{twd:.1f},T,,M,{tws:.1f},N,,M"),
        sentence(f"SDDPT,{depth:.1f},0.0"),
        sentence(f"IIMTW,{temp:.1f},C"),
        sentence(f"IIHDG,{hdg - 2:.1f},,,2.0,E"),
    ]
    return "".join(parts)


def _serve_client(conn: socket.socket, addr, period: float) -> None:
    print(f"[sim] Client verbunden: {addr}")
    step = 0
    try:
        while True:
            conn.sendall(build_burst(step).encode("ascii"))
            step += 1
            time.sleep(period)
    except OSError:
        print(f"[sim] Client getrennt: {addr}")
    finally:
        conn.close()


def _serve_client_quiet(conn: socket.socket, period: float) -> None:
    """Wie _serve_client, aber ohne Konsolenausgabe (für den eingebetteten
    Demo-Datenbus in der GUI)."""
    step = 0
    try:
        while True:
            conn.sendall(build_burst(step).encode("ascii"))
            step += 1
            time.sleep(period)
    except OSError:
        pass
    finally:
        conn.close()


def start_demo_bus(host: str = "127.0.0.1", port: int = 2100,
                   period: float = 1.0) -> socket.socket:
    """Startet den Simulator **eingebettet** in Hintergrund-Threads.

    Für die App („Demo-Datenbus"): ein Testuser bekommt live NMEA-Daten eines
    simulierten Bootes, ganz ohne echtes Gateway. Lauscht standardmäßig nur auf
    localhost. Gibt das Server-Socket zurück (zum Schließen); die Threads sind
    Daemons und enden mit dem Programm.
    """
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)

    def _accept_loop() -> None:
        while True:
            try:
                conn, _addr = server.accept()
            except OSError:
                break                      # Socket geschlossen -> Schleife beenden
            threading.Thread(target=_serve_client_quiet, args=(conn, period),
                             daemon=True).start()

    threading.Thread(target=_accept_loop, daemon=True).start()
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NMEA0183-Testsimulator für SailLog")
    parser.add_argument("--host", default="0.0.0.0", help="Bind-Adresse (Standard: alle)")
    parser.add_argument("--port", type=int, default=2000, help="TCP-Port (Standard: 2000)")
    parser.add_argument(
        "--period", type=float, default=1.0, help="Sekunden zwischen den Sätzen"
    )
    args = parser.parse_args(argv)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    print(f"[sim] NMEA0183-Simulator lauscht auf {args.host}:{args.port} (TCP)")
    print("[sim] Beenden mit Strg+C")
    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=_serve_client, args=(conn, addr, args.period), daemon=True
            ).start()
    except KeyboardInterrupt:
        print("\n[sim] beendet")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
