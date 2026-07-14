"""Foto-Import: verkleinert Bilder und überwacht einen Ordner.

Wie bei TripCon: Bilder in einen Ordner legen → saillog erzeugt einen
Auto-Eintrag mit dem Bild und den aktuellen NMEA-Daten. Die Bilder werden auf
eine vernünftige Größe verkleinert (max. Kantenlänge, JPEG), damit keine
riesige Datensammlung entsteht. Verarbeitete Originale wandern in den
Unterordner ``verarbeitet``.

Das Verkleinern braucht **Pillow** (``pip install pillow``). Ohne Pillow ist der
Foto-Import deaktiviert; alles andere läuft unverändert.
"""

from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


def available() -> bool:
    """True, wenn Pillow zum Verkleinern vorhanden ist."""
    try:
        import PIL.Image  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _encode(im, max_px: int, quality: int) -> bytes:
    from PIL import ImageOps
    im = ImageOps.exif_transpose(im)          # Orientierung aus EXIF
    im = im.convert("RGB")
    im.thumbnail((max_px, max_px))             # Seitenverhältnis bleibt
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def resize_to_jpeg(path, max_px: int = 1600, quality: int = 82) -> Optional[bytes]:
    """Lädt ein Bild (Pfad), dreht es nach EXIF, verkleinert es -> JPEG-Bytes.

    Gibt None zurück, wenn Pillow fehlt oder das Bild nicht lesbar ist.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(path) as im:
            return _encode(im, max_px, quality)
    except Exception:  # noqa: BLE001
        return None


def resize_bytes_to_jpeg(data: bytes, max_px: int = 1600,
                         quality: int = 82) -> Optional[bytes]:
    """Wie resize_to_jpeg, aber aus Bild-Bytes (z.B. BLOB aus einer DB)."""
    if not data:
        return None
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(io.BytesIO(data)) as im:
            return _encode(im, max_px, quality)
    except Exception:  # noqa: BLE001
        return None


class PhotoWatcher:
    """Überwacht einen Ordner und meldet neue (stabile) Bilder als JPEG."""

    def __init__(
        self,
        folder: str,
        on_photo: Callable[[bytes, str], None],
        max_px: int = 1600,
        poll: float = 3.0,
        processed_dirname: str = "verarbeitet",
        recursive: bool = False,
    ) -> None:
        self._folder = Path(folder)
        self._on_photo = on_photo
        self._max_px = max_px
        self._poll = poll
        self._recursive = recursive
        self._processed = self._folder / processed_dirname
        self._pending: Dict[str, int] = {}   # Pfad -> zuletzt gesehene Größe
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # --- Steuerung ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # --- Kernlogik ----------------------------------------------------------

    def _list_images(self):
        try:
            items = self._folder.rglob("*") if self._recursive else self._folder.iterdir()
            out = []
            for p in items:
                if not (p.is_file() and p.suffix.lower() in _IMAGE_EXT):
                    continue
                # den „verarbeitet"-Ordner nicht erneut einlesen
                if self._processed in p.parents:
                    continue
                out.append(p)
            return out
        except OSError:
            return []

    def scan_once(self) -> int:
        """Ein Durchlauf. Verarbeitet Bilder, deren Größe seit dem letzten
        Durchlauf unverändert ist (fertig kopiert). Gibt die Anzahl zurück."""
        processed = 0
        current = {}
        for path in self._list_images():
            key = str(path)                               # voller Pfad -> eindeutig
            try:
                size = path.stat().st_size
            except OSError:
                continue
            current[key] = size
            prev = self._pending.get(key)
            if prev is None:
                self._pending[key] = size                 # erst nächstes Mal
            elif prev == size:
                if self._process(path):
                    processed += 1
                self._pending.pop(key, None)
            else:
                self._pending[key] = size                 # noch am Kopieren
        for key in list(self._pending):
            if key not in current:
                self._pending.pop(key, None)
        return processed

    def _process(self, path: Path) -> bool:
        jpeg = resize_to_jpeg(path, self._max_px)
        if not jpeg:
            return False
        try:
            self._on_photo(jpeg, path.name)
        except Exception:  # noqa: BLE001
            return False
        self._move_to_processed(path)
        return True

    def _move_to_processed(self, path: Path) -> None:
        try:
            self._processed.mkdir(exist_ok=True)
            target = self._processed / path.name
            i = 1
            while target.exists():
                target = self._processed / f"{path.stem}_{i}{path.suffix}"
                i += 1
            path.rename(target)
        except OSError:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self._poll)
