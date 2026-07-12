"""Tests für die Crewliste (Speicherung + HTML-Erzeugung)."""

import os
import tempfile
import unittest

from triplog import crewlist
from triplog.storage import CrewMember, LogbookStore, Person, Trip


class CrewStoreTest(unittest.TestCase):
    def setUp(self):
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.store = LogbookStore(self._path)

    def tearDown(self):
        os.unlink(self._path)

    def test_add_and_list_ordered(self):
        trip_id = self.store.add_trip(Trip(name="Adria"))
        self.store.add_crew(CrewMember(trip_id=trip_id, position="Crew",
                                       last_name="Meyer", sort_order=1))
        self.store.add_crew(CrewMember(trip_id=trip_id, position="Skipper",
                                       last_name="Haudenschild", sort_order=0))
        crew = self.store.crew_for_trip(trip_id)
        self.assertEqual([m.last_name for m in crew], ["Haudenschild", "Meyer"])
        self.assertEqual(crew[0].position, "Skipper")

    def test_update_and_delete(self):
        trip_id = self.store.add_trip(Trip(name="X"))
        cid = self.store.add_crew(CrewMember(trip_id=trip_id, last_name="Alt"))
        member = self.store.crew_for_trip(trip_id)[0]
        member.last_name = "Neu"
        member.passport_no = "C1234567"
        self.store.update_crew(member)
        self.assertEqual(self.store.crew_for_trip(trip_id)[0].last_name, "Neu")
        self.assertEqual(self.store.crew_for_trip(trip_id)[0].passport_no, "C1234567")
        self.store.delete_crew(cid)
        self.assertEqual(self.store.crew_for_trip(trip_id), [])

    def test_crew_is_trip_scoped(self):
        t1 = self.store.add_trip(Trip(name="A"))
        t2 = self.store.add_trip(Trip(name="B"))
        self.store.add_crew(CrewMember(trip_id=t1, last_name="Eins"))
        self.assertEqual(len(self.store.crew_for_trip(t1)), 1)
        self.assertEqual(len(self.store.crew_for_trip(t2)), 0)
        self.assertEqual(len(self.store.crew_for_trip(None)), 0)


class PersonRosterTest(unittest.TestCase):
    def setUp(self):
        fd, self._path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.store = LogbookStore(self._path)

    def tearDown(self):
        os.unlink(self._path)

    def test_save_and_list(self):
        self.store.save_person(Person(last_name="Meyer", first_name="Anna",
                                      passport_no="A1"))
        self.store.save_person(Person(last_name="Aebi", first_name="Urs"))
        persons = self.store.all_persons()
        self.assertEqual([p.last_name for p in persons], ["Aebi", "Meyer"])  # sortiert

    def test_save_person_upserts_no_duplicate(self):
        self.store.save_person(Person(last_name="Meyer", first_name="Anna",
                                      passport_no="A1"))
        # gleicher Name -> aktualisiert, keine Dublette
        self.store.save_person(Person(last_name="meyer", first_name="anna",
                                      passport_no="A2", nationality="CH"))
        persons = self.store.all_persons()
        self.assertEqual(len(persons), 1)
        self.assertEqual(persons[0].passport_no, "A2")
        self.assertEqual(persons[0].nationality, "CH")

    def test_empty_person_not_saved(self):
        self.assertIsNone(self.store.save_person(Person()))
        self.assertEqual(self.store.all_persons(), [])

    def test_delete_person(self):
        pid = self.store.save_person(Person(last_name="Weg", first_name="Da"))
        self.store.delete_person(pid)
        self.assertEqual(self.store.all_persons(), [])

    def test_extended_fields_and_crud(self):
        p = Person(last_name="Haudenschild", first_name="Peter",
                   email="peter@haudenschild.ch", street="Gässli 10",
                   zip_code="4704", city="Niederbipp", nationality="Switzerland",
                   passport_no="E5663801", birth_place="Niederbipp",
                   birth_date="18.02.1968")
        pid = self.store.add_person(p)              # „Neu"
        got = self.store.get_person(pid)
        self.assertEqual(got.email, "peter@haudenschild.ch")
        self.assertEqual(got.city, "Niederbipp")
        got.city = "Bern"                            # „Ändern"
        self.store.update_person(got)
        self.assertEqual(self.store.get_person(pid).city, "Bern")

    def test_person_photo_roundtrip(self):
        pid = self.store.add_person(Person(last_name="Foto", first_name="Test"))
        self.store.set_person_photo(pid, b"JPEGDATA")
        self.assertEqual(self.store.get_person_photo(pid), b"JPEGDATA")
        self.assertIn(pid, self.store.persons_with_photos())
        # Foto verschwindet mit der Person
        self.store.delete_person(pid)
        self.assertIsNone(self.store.get_person_photo(pid))


class CrewListHtmlTest(unittest.TestCase):
    def test_html_contains_boat_and_crew(self):
        boat = {"ship_name": "Masarasi", "ship_flag": "Schweiz",
                "call_sign": "HBY1234", "home_port": "Kastela"}
        crew = [
            CrewMember(position="Skipper", last_name="Haudenschild",
                       first_name="Peter", nationality="CH", passport_no="X999"),
            CrewMember(position="Crew", last_name="Meyer", first_name="Anna"),
        ]
        html = crewlist.build_html(boat, crew, place="Dubrovnik", date_str="09.07.2026")
        self.assertIn("Crewliste", html)
        self.assertIn("Crew List", html)          # zweisprachig
        self.assertIn("Masarasi", html)
        self.assertIn("HBY1234", html)
        self.assertIn("Haudenschild", html)
        self.assertIn("Skipper", html)
        self.assertIn("Dubrovnik, 09.07.2026", html)
        self.assertIn("window.print()", html)     # Druck-Knopf

    def test_html_escapes_and_pads_rows(self):
        # HTML-Sonderzeichen werden maskiert; leere Zeilen aufgefüllt.
        crew = [CrewMember(last_name="A & B <script>")]
        html = crewlist.build_html({"ship_name": "S/Y <Test>"}, crew)
        self.assertIn("A &amp; B &lt;script&gt;", html)
        self.assertIn("S/Y &lt;Test&gt;", html)
        # mindestens 8 nummerierte Zeilen (auch leer)
        self.assertIn(">8<", html)


if __name__ == "__main__":
    unittest.main()
