"""AIS-Decoder für TripLog.

Dekodiert NMEA-`!AIVDM`/`!AIVDO`-Sätze (Einzel- und Mehrteiler) und pflegt
eine Liste der AIS-Ziele (Position, COG, SOG, Heading, Name). Reine
Standardbibliothek.

Unterstützte Nachrichtentypen:
  1,2,3  Klasse-A-Positionsmeldung
  18,19  Klasse-B-Positionsmeldung (19 zusätzlich mit Name)
  5      Klasse-A-Schiffsdaten (Name, Rufzeichen, Typ)
  24     Klasse-B-Schiffsdaten (Name)
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# AIS 6-bit-ASCII-Zeichensatz (für Namen/Rufzeichen)
_SIXBIT = "@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_ !\"#$%&'()*+,-./0123456789:;<=>?"


def _dearmor(payload: str) -> str:
    """Wandelt die 6-bit-armored Nutzlast in einen Bit-String."""
    bits = []
    for ch in payload:
        v = ord(ch) - 48
        if v > 40:
            v -= 8
        if v < 0 or v > 63:
            return ""
        bits.append(format(v, "06b"))
    return "".join(bits)


def _u(bits: str, a: int, z: int) -> int:
    return int(bits[a:z], 2)


def _s(bits: str, a: int, z: int) -> int:
    v = int(bits[a:z], 2)
    return v - (1 << (z - a)) if bits[a] == "1" else v


def _text(bits: str, a: int, z: int) -> str:
    out = []
    for i in range(a, min(z, len(bits) - 5), 6):
        out.append(_SIXBIT[int(bits[i:i + 6], 2)])
    return "".join(out).split("@")[0].strip()


def decode_payload(payload: str) -> Optional[Dict]:
    """Dekodiert eine (ggf. zusammengesetzte) AIS-Nutzlast in ein dict."""
    bits = _dearmor(payload)
    if len(bits) < 38:
        return None
    msg_type = _u(bits, 0, 6)
    mmsi = _u(bits, 8, 38)
    result: Dict = {"type": msg_type, "mmsi": mmsi}

    if msg_type in (1, 2, 3) and len(bits) >= 137:
        result.update(_position(bits, sog=(50, 60), lon=(61, 89), lat=(89, 116),
                                cog=(116, 128), hdg=(128, 137)))
    elif msg_type == 18 and len(bits) >= 133:
        result.update(_position(bits, sog=(46, 56), lon=(57, 85), lat=(85, 112),
                                cog=(112, 124), hdg=(124, 133)))
    elif msg_type == 19 and len(bits) >= 263:
        result.update(_position(bits, sog=(46, 56), lon=(57, 85), lat=(85, 112),
                                cog=(112, 124), hdg=(124, 133)))
        result["name"] = _text(bits, 143, 263)
    elif msg_type == 5 and len(bits) >= 232:
        result["name"] = _text(bits, 112, 232)
        result["callsign"] = _text(bits, 70, 112)
    elif msg_type == 24 and len(bits) >= 40:
        part = _u(bits, 38, 40)
        if part == 0 and len(bits) >= 160:
            result["name"] = _text(bits, 40, 160)
    else:
        return result  # Typ erkannt, aber nicht weiter dekodiert
    return result


def _position(bits, sog, lon, lat, cog, hdg) -> Dict:
    out: Dict = {}
    s = _u(bits, *sog) / 10.0
    out["sog"] = None if s > 102.2 else s
    lon_v = _s(bits, *lon) / 600000.0
    lat_v = _s(bits, *lat) / 600000.0
    out["lon"] = None if abs(lon_v) > 180 else lon_v
    out["lat"] = None if abs(lat_v) > 90 else lat_v
    raw_cog = _u(bits, *cog)
    # Rohwert des COG-Feldes (0–3599 gültig, 3600 = „nicht verfügbar").
    # Wird für die Erkennung fehlerhaft in ganzen Grad kodierter Feeds
    # gebraucht (siehe AisDecoder._update_cog_mode).
    out["cog_raw"] = None if raw_cog >= 3600 else raw_cog
    c = raw_cog / 10.0
    out["cog"] = None if c >= 360 else c
    h = _u(bits, *hdg)
    out["heading"] = None if h == 511 else h
    return out


class AisTargets:
    """Thread-sichere Liste der AIS-Ziele (Schlüssel: MMSI)."""

    def __init__(self, max_age: float = 600.0) -> None:
        self._lock = threading.Lock()
        self._targets: Dict[int, Dict] = {}
        self._max_age = max_age

    def update(self, mmsi: int, fields: Dict, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        with self._lock:
            rec = self._targets.setdefault(mmsi, {"mmsi": mmsi})
            for key, value in fields.items():
                if value is not None:
                    rec[key] = value
            rec["last_seen"] = now

    def all(self, now: Optional[float] = None) -> List[Dict]:
        if now is None:
            now = time.time()
        with self._lock:
            return [
                dict(rec) for rec in self._targets.values()
                if now - rec.get("last_seen", 0) <= self._max_age
            ]

    def count(self, now: Optional[float] = None) -> int:
        return len(self.all(now))


# So viele verschiedene bewegte Ziele mit COG-Feld < 360 müssen auftreten
# (ohne dass je ein Feld ≥ 360 vorkam), bis ein Feed als „ganze Grad statt
# Zehntelgrad" gilt. In echtem, normkonformem AIS-Verkehr taucht fast sofort
# ein Ziel mit COG ≥ 36,0° (Feldwert ≥ 360) auf und sperrt auf „Zehntel".
_COG_WHOLE_DEG_VOTES = 4


class AisDecoder:
    """Nimmt `!AIVDM`-Zeilen entgegen und aktualisiert die Zielliste.

    Erkennt zusätzlich fehlerhaft kodierte Feeds: manche NMEA2000→0183-
    Umsetzer (z.B. der B&G-Multiplexer an Bord) schreiben COG in **ganzen
    Grad** statt in Zehntelgrad. `cog_mode` hält den erkannten Zustand
    ("unknown" | "tenths" | "whole"); im Zustand "whole" wird COG korrekt
    hochskaliert.
    """

    def __init__(self, targets: AisTargets) -> None:
        self._targets = targets
        self._parts: Dict[str, Dict[int, str]] = {}
        self.cog_mode = "unknown"
        self._whole_mmsis: set = set()

    def add_sentence(self, line: str, now: Optional[float] = None) -> Optional[Dict]:
        line = line.strip()
        if not (line.startswith("!AIVDM") or line.startswith("!AIVDO")):
            return None
        star = line.rfind("*")
        body = line[:star] if star != -1 else line
        f = body.split(",")
        if len(f) < 7:
            return None
        try:
            frags, num = int(f[1]), int(f[2])
        except ValueError:
            return None
        payload = f[5]

        if frags <= 1:
            return self._decode(payload, now)

        # Mehrteiler zusammensetzen. Schlüssel ist der Funkkanal (f[4]); die
        # Sequenz-ID (f[3]) wird bewusst NICHT genutzt: manche Geräte (z.B.
        # Wetherdock easyTRX2) vergeben sie je Satz neu, sodass die Teile
        # eines Mehrteilers unterschiedliche IDs tragen. Da die Teile in der
        # Praxis unmittelbar aufeinanderfolgen, reicht das Sammeln je Kanal.
        channel = f[4] or "_"
        buf = self._parts.setdefault(channel, {})
        if num == 1:
            buf.clear()  # ein neuer Mehrteiler beginnt
        buf[num] = payload
        if all(i in buf for i in range(1, frags + 1)):
            full = "".join(buf[i] for i in range(1, frags + 1))
            self._parts.pop(channel, None)
            return self._decode(full, now)
        return None

    def _decode(self, payload: str, now: Optional[float]) -> Optional[Dict]:
        msg = decode_payload(payload)
        if not msg or not msg.get("mmsi"):
            return None
        self._update_cog_mode(msg)
        # Fehlerhaften Feed korrigieren: liegt COG in ganzen Grad vor, ist der
        # Rohwert (0–359) bereits der Kurs — nicht durch 10 teilen.
        cog_raw = msg.get("cog_raw")
        if self.cog_mode == "whole" and cog_raw is not None and cog_raw < 360:
            msg["cog"] = float(cog_raw)
        fields = {k: msg[k] for k in ("lat", "lon", "sog", "cog", "heading",
                                       "name", "callsign") if k in msg}
        if fields:
            self._targets.update(msg["mmsi"], fields, now=now)
        return msg

    def _update_cog_mode(self, msg: Dict) -> None:
        """Erkennt anhand mehrerer Sätze, ob COG in ganzen Grad kodiert ist."""
        cog_raw = msg.get("cog_raw")
        if cog_raw is None:
            return
        if cog_raw >= 360:
            # Feldwert ≥ 36,0° kann es nur bei Zehntelgrad-Kodierung geben —
            # eindeutig normkonform (überstimmt auch eine frühere Fehlannahme).
            self.cog_mode = "tenths"
            return
        if self.cog_mode == "unknown":
            sog = msg.get("sog")
            if sog and sog > 2.0:  # nur bewegte Ziele haben aussagekräftiges COG
                self._whole_mmsis.add(msg["mmsi"])
                if len(self._whole_mmsis) >= _COG_WHOLE_DEG_VOTES:
                    self.cog_mode = "whole"


def _main(argv=None) -> int:
    """Kommandozeilen-Decoder: prüft AIS-Sätze direkt am Boot.

        python -m triplog.ais "!AIVDM,1,1,,B,..."   # eine/mehrere Zeilen
        type rohdaten.txt | python -m triplog.ais  # oder aus stdin

    Zeigt je Ziel Typ, MMSI, Name, SOG, COG und Heading — zum Abgleich mit
    der COG-Spalte des AIS-Empfängers.
    """
    import sys

    lines = list(argv) if argv else sys.argv[1:]
    if not lines:
        lines = [ln.rstrip("\n") for ln in sys.stdin]

    targets = AisTargets()
    decoder = AisDecoder(targets)
    for line in lines:
        for part in line.replace("\r", "\n").split("\n"):
            if part.strip():
                decoder.add_sentence(part, now=0.0)

    rows = sorted(targets.all(now=0.0), key=lambda r: r["mmsi"])
    print(f"{'MMSI':>10}  {'Name':16} {'SOG':>6} {'COG':>6} {'HDG':>6}  Position")
    for r in rows:
        pos = ""
        if r.get("lat") is not None and r.get("lon") is not None:
            pos = f"{r['lat']:.5f}, {r['lon']:.5f}"
        sog = "—" if r.get("sog") is None else f"{r['sog']:.1f}"
        cog = "—" if r.get("cog") is None else f"{r['cog']:.1f}"
        hdg = "—" if r.get("heading") is None else f"{r['heading']}"
        print(f"{r['mmsi']:>10}  {r.get('name', ''):16} {sog:>6} {cog:>6} {hdg:>6}  {pos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
