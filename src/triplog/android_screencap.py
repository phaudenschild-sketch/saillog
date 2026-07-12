"""Plotter-Screenshot per ADB vom Android-Tablet (Orca-/Plotter-Anzeige).

Nutzt die Android Debug Bridge (``adb``): ``adb exec-out screencap -p`` liefert
den aktuellen Tablet-Bildschirm als PNG. Damit holt triplog den Plotter-/
Orca-Bildschirm aus der Ferne ins Logbuch — ohne am Tablet zu tippen.

Voraussetzungen:
- ``adb`` installiert (SDK Platform-Tools); Pfad ggf. in den Einstellungen setzen.
- Tablet gekoppelt (USB oder WLAN) und „immer erlauben" bestätigt
  (Entwickleroptionen → USB-/Drahtlos-Debugging).

``exec-out`` liefert die PNG-Bytes binärsicher (kein CRLF-Problem wie bei
``adb shell screencap``).
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Tuple


def _adb_base(adb_path: str, serial: str) -> List[str]:
    cmd = [adb_path or "adb"]
    if serial:
        cmd += ["-s", serial]
    return cmd


def available(adb_path: str = "adb") -> bool:
    """True, wenn adb auffindbar ist (im PATH oder als vollständiger Pfad)."""
    if not adb_path:
        adb_path = "adb"
    if shutil.which(adb_path) is not None:
        return True
    # vollständiger Pfad, den which nicht auflöst (z.B. mit .exe unter Windows)
    from pathlib import Path
    return Path(adb_path).is_file()


def devices(adb_path: str = "adb", timeout: float = 10.0) -> List[Tuple[str, str]]:
    """Liste (Serial, Status) der bekannten adb-Geräte."""
    try:
        proc = subprocess.run(
            [adb_path or "adb", "devices"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    result: List[Tuple[str, str]] = []
    for line in proc.stdout.splitlines()[1:]:      # erste Zeile ist die Überschrift
        line = line.strip()
        if not line or "\t" not in line:
            continue
        serial, _, status = line.partition("\t")
        result.append((serial.strip(), status.strip()))
    return result


def connect(address: str, adb_path: str = "adb",
            timeout: float = 10.0) -> Tuple[bool, str]:
    """`adb connect <ip:port>` für drahtloses ADB. (ok, Meldung)."""
    if not address:
        return False, "keine Adresse"
    try:
        proc = subprocess.run(
            [adb_path or "adb", "connect", address],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    # "connected to ..." und "already connected to ..." gelten als Erfolg
    ok = "connected to" in out.lower()
    return ok, out


def enable_tcpip(adb_path: str = "adb", serial: str = "", port: int = 5555,
                 timeout: float = 10.0) -> Tuple[bool, str]:
    """`adb tcpip <port>` — schaltet das (per USB verbundene) Tablet auf WLAN-ADB."""
    cmd = _adb_base(adb_path, serial) + ["tcpip", str(port)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    ok = proc.returncode == 0 and "error" not in out.lower()
    return ok, out


def wlan_ip(adb_path: str = "adb", serial: str = "",
            timeout: float = 10.0) -> Optional[str]:
    """Liest die (W)LAN-IP des Tablets. Probiert wlan0, sonst irgendein IPv4."""
    import re
    for args in (["shell", "ip", "-o", "-4", "addr", "show", "wlan0"],
                 ["shell", "ip", "-o", "-4", "addr", "show"]):
        cmd = _adb_base(adb_path, serial) + args
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            continue
        for match in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", proc.stdout or ""):
            ip = match.group(1)
            if not ip.startswith("127."):
                return ip
    return None


def _ensure_network(adb_path: str, serial: str) -> None:
    """Bei einer Netzwerk-Adresse (ip:port) vor dem Zugriff (re)connecten.

    ``adb connect`` ist idempotent — ist die Verbindung schon da, kostet es
    fast nichts; war sie weg (WLAN-Aussetzer), wird sie neu aufgebaut."""
    if serial and ":" in serial:
        connect(serial, adb_path)


def capture_png(adb_path: str = "adb", serial: str = "",
                timeout: float = 15.0) -> Optional[bytes]:
    """Holt den Tablet-Bildschirm als PNG-Bytes. None bei Fehler/kein Bild."""
    _ensure_network(adb_path, serial)
    cmd = _adb_base(adb_path, serial) + ["exec-out", "screencap", "-p"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    data = proc.stdout
    if not data or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return data


def capture_jpeg(adb_path: str = "adb", serial: str = "",
                 max_px: int = 1600, timeout: float = 15.0) -> Optional[bytes]:
    """Screenshot holen und (falls Pillow da ist) auf JPEG verkleinern.

    Ohne Pillow werden die PNG-Originalbytes zurückgegeben (mit dem passenden
    MIME muss der Aufrufer dann selbst umgehen)."""
    png = capture_png(adb_path, serial, timeout)
    if png is None:
        return None
    from triplog import photos
    jpeg = photos.resize_bytes_to_jpeg(png, max_px=max_px)
    return jpeg if jpeg else png
