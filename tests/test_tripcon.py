"""Tests für den TripCon-Import gegen eine nachgebaute TripCon-DB."""

import os
import sqlite3
import struct
import tempfile
import unittest
import zlib

from masarasi.storage import LogbookStore
from masarasi import tripcon


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

    c.execute("CREATE TABLE B109_Weather (ID INTEGER, Comment TEXT, ValueType INTEGER, Value BLOB, ReportDZ TEXT, Filename TEXT, CreateDZ TEXT, Active INTEGER, TextValue TEXT)")
    c.execute("INSERT INTO B109_Weather VALUES (13,'',3,?, '2013-09-26 08:20:16.000Z','OpenPortGuide','2013-10-22 08:20:37.841Z',1,NULL)", (_jpg(),))

    c.execute("CREATE TABLE S003_Ships (ID INTEGER, ShipName TEXT, Picture BLOB)")
    c.execute("INSERT INTO S003_Ships VALUES (2,'Tymanfaya',?)", (_jpg(),))
    c.execute("CREATE TABLE S006_Persons (ID INTEGER, LastName TEXT, Picture BLOB)")
    c.execute("INSERT INTO S006_Persons VALUES (15,'Haudenschild',?)", (_jpg(),))

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

    def test_import_into_masarasi(self):
        db_path = os.path.join(self.tmp.name, "masarasi.sqlite3")
        result = tripcon.import_into_masarasi(self.conn, db_path)
        self.assertEqual(result["entries"], 2)
        store = LogbookStore(db_path)
        self.assertEqual(store.count(), 2)
        # Erneuter Import ersetzt, verdoppelt nicht
        result2 = tripcon.import_into_masarasi(self.conn, db_path)
        self.assertEqual(result2["entries"], 2)
        self.assertEqual(store.count(), 2)

    def test_import_links_entry_image(self):
        db_path = os.path.join(self.tmp.name, "masarasi.sqlite3")
        result = tripcon.import_into_masarasi(self.conn, db_path)
        # B104_BinDat 216 zeigt über LogID 1362 auf einen Eintrag
        self.assertEqual(result["image_method"], "bindat_logid")
        self.assertEqual(result["images"], 1)
        store = LogbookStore(db_path)
        # Genau ein Eintrag hat ein Bild bekommen
        with_images = store.entries_with_images()
        self.assertEqual(len(with_images), 1)
        (entry_id,) = tuple(with_images)
        self.assertIsNotNone(store.get_image(entry_id))

    def test_import_stammdaten_photos(self):
        db_path = os.path.join(self.tmp.name, "masarasi.sqlite3")
        store = LogbookStore(db_path)
        from masarasi.storage import Person, Ship
        ship_id = store.add_ship(Ship(name="Tymanfaya"))
        person_id = store.add_person(Person(last_name="Haudenschild"))
        result = tripcon.import_into_masarasi(self.conn, db_path)
        self.assertEqual(result["ship_photos"], 1)
        self.assertEqual(result["person_photos"], 1)
        self.assertIsNotNone(store.get_ship_photo(ship_id))
        self.assertIsNotNone(store.get_person_photo(person_id))

    def test_import_stammdaten_photos_skips_unknown(self):
        # Ohne passende Stammdaten werden keine Fotos übernommen
        db_path = os.path.join(self.tmp.name, "masarasi.sqlite3")
        result = tripcon.import_into_masarasi(self.conn, db_path)
        self.assertEqual(result["ship_photos"], 0)
        self.assertEqual(result["person_photos"], 0)

    def test_import_creates_and_links_trips(self):
        db_path = os.path.join(self.tmp.name, "masarasi.sqlite3")
        tripcon.import_into_masarasi(self.conn, db_path)
        store = LogbookStore(db_path)
        trips = store.all_trips()
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0].start_location, "Vulcano")
        self.assertEqual(trips[0].end_location, "Isola Salina")
        self.assertEqual(trips[0].status, "closed")
        # Einträge sind dem Törn zugeordnet
        self.assertEqual(store.count(trip_id=trips[0].id), 2)
        # Erneuter Import verdoppelt die Törns nicht
        tripcon.import_into_masarasi(self.conn, db_path)
        self.assertEqual(len(store.all_trips()), 1)

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
        self.assertEqual(counts.get("plotter"), 2)
        self.assertEqual(counts.get("wetter"), 1)
        self.assertEqual(counts.get("schiffe"), 1)
        self.assertEqual(counts.get("crew"), 1)
        self.assertTrue((out / "plotter").is_dir())
        files = os.listdir(out / "plotter")
        self.assertEqual(len(files), 2)


if __name__ == "__main__":
    unittest.main()
