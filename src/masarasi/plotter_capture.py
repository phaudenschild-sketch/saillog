"""Bildschirmausschnitt-Erfassung für das Kartenplotter-Feld (GoFree).

Greift unter Windows den Bereich ab, in dem der per GoFree remote gespiegelte
Kartenplotter angezeigt wird, und liefert das Bild als PNG-Bytes. Nutzt
Pillow (`pip install pillow`); ist Pillow nicht installiert, bleibt die
Auto-Aufnahme deaktiviert und masarasi läuft normal weiter.
"""

from __future__ import annotations

import io
from typing import Optional, Sequence

try:  # Pillow ist optional
    from PIL import ImageGrab  # type: ignore

    _HAVE_PIL = True
except Exception:  # noqa: BLE001
    ImageGrab = None  # type: ignore
    _HAVE_PIL = False


def available() -> bool:
    """True, wenn die Bildschirmaufnahme (Pillow) verfügbar ist."""
    return _HAVE_PIL


def load_image_as_png(path: str) -> Optional[bytes]:
    """Lädt eine Bilddatei und gibt sie als PNG-Bytes zurück.

    Mit Pillow werden beliebige Formate (JPG, PNG, GIF, BMP …) gelesen und in
    PNG umgewandelt. Ohne Pillow werden nur PNG/GIF akzeptiert (die tkinter
    direkt anzeigen kann); alles andere ergibt None.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if _HAVE_PIL:
        try:
            from PIL import Image  # type: ignore

            import io as _io

            with Image.open(_io.BytesIO(raw)) as img:
                buffer = _io.BytesIO()
                img.convert("RGB").save(buffer, format="PNG")
                return buffer.getvalue()
        except Exception:  # noqa: BLE001
            return None
    # Ohne Pillow: nur direkt anzeigbare Formate durchreichen
    if raw[:8] == b"\x89PNG\r\n\x1a\n" or raw[:6] in (b"GIF87a", b"GIF89a"):
        return raw
    return None


def grab_png(region: Optional[Sequence[int]]) -> Optional[bytes]:
    """Erfasst den Bereich [links, oben, rechts, unten] als PNG-Bytes.

    Gibt None zurück, wenn Pillow fehlt, kein Bereich gesetzt ist oder der
    Bereich ungültig ist.
    """
    if not _HAVE_PIL or not region or len(region) != 4:
        return None
    left, top, right, bottom = (int(v) for v in region)
    if right - left < 2 or bottom - top < 2:
        return None
    try:
        image = ImageGrab.grab(bbox=(left, top, right, bottom))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - Aufnahme darf das Logging nie stören
        return None
