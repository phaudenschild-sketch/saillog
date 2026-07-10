#!/usr/bin/env python3
"""masarasi — altes TripCon-Logbuch (.tcdb) einlesen und zugänglich machen.

Standard: exportiert Törns als CSV, GPX-Tracks und extrahiert alle Bilder
in einen Ausgabeordner. Optional zusätzlich Import in die masarasi-App.

    # Export (CSV + GPX + Bilder) in einen Ordner
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export"

    # zusätzlich in die masarasi-Logbuch-DB importieren (erscheint in der App)
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export" --into-app

    # nur Bilder extrahieren
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export" --only-images
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from masarasi import tripcon  # noqa: E402
from masarasi.config import Config  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Importiert ein TripCon-Logbuch (.tcdb).")
    parser.add_argument("tcdb", help="Pfad zur TripCon-Sicherung (.tcdb)")
    parser.add_argument("--out", required=True, help="Ausgabeordner für Export/Bilder")
    parser.add_argument(
        "--into-app", action="store_true",
        help="Einträge zusätzlich in die masarasi-Logbuch-DB importieren",
    )
    parser.add_argument(
        "--only-images", action="store_true", help="nur Bilder extrahieren"
    )
    parser.add_argument(
        "--db", default=None,
        help="Ziel-DB für --into-app (Standard: masarasi-Konfiguration)",
    )
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    print(f"Öffne TripCon-DB: {args.tcdb}")
    conn = tripcon.connect(args.tcdb)
    try:
        trips = tripcon.load_trips(conn)
        print(f"  {len(trips)} Törn(s) gefunden.")

        print("Extrahiere Bilder …")
        counts = tripcon.extract_images(conn, out / "bilder")
        if counts:
            for sub, n in counts.items():
                print(f"  {n:>5} Bild(er) → bilder/{sub}")
        else:
            print("  (keine Bilder gefunden)")

        if args.only_images:
            print(f"\nFertig. Bilder in: {out / 'bilder'}")
            return 0

        print("Baue Logbuch-Einträge …")
        entries = tripcon.build_entries(conn)
        csv_path = out / "logbuch.csv"
        tripcon.export_csv(entries, str(csv_path))
        print(f"  {len(entries)} Eintrag/Einträge → {csv_path}")

        print("Schreibe GPX-Tracks pro Törn …")
        files = tripcon.export_gpx_tracks(conn, trips, out / "tracks")
        print(f"  {files} GPX-Datei(en) → {out / 'tracks'}")

        if args.into_app:
            db_path = args.db or Config.load().db_path
            print(f"Importiere in masarasi-DB: {db_path}")
            result = tripcon.import_into_masarasi(conn, db_path)
            print(f"  {result['entries']} Eintrag/Einträge importiert (Typ 'tripcon').")
            method = result["image_method"]
            if result["images"]:
                print(f"  {result['images']} Plotterbild(er) an Einträge gehängt "
                      f"(Verknüpfung: {method}).")
            else:
                print(f"  Keine Plotterbilder verknüpft (Methode: {method}).")
            if result["ship_photos"]:
                print(f"  {result['ship_photos']} Schiffsfoto(s) in die Stammdaten übernommen.")
            if result["person_photos"]:
                print(f"  {result['person_photos']} Personenfoto(s) in die Stammdaten übernommen.")
            print("  → In masarasi sichtbar (ältere Törns ggf. über Export/Scrollen).")
    finally:
        conn.close()

    print(f"\nFertig. Alles unter: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
