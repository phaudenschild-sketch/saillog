"""Analyse & Import alter Logbuch-Sicherungen.

Liest eine Sicherung (SQLite-Datei, ZIP, Ordner …) LOKAL ein und beschreibt
ihre Struktur, damit ein passender Import gebaut werden kann. Findet und
extrahiert außerdem eingebettete Bilder (z.B. Kartenplotter-Screenshots aus
TripCon/GoFree), egal ob als Datei oder als BLOB in einer Datenbank.

    python inspect_backup.py <pfad-zur-sicherung>
    python inspect_backup.py <pfad> --extract-images bilder_out
"""

from __future__ import annotations

import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Magische Signaturen zur Formaterkennung
_IMAGE_MAGIC: List[Tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
]


def image_ext(data: bytes) -> Optional[str]:
    """Gibt die Bildendung zurück, falls die Bytes ein bekanntes Bild sind."""
    for magic, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    # WEBP: RIFF....WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def detect_type(head: bytes) -> str:
    """Grobe Typerkennung anhand der ersten Bytes."""
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    if head.startswith(b"%PDF"):
        return "pdf"
    ext = image_ext(head)
    if ext:
        return "image/" + ext
    stripped = head.lstrip()
    if stripped[:5].lower() == b"<?xml" or stripped[:1] == b"<":
        return "xml"
    if stripped[:1] in (b"{", b"["):
        return "json"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binär/unbekannt"


def _read_head(path: Path, size: int = 32) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(size)


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


# --- SQLite ----------------------------------------------------------------

def inspect_sqlite(path: Path) -> List[str]:
    """Beschreibt Tabellen, Spalten, Zeilenzahlen und Beispieldaten."""
    out: List[str] = []
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return [f"  SQLite konnte nicht geöffnet werden: {exc}"]
    conn.text_factory = bytes
    try:
        tables = [
            row[0].decode("utf-8", "replace")
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        if not tables:
            return ["  (keine Tabellen gefunden)"]
        out.append(f"  {len(tables)} Tabelle(n):")
        for table in tables:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            colnames = [c[1].decode("utf-8", "replace") for c in cols]
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                count = "?"
            out.append(f"\n  ▸ {table}  ({count} Zeilen)")
            out.append(f"      Spalten: {', '.join(colnames)}")
            # Bild-BLOB-Spalten aufspüren und Beispielzeilen zeigen
            image_cols = _find_image_columns(conn, table, colnames)
            if image_cols:
                out.append(f"      ⭑ Bilder in Spalte(n): {', '.join(image_cols)}")
            out += _sample_rows(conn, table, colnames)
    finally:
        conn.close()
    return out


def _find_image_columns(conn, table: str, colnames: List[str]) -> List[str]:
    found = []
    for col in colnames:
        try:
            rows = conn.execute(
                f'SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL LIMIT 5'
            ).fetchall()
        except sqlite3.Error:
            continue
        for (value,) in rows:
            if isinstance(value, (bytes, bytearray)) and image_ext(bytes(value[:16])):
                found.append(col)
                break
    return found


def _sample_rows(conn, table: str, colnames: List[str], limit: int = 2) -> List[str]:
    out = []
    try:
        rows = conn.execute(f'SELECT * FROM "{table}" LIMIT {limit}').fetchall()
    except sqlite3.Error:
        return out
    for row in rows:
        parts = []
        for name, value in zip(colnames, row):
            parts.append(f"{name}={_preview_value(value)}")
        out.append("      · " + ", ".join(parts))
    return out


def _preview_value(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        ext = image_ext(raw[:16])
        if ext:
            return f"<BILD/{ext} {len(raw)} B>"
        # TEXT-Spalten kommen wegen text_factory=bytes als Bytes an:
        # echten UTF-8-Text als Text zeigen, sonst als BLOB
        if b"\x00" not in raw:
            try:
                text = raw.decode("utf-8")
                return text if len(text) <= 60 else text[:57] + "…"
            except UnicodeDecodeError:
                pass
        return f"<BLOB {len(raw)} B>"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "…"


def extract_images_from_sqlite(path: Path, out_dir: Path) -> int:
    """Schreibt alle Bild-BLOBs aus einer SQLite-DB als Dateien heraus."""
    count = 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.text_factory = bytes
    try:
        tables = [
            r[0].decode("utf-8", "replace")
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            colnames = [c[1].decode("utf-8", "replace") for c in cols]
            for col in colnames:
                try:
                    rows = conn.execute(
                        f'SELECT rowid, "{col}" FROM "{table}" '
                        f'WHERE "{col}" IS NOT NULL'
                    )
                except sqlite3.Error:
                    continue
                for rowid, value in rows:
                    if not isinstance(value, (bytes, bytearray)):
                        continue
                    ext = image_ext(bytes(value[:16]))
                    if not ext:
                        continue
                    out_dir.mkdir(parents=True, exist_ok=True)
                    name = f"{table}_{col}_{rowid}.{ext}"
                    (out_dir / name).write_bytes(value)
                    count += 1
    finally:
        conn.close()
    return count


# --- ZIP -------------------------------------------------------------------

def inspect_zip(path: Path) -> List[str]:
    out = []
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        out.append(f"  ZIP mit {len(infos)} Einträgen:")
        images = 0
        dbs = 0
        for info in infos[:60]:
            tag = ""
            lower = info.filename.lower()
            if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")):
                tag = "  [Bild]"
                images += 1
            elif lower.endswith((".db", ".sqlite", ".sqlite3", ".sl2", ".sl3")):
                tag = "  [Datenbank]"
                dbs += 1
            out.append(f"    {info.filename}  ({_human(info.file_size)}){tag}")
        if len(infos) > 60:
            out.append(f"    … und {len(infos) - 60} weitere")
        out.append(f"  → {images} Bild(er), {dbs} Datenbank(en) im Archiv")
    return out


def extract_images_from_zip(path: Path, out_dir: Path) -> int:
    count = 0
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            with zf.open(info) as handle:
                head = handle.read(16)
            if image_ext(head):
                out_dir.mkdir(parents=True, exist_ok=True)
                target = out_dir / Path(info.filename).name
                target.write_bytes(zf.read(info))
                count += 1
    return count


# --- Verzeichnis -----------------------------------------------------------

def inspect_dir(path: Path) -> List[str]:
    out = []
    all_files = sorted(p for p in path.rglob("*") if p.is_file())
    out.append(f"  Ordner mit {len(all_files)} Datei(en):")
    by_type: Dict[str, int] = {}
    for file in all_files:
        try:
            head = _read_head(file)
        except OSError:
            continue
        kind = detect_type(head)
        by_type[kind] = by_type.get(kind, 0) + 1
    for kind, n in sorted(by_type.items(), key=lambda x: -x[1]):
        out.append(f"    {n:>4}×  {kind}")
    out.append("\n  Größte Dateien:")
    biggest = sorted(all_files, key=lambda p: p.stat().st_size, reverse=True)[:12]
    for file in biggest:
        rel = file.relative_to(path)
        out.append(f"    {_human(file.stat().st_size):>9}  {rel}")
    return out


# --- Einstiegspunkt für die Analyse ----------------------------------------

def inspect_path(path_str: str) -> str:
    """Analysiert eine Sicherung und gibt einen Textbericht zurück."""
    path = Path(path_str).expanduser()
    if not path.exists():
        return f"Pfad nicht gefunden: {path}"

    lines = [f"Analyse: {path}"]
    if path.is_dir():
        lines.append(f"Typ: Ordner")
        lines += inspect_dir(path)
        return "\n".join(lines)

    size = path.stat().st_size
    head = _read_head(path, 64)
    kind = detect_type(head)
    lines.append(f"Typ: {kind}   Größe: {_human(size)}")

    if kind == "sqlite":
        lines += inspect_sqlite(path)
    elif kind == "zip":
        lines += inspect_zip(path)
    elif kind.startswith("image/"):
        lines.append("  (Eine einzelne Bilddatei — vermutlich ein Plotter-Screenshot.)")
    elif kind in ("xml", "json", "text"):
        preview = path.read_bytes()[:1500].decode("utf-8", "replace")
        lines.append("  Vorschau (erste 1500 Zeichen):")
        lines.append("  " + preview.replace("\n", "\n  "))
    else:
        lines.append("  Hex-Vorschau der ersten 32 Bytes:")
        lines.append("  " + head[:32].hex(" "))
    return "\n".join(lines)


def extract_images(path_str: str, out_dir_str: str) -> int:
    """Zieht alle gefundenen Bilder aus der Sicherung in out_dir."""
    path = Path(path_str).expanduser()
    out_dir = Path(out_dir_str).expanduser()
    if path.is_dir():
        count = 0
        # Dateiliste vorab einsammeln, damit ein Ausgabeordner INNERHALB des
        # Sicherungsordners nicht mitgescannt wird
        files = [p for p in path.rglob("*") if p.is_file()]
        out_resolved = out_dir.resolve()
        for file in files:
            if out_resolved in file.resolve().parents:
                continue
            try:
                head = _read_head(file)
            except OSError:
                continue
            if image_ext(head):
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / file.name).write_bytes(file.read_bytes())
                count += 1
        return count
    kind = detect_type(_read_head(path, 32))
    if kind == "sqlite":
        return extract_images_from_sqlite(path, out_dir)
    if kind == "zip":
        return extract_images_from_zip(path, out_dir)
    if kind.startswith("image/"):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / path.name).write_bytes(path.read_bytes())
        return 1
    return 0
