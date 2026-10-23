import os
import tempfile
import unittest
from datetime import date

os.environ["PLAASKIT_HOME"] = tempfile.mkdtemp()

from plaaskit import alerts, db, sms, trace, water  # noqa: E402
from plaaskit.cli import main  # noqa: E402


def day(d, tmin=10, tmax=25, rain=0, et0=4, gust=20, rh=50):
    return {"date": d, "tmin": tmin, "tmax": tmax, "rain": rain, "et0": et0, "gust": gust, "rh": rh}


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        trace.init_farm(self.conn, "Mvelase Farm", "mvf", -29.6, 30.4, owner="T. Mvelase", allocation=1000)
        trace.add_field(self.conn, "North", 2.0)
        trace.add_planting(self.conn, "North", "Maize", date(2026, 9, 1))


class TraceTests(Base):
    def test_pesticide_requires_registration_number(self):
        p = trace.resolve_planting(self.conn, "north")
        with self.assertRaises(ValueError):
            trace.add_input(self.conn, p, date(2026, 10, 1), "fungicide", "Copper")

    def test_phi_blocks_early_harvest_and_force_is_audited(self):
        p = trace.resolve_planting(self.conn, "North")
        trace.add_input(self.conn, p, date(2026, 10, 10), "insecticide", "Karate", "lambda", "L1234", 1, "l/ha", 7)
        with self.assertRaises(trace.ComplianceError):
            trace.add_harvest(self.conn, p, date(2026, 10, 15), 100)
        lot = trace.add_harvest(self.conn, p, date(2026, 10, 15), 100, force=True)
        self.assertFalse(trace.trace_lot(self.conn, lot)["phi_compliant"])
        ok = trace.add_harvest(self.conn, p, date(2026, 10, 17), 100)
        self.assertTrue(trace.trace_lot(self.conn, ok)["phi_compliant"])

    def test_lot_codes_are_sequential_and_traceable(self):
        p = trace.resolve_planting(self.conn, "North")
        a = trace.add_harvest(self.conn, p, date(2026, 12, 1), 500, "Packhouse")
        b = trace.add_harvest(self.conn, p, date(2026, 12, 1), 300)
        self.assertEqual((a, b), ("MVF-261201-MAI-01", "MVF-261201-MAI-02"))
        text = trace.format_trace(trace.trace_lot(self.conn, a.lower()))
        self.assertIn("Mvelase Farm", text)

    def test_register_csv(self):
        p = trace.resolve_planting(self.conn, "North")
        trace.add_input(self.conn, p, date(2026, 10, 10), "fertiliser", "LAN", dose=100, unit="kg/ha")
        rows = trace.input_register(self.conn, date(2026, 1, 1), date(2026, 12, 31))
        path = os.path.join(os.environ["PLAASKIT_HOME"], "r.csv")
        trace.write_register_csv(path, rows)
        with open(path) as fh:
            self.assertIn("LAN", fh.read())


class AlertTests(Base):
    def test_frost_heat_wind_rain(self):
        fc = [day("2026-10-24", tmin=-1), day("2026-10-25", tmin=1.5), day("2026-10-26", tmax=37),
              day("2026-10-27", rain=40, gust=70)]
        kinds = [a.kind for a in alerts.evaluate(fc)]
        for k in ("frost_hard", "frost", "heat", "rain", "wind"):
            self.assertIn(k, kinds)

    def test_fungal_needs_consecutive_humid_days(self):
        one = [day("2026-10-24", rh=90), day("2026-10-25", rh=40)]
        two = [day("2026-10-24", rh=90), day("2026-10-25", rh=90)]
        self.assertNotIn("fungal", [a.kind for a in alerts.evaluate(one)])
        self.assertIn("fungal", [a.kind for a in alerts.evaluate(two)])

    def test_armyworm_only_for_maize(self):
        fc = [day("2026-10-24", tmin=17, tmax=30)]
        self.assertTrue(any(a.kind == "armyworm" for a in alerts.evaluate(fc, {"maize"})))
        self.assertFalse(any(a.kind == "armyworm" for a in alerts.evaluate(fc, {"citrus"})))

    def test_languages_render(self):
        a = alerts.Alert("frost", "medium", "2026-10-24", {"tmin": 1})
        self.assertIn("Frost risk", alerts.render(a, "en"))
        self.assertIn("Rypgevaar", alerts.render(a, "af"))

    def test_dispatch_dedupes(self):
        self.conn.execute("INSERT INTO subscriber (name, phone) VALUES ('A', '+27821234567')")
        sent = []
        orig = sms.send
        sms.send = lambda p, t: (sent.append((p, t)) or (True, "ok"))
        try:
            farm = trace.get_farm(self.conn)
            al = [alerts.Alert("frost", "medium", "2026-10-24", {"tmin": 1})]
            alerts.dispatch(self.conn, farm, al, send=True)
            alerts.dispatch(self.conn, farm, al, send=True)
        finally:
            sms.send = orig
        self.assertEqual(len(sent), 1)

    def test_run_check_creates_irrigation_alert(self):
        today = date(2026, 10, 23)
        days = [day(f"2026-10-{d}", et0=6) for d in range(16, 29)]
        out = []
        found = alerts.run_check(self.conn, fetch=lambda la, lo: (days, False), today=today, out=out.append)
        self.assertIn("irrigate", [a.kind for a in found])


class WaterTests(Base):
    def test_kc_curve(self):
        self.assertAlmostEqual(water.kc("maize", 0), 0.30)
        self.assertAlmostEqual(water.kc("maize", 70), 1.20)
        self.assertEqual(water.kc("citrus", 5), 0.65)
        self.assertEqual(water.kc("unknowncrop", 5), water.DEFAULT_KC)

    def test_log_converts_units_and_report(self):
        f = trace.get_field(self.conn, "North")
        self.assertEqual(water.log_water(self.conn, f, date(2026, 10, 1), "borehole", mm=10), (10.0, 200.0))
        self.assertEqual(water.log_water(self.conn, f, date(2026, 10, 2), m3=40)[0], 2.0)
        rows, total, alloc, pct = water.usage_summary(self.conn, 2026)
        self.assertEqual((total, alloc, pct), (240.0, 1000, 24.0))

    def test_wait_when_rain_forecast(self):
        f = trace.get_field(self.conn, "North")
        today = date(2026, 10, 23)
        days = [day(f"2026-10-{d}", et0=6) for d in range(16, 23)] + [day("2026-10-23", rain=30), day("2026-10-24")]
        self.assertEqual(water.advise_field(self.conn, f, days, today)[0]["action"], "wait")


class SmsTests(unittest.TestCase):
    def test_msisdn(self):
        for n in ("0821234567", "082 123 4567", "27821234567", "+27821234567"):
            self.assertEqual(sms.normalise_msisdn(n), "+27821234567")
        with self.assertRaises(ValueError):
            sms.normalise_msisdn("12345")


class CliSmoke(unittest.TestCase):
    def test_end_to_end(self):
        run = lambda *a: main(list(a))  # noqa: E731
        self.assertEqual(run("init", "--name", "T Farm", "--code", "TF", "--lat", "-26", "--lon", "28"), 0)
        self.assertEqual(run("field", "add", "A", "--ha", "1.5"), 0)
        self.assertEqual(run("plant", "add", "--field", "A", "--crop", "tomato", "--date", "2026-08-01"), 0)
        self.assertEqual(run("spray", "add", "--field", "A", "--product", "X", "--reg-no", "L1",
                             "--phi", "7", "--date", "2026-10-01"), 0)
        self.assertEqual(run("harvest", "add", "--field", "A", "--kg", "50", "--date", "2026-10-03"), 2)
        self.assertEqual(run("harvest", "add", "--field", "A", "--kg", "50", "--date", "2026-10-09"), 0)
        self.assertEqual(run("trace", "TF-261009-TOM-01"), 0)
        self.assertEqual(run("field", "add", "A", "--ha", "1"), 2)


if __name__ == "__main__":
    unittest.main()
