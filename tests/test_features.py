"""Tests für die neuen Funktionen: Motor-Erkennung, Felder, Törns."""

import os
import sqlite3
import tempfile
import time
import unittest

from masarasi import nmea
from masarasi.livedata import LiveData
from masarasi.logbook import LogbookService
from masarasi.nmea import NmeaParser, engine_running
from masarasi.storage import LogbookStore, LogEntry, Trip


class EngineNmeaTest(unittest.TestCase):
    def setUp(self):
        self.parser = NmeaParser()

    def test_rpm_engine(self):
        result = self.parser.parse("$ERRPM,E,1,2100.0,10.5,A")
        self.assertAlmostEqual(result[nmea.ENGINE_RPM], 2100.0)

    def test_rpm_zero(self):
        result = self.parser.parse("$ERRPM,E,1,0.0,,A")
        self.assertEqual(result[nmea.ENGINE_RPM], 0.0)

    def test_rpm_invalid_status(self):
        self.assertEqual(self.parser.parse("$ERRPM,E,1,2100.0,,V"), {})

    def test_xdr_engine_rpm_and_oil(self):
        result = self.parser.parse("$IIXDR,T,1850,R,ENGINE#0,P,3.2,B,OILENGINE#0")
        self.assertAlmostEqual(result[nmea.ENGINE_RPM], 1850)
        self.assertAlmostEqual(result[nmea.OIL_PRESSURE], 3.2)

    def test_xdr_barometer_not_oil(self):
        # Luftdruck darf NICHT als Öldruck gewertet werden
        result = self.parser.parse("$IIXDR,P,1.013,B,Barometer")
        self.assertNotIn(nmea.OIL_PRESSURE, result)

    def test_engine_running_helper(self):
        self.assertEqual(engine_running({"engine_rpm": 2000}), 1)
        self.assertEqual(engine_running({"engine_rpm": 0}), 0)
        self.assertEqual(engine_running({"oil_pressure_bar": 3.0}), 1)
        self.assertIsNone(engine_running({"sog_kn": 5.0}))

    def test_engine_running_from_voltage(self):
        # Ohne Drehzahl entscheidet die Lichtmaschinen-Spannung
        self.assertEqual(engine_running({"alternator_v": 14.2}), 1)
        self.assertEqual(engine_running({"alternator_v": 12.6}), 0)
        # Drehzahl hat Vorrang: rpm>0 -> läuft, auch bei niedriger Spannung
        self.assertEqual(engine_running({"alternator_v": 12.5, "engine_rpm": 1538}), 1)
        self.assertEqual(engine_running({"alternator_v": 13.5, "engine_rpm": 0}), 0)

    def test_maretron_pmarepd(self):
        # Echte Maretron-Zeile: Kühlwasser 90.8°C, Bord 13.1V, Motorstunden 181.9h
        r = self.parser.parse("$PMAREPD,0,,,90.8,13.1,-0.1,181.9,,,0,0,-1,-1,A*01")
        self.assertAlmostEqual(r[nmea.ENGINE_TEMP], 90.8)
        self.assertAlmostEqual(r[nmea.ALT_VOLTAGE], 13.1)
        self.assertAlmostEqual(r[nmea.ENGINE_HOURS], 181.9)
        self.assertNotIn(nmea.OIL_PRESSURE, r)   # Öldruck-Feld leer (kein Sensor)

    def test_maretron_rpm(self):
        r = self.parser.parse("$IIRPM,E,0,1538,,A*58")
        self.assertAlmostEqual(r[nmea.ENGINE_RPM], 1538)

    def test_xdr_atmos_pressure(self):
        r = self.parser.parse("$IIXDR,P,101900,P,ENV_ATMOS_P")
        self.assertAlmostEqual(r[nmea.BARO], 1019.0)   # Pascal -> mbar

    def test_xdr_env_outair(self):
        r = self.parser.parse("$IIXDR,C,28.5,C,ENV_OUTAIR_T")
        self.assertAlmostEqual(r[nmea.AIR_TEMP], 28.5)

    def test_xdr_voltage(self):
        result = self.parser.parse("$IIXDR,U,14.2,V,ALTERNATOR")
        self.assertAlmostEqual(result[nmea.ALT_VOLTAGE], 14.2)

    def test_xdr_engine_temperature(self):
        result = self.parser.parse("$IIXDR,C,86.0,C,ENGINETEMP#0")
        self.assertAlmostEqual(result[nmea.ENGINE_TEMP], 86.0)

    def test_xdr_water_temp_not_engine(self):
        # Nicht-motorbezogene Temperatur wird nicht als Motortemperatur gewertet
        result = self.parser.parse("$IIXDR,C,19.0,C,AIRTEMP")
        self.assertNotIn(nmea.ENGINE_TEMP, result)

    def test_xdr_tachometer_any_id(self):
        result = self.parser.parse("$IIXDR,T,1850.0,R,#0")
        self.assertAlmostEqual(result[nmea.ENGINE_RPM], 1850.0)

    def test_real_bg_xdr(self):
        # Echter B&G-Satz: Luft, Krängung, Trimm, Luftdruck, Ruder
        line = ("$IIXDR,C,28.9,C,AIRTEMP,A,-0.9,D,HEEL,A,0.3,D,TRIM,"
                "P,1.021,B,BARO,A,32.2,D,RUDDER*0C")
        r = self.parser.parse(line)
        self.assertAlmostEqual(r[nmea.AIR_TEMP], 28.9)
        self.assertAlmostEqual(r[nmea.HEEL], -0.9)
        self.assertAlmostEqual(r[nmea.TRIM], 0.3)
        self.assertAlmostEqual(r[nmea.BARO], 1021.0)
        self.assertAlmostEqual(r[nmea.RUDDER], 32.2)
        # In diesem Satz stecken KEINE Motordaten
        self.assertNotIn(nmea.ENGINE_TEMP, r)
        self.assertNotIn(nmea.ENGINE_RPM, r)

    def test_real_bg_vlw_ground_fallback(self):
        # Echter B&G-Satz: Wasser-Gesamt leer -> Grund-Gesamt (Feld 5) als Log
        r = self.parser.parse("$SDVLW,,N,1203.2,N,1431.3,N,1431.8,N*4D")
        self.assertAlmostEqual(r[nmea.LOG_TOTAL], 1431.3)

    def test_vlw_log(self):
        result = self.parser.parse("$VWVLW,305.70,N,12.3,N")
        self.assertAlmostEqual(result[nmea.LOG_TOTAL], 305.70)

    def test_xdr_engine_hours(self):
        result = self.parser.parse("$IIXDR,G,1234.5,H,ENGINEHOURS#0")
        self.assertAlmostEqual(result[nmea.ENGINE_HOURS], 1234.5)


class EditEntryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from masarasi.storage import LogbookStore
        self.store = LogbookStore(os.path.join(self.tmp.name, "log.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_and_get(self):
        from masarasi.storage import LogEntry
        e = LogEntry.from_snapshot("2026-07-05T10:00:00Z", "manual",
                                   {"lat": 47.5, "lon": 9.4}, note="alt")
        self.store.add(e)
        loaded = self.store.get(e.id)
        self.assertEqual(loaded.note, "alt")
        self.assertIsNone(loaded.edited)
        # bearbeiten
        loaded.note = "korrigiert"
        loaded.mainsail = "Reff 2"
        loaded.edited = 1
        loaded.edited_dz = "2026-07-06T12:00:00Z"
        self.store.update(loaded)
        again = self.store.get(e.id)
        self.assertEqual(again.note, "korrigiert")
        self.assertEqual(again.mainsail, "Reff 2")
        self.assertEqual(again.edited, 1)
        # Messwerte unverändert
        self.assertEqual(again.lat, 47.5)


class StorageFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "log.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_fields_roundtrip(self):
        store = LogbookStore(self.db)
        entry = LogEntry.from_snapshot(
            timestamp="2026-07-05T10:00:00Z",
            entry_type="manual",
            measurements={"lat": 47.5, "lon": 9.4},
            engine_on=1,
            mainsail="Reff 1",
            genoa_percent=80.0,
            spinnaker=1,
            wave_height_m=1.5,
            cloud_cover="wolkig",
            precipitation="Regen",
            visibility="mässig",
        )
        store.add(entry)
        loaded = store.all()[0]
        self.assertEqual(loaded.engine_on, 1)
        self.assertEqual(loaded.mainsail, "Reff 1")
        self.assertEqual(loaded.genoa_percent, 80.0)
        self.assertEqual(loaded.spinnaker, 1)
        self.assertEqual(loaded.wave_height_m, 1.5)
        self.assertEqual(loaded.cloud_cover, "wolkig")
        self.assertEqual(loaded.precipitation, "Regen")
        self.assertEqual(loaded.visibility, "mässig")

    def test_migration_from_old_schema(self):
        # Alte DB nur mit den ursprünglichen Spalten anlegen
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "timestamp TEXT NOT NULL, entry_type TEXT, lat REAL, lon REAL, "
            "note TEXT, crew TEXT, location TEXT)"
        )
        conn.execute(
            "INSERT INTO log_entries (timestamp, entry_type, lat, lon, note) "
            "VALUES ('2020-01-01T00:00:00Z','manual',47.0,9.0,'alt')"
        )
        conn.commit()
        conn.close()
        # LogbookStore muss die fehlenden Spalten ergänzen und lesen können
        store = LogbookStore(self.db)
        entries = store.all()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].note, "alt")
        self.assertIsNone(entries[0].mainsail or None)
        # Neuer Eintrag mit neuen Feldern funktioniert
        store.add(LogEntry(timestamp="2026-01-01T00:00:00Z", mainsail="Voll"))
        self.assertEqual(store.count(), 2)


class TripsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LogbookStore(os.path.join(self.tmp.name, "log.sqlite3"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_trip_lifecycle(self):
        trip = Trip(name="Ägäis", start_location="Kos", start_dz="2026-06-01T08:00:00Z",
                    start_water_l=200, start_diesel_l=80, start_engine_hours=450.0,
                    start_log_nm=31000.0)
        self.store.add_trip(trip)
        self.assertIsNotNone(trip.id)
        self.assertEqual(self.store.open_trip().id, trip.id)

        # Einträge dem Törn zuordnen
        self.store.add(LogEntry(timestamp="2026-06-01T09:00:00Z", trip_id=trip.id))
        self.store.add(LogEntry(timestamp="2026-06-01T10:00:00Z", trip_id=trip.id))
        self.store.add(LogEntry(timestamp="2026-06-05T10:00:00Z"))  # ohne Törn
        self.assertEqual(self.store.count(trip_id=trip.id), 2)
        self.assertEqual(self.store.count(), 3)

        # Abschließen
        trip.end_location = "Rhodos"
        trip.end_dz = "2026-06-08T18:00:00Z"
        trip.status = "closed"
        self.store.update_trip(trip)
        self.assertIsNone(self.store.open_trip())
        loaded = self.store.get_trip(trip.id)
        self.assertEqual(loaded.end_location, "Rhodos")
        self.assertEqual(loaded.status, "closed")
        self.assertEqual(loaded.start_engine_hours, 450.0)

    def test_delete_trip_unassigns_entries(self):
        trip = Trip(name="Test", start_dz="2026-06-01T08:00:00Z")
        self.store.add_trip(trip)
        self.store.add(LogEntry(timestamp="2026-06-01T09:00:00Z", trip_id=trip.id))
        self.store.delete_trip(trip.id)
        self.assertIsNone(self.store.get_trip(trip.id))
        self.assertIsNone(self.store.all()[0].trip_id)


class LogbookServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LogbookStore(os.path.join(self.tmp.name, "log.sqlite3"))
        self.live = LiveData()
        self.service = LogbookService(self.store, self.live)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manual_derives_engine_on(self):
        self.live.update({"engine_rpm": 1800.0, "lat": 47.0, "lon": 9.0})
        entry = self.service.add_manual(note="Motor an")
        self.assertEqual(entry.engine_on, 1)

    def test_manual_engine_override(self):
        self.live.update({"engine_rpm": 1800.0})
        entry = self.service.add_manual(engine_on=0)
        self.assertEqual(entry.engine_on, 0)

    def test_auto_entry_uses_current_trip(self):
        trip = self.service.start_trip(Trip(name="X", start_location="A"))
        self.service.current_trip_id = trip.id
        self.live.update({"lat": 47.0, "lon": 9.0})
        entry = self.service.record_auto(trip_id=self.service.current_trip_id)
        self.assertEqual(entry.trip_id, trip.id)

    def test_conditions_logged_in_auto_and_manual(self):
        # Dauerhafte Maskenwerte werden bei Auto- und Manuell-Log mitgeschrieben
        self.live.update({"lat": 47.0, "lon": 9.0, "log_total_nm": 305.7})
        conditions = {
            "engine_mode": "aus",
            "mainsail": "Reff 1",
            "genoa_percent": 60.0,
            "spinnaker": 0,
            "wave_height_m": 1.5,
            "cloud_cover": "wolkig",
            "precipitation": "kein",
            "visibility": "gut",
            "logevent": "Routineeintrag",
            "note": "Bedingungen stabil",
        }
        auto = self.service.record_auto(conditions=conditions)
        self.assertEqual(auto.mainsail, "Reff 1")
        self.assertEqual(auto.genoa_percent, 60.0)
        self.assertEqual(auto.cloud_cover, "wolkig")
        self.assertEqual(auto.logevent, "Routineeintrag")
        self.assertEqual(auto.engine_on, 0)  # Motor-Modus 'aus'
        self.assertEqual(auto.log_total_nm, 305.7)

        manual = self.service.add_current(conditions=conditions, note="Halt")
        self.assertEqual(manual.mainsail, "Reff 1")
        self.assertEqual(manual.note, "Halt")

    def test_engine_mode_auto_derives_from_nmea(self):
        self.live.update({"engine_rpm": 1500.0})
        entry = self.service.record_auto(conditions={"engine_mode": "automatisch"})
        self.assertEqual(entry.engine_on, 1)


class SerialSourceTest(unittest.TestCase):
    def test_serial_source_does_not_crash(self):
        # Ohne echten COM-Port/pyserial darf die Quelle nicht abstürzen,
        # sondern nur einen Fehlerstatus melden.
        from masarasi.livedata import LiveData
        from masarasi.source import NmeaSource

        statuses = []
        src = NmeaSource(
            "COM_DOES_NOT_EXIST", 115200, LiveData(), protocol="serial",
            on_status=lambda s, _m: statuses.append(s), reconnect_delay=0.1,
        )
        src.start()
        time.sleep(0.4)
        src.stop()
        self.assertTrue(statuses)  # irgendein Status wurde gemeldet


if __name__ == "__main__":
    unittest.main()
