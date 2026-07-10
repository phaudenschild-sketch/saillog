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
import concurrent.futures
import json
import socket
import time
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


def _tcp_open(host: str, port: int, timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sweep_tcp(host: str, ports: List[int], workers: int = 200) -> List[int]:
    """Schneller, paralleler Verbindungsscan. Gibt die offenen Ports zurück."""
    open_ports: List[int] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_tcp_open, host, p): p for p in ports}
        for fut in concurrent.futures.as_completed(futures):
            try:
                if fut.result():
                    open_ports.append(futures[fut])
            except Exception:  # noqa: BLE001
                pass
    return sorted(open_ports)


def _printable(data: bytes, limit: int = 120) -> str:
    text = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:limit])
    return text + ("…" if len(data) > limit else "")


def _raw_preview(host: str, port: int, seconds: float = 2.0, limit: int = 256) -> bytes:
    """Liest, was ein Port von selbst sendet (ohne Anfrage)."""
    try:
        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.settimeout(seconds)
            data = b""
            try:
                while len(data) < limit:
                    chunk = sock.recv(limit - len(data))
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            return data
    except OSError:
        return b""


def _http_probe(host: str, port: int, timeout: float = 2.0) -> bytes:
    """Schickt ein HTTP GET und liest die Antwort (Statuszeile/Header)."""
    try:
        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.settimeout(timeout)
            sock.sendall(
                b"GET / HTTP/1.0\r\nHost: " + host.encode() + b"\r\n\r\n"
            )
            data = b""
            try:
                while len(data) < 400:
                    chunk = sock.recv(400 - len(data))
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            return data
    except OSError:
        return b""


def scan_sweep(host: str, up_to: int = 10240) -> None:
    ports = sorted(set(list(range(1, up_to + 1)) +
                       [10110, 10111, 11000, 39150, 2947, 8080, 8375, 50000, 60001]))
    print(f"\nBreiter Portscan an {host} ({len(ports)} Ports, ~15–30 s) …")
    open_ports = sweep_tcp(host, ports)
    if not open_ports:
        print("  Keine offenen TCP-Ports gefunden. Ist der PC im selben WLAN wie der")
        print("  Orca? Stimmt die IP? Muss die Datenausgabe in der Orca-App aktiv sein?")
        return
    print(f"  Offene Ports: {', '.join(map(str, open_ports))}\n")
    for port in open_ports:
        result = probe_tcp(host, port, listen_seconds=2.5)
        if result:
            print(f"  ✓ Port {port}: {_format_types(result)}")
            print(f"      → In masarasi: Host={host}  Port={port}  Protokoll=tcp")
            continue
        if result == {}:
            # Sendet von selbst Daten, aber kein NMEA0183 -> Rohvorschau zeigen
            raw = _raw_preview(host, port)
            print(f"  · Port {port}: sendet Daten, kein NMEA0183. Vorschau:")
            print(f"      {_printable(raw)}")
            continue
        # Sendet nicht von selbst -> HTTP/API prüfen
        http = _http_probe(host, port)
        if http[:4] in (b"HTTP",):
            first = http.split(b"\r\n", 1)[0].decode("ascii", "ignore")
            server = ""
            for line in http.split(b"\r\n"):
                if line.lower().startswith(b"server:"):
                    server = line.decode("ascii", "ignore")
                    break
            print(f"  · Port {port}: HTTP-Server/API — {first}   {server}".rstrip())
        elif http:
            print(f"  · Port {port}: antwortet auf Anfrage. Vorschau: {_printable(http)}")
        else:
            print(f"  · Port {port}: offen, still (evtl. WebSocket/proprietär)")


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


def listen_gofree(seconds: float = 8.0, iface: Optional[str] = None,
                  on_packet=None) -> List[Dict]:
    """Lauscht auf GoFree-Ankündigungen (Multicast 239.2.1.1:2052).

    iface: lokale Interface-IP für den Multicast-Beitritt (wie TripCon, das an
    eine bestimmte IP bindet). Ohne Angabe = alle Interfaces (INADDR_ANY) —
    auf Rechnern mit mehreren Netzen (VM/WLAN+LAN) sollte man die richtige IP
    angeben, sonst wird evtl. auf dem falschen Netz gelauscht.
    on_packet(addr, data, parsed): optionaler Rückruf für JEDES empfangene
    Paket (auch nicht parsebare) — für die Roh-Diagnose.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    found: Dict[str, Dict] = {}
    if_addr = socket.inet_aton(iface) if iface else socket.inet_aton("0.0.0.0")
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:  # nicht überall vorhanden (z.B. Windows)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        sock.bind(("", GOFREE_PORT))
        mreq = socket.inet_aton(GOFREE_MULTICAST) + if_addr
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        if iface:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, if_addr)
        # Gesamtdauer begrenzen (nicht pro Paket): ein chattiges MFD sendet
        # sonst ununterbrochen und der Lauf endet nie.
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.5, remaining))
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            ann = parse_gofree_announcement(data)
            if on_packet is not None:
                on_packet(addr, data, ann)
            if ann:
                if not ann.get("ip"):
                    ann["ip"] = addr[0]
                found[str(ann.get("ip"))] = ann
    except OSError:
        pass
    finally:
        sock.close()
    return list(found.values())


def gofree_source_hints(device: Dict) -> List[str]:
    """Liefert masarasi-Quellzeilen (TCP host:port) für die NMEA-Dienste."""
    ip = device.get("ip")
    hints: List[str] = []
    for s in device.get("services", []):
        name = str(s.get("name") or "").lower()
        port = s.get("port")
        if port and ("nmea" in name or "0183" in name or "tcp" in name):
            hints.append(f"TCP {ip}:{port}")
    return hints


def scan_gofree(seconds: float = 8.0, iface: Optional[str] = None,
                raw: bool = False) -> None:
    where = f" über {iface}" if iface else ""
    print(f"\nLausche {int(seconds)} s auf GoFree-Ankündigungen "
          f"({GOFREE_MULTICAST}:{GOFREE_PORT}){where} …")

    packets: List = []
    seen: set = set()   # identische Ankündigungen nur einmal ausgeben

    def _on_packet(addr, data, parsed):
        packets.append((addr, data, parsed))
        if not raw:
            return
        key = hash(data)
        if key in seen:
            return
        seen.add(key)
        kind = "JSON erkannt" if parsed else "roh/unbekannt"
        print(f"\n  ⟵ {len(data)} B von {addr[0]}:{addr[1]}  ({kind}) "
              f"[weitere identische werden unterdrückt]")
        if parsed:
            # vollständiges JSON hübsch ausgeben (die Dienste stehen mittendrin)
            print(json.dumps(parsed.get("raw", {}), indent=2, ensure_ascii=False))
        else:
            print(f"      Text: {_printable(data, limit=1400)}")
            print(f"      Hex : {data[:96].hex(' ')}")

    devices = listen_gofree(seconds, iface=iface, on_packet=_on_packet)

    if not devices:
        if packets:
            print(f"\n  {len(packets)} Paket(e) empfangen, aber keins als GoFree-JSON")
            print("  erkennbar. Bitte die obige Roh-Ausgabe (mit --raw) schicken —")
            print("  daraus lässt sich das Format ableiten.")
            if not raw:
                print("  Tipp: nochmal mit  --gofree --raw  starten.")
        else:
            print("  Keine Pakete gehört. Ist der PC im Plotter-Netz und GoFree am")
            print("  MFD aktiviert? Auf Rechnern mit mehreren Netzen die richtige")
            print("  lokale IP angeben:  --gofree --iface 192.168.0.123")
        return

    for d in devices:
        print(f"\n  ✓ {d.get('name') or '?'}  ({d.get('model') or '?'})  IP {d.get('ip')}")
        if not d["services"]:
            print("      (keine Dienste in der Ankündigung)")
        for s in d["services"]:
            print(f"      · {s['name']}  Port {s['port']}"
                  + (f"  v{s['version']}" if s['version'] is not None else ""))
        for hint in gofree_source_hints(d):
            print(f"      → in masarasi als Quelle eintragen:  {hint}")
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
    parser.add_argument(
        "--iface", default=None, metavar="IP",
        help="lokale Interface-IP für GoFree-Multicast (z.B. 192.168.0.123)",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="empfangene GoFree-Pakete roh anzeigen (Text + Hex) — zur Diagnose",
    )
    parser.add_argument(
        "--seconds", type=float, default=8.0,
        help="Dauer des GoFree-Lauschens in Sekunden (Standard 8)",
    )
    parser.add_argument(
        "--sweep", action="store_true",
        help="breiter Portscan (alle offenen Ports am Host finden, z.B. Orca)",
    )
    args = parser.parse_args(argv)

    if not args.host and not args.udp and not args.gofree:
        parser.error("Bitte eine Host-IP angeben oder --udp / --gofree verwenden.")

    if args.gofree:
        scan_gofree(seconds=args.seconds, iface=args.iface, raw=args.raw)

    if args.host and args.sweep:
        scan_sweep(args.host)
        print("\nFertig. Die passende Zeile oben in masarasi eintragen und 'Verbinden'.")
        return 0

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
