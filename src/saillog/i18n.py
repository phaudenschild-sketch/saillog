"""Leichtgewichtige Mehrsprachigkeit (i18n) für SailLog.

Übersetzungen liegen als JSON-Kataloge, die den **deutschen Originaltext auf
den Zieltext** abbilden. Der deutsche Text ist zugleich der Schlüssel:
fehlt eine Übersetzung, wird automatisch der deutsche Originaltext angezeigt —
nichts bricht, auch bei unvollständigen Katalogen.

Kataloge werden aus zwei Orten geladen und zusammengeführt:

* **mitgeliefert:** ``<paket>/lang/<code>.json``
* **benutzerdefiniert:** ``~/.saillog/lang/<code>.json`` (ergänzt/überschreibt)

So kann jede Person eine Sprache anpassen oder ergänzen, ohne den Code
anzufassen — passend zur „einfach `git pull`"-Philosophie der App.

Verwendung::

    from saillog.i18n import t
    ttk.Button(frame, text=t("Speichern"))
    label.config(text=t("Motor {rpm} U/min", rpm=1785))
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Dict, Optional


def _package_lang_dir() -> Path:
    """Ordner der mitgelieferten Kataloge — auch im PyInstaller-Bundle.

    Als gefrorene EXE (PyInstaller) liegen die Datendateien unter
    ``sys._MEIPASS`` (bei onedir der ``_internal``-Ordner); aus dem Quellcode
    neben diesem Modul."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        frozen = Path(base) / "saillog" / "lang"
        if frozen.is_dir():
            return frozen
    return Path(__file__).resolve().parent / "lang"


# Mitgelieferte Kataloge (im Paket bzw. im EXE-Bundle)
_LANG_DIR = _package_lang_dir()

# Sonderschlüssel im Katalog: hält den Anzeigenamen der Sprache
_NAME_KEY = "__language__"

# Anzeigename je Sprachcode, falls der Katalog keinen __language__-Eintrag hat
_FALLBACK_NAMES = {"de": "Deutsch", "en": "English"}

_lock = threading.Lock()
_current = "de"
_catalog: Dict[str, str] = {}


def _user_lang_dir() -> Optional[Path]:
    """Benutzer-Sprachordner (~/.saillog/lang). Spät importiert, um eine
    Import-Zirkularität mit ``config`` zu vermeiden."""
    try:
        from saillog.config import CONFIG_PATH
        return CONFIG_PATH.parent / "lang"
    except Exception:  # pragma: no cover - defensive
        return None


def _load_catalog(code: str) -> Dict[str, str]:
    """Lädt den Katalog eines Sprachcodes (Paket zuerst, Benutzer ergänzt)."""
    data: Dict[str, str] = {}
    bases = [_LANG_DIR]
    user = _user_lang_dir()
    if user is not None:
        bases.append(user)
    for base in bases:
        f = base / f"{code}.json"
        if f.is_file():
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update({str(k): str(v) for k, v in raw.items()})
            except (ValueError, OSError):
                pass
    return data


def available_languages() -> Dict[str, str]:
    """``{code: Anzeigename}`` aller gefundenen Kataloge (immer inkl. ``de``)."""
    codes = {"de"}
    bases = [_LANG_DIR]
    user = _user_lang_dir()
    if user is not None:
        bases.append(user)
    for base in bases:
        if base.is_dir():
            for f in base.glob("*.json"):
                codes.add(f.stem)
    result: Dict[str, str] = {}
    for code in sorted(codes):
        cat = _load_catalog(code)
        result[code] = cat.get(_NAME_KEY, _FALLBACK_NAMES.get(code, code))
    return result


def set_language(code: Optional[str]) -> None:
    """Aktive Sprache setzen. ``de`` (oder unbekannt) = deutscher Originaltext."""
    global _current, _catalog
    code = code or "de"
    cat = {} if code == "de" else _load_catalog(code)
    with _lock:
        _current = code
        _catalog = cat


def current_language() -> str:
    return _current


def t(text: str, /, _ctx: Optional[str] = None, **kwargs) -> str:
    """Übersetzt ``text`` in die aktive Sprache.

    Fehlt eine Übersetzung, wird der deutsche Originaltext zurückgegeben.
    Platzhalter werden per :meth:`str.format` gefüllt::

        t("Motor {rpm} U/min", rpm=1785)

    ``_ctx`` unterscheidet gleich geschriebene, aber unterschiedlich zu
    übersetzende Wörter (Homographen, z.B. „Länge" = Longitude vs. Bootslänge).
    Der Katalogschlüssel ist dann ``"<ctx>\\x04<text>"`` (wie gettext-pgettext);
    fehlt er, wird der deutsche Originaltext zurückgegeben.
    """
    key = f"{_ctx}\x04{text}" if _ctx else text
    with _lock:
        out = _catalog.get(key, text)
    if kwargs:
        try:
            return out.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # Übersetzung mit unpassenden Platzhaltern -> deutscher Text
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return out
    return out
