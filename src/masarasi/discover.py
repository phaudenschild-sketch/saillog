"""Discovery-Helfer: findet NMEA-Datenquellen im Bordnetz.

Am Boot ausführen, um IP/Port von Orca Core, B&G-Plotter & Co. zu finden:

    python -m masarasi.discover 192.168.4.1        # TCP-Ports an einem Host scannen
    python -m masarasi.discover 192.168.4.1 --full # mehr Ports probieren
    python -m masarasi.discover --udp              # auf UDP-Broadcasts lauschen

Der Scanner verbindet sich testweise, liest ein paar Sekunden mit und meldet,
auf welchem Port NMEA0183-Sätze ankommen und welche Satztypen dabei sind.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
from typing import Dict, List, Optional

from masarasi.nmea import NmeaParser

# GoFree-Dienstankündigung: MFDs (B&G/Simrad/Lowrance) senden hierhin JSON
GOFREE_MULTICAST = "239.2.1.1"
GOFREE_PORT = 2052

# Häufige NMEA-über-IP-Ports bei Marine-Gateways/Plottern
COMMON_TCP_PORTS = [2000, 10110, 2053, 2052, 10111, 2947, 39150, 8375, 4800, 3000]
COMMON_UDP_PORTS = [2000, 10110, 2052, 2053, 4800, 8375]

# Orca Core = 2000, Navico/B&G = 2052/2053, IANA NMEA-0183 = 10110
PORT_HINTS = {
    2000: "Orca Core / Yacht Devices / viele Gateways",
    2052: "Navico/B&G GoFree",
    2053: "Navico/B&G GoFree",
    10110: "NMEA-0183 (IANA-Standardport)",
    2947: "gpsd",
}


def _sentence_types(data: bytes) -> Dict[str, int]:
    """Zählt erkannte NMEA-Satztypen in einem Byte-Puffer."""
    parser = NmeaParser()
    counts: Dict[str, int] = {}
    text = data.decode("ascii", errors="ignore")
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip()
        if len(line) < 6 or line[0] not in "$!":
            continue
        star = line.rfind("*")
        address = (line[1:star] if star != -1 else line[1:]).split(",")[0]
        if len(address) >= 5:
            counts[address[-3:]] = counts.get(address[-3:], 0) + 1
    return counts


def probe_tcp(host: str, port: int, listen_seconds: float = 3.0) -> Optional[Dict[str, int]]:
    """Verbindet per TCP und liest kurz mit. None = keine Verbindung/keine Daten."""
    try:
        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.settimeout(listen_seconds)
            buffer = b""
            try:
                while len(buffer) < 8192:
                    chunk = sock.recv(2048)
                    if not chunk:
                        break
                    buffer += chunk
                    if b"$" in buffer or b"!" in buffer:
                        # Genug für eine Erkennung, noch kurz weiterlesen
                        sock.settimeout(0.8)
            except socket.timeout:
                pass
    except OSError:
        return None
    types = _sentence_types(buffer)
    return types if types else ({} if buffer else None)


def probe_udp(port: int, listen_seconds: float = 4.0) -> Optional[Dict[str, int]]:
    """Lauscht auf einem UDP-Port. None = nichts empfangen."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(listen_seconds)
        buffer = b""
        try:
            while len(buffer) < 8192:
                data, _addr = sock.recvfrom(2048)
                buffer += data
        except socket.timeout:
            pass
    except OSError:
        return None
    finally:
        sock.close()
    types = _sentence_types(buffer)
    return types if types else (None if not buffer else {})


def _format_types(types: Dict[str, int]) -> str:
    if not types:
        return "Daten empfangen, aber keine NMEA0183-Sätze erkannt"
    parts = [f"{name}×{count}" for name, count in sorted(types.items())]
    return "NMEA-Sätze: " + ", ".join(parts)


def scan_tcp(host: str, ports: List[int]) -> None:
    print(f"\nScanne TCP-Ports an {host} …")
    found = False
    for port in ports:
        result = probe_tcp(host, port)
        hint = f"  [{PORT_HINTS[port]}]" if port in PORT_HINTS else ""
        if result is None:
            continue
        found = True
        print(f"  ✓ Port {port} offen{hint}")
        print(f"      {_format_types(result)}")
        if result:
            print(f"      → In masarasi: Host={host}  Port={port}  Protokoll=tcp")
    if not found:
        print("  Kein offener Datenport gefunden. Stimmt die IP? Ist der PC im")
        print("  richtigen WLAN (Orca-/B&G-Netz)? Ggf. --full für mehr Ports.")


def scan_udp(ports: List[int]) -> None:
    print("\nLausche auf UDP-Broadcasts (z.B. B&G/Navico) …")
    found = False
    for port in ports:
        result = probe_udp(port)
        hint = f"  [{PORT_HINTS[port]}]" if port in PORT_HINTS else ""
        if result is None:
            continue
        found = True
        print(f"  ✓ UDP-Port {port} empfängt Daten{hint}")
        print(f"      {_format_types(result)}")
        if result:
            print(f"      → In masarasi: Port={port}  Protokoll=udp")
    if not found:
        print("  Keine UDP-Broadcasts empfangen.")


def parse_gofree_announcement(data: bytes) -> Optional[Dict]:
    """Parst eine GoFree-Dienstankündigung (JSON). None, wenn kein GoFree."""
    try:
        obj = json.loads(data.decode("utf-8", "ignore"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    # Feldnamen tolerant behandeln (unterschiedliche Firmware-Stände)
    def pick(*names):
        for n in names:
            for key in obj:
                if key.lower() == n.lower():
                    return obj[key]
        return None

    services = pick("Services", "Service") or []
    if not isinstance(services, list):
        services = []
    norm_services = []
    for s in services:
        if not isinstance(s, dict):
            continue
        name = None
        port = None
        version = None
        for key, val in s.items():
            kl = key.lower()
            if kl in ("service", "name"):
                name = val
            elif kl == "port":
                port = val
            elif kl == "version":
                version = val
        norm_services.append({"name": name, "port": port, "version": version})
    return {
        "name": pick("Name"),
        "model": pick("Model", "ModelDescription"),
        "ip": pick("IP", "Ip", "Address"),
        "services": norm_services,
        "raw": obj,
    }


def listen_gofree(seconds: float = 8.0) -> List[Dict]:
    """Lauscht auf GoFree-Ankündigungen (Multicast 239.2.1.1:2052)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    found: Dict[str, Dict] = {}
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", GOFREE_PORT))
        mreq = struct.pack(
            "4sl", socket.inet_aton(GOFREE_MULTICAST), socket.INADDR_ANY
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.settimeout(seconds)
        deadline_reached = False
        while not deadline_reached:
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                break
            ann = parse_gofree_announcement(data)
            if ann:
                if not ann.get("ip"):
                    ann["ip"] = addr[0]
                found[str(ann.get("ip"))] = ann
    except OSError:
        pass
    finally:
        sock.close()
    return list(found.values())


def scan_gofree(seconds: float = 8.0) -> None:
    print(f"\nLausche {int(seconds)} s auf GoFree-Ankündigungen "
          f"({GOFREE_MULTICAST}:{GOFREE_PORT}) …")
    devices = listen_gofree(seconds)
    if not devices:
        print("  Keine GoFree-Geräte gehört. Ist der PC im Plotter-WLAN und GoFree am")
        print("  MFD aktiviert? (Manche MFDs senden nur bei aktivem GoFree.)")
        return
    for d in devices:
        print(f"\n  ✓ {d.get('name') or '?'}  ({d.get('model') or '?'})  IP {d.get('ip')}")
        if not d["services"]:
            print("      (keine Dienste in der Ankündigung)")
        for s in d["services"]:
            print(f"      · {s['name']}  Port {s['port']}"
                  + (f"  v{s['version']}" if s['version'] is not None else ""))
    print("\n  Hinweis: Der Live-Plotterbildschirm läuft über einen lizenzierten")
    print("  Navico-Videokanal (Tier 3) und ist in dieser Liste NICHT als offener")
    print("  Dienst enthalten. Datendienste (NMEA/WebSocket) sind nutzbar.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Findet NMEA-Datenquellen (Orca Core, B&G, …) im Bordnetz."
    )
    parser.add_argument(
        "host", nargs="?", help="IP der Datenquelle (z.B. 192.168.4.1) für den TCP-Scan"
    )
    parser.add_argument("--udp", action="store_true", help="zusätzlich UDP abhören")
    parser.add_argument(
        "--full", action="store_true", help="mehr Ports probieren (langsamer)"
    )
    parser.add_argument(
        "--gofree", action="store_true",
        help="auf GoFree-Dienstankündigungen von B&G/Navico-MFDs lauschen",
    )
    args = parser.parse_args(argv)

    if not args.host and not args.udp and not args.gofree:
        parser.error("Bitte eine Host-IP angeben oder --udp / --gofree verwenden.")

    if args.gofree:
        scan_gofree()

    tcp_ports = COMMON_TCP_PORTS if args.full else COMMON_TCP_PORTS[:5]
    udp_ports = COMMON_UDP_PORTS

    if args.host:
        scan_tcp(args.host, tcp_ports)
    if args.udp:
        scan_udp(udp_ports)

    print("\nFertig. Die passende Zeile oben in masarasi eintragen und 'Verbinden'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
