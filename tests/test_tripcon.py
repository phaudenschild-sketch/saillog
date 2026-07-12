"""Tests für den TripCon-Import gegen eine nachgebaute TripCon-DB."""

import os
import sqlite3
import struct
import tempfile
import unittest
import zlib

from saillog.storage import LogbookStore
from saillog import tripcon


def _jpg() -> bytes:
    """Minimale Bytes mit JPEG-Signatur (für die Bilderkennung genügt das)."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 40


def _png() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b"")


def _build_tripcon_db(path: str) -> None:
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE B105_Trips (ID INTEGER, FromLocation TEXT, FromDZ TEXT, ToLocation TEXT, ToDZ TEXT)")
    c.execute("INSERT INTO B105_Trips VALUES (37,'Vulcano','2013-07-14 07:55:00.000Z','Isola Salina','2013-07-14 10:45:00.000Z')")

    c.execute("CREATE TABLE B100_Log (ID INTEGER, Trip INTEGER, TripDZ TEXT, CreateDZ TEXT, "
              "LogEvent INTEGER, Clouds INTEGER, Precipitation INTEGER, Sight INTEGER)")
    c.execute("INSERT INTO B100_Log VALUES (1362,37,'2013-07-14 08:15:00.000Z','',137,NULL,NULL,138)")
    c.execute("INSERT INTO B100_Log VALUES (1363,37,'2013-07-14 08:30:00.000Z','',NULL,NULL,NULL,NULL)")

    # LogEvent/Sicht sind codierte ParamValue-IDs -> Text über zwei Tabellen
    c.execute("CREATE TABLE S005_ParamValue (ID INTEGER, MasterID INTEGER, LabelID INTEGER, PID INTEGER)")
    c.execute("INSERT INTO S005_ParamValue VALUES (137,0,500,2)")   # LogEvent 137 -> Label 500
    c.execute("INSERT INTO S005_ParamValue VALUES (138,0,501,3)")   # Sicht   138 -> Label 501
    c.execute("CREATE TABLE S000_Translation (LangID INTEGER, LabelID INTEGER, Label TEXT, Comment TEXT)")
    # Deutsch (142) hat die meisten Einträge -> Hauptsprache
    c.execute("INSERT INTO S000_Translation VALUES (142,500,'Manöver','')")
    c.execute("INSERT INTO S000_Translation VALUES (142,501,'sehr gut (20 NM)','')")
    c.execute("INSERT INTO S000_Translation VALUES (142,502,'Sonstiges','')")
    c.execute("INSERT INTO S000_Translation VALUES (7,500,'Maneuver','')")  # Englisch, weniger Einträge

    # Koordinaten in Dezimal-Bogenminuten (2305.0' = 38.4167°)
    c.execute("CREATE TABLE VPosition (LogID INTEGER, Latitude REAL, Longitude REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VPosition VALUES (1362,2305.0,895.0,0)")
    c.execute("INSERT INTO VPosition VALUES (1363,2304.0,894.0,0)")

    c.execute("CREATE TABLE VSpeedOverGround (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VSpeedOverGround VALUES (1362,3.0,0)")
    c.execute("CREATE TABLE VCourseOverGround (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VCourseOverGround VALUES (1362,209,0)")
    c.execute("CREATE TABLE VWaterDepth (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VWaterDepth VALUES (1362,6.4,1)")
    c.execute("CREATE TABLE VWaterTemperature (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VWaterTemperature VALUES (1362,18.0,1)")
    c.execute("CREATE TABLE VSpeedThroughWater (LogID INTEGER, Value REAL, AutoRecord INTEGER)")  # leer
    c.execute("CREATE TABLE VApparentWind (LogID INTEGER, Direction REAL, Speed REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VApparentWind VALUES (1362,180,2.8,0)")
    c.execute("CREATE TABLE VTrueWind (LogID INTEGER, Direction REAL, Speed REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VTrueWind VALUES (1362,90,6.8,0)")
    c.execute("CREATE TABLE VAirTemperature (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VAirTemperature VALUES (1362,23.0,1)")
    c.execute("CREATE TABLE VAirPressure (LogID INTEGER, Value REAL, AutoRecord INTEGER)")
    c.execute("INSERT INTO VAirPressure VALUES (1362,1016.0,0)")

    c.execute("CREATE TABLE B103_Comment (ID INTEGER, Comment TEXT, LogID INTEGER, CreateDZ TEXT)")
    c.execute("INSERT INTO B103_Comment VALUES (1,'Ablegen bei Sonne',1362,'2013-07-17 15:05:34.946Z')")

    c.execute("CREATE TABLE B111_TrackInfo (ID INTEGER, Trip INTEGER, Latitude REAL, Longitude REAL, CreateDZ TEXT, Speed REAL, CoG REAL)")
    c.execute("INSERT INTO B111_TrackInfo VALUES (1,37,2305.0,895.0,'2013-07-14 08:15:00.000Z',3.0,209)")
    c.execute("INSERT INTO B111_TrackInfo VALUES (2,37,2306.0,896.0,'2013-07-14 08:16:00.000Z',3.2,210)")

    c.execute("CREATE TABLE B104_BinDat (ID INTEGER, BinType INTEGER, Value BLOB, LogID INTEGER, TripID INTEGER, DefBinDat INTEGER, Active INTEGER, EditDZ TEXT, CreateDZ TEXT)")
    c.execute("INSERT INTO B104_BinDat VALUES (211,1,?,NULL,37,0,1,NULL,'')", (_jpg(),))
    c.execute("INSERT INTO B104_BinDat VALUES (216,1,?,1362,NULL,1,1,NULL,'2013-07-17 14:48:11.125Z')", (_png(),))
    # zweites Bild am selben Eintrag (mehrere Bilder je Eintrag)
    c.execute("INSERT INTO B104_BinDat VALUES (217,1,?,1362,NULL,1,1,NULL,'2013-07-17 14:49:00.000Z')", (_png(),))

    c.execute("CREATE TABLE B109_Weather (ID INTEGER, Comment TEXT, ValueType INTEGER, Value BLOB, ReportDZ TEXT, Filename TEXT, CreateDZ TEXT, Active INTEGER, TextValue TEXT)")
    c.execute("INSERT INTO B109_Weather VALUES (13,'',3,?, '2013-09-26 08:20:16.000Z','OpenPortGuide','2013-10-22 08:20:37.841Z',1,NULL)", (_jpg(),))

    # Schiffe/Personen mit den echten TripCon-Spaltennamen (DB-Version 366)
    c.execute("CREATE TABLE S003_Ships (ID INTEGER, ShipName TEXT, Number TEXT, ShipType TEXT, "
              "FlagOf TEXT, PortOfRegistry TEXT, CallSign TEXT, MMSI TEXT, LoA REAL, WoA REAL, "
              "PassHeight REAL, Keeltype TEXT, Draft_Max REAL, Displace REAL, Picture BLOB, "
              "TransInstDepth REAL, CorrFactLog REAL, TypeOfDrive TEXT, Active INTEGER)")
    c.execute("INSERT INTO S003_Ships VALUES (2,'Tymanfaya','CH-1234','Sailing Yacht','CH',"
              "'Lavagna','HBY1234','269123456',13.5,4.2,19.0,'Fin',2.1,12.0,?,2.3,1.02,'Sail',1)",
              (_jpg(),))
    c.execute("CREATE TABLE S006_Persons (ID INTEGER, LastName TEXT, FirstName TEXT, Email TEXT, "
              "Nationality TEXT, Passport_Nr TEXT, Birthday TEXT, Picture BLOB, Address TEXT, "
              "ZipCode TEXT, City TEXT, PlaceOfBirth TEXT, Active INTEGER)")
    c.execute("INSERT INTO S006_Persons VALUES (15,'Haudenschild','Peter','peter@haudenschild.ch',"
              "'CH','X1234567','1965-03-12 00:00:00.000Z',?,'Seeweg 1','8000','Zürich','Bern',1)",
              (_jpg(),))

    conn.commit()
    conn.close()


class TripconTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "TripCon.tcdb")
        _build_tripcon_db(self.db)
        self.conn = tripcon.connect(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_coord_conversion(self):
        self.assertAlmostEqual(tripcon.coord_to_degrees(2305.0), 38.4167, places=3)
        self.assertAlmostEqual(tripcon.coord_to_degrees("895,0"), 14.9167, places=3)

    def test_to_iso(self):
        self.assertEqual(tripcon.to_iso("2013-07-14 08:15:00.000Z"), "2013-07-14T08:15:00Z")
        self.assertEqual(tripcon.to_iso(""), "")

    def test_build_entries(self):
        entries = tripcon.build_entries(self.conn)
        self.assertEqual(len(entries), 2)
        e = entries[0]
        self.assertEqual(e.timestamp, "2013-07-14T08:15:00Z")
        self.assertAlmostEqual(e.lat, 38.4167, places=3)
        self.assertAlmostEqual(e.lon, 14.9167, places=3)
        self.assertAlmostEqual(e.sog_kn, 3.0)
        self.assertAlmostEqual(e.cog_deg, 209)
        self.assertAlmostEqual(e.depth_m, 6.4)
        self.assertAlmostEqual(e.water_temp_c, 18.0)
        self.assertAlmostEqual(e.aws_kn, 2.8)
        self.assertAlmostEqual(e.awa_deg, 180)
        self.assertAlmostEqual(e.tws_kn, 6.8)
        self.assertEqual(e.location, "Vulcano → Isola Salina")
        self.assertIn("Ablegen bei Sonne", e.note)
        self.assertIn("23°C", e.note)   # Lufttemperatur in der Notiz
        self.assertIn("1016 hPa", e.note)
        # Codierte Felder aufgelöst (deutsche Hauptsprache)
        self.assertEqual(e.logevent, "Manöver")           # LogEvent 137 -> Label 500
        self.assertEqual(e.visibility, "sehr gut (20 NM)")  # Sicht 138 -> Label 501

    def test_import_into_saillog(self):
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        result = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result["entries"], 2)
        store = LogbookStore(db_path)
        self.assertEqual(store.count(), 2)
        # Erneuter Import ersetzt, verdoppelt nicht
        result2 = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result2["entries"], 2)
        self.assertEqual(store.count(), 2)

    def test_import_links_entry_images_multiple(self):
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        result = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result["image_method"], "bindat_logid")
        # 216 und 217 hängen über LogID 1362 am selben Eintrag → 2 Bilder
        self.assertEqual(result["images"], 2)
        # 211 hat keine LogID (nur TripID 37) → an den ersten Eintrag des Törns
        self.assertEqual(result["trip_images"], 1)
        store = LogbookStore(db_path)
        entries = store.all(newest_first=False)
        first = [e for e in entries if e.timestamp == "2013-07-14T08:15:00Z"][0]
        # erster Eintrag: 216 + 217 (Eintrag) + 211 (törnweit) = 3 Bilder
        self.assertEqual(store.count_entry_images(first.id), 3)

    def test_analyze_tcdb(self):
        info = tripcon.analyze_tcdb(self.conn)
        self.assertEqual(info["integrity"], "ok")
        self.assertEqual(info["trips"], 1)
        self.assertEqual(info["log_entries"], 2)
        self.assertEqual(info["plotter_images"], 3)      # 211, 216, 217
        self.assertEqual(info["plotter_with_log"], 2)     # 216, 217
        self.assertEqual(info["plotter_trip_only"], 1)    # 211
        self.assertEqual(info["ships"], 1)
        self.assertEqual(info["persons"], 1)
        self.assertTrue(info["date_from"].startswith("2013-07-14"))

    def test_import_creates_stammdaten_with_photos(self):
        # Schiff und Person werden automatisch angelegt und mit Foto versehen
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        result = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result["ships_created"], 1)
        self.assertEqual(result["ship_photos"], 1)
        self.assertEqual(result["persons_created"], 1)
        self.assertEqual(result["person_photos"], 1)
        store = LogbookStore(db_path)
        ships = store.all_ships()
        self.assertEqual([s.name for s in ships], ["Tymanfaya"])
        self.assertIsNotNone(store.get_ship_photo(ships[0].id))
        persons = store.all_persons()
        self.assertEqual([p.last_name for p in persons], ["Haudenschild"])
        self.assertIsNotNone(store.get_person_photo(persons[0].id))

    def test_import_maps_ship_fields(self):
        # Alle relevanten Schiffs-Kennwerte werden aus TripCon übernommen
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        tripcon.import_into_saillog(self.conn, db_path)
        ship = LogbookStore(db_path).all_ships()[0]
        self.assertEqual(ship.ship_number, "CH-1234")
        self.assertEqual(ship.ship_type, "Sailing Yacht")
        self.assertEqual(ship.flag, "CH")
        self.assertEqual(ship.home_port, "Lavagna")
        self.assertEqual(ship.call_sign, "HBY1234")
        self.assertEqual(ship.mmsi, "269123456")
        self.assertAlmostEqual(ship.length_m, 13.5)
        self.assertAlmostEqual(ship.beam_m, 4.2)
        self.assertAlmostEqual(ship.clearance_height_m, 19.0)
        self.assertEqual(ship.keel_type, "Fin")
        self.assertAlmostEqual(ship.max_draft_m, 2.1)
        self.assertAlmostEqual(ship.displacement_t, 12.0)
        self.assertAlmostEqual(ship.echo_depth_m, 2.3)
        self.assertAlmostEqual(ship.log_correction, 1.02)
        self.assertEqual(ship.sails, "Sail")

    def test_import_maps_person_fields(self):
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        tripcon.import_into_saillog(self.conn, db_path)
        person = LogbookStore(db_path).all_persons()[0]
        self.assertEqual(person.first_name, "Peter")
        self.assertEqual(person.email, "peter@haudenschild.ch")
        self.assertEqual(person.nationality, "CH")
        self.assertEqual(person.passport_no, "X1234567")
        self.assertEqual(person.birth_date, "1965-03-12")   # Uhrzeit/Z entfernt
        self.assertEqual(person.street, "Seeweg 1")
        self.assertEqual(person.zip_code, "8000")
        self.assertEqual(person.city, "Zürich")
        self.assertEqual(person.birth_place, "Bern")

    def test_reimport_backfills_empty_fields(self):
        # Bereits angelegte Stammdaten (nur Name) werden beim Re-Import nachgefüllt,
        # bestehende Eingaben aber nicht überschrieben.
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        store = LogbookStore(db_path)
        from saillog.storage import Person, Ship
        store.add_ship(Ship(name="Tymanfaya", home_port="Eigenhafen"))
        store.add_person(Person(last_name="Haudenschild", first_name="Peter",
                                nationality="Schweiz"))
        result = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result["ships_matched"], 1)
        self.assertEqual(result["persons_matched"], 1)
        ship = store.all_ships()[0]
        self.assertEqual(ship.home_port, "Eigenhafen")   # Eingabe bleibt
        self.assertEqual(ship.call_sign, "HBY1234")       # leeres Feld nachgefüllt
        self.assertAlmostEqual(ship.log_correction, 1.02)  # Standard 1.0 -> gefüllt
        person = store.all_persons()[0]
        self.assertEqual(person.nationality, "Schweiz")   # Eingabe bleibt
        self.assertEqual(person.passport_no, "X1234567")   # leeres Feld nachgefüllt

    def test_import_stammdaten_idempotent(self):
        # Erneuter Import legt keine Dubletten an
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        tripcon.import_into_saillog(self.conn, db_path)
        result2 = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result2["ships_created"], 0)
        self.assertEqual(result2["ships_matched"], 1)
        self.assertEqual(result2["persons_created"], 0)
        self.assertEqual(result2["persons_matched"], 1)
        store = LogbookStore(db_path)
        self.assertEqual(len(store.all_ships()), 1)
        self.assertEqual(len(store.all_persons()), 1)

    def test_import_matches_existing_ship_without_duplicate(self):
        # Ein vorab angelegtes Schiff wird per Name erkannt, nicht dupliziert
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        store = LogbookStore(db_path)
        from saillog.storage import Ship
        store.add_ship(Ship(name="Tymanfaya", home_port="Lavagna"))
        result = tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(result["ships_created"], 0)
        self.assertEqual(result["ships_matched"], 1)
        self.assertEqual(result["ship_photos"], 1)
        ships = store.all_ships()
        self.assertEqual(len(ships), 1)
        self.assertEqual(ships[0].home_port, "Lavagna")  # bestehende Daten bleiben

    def test_import_creates_and_links_trips(self):
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        tripcon.import_into_saillog(self.conn, db_path)
        store = LogbookStore(db_path)
        trips = store.all_trips()
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0].start_location, "Vulcano")
        self.assertEqual(trips[0].end_location, "Isola Salina")
        self.assertEqual(trips[0].status, "closed")
        # Einträge sind dem Törn zugeordnet
        self.assertEqual(store.count(trip_id=trips[0].id), 2)
        # Erneuter Import verdoppelt die Törns nicht
        tripcon.import_into_saillog(self.conn, db_path)
        self.assertEqual(len(store.all_trips()), 1)

    def test_import_assigns_single_ship_to_trips(self):
        # B105_Trips ohne Schiff-Feld, aber genau ein Schiff -> allen Törns
        # dieses Schiff zuordnen (eine TripCon-Sicherung ist i.d.R. pro Boot).
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        tripcon.import_into_saillog(self.conn, db_path)
        store = LogbookStore(db_path)
        trip = store.all_trips()[0]
        self.assertIsNotNone(trip.ship_id)
        ship = store.get_ship(trip.ship_id)
        self.assertEqual(ship.name, "Tymanfaya")

    def test_reimport_backfills_trip_ship(self):
        # Törn zuerst ohne Schiff importiert (kein Schiff in der DB), dann mit
        db_path = os.path.join(self.tmp.name, "saillog.sqlite3")
        store = LogbookStore(db_path)
        # Törn vorab anlegen wie der Import es täte, aber ohne Schiff
        from saillog.storage import Trip
        store.add_trip(Trip(
            name="TripCon #37: Vulcano → Isola Salina", status="closed",
            start_location="Vulcano", start_dz="2013-07-14T07:55:00Z",
            end_location="Isola Salina", end_dz="2013-07-14T10:45:00Z"))
        tripcon.import_into_saillog(self.conn, db_path)
        trips = store.all_trips()
        self.assertEqual(len(trips), 1)          # kein Duplikat
        self.assertIsNotNone(trips[0].ship_id)   # Schiff nachgetragen

    def test_import_uses_per_trip_ship_column(self):
        # Eigene DB: zwei Schiffe, B105_Trips mit Ship-Spalte pro Törn
        path = os.path.join(self.tmp.name, "multi.tcdb")
        c = sqlite3.connect(path)
        cur = c.cursor()
        cur.execute("CREATE TABLE B105_Trips (ID INTEGER, Ship INTEGER, "
                    "FromLocation TEXT, FromDZ TEXT, ToLocation TEXT, ToDZ TEXT)")
        cur.execute("INSERT INTO B105_Trips VALUES "
                    "(1,2,'Kiel','2020-06-01 08:00:00.000Z','Rügen','2020-06-02 18:00:00.000Z')")
        cur.execute("INSERT INTO B105_Trips VALUES "
                    "(2,3,'Split','2021-07-01 08:00:00.000Z','Vis','2021-07-01 15:00:00.000Z')")
        cur.execute("CREATE TABLE B100_Log (ID INTEGER, Trip INTEGER, TripDZ TEXT, "
                    "CreateDZ TEXT, LogEvent INTEGER, Clouds INTEGER, "
                    "Precipitation INTEGER, Sight INTEGER)")
        cur.execute("INSERT INTO B100_Log VALUES (10,1,'2020-06-01 09:00:00.000Z','',NULL,NULL,NULL,NULL)")
        cur.execute("INSERT INTO B100_Log VALUES (11,2,'2021-07-01 09:00:00.000Z','',NULL,NULL,NULL,NULL)")
        cur.execute("CREATE TABLE VPosition (LogID INTEGER, Latitude REAL, Longitude REAL, AutoRecord INTEGER)")
        cur.execute("CREATE TABLE S003_Ships (ID INTEGER, ShipName TEXT)")
        cur.execute("INSERT INTO S003_Ships VALUES (2,'Nordwind')")
        cur.execute("INSERT INTO S003_Ships VALUES (3,'Adria')")
        c.commit(); c.close()

        conn = tripcon.connect(path)
        try:
            db_path = os.path.join(self.tmp.name, "multi.sqlite3")
            tripcon.import_into_saillog(conn, db_path)
            store = LogbookStore(db_path)
            by_route = {t.start_location: store.get_ship(t.ship_id).name
                        for t in store.all_trips()}
            self.assertEqual(by_route["Kiel"], "Nordwind")
            self.assertEqual(by_route["Split"], "Adria")
        finally:
            conn.close()

    def test_gpx_tracks(self):
        out = os.path.join(self.tmp.name, "tracks")
        from pathlib import Path
        files = tripcon.export_gpx_tracks(self.conn, tripcon.load_trips(self.conn), Path(out))
        self.assertEqual(files, 1)
        produced = os.listdir(out)
        self.assertEqual(len(produced), 1)
        with open(os.path.join(out, produced[0]), encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn('lat="38.416667"', content)
        self.assertIn("<time>2013-07-14T08:15:00Z</time>", content)
        self.assertIn("</gpx>", content)

    def test_extract_images(self):
        from pathlib import Path
        out = Path(self.tmp.name) / "bilder"
        counts = tripcon.extract_images(self.conn, out)
        self.assertEqual(counts.get("plotter"), 3)
        self.assertEqual(counts.get("wetter"), 1)
        self.assertEqual(counts.get("schiffe"), 1)
        self.assertEqual(counts.get("crew"), 1)
        self.assertTrue((out / "plotter").is_dir())
        files = os.listdir(out / "plotter")
        self.assertEqual(len(files), 3)


if __name__ == "__main__":
    unittest.main()
