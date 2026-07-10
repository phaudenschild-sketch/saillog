#!/usr/bin/env python3
"""masarasi — falsch zugeordnete Logbuch-Einträge einem anderen Törn zuordnen.

Hintergrund: Vor dem Fix konnten automatische Einträge (AutoLog/Foto) in den
gerade *angesehenen* Törn statt in den *offenen* Törn geraten. Dieses Werkzeug
zeigt die Törns und die (evtl. falsch einsortierten) Auto-Einträge und kann sie
gezielt umhängen.

Sicher: ohne --apply passiert nichts (nur Vorschau). Mit --apply wird zuerst
eine Sicherungskopie der Datenbank angelegt, dann umsortiert.

    # 1) Überblick: welche Törns gibt es, wo liegen die Auto-Einträge?
    python fix_trips.py

    # 2) Einen einzelnen Törn im Detail ansehen
    python fix_trips.py --show 53

    # 3) Auto-/Foto-Einträge von heute aus Törn 12 in Törn 53 verschieben (Vorschau)
    python fix_trips.py --move --from 12 --to 53 --type auto --since 2026-07-10

    # 4) dasselbe wirklich ausführen (legt vorher ein Backup an)
    python fix_trips.py --move --from 12 --to 53 --type auto --since 2026-07-10 --apply

    # --to none  hängt die Einträge von jedem Törn ab (ohne Törn-Zuordnung)
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from masarasi.config import Config  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_since(value: str) -> str:
    """Datum/Zeit -> ISO-Untergrenze (Datum -> Tagesbeginn)."""
    if not value:
        return ""
    return value if "T" in value else value + "T00:00:00Z"


def _norm_until(value: str) -> str:
    """Datum/Zeit -> ISO-Obergrenze (Datum -> Tagesende)."""
    if not value:
        return ""
    return value if "T" in value else value + "T23:59:59Z"


def _trip_label(row) -> str:
    status = "offen" if row["status"] == "open" else "abgeschlossen"
    name = row["name"] or f"{row['start_location'] or '?'} → {row['end_location'] or '…'}"
    return f"#{row['id']} {name}  [{status}]"


def list_trips(conn) -> None:
    trips = conn.execute(
        "SELECT id, name, status, start_location, end_location, start_dz "
        "FROM trips ORDER BY id DESC"
    ).fetchall()
    print("\nTörns (neueste zuerst):")
    if not trips:
        print("  (keine)")
    for t in trips:
        counts = conn.execute(
            "SELECT entry_type, COUNT(*) n FROM log_entries WHERE trip_id = ? "
            "GROUP BY entry_type",
            (t["id"],),
        ).fetchall()
        detail = ", ".join(f"{r['entry_type']} {r['n']}" for r in counts) or "—"
        total = sum(r["n"] for r in counts)
        print(f"  {_trip_label(t):<48} Einträge: {total:>4}  ({detail})")

    unassigned = conn.execute(
        "SELECT COUNT(*) FROM log_entries WHERE trip_id IS NULL"
    ).fetchone()[0]
    if unassigned:
        print(f"  {'ohne Törn-Zuordnung':<48} Einträge: {unassigned:>4}")


def recent_auto(conn, days: int = 3) -> None:
    """Zeigt automatische Einträge der letzten Tage, gruppiert nach Törn —
    dort sieht man, ob heutige Auto-Einträge im falschen Törn gelandet sind."""
    # seit heute 00:00 UTC minus (days-1) Tage
    day0 = datetime.now(timezone.utc).toordinal() - (days - 1)
    since = datetime.fromordinal(day0).strftime("%Y-%m-%dT00:00:00Z")
    rows = conn.execute(
        "SELECT id, timestamp, entry_type, trip_id, logevent "
        "FROM log_entries WHERE entry_type IN ('auto') AND timestamp >= ? "
        "ORDER BY trip_id, timestamp",
        (since,),
    ).fetchall()
    print(f"\nAuto-Einträge seit {since[:10]} (nach Törn gruppiert):")
    if not rows:
        print("  (keine)")
        return
    current = object()
    for r in rows:
        if r["trip_id"] != current:
            current = r["trip_id"]
            label = f"Törn #{current}" if current is not None else "ohne Törn-Zuordnung"
            print(f"  {label}:")
        print(f"      Eintrag {r['id']:>5}  {r['timestamp']}  {r['logevent'] or ''}")


def show_trip(conn, trip_id: int) -> None:
    t = conn.execute(
        "SELECT id, name, status, start_location, end_location FROM trips WHERE id = ?",
        (trip_id,),
    ).fetchone()
    if t is None:
        print(f"Törn #{trip_id} nicht gefunden.")
        return
    print(f"\n{_trip_label(t)}")
    rows = conn.execute(
        "SELECT id, timestamp, entry_type, logevent, note FROM log_entries "
        "WHERE trip_id = ? ORDER BY timestamp, id",
        (trip_id,),
    ).fetchall()
    print(f"  {len(rows)} Eintrag/Einträge:")
    for r in rows:
        extra = r["logevent"] or r["note"] or ""
        print(f"    {r['id']:>5}  {r['timestamp']}  {r['entry_type']:<8} {extra[:50]}")


def _select_move(conn, from_id, to_id, types, since, until):
    where = ["trip_id IS ?" if from_id is None else "trip_id = ?"]
    params = [from_id]
    if types:
        where.append("entry_type IN (%s)" % ",".join("?" for _ in types))
        params.extend(types)
    if since:
        where.append("timestamp >= ?")
        params.append(since)
    if until:
        where.append("timestamp <= ?")
        params.append(until)
    clause = " AND ".join(where)
    rows = conn.execute(
        f"SELECT id, timestamp, entry_type, logevent FROM log_entries WHERE {clause} "
        "ORDER BY timestamp, id",
        params,
    ).fetchall()
    return rows, clause, params


def move_entries(conn, db_path, from_id, to_id, types, since, until, apply) -> None:
    if to_id is not None:
        if conn.execute("SELECT 1 FROM trips WHERE id = ?", (to_id,)).fetchone() is None:
            print(f"Ziel-Törn #{to_id} existiert nicht — abgebrochen.")
            return

    rows, clause, params = _select_move(conn, from_id, to_id, types, since, until)
    src = f"#{from_id}" if from_id is not None else "ohne Zuordnung"
    dst = f"#{to_id}" if to_id is not None else "ohne Zuordnung (abgehängt)"
    print(f"\n{len(rows)} Eintrag/Einträge von {src} → {dst}:")
    for r in rows[:40]:
        print(f"    {r['id']:>5}  {r['timestamp']}  {r['entry_type']:<8} {r['logevent'] or ''}")
    if len(rows) > 40:
        print(f"    … und {len(rows) - 40} weitere")
    if not rows:
        print("  Nichts zu verschieben.")
        return

    if not apply:
        print("\n(Vorschau — nichts geändert. Zum Ausführen --apply anhängen.)")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{db_path}.bak_{stamp}"
    shutil.copy2(db_path, backup)
    print(f"\nSicherungskopie: {backup}")
    conn.execute(
        f"UPDATE log_entries SET trip_id = ? WHERE {clause}", [to_id] + params
    )
    conn.commit()
    print(f"{len(rows)} Eintrag/Einträge verschoben nach {dst}.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Zeigt/korrigiert die Törn-Zuordnung von Logbuch-Einträgen."
    )
    parser.add_argument("--db", default=None, help="Pfad zur Logbuch-DB (Standard: Konfiguration)")
    parser.add_argument("--show", type=int, metavar="TRIP_ID", help="Einträge eines Törns anzeigen")
    parser.add_argument("--move", action="store_true", help="Einträge umhängen")
    parser.add_argument("--from", dest="from_id", help="Quell-Törn-ID (oder 'none' = ohne Zuordnung)")
    parser.add_argument("--to", dest="to_id", help="Ziel-Törn-ID (oder 'none' = abhängen)")
    parser.add_argument("--type", default="", help="nur diese Typen, kommagetrennt (z.B. auto)")
    parser.add_argument("--since", default="", help="nur Einträge ab (Datum oder ISO-Zeit)")
    parser.add_argument("--until", default="", help="nur Einträge bis (Datum oder ISO-Zeit)")
    parser.add_argument("--apply", action="store_true", help="Änderung wirklich ausführen")
    args = parser.parse_args(argv)

    db_path = args.db or Config.load().db_path
    if not Path(db_path).exists():
        print(f"Datenbank nicht gefunden: {db_path}")
        return 1
    print(f"Datenbank: {db_path}")
    conn = _connect(db_path)
    try:
        if args.show is not None:
            show_trip(conn, args.show)
            return 0
        if args.move:
            if args.from_id is None or args.to_id is None:
                parser.error("--move braucht --from und --to")
            from_id = None if args.from_id.lower() == "none" else int(args.from_id)
            to_id = None if args.to_id.lower() == "none" else int(args.to_id)
            types = [t.strip() for t in args.type.split(",") if t.strip()]
            move_entries(conn, db_path, from_id, to_id, types,
                         _norm_since(args.since), _norm_until(args.until), args.apply)
            return 0
        # Standard: Überblick
        list_trips(conn)
        recent_auto(conn)
        print("\nTipp: Einträge umhängen mit  --move --from A --to B [--type auto] "
              "[--since JJJJ-MM-TT] [--apply]")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
