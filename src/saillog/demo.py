"""Beispiel-Daten für den allerersten Start (Demo-Törn).

Wird **nur** angelegt, wenn das Logbuch brandneu ist (frische Installation) —
bestehende Daten werden nie verändert oder ergänzt. Der Demo-Törn ist klar als
Beispiel gekennzeichnet und lässt sich in der App jederzeit löschen
(Törn wählen → „Törn abschließen…"/Eintrag löschen, oder den ganzen Törn über
„Extras → Törns/Etappen gruppieren…" bzw. per Rechtsklick entfernen).
"""

from __future__ import annotations

from typing import List

from saillog.storage import LogbookStore, LogEntry, Trip

_NOTE = "Beispiel-Daten zum Ausprobieren — kannst du bedenkenlos löschen."

# (Uhrzeit, lat, lon, SOG, COG, Tiefe, TWS, TWD, Bewölkung, Anlass, Notiz,
#  engine_on, Großsegel, Genua%, Typ)
_ENTRIES = [
    ("07:35", 43.5480, 16.4250, 4.6, 168, 22.0, 9, 300, "heiter",
     "Ablegen", "Leinen los in Kaštela, Motor an.", 1, "", None, "manual"),
    ("08:05", 43.5210, 16.4300, 5.8, 175, 31.0, 11, 295, "heiter",
     "Segel setzen", "Segel gesetzt, Motor aus — schöner Am-Wind-Kurs.", 0, "Voll", 90, "auto"),
    ("08:45", 43.4880, 16.4360, 6.1, 172, 45.0, 12, 298, "wolkig",
     "Routineeintrag", "", 0, "Voll", 90, "auto"),
    ("09:30", 43.4520, 16.4120, 5.9, 205, 52.0, 13, 300, "wolkig",
     "Wende", "Wende auf Backbordbug.", 0, "Voll", 80, "auto"),
    ("10:15", 43.4360, 16.3780, 6.3, 250, 60.0, 12, 302, "wolkig",
     "Routineeintrag", "Delfine gesichtet 🐬", 0, "Voll", 80, "auto"),
    ("11:00", 43.4600, 16.4000, 5.4, 20, 38.0, 10, 305, "heiter",
     "Halse", "Halse Richtung Split.", 0, "Voll", 80, "auto"),
    ("11:40", 43.4980, 16.4300, 4.2, 35, 24.0, 8, 300, "wolkenlos",
     "Segel einholen", "Segel geborgen, Motor an fürs Hafenmanöver.", 1, "", None, "auto"),
    ("12:10", 43.5070, 16.4400, 0.8, 40, 12.0, 6, 298, "wolkenlos",
     "Anlegen", "Festgemacht in Split. Schöner Törn!", 1, "", None, "manual"),
]

_DATE = "2024-06-15"


def seed_demo_data(store: LogbookStore) -> int:
    """Legt den Demo-Törn samt Einträgen an. Gibt die Zahl der Einträge zurück."""
    trip = Trip(
        name="Beispiel-Törn Adria (Demo)",
        status="closed",
        start_location="Kaštela",
        start_dz=f"{_DATE}T07:30:00Z",
        end_location="Split",
        end_dz=f"{_DATE}T12:10:00Z",
        start_water_l=200.0, start_diesel_l=160.0,
        start_engine_hours=1240.0, start_log_nm=3120.0,
        end_water_l=185.0, end_diesel_l=150.0,
        end_engine_hours=1241.5, end_log_nm=3138.0,
        note=_NOTE,
    )
    trip_id = store.add_trip(trip)

    entries: List[LogEntry] = []
    for (hhmm, lat, lon, sog, cog, depth, tws, twd, cloud, anlass,
         note, engine_on, mainsail, genoa, etype) in _ENTRIES:
        entries.append(LogEntry(
            timestamp=f"{_DATE}T{hhmm}:00Z",
            entry_type=etype,
            trip_id=trip_id,
            lat=lat, lon=lon, sog_kn=sog, cog_deg=cog, depth_m=depth,
            tws_kn=tws, twd_deg=twd, water_temp_c=21.0,
            engine_on=engine_on, mainsail=mainsail, genoa_percent=genoa,
            cloud_cover=cloud, precipitation="", visibility="gut",
            logevent=anlass, note=note,
        ))
    store.add_many(entries)
    return len(entries)
