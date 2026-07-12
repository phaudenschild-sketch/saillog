"""HTML → PDF über einen installierten Chromium-Browser (Edge/Chrome/…).

SailLog erzeugt Berichte als HTML. Für ein *echtes* PDF (statt „im Browser
drucken") wird ein bereits vorhandener Chromium-basierter Browser im
Headless-Modus mit ``--print-to-pdf`` aufgerufen — das braucht **keine**
zusätzlichen Python-Pakete. Auf Windows 11 ist Microsoft Edge immer vorhanden.

``html_to_pdf`` gibt True zurück, wenn eine gültige PDF entstanden ist. Findet
sich kein Browser, liefert ``find_browser`` None und der Aufrufer fällt auf den
HTML-/Browser-Weg zurück.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

# Kandidaten im PATH (Reihenfolge = Präferenz)
_PATH_NAMES = [
    "msedge", "microsoft-edge", "microsoft-edge-stable",
    "google-chrome", "google-chrome-stable", "chrome",
    "chromium", "chromium-browser",
    "brave", "brave-browser",
]


def _windows_candidates() -> List[str]:
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    rel = [
        r"Microsoft\Edge\Application\msedge.exe",
        r"Google\Chrome\Application\chrome.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
        r"Chromium\Application\chrome.exe",
    ]
    out = []
    for base in (pf, pfx86, local):
        if not base:
            continue
        for r in rel:
            out.append(os.path.join(base, r))
    return out


def _macos_candidates() -> List[str]:
    return [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]


def find_browser(explicit: str = "") -> Optional[str]:
    """Pfad zu einem Chromium-Browser oder None.

    Reihenfolge: expliziter Pfad → Umgebungsvariable ``TRIPLOG_BROWSER`` →
    PATH → bekannte Installationsorte je Betriebssystem.
    """
    if explicit and Path(explicit).exists():
        return explicit
    env = os.environ.get("TRIPLOG_BROWSER", "")
    if env and Path(env).exists():
        return env
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    candidates = _windows_candidates() if os.name == "nt" else _macos_candidates()
    for path in candidates:
        if Path(path).exists():
            return path
    return None


def _looks_like_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def html_to_pdf(html: str, out_path: str, browser: str = "",
                wait_ms: int = 3000, timeout: float = 120.0) -> bool:
    """Schreibt ``html`` als echtes PDF nach ``out_path`` (Chromium headless).

    ``wait_ms`` gibt der Seite Zeit zum Rendern (für die eingebettete Karte
    höher wählen). Gibt True bei Erfolg zurück, sonst False.
    """
    exe = find_browser(browser)
    if not exe:
        return False

    tmpdir = tempfile.mkdtemp(prefix="saillog_pdf_")
    html_file = Path(tmpdir) / "report.html"
    profile = Path(tmpdir) / "profile"          # eigenes Profil: stört kein laufendes Edge/Chrome
    try:
        html_file.write_text(html, encoding="utf-8")
        base = [
            exe,
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--no-pdf-header-footer",
            f"--virtual-time-budget={int(wait_ms)}",
            f"--print-to-pdf={out_path}",
            html_file.as_uri(),
        ]
        if os.name != "nt":
            base.insert(1, "--no-sandbox")
        # Neuer Headless-Modus zuerst, klassischer als Ausweichlösung.
        for headless in ("--headless=new", "--headless"):
            args = [base[0], headless] + base[1:]
            try:
                subprocess.run(
                    args, timeout=timeout, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if os.path.exists(out_path) and _looks_like_pdf(out_path):
                return True
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def html_to_png(html: str, out_path: str, width: int = 1000, height: int = 640,
                browser: str = "", wait_ms: int = 8000,
                timeout: float = 120.0) -> bool:
    """Rendert ``html`` als PNG-Screenshot (Chromium headless).

    Für die Kartendarstellung im PDF: die interaktive Leaflet-Karte wird einmal
    „abfotografiert" (inkl. OSM-Hintergrund), das Bild kommt dann statisch in den
    Bericht. ``wait_ms`` gibt den Kacheln Zeit zum Laden.
    """
    exe = find_browser(browser)
    if not exe:
        return False
    tmpdir = tempfile.mkdtemp(prefix="saillog_png_")
    html_file = Path(tmpdir) / "map.html"
    profile = Path(tmpdir) / "profile"
    try:
        html_file.write_text(html, encoding="utf-8")
        base = [
            exe, "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", f"--user-data-dir={profile}",
            f"--window-size={int(width)},{int(height)}",
            f"--virtual-time-budget={int(wait_ms)}",
            f"--screenshot={out_path}", html_file.as_uri(),
        ]
        if os.name != "nt":
            base.insert(1, "--no-sandbox")
        for headless in ("--headless=new", "--headless"):
            args = [base[0], headless] + base[1:]
            try:
                subprocess.run(args, timeout=timeout, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
            except (subprocess.TimeoutExpired, OSError):
                continue
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# Kleiner Selbsttest von der Kommandozeile: python -m saillog.pdf <in.html> <out.pdf>
if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 3:
        print("Aufruf: python -m saillog.pdf <eingabe.html> <ausgabe.pdf>")
        raise SystemExit(2)
    ok = html_to_pdf(Path(sys.argv[1]).read_text(encoding="utf-8"), sys.argv[2])
    print("PDF erstellt." if ok else "Kein Chromium-Browser gefunden / Fehler.")
    raise SystemExit(0 if ok else 1)
