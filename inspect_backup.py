#!/usr/bin/env python3
"""triplog — Analyse einer alten Logbuch-Sicherung (lokal, ohne Upload).

    python inspect_backup.py "C:\\Pfad\\zur\\sicherung.db"
    python inspect_backup.py "C:\\Pfad\\zum\\backup-ordner"
    python inspect_backup.py "C:\\...\\sicherung.db" --extract-images bilder_out

Der erste Aufruf beschreibt die Struktur (Tabellen, Bilder, Formate) — diese
Ausgabe kannst du kopieren und mir schicken. Mit --extract-images werden alle
gefundenen Bilder (z.B. Kartenplotter-Screenshots) in einen Ordner geschrieben.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from triplog.legacy import extract_images, inspect_path  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Analysiert eine alte Logbuch-Sicherung.")
    parser.add_argument("pfad", help="Pfad zur Sicherungsdatei oder zum Ordner")
    parser.add_argument(
        "--extract-images",
        metavar="ORDNER",
        help="alle gefundenen Bilder in diesen Ordner extrahieren",
    )
    args = parser.parse_args(argv)

    print(inspect_path(args.pfad))

    if args.extract_images:
        print(f"\nExtrahiere Bilder nach: {args.extract_images} …")
        count = extract_images(args.pfad, args.extract_images)
        print(f"→ {count} Bild(er) geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
