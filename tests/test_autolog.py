"""Tests für die AutoLog-Auslöser."""

import unittest

from saillog.autolog import AutoLogEngine, AutoLogSettings


def _engine(**kw):
    # alles aus, nur die im Test gewünschten Auslöser an
    base = dict(
        interval_enabled=False, sog_enabled=False, stw_enabled=False,
        course_enabled=False, depth_enabled=False, decel_enabled=False,
        distance_enabled=False,
    )
    base.update(kw)
    return AutoLogEngine(AutoLogSettings(**base))


class SettingsTest(unittest.TestCase):
    def test_roundtrip(self):
        s = AutoLogSettings(sog_threshold=7.5, depth_enabled=False)
        s2 = AutoLogSettings.from_dict(s.to_dict())
        self.assertEqual(s2.sog_threshold, 7.5)
        self.assertFalse(s2.depth_enabled)

    def test_from_none_gives_defaults(self):
        s = AutoLogSettings.from_dict(None)
        self.assertTrue(s.enabled)
        self.assertEqual(s.interval_seconds, 3600)

    def test_legacy_course_avg_seconds_maps_to_cooldown(self):
        # Alte Konfigurationen mit „course_avg_seconds" werden auf den neuen
        # Mindestabstand „course_cooldown_seconds" übernommen.
        s = AutoLogSettings.from_dict({"course_avg_seconds": 90})
        self.assertEqual(s.course_cooldown_seconds, 90)
        self.assertTrue(s.course_skip_motor)   # neuer Standard


class IntervalTest(unittest.TestCase):
    def test_fires_at_interval(self):
        e = _engine(interval_enabled=True, interval_seconds=60, align_boundary=False)
        e.start(1000.0)
        self.assertIsNone(e.evaluate({}, 1000.0))
        self.assertIsNone(e.evaluate({}, 1059.0))
        self.assertEqual(e.evaluate({}, 1060.0), "Intervall")
        self.assertIsNone(e.evaluate({}, 1061.0))          # neu geladen
        self.assertEqual(e.evaluate({}, 1120.0), "Intervall")

    def test_aligned_to_boundary(self):
        e = _engine(interval_enabled=True, interval_seconds=3600, align_boundary=True)
        e.start(100.0)                                     # nächste Grenze = 3600
        self.assertIsNone(e.evaluate({}, 100.0))
        self.assertEqual(e.evaluate({}, 3600.0), "Intervall")


class DistanceTest(unittest.TestCase):
    def test_fires_after_distance(self):
        e = _engine(distance_enabled=True, distance_threshold=0.5)
        e.note_entry(0.0, {"lat": 43.0, "lon": 16.0})
        self.assertIsNone(e.evaluate({"lat": 43.0, "lon": 16.0}, 1.0))
        # ~0.6 NM nördlich (0.01° Breite ≈ 0.6 NM)
        self.assertIn("Strecke", e.evaluate({"lat": 43.01, "lon": 16.0}, 2.0))


class DepthTest(unittest.TestCase):
    def test_shallow_edge_with_hysteresis(self):
        e = _engine(depth_enabled=True, depth_threshold=2.0)
        self.assertIsNone(e.evaluate({"depth_m": 5.0}, 0.0))
        self.assertIn("Flachwasser", e.evaluate({"depth_m": 1.5}, 1.0))
        self.assertIsNone(e.evaluate({"depth_m": 1.8}, 2.0))   # bleibt flach -> kein erneutes
        self.assertIsNone(e.evaluate({"depth_m": 3.5}, 3.0))   # tiefer -> wieder scharf
        self.assertIn("Flachwasser", e.evaluate({"depth_m": 1.0}, 4.0))


class DecelTest(unittest.TestCase):
    def test_abrupt_deceleration(self):
        e = _engine(decel_enabled=True, decel_threshold=5.0)
        self.assertIsNone(e.evaluate({"sog_kn": 10.0}, 0.0))
        self.assertEqual(e.evaluate({"sog_kn": 2.0}, 1.0), "Abrupte Verzögerung")

    def test_gentle_slowdown_ignored(self):
        e = _engine(decel_enabled=True, decel_threshold=5.0)
        e.evaluate({"sog_kn": 10.0}, 0.0)
        self.assertIsNone(e.evaluate({"sog_kn": 9.0}, 1.0))  # 1 kn/s < 5


class SogTest(unittest.TestCase):
    def test_rising_edge(self):
        e = _engine(sog_enabled=True, sog_threshold=8.0)
        self.assertIsNone(e.evaluate({"sog_kn": 5.0}, 0.0))
        self.assertIn("SOG", e.evaluate({"sog_kn": 9.0}, 1.0))
        self.assertIsNone(e.evaluate({"sog_kn": 9.0}, 2.0))   # bleibt schnell -> nur einmal
        self.assertIsNone(e.evaluate({"sog_kn": 6.0}, 3.0))   # wieder scharf
        self.assertIn("SOG", e.evaluate({"sog_kn": 8.5}, 4.0))


class CourseTest(unittest.TestCase):
    @staticmethod
    def _feed(e, headings, *, sog=4.0, start=0.0, dt=1.0, extra=None):
        """Speist eine Kursfolge im 1-s-Takt ein und zählt die Kurswechsel-Auslöser.

        Das Glättungsfenster (5 s) verlangt eine *fortlaufende* Drehung — ein
        Sprung über zwei Messwerte wird herausgemittelt. Deshalb geben die
        Tests realistische Kursrampen vor.
        """
        fires = 0
        t = start
        for h in headings:
            snap = {"cog_deg": h, "sog_kn": sog}
            if extra:
                snap.update(extra)
            r = e.evaluate(snap, t)
            if r and "Kurswechsel" in r:
                fires += 1
            t += dt
        return fires

    def test_course_change_fires(self):
        # Drehung 10° -> 60° (50°) über 10 s, danach halten: löst aus (> 40°).
        e = _engine(course_enabled=True, course_threshold=40.0)
        headings = [10.0 + i * 5.0 for i in range(11)] + [60.0] * 5
        self.assertGreaterEqual(self._feed(e, headings), 1)

    def test_small_change_ignored(self):
        # Drehung 10° -> 30° (nur 20°): darf nicht auslösen.
        e = _engine(course_enabled=True, course_threshold=40.0)
        headings = [10.0 + i * 2.0 for i in range(11)] + [30.0] * 6
        self.assertEqual(self._feed(e, headings), 0)

    def test_tack_single_entry(self):
        # Eine Wende (~90° beim Kreuzen) muss GENAU EINEN Eintrag erzeugen —
        # nicht zwei hintereinander. Der Mindestabstand (Cooldown) verhindert,
        # dass die Restdrehung nach dem ersten Auslösen einen zweiten Eintrag
        # ergibt.
        e = _engine(course_enabled=True, course_threshold=40.0,
                    course_cooldown_seconds=120)
        headings = [i * 9.0 for i in range(11)] + [90.0] * 15   # 0 -> 90, halten
        self.assertEqual(self._feed(e, headings), 1)

    def test_overshoot_correction_single_entry(self):
        # Wende auf 120° und danach Korrektur zurück auf 90° (normales Segeln):
        # solange das innerhalb der 2-Minuten-Sperre passiert, ergibt der ganze
        # Vorgang GENAU EINEN Eintrag — nicht mehrere durch die Nachkorrektur.
        e = _engine(course_enabled=True, course_threshold=40.0,
                    course_cooldown_seconds=120)
        up = [i * 12.0 for i in range(11)]          # 0 -> 120 über 10 s
        hold = [120.0] * 5
        back = [120.0 - i * 6.0 for i in range(6)]  # 120 -> 90 zurück korrigiert
        tail = [90.0] * 10                          # halten (alles < 120 s)
        self.assertEqual(self._feed(e, up + hold + back + tail), 1)

    def test_full_circle_fires(self):
        # 360°-Kreis bei 4 kn: der Kurs schließt sich (Anfang = Ende), muss aber
        # trotzdem auslösen, weil sich die Drehung fortlaufend aufsummiert.
        e = _engine(course_enabled=True, course_threshold=40.0,
                    course_cooldown_seconds=120)
        headings = [(i / 30.0 * 360.0) % 360.0 for i in range(31)]
        self.assertGreater(self._feed(e, headings, dt=2.0), 0)

    def test_motor_running_skips_course(self):
        # Bei laufendem Motor (course_skip_motor=True) wird ein Kurswechsel
        # nicht als Eintrag gewertet.
        e = _engine(course_enabled=True, course_threshold=40.0,
                    course_skip_motor=True)
        headings = [i * 9.0 for i in range(11)] + [90.0] * 5
        fires = self._feed(e, headings, extra={"engine_rpm": 1800.0})
        self.assertEqual(fires, 0)

    def test_motor_running_counts_when_not_skipped(self):
        # Ist die Motor-Ausnahme abgeschaltet, löst die Wende auch unter
        # Maschine aus.
        e = _engine(course_enabled=True, course_threshold=40.0,
                    course_skip_motor=False)
        headings = [i * 9.0 for i in range(11)] + [90.0] * 5
        fires = self._feed(e, headings, extra={"engine_rpm": 1800.0})
        self.assertGreaterEqual(fires, 1)

    def test_not_under_way_ignored(self):
        # Unter 2 kn (Manövrieren/Hafen) darf ein Kurswechsel nicht auslösen.
        e = _engine(course_enabled=True, course_threshold=40.0)
        headings = [i * 9.0 for i in range(11)] + [90.0] * 5
        self.assertEqual(self._feed(e, headings, sog=0.5), 0)


class MasterSwitchTest(unittest.TestCase):
    def test_disabled_never_fires(self):
        e = _engine(interval_enabled=True, interval_seconds=1, align_boundary=False)
        e.settings.enabled = False
        e.start(0.0)
        self.assertIsNone(e.evaluate({}, 100.0))


class TrackTest(unittest.TestCase):
    def _tengine(self, **kw):
        base = dict(track_enabled=True, track_interval_seconds=60,
                    track_course_threshold=10.0, track_min_move_nm=0.02)
        base.update(kw)
        return AutoLogEngine(AutoLogSettings(**base))

    def test_fires_on_course_change(self):
        e = self._tengine()
        e.start(1000.0)
        moving = {"lat": 43.0, "lon": 16.0, "sog_kn": 6.0}
        # erster Punkt (Intervall sofort ab start), danach note_track
        self.assertTrue(e.evaluate_track({**moving, "cog_deg": 90.0}, 1000.0))
        e.note_track(1000.0, {**moving, "cog_deg": 90.0})
        # kleiner Kurs + kurz danach -> kein Punkt
        self.assertIsNone(e.evaluate_track({**moving, "cog_deg": 95.0}, 1005.0))
        # deutliche Kursänderung -> Punkt, auch vor Ablauf des Intervalls
        self.assertEqual(
            e.evaluate_track({**moving, "cog_deg": 130.0}, 1010.0), "Kurswechsel")

    def test_interval_when_straight(self):
        e = self._tengine()
        e.start(0.0)
        p = {"lat": 43.0, "lon": 16.0, "sog_kn": 6.0, "cog_deg": 90.0}
        self.assertTrue(e.evaluate_track(p, 0.0))        # Startpunkt
        e.note_track(0.0, p)
        self.assertIsNone(e.evaluate_track(p, 30.0))     # gerade, Intervall nicht um
        # bewegte Position nach 60 s -> Intervall-Punkt
        p2 = {"lat": 43.02, "lon": 16.0, "sog_kn": 6.0, "cog_deg": 90.0}
        self.assertEqual(e.evaluate_track(p2, 60.0), "Intervall")

    def test_min_move_suppresses_interval_in_harbour(self):
        e = self._tengine()
        e.start(0.0)
        p = {"lat": 43.0, "lon": 16.0, "sog_kn": 0.0, "cog_deg": 90.0}
        self.assertTrue(e.evaluate_track(p, 0.0))
        e.note_track(0.0, p)
        # gleiche Position, Intervall um -> unterdrückt (Mindestbewegung)
        self.assertIsNone(e.evaluate_track(p, 60.0))

    def test_disabled_track(self):
        e = self._tengine(track_enabled=False)
        e.start(0.0)
        self.assertIsNone(
            e.evaluate_track({"lat": 43.0, "lon": 16.0, "cog_deg": 90.0}, 100.0))

    def test_no_position_no_point(self):
        e = self._tengine()
        e.start(0.0)
        self.assertIsNone(e.evaluate_track({"cog_deg": 90.0}, 100.0))


if __name__ == "__main__":
    unittest.main()
