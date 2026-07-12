#!/usr/bin/env python3
"""triplog — altes TripCon-Logbuch (.tcdb) einlesen und zugänglich machen.

Standard: exportiert Törns als CSV, GPX-Tracks und extrahiert alle Bilder
in einen Ausgabeordner. Optional zusätzlich Import in die triplog-App.

    # Export (CSV + GPX + Bilder) in einen Ordner
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export"

    # zusätzlich in die triplog-Logbuch-DB importieren (erscheint in der App)
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export" --into-app

    # nur Bilder extrahieren
    python import_tripcon.py "C:\\...\\TripCon_20250417.tcdb" --out "C:\\claude\\tripcon-export" --only-images
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from triplog import tripcon  # noqa: E402
from triplog.config import Config  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Importiert ein TripCon-Logbuch (.tcdb).")
    parser.add_argument("tcdb", help="Pfad zur TripCon-Sicherung (.tcdb)")
    parser.add_argument("--out", default=None, help="Ausgabeordner für Export/Bilder")
    parser.add_argument(
        "--into-app", action="store_true",
        help="Einträge zusätzlich in die triplog-Logbuch-DB importieren",
    )
    parser.add_argument(
        "--only-images", action="store_true", help="nur Bilder extrahieren"
    )
    parser.add_argument(
        "--show-columns", action="store_true",
        help="nur die Spalten der Stammdaten-Tabellen anzeigen (zur Diagnose)",
    )
    parser.add_argument(
        "--info", action="store_true",
        help="nur analysieren (Integrität, Törns, Einträge, Bilder, Zeitraum)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Ziel-DB für --into-app (Standard: triplog-Konfiguration)",
    )
    args = parser.parse_args(argv)

    if args.show_columns:
        print(f"Öffne TripCon-DB: {args.tcdb}")
        conn = tripcon.connect(args.tcdb)
        try:
            for table in ("S003_Ships", "S006_Persons"):
                cols = tripcon._columns(conn, table)
                print(f"\n{table} ({len(cols)} Spalten):")
                for col in cols:
                    print(f"  {col}")
        finally:
            conn.close()
        return 0

    if args.info:
        print(f"Analysiere TripCon-DB: {args.tcdb}")
        conn = tripcon.connect(args.tcdb)
        try:
            info = tripcon.analyze_tcdb(conn)
        finally:
            conn.close()
        ok = info.get("integrity") == "ok"
        print(f"  Integrität:      {info.get('integrity')}"
              + ("  ✓" if ok else "  ⚠ (Datei evtl. beschädigt)"))
        print(f"  Törns:           {info.get('trips')}")
        print(f"  Zeitraum:        {info.get('date_from', '')[:10]}"
              f" … {info.get('date_to', '')[:10]}")
        print(f"  Logbuch-Zeilen:  {info.get('log_entries')}")
        print(f"  Track-Punkte:    {info.get('track_points')}")
        print(f"  Plotterbilder:   {info.get('plotter_images')}  "
              f"(mit Eintrag: {info.get('plotter_with_log')}, "
              f"nur Törn: {info.get('plotter_trip_only')}, "
              f"Verknüpfung: {info.get('image_method')})")
        print(f"  Wetterbilder:    {info.get('weather_images')}")
        print(f"  Schiffe:         {info.get('ships')}  "
              f"(mit Foto: {info.get('ship_images')})")
        print(f"  Personen:        {info.get('persons')}  "
              f"(mit Foto: {info.get('person_images')})")
        return 0

    if not args.out:
        parser.error("--out ist erforderlich (außer bei --show-columns)")

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
            print(f"Importiere in triplog-DB: {db_path}")
            result = tripcon.import_into_triplog(conn, db_path)
            print(f"  {result['entries']} Eintrag/Einträge importiert (Typ 'tripcon').")
            method = result["image_method"]
            trip_imgs = result.get("trip_images", 0)
            if result["images"] or trip_imgs:
                extra = f" + {trip_imgs} törnweite" if trip_imgs else ""
                print(f"  {result['images']} Plotterbild(er) an Einträge gehängt"
                      f"{extra} (Verknüpfung: {method}).")
            else:
                print(f"  Keine Plotterbilder verknüpft (Methode: {method}).")
            print(f"  Schiffe: {result['ships_created']} neu, "
                  f"{result['ships_matched']} vorhanden, "
                  f"{result['ship_photos']} mit Foto.")
            if result["ship_fields"]:
                print(f"    übernommene Felder: {', '.join(result['ship_fields'])}")
            print(f"  Personen: {result['persons_created']} neu, "
                  f"{result['persons_matched']} vorhanden, "
                  f"{result['person_photos']} mit Foto.")
            if result["person_fields"]:
                print(f"    übernommene Felder: {', '.join(result['person_fields'])}")
            print("  → In triplog sichtbar (ältere Törns ggf. über Export/Scrollen).")
    finally:
        conn.close()

    print(f"\nFertig. Alles unter: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
