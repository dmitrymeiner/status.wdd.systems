"""Kontrola výpočtů dostupnosti (SLA 2.1–2.5, 3.3). Spuštění: python3 -m unittest discover tests"""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build  # noqa: E402

TZ = ZoneInfo("Europe/Prague")


def t(text):
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp())


def svc(rule, keys, sla=True, since=date(2026, 9, 1)):
    return {"key": "x", "name": "X", "desc": "", "rule": rule, "sla": sla, "since_date": since,
            "parts": [{"key": f"x/{k}", "name": k, "monitor": i + 1} for i, k in enumerate(keys)],
            "word_down": "výpadek", "label_ok": "V provozu", "label_bad": "Výpadek", "show_pct": True}


def maint(start, end, notice, services=("x",)):
    return {"kind": "udrzba", "slug": "u", "title": "U", "start": t(start), "end": t(end), "services": list(services),
            "notice": t(notice), "valid": t(notice) <= t(start[:10] + " 00:00") - 3 * 86400, "impact": "", "body": ""}


def security(start, end, services=("x",)):
    """Bezpečnostní údržba bez oznámení (SLA 3.3)."""
    return {"kind": "udrzba", "slug": "b", "title": "B", "start": t(start), "end": t(end), "services": list(services),
            "notice": None, "valid": False, "security": True, "impact": "", "body": ""}


class Intervals(unittest.TestCase):
    def test_union_intersect_subtract(self):
        self.assertEqual(build.union([(5, 10), (1, 3), (2, 6)]), [(1, 10)])
        self.assertEqual(build.intersect([(0, 10)], [(5, 15), (20, 30)]), [(5, 10)])
        self.assertEqual(build.subtract([(0, 10)], [(2, 3), (5, 7)]), [(0, 2), (3, 5), (7, 10)])
        self.assertEqual(build.measure([(0, 10), (5, 20)]), 20)


class Availability(unittest.TestCase):
    def setUp(self):
        self.clock = build.Clock(TZ, t("2026-11-15 12:00"))

    def run_svc(self, s, downs, events=()):
        parts = {p["key"]: {"status": "up", "down": downs.get(p["key"].split("/")[1], [])} for p in s["parts"]}
        return build.analyse(s, parts, list(events), self.clock)

    def month(self, r, y, m):
        return next(x for x in r["months"] if (x["year"], x["month"]) == (y, m))

    def test_dns_needs_both_down(self):
        # ns1 10:00–10:30, ns2 10:20–10:40 → výpadek DNS jen 10:20–10:30 (SLA 2.1: stačí jeden jmenný server)
        r = self.run_svc(svc("kterakoli", ["ns1", "ns2"]), {
            "ns1": [(t("2026-10-10 10:00"), t("2026-10-10 10:30"))],
            "ns2": [(t("2026-10-10 10:20"), t("2026-10-10 10:40"))]})
        self.assertEqual(self.month(r, 2026, 10)["down"], 600)
        day = next(d for d in r["days"] if d["date"] == date(2026, 10, 10))
        self.assertEqual(day["level"], "warn")

    def test_email_any_part_down(self):
        r = self.run_svc(svc("vse", ["smtp", "imap"]), {
            "smtp": [(t("2026-10-10 10:00"), t("2026-10-10 10:30"))],
            "imap": [(t("2026-10-10 10:20"), t("2026-10-10 10:40"))]})
        self.assertEqual(self.month(r, 2026, 10)["down"], 40 * 60)
        day = next(d for d in r["days"] if d["date"] == date(2026, 10, 10))
        self.assertEqual(day["level"], "bad")

    def test_sla_threshold_216_minutes(self):
        # září = 30 dní; 216 min výpadku = přesně 99,5 % (SLA 2.2), 217 min už pod
        s = svc("vse", ["web"])
        r = self.run_svc(s, {"web": [(t("2026-09-10 01:00"), t("2026-09-10 01:00") + 216 * 60)]})
        m = self.month(r, 2026, 9)
        self.assertAlmostEqual(m["pct"], 99.5)
        self.assertEqual(build.pct(m["pct"]), "99,50 %")
        r = self.run_svc(s, {"web": [(t("2026-09-10 01:00"), t("2026-09-10 01:00") + 217 * 60)]})
        self.assertLess(self.month(r, 2026, 9)["pct"], build.SLA_TARGET)
        self.assertEqual(build.pct(self.month(r, 2026, 9)["pct"]), "99,49 %")

    def test_maintenance_needs_notice(self):
        s = svc("vse", ["web"])
        down = {"web": [(t("2026-10-20 21:00"), t("2026-10-20 21:40"))]}
        ok = self.run_svc(s, down, [maint("2026-10-20 21:00", "2026-10-20 22:00", "2026-10-16 10:00")])
        self.assertEqual(self.month(ok, 2026, 10)["down"], 0)
        late = self.run_svc(s, down, [maint("2026-10-20 21:00", "2026-10-20 22:00", "2026-10-19 10:00")])
        self.assertEqual(self.month(late, 2026, 10)["down"], 40 * 60)

    def test_security_restart_not_counted(self):
        # SLA 3.3: restart 66 s během bezpečnostní údržby se nezapočítá, den je „údržba“, ne výpadek
        s = svc("vse", ["web"])
        down = {"web": [(t("2026-11-06 16:41"), t("2026-11-06 16:41") + 66)]}
        r = self.run_svc(s, down, [security("2026-11-06 16:37", "2026-11-06 21:50")])
        self.assertEqual(self.month(r, 2026, 11)["down"], 0)
        day = next(d for d in r["days"] if d["date"] == date(2026, 11, 6))
        self.assertEqual(day["level"], "maint")
        # bez záznamu o údržbě je to krátký výpadek a započítá se
        r = self.run_svc(s, down)
        self.assertEqual(self.month(r, 2026, 11)["down"], 66)
        self.assertEqual(next(d for d in r["days"] if d["date"] == date(2026, 11, 6))["level"], "warn")

    def test_security_restart_limits(self):
        s = svc("vse", ["web"])
        win = security("2026-10-05 20:00", "2026-10-25 23:00")
        # jeden restart 20 min → nezapočítá se 15 min, započítá se 5 min
        r = self.run_svc(s, {"web": [(t("2026-10-05 21:00"), t("2026-10-05 21:20"))]}, [win])
        self.assertEqual(self.month(r, 2026, 10)["down"], 5 * 60)
        self.assertEqual(next(d for d in r["days"] if d["date"] == date(2026, 10, 5))["level"], "warn")
        # pět restartů po 14 min = 70 min → nad měsíční limit 60 min se započítá 10 min
        downs = [(t(f"2026-10-{d:02d} 22:00"), t(f"2026-10-{d:02d} 22:14")) for d in (6, 8, 10, 12, 14)]
        r = self.run_svc(s, {"web": downs}, [win])
        self.assertEqual(self.month(r, 2026, 10)["down"], 10 * 60)
        # výpadek mimo okno údržby se započítá celý
        r = self.run_svc(s, {"web": [(t("2026-10-26 10:00"), t("2026-10-26 10:05"))]}, [win])
        self.assertEqual(self.month(r, 2026, 10)["down"], 5 * 60)

    def test_security_without_outage_is_hidden(self):
        # kolo aktualizací bez výpadku: den zelený, karta se neukáže
        s = svc("vse", ["web"])
        ev = security("2026-11-04 18:15", "2026-11-04 18:30")
        r = self.run_svc(s, {}, [ev])
        self.assertEqual(next(d for d in r["days"] if d["date"] == date(2026, 11, 4))["level"], "ok")
        self.assertEqual(build.visible_events([ev], [r]), [])
        # s výpadkem během okna se karta ukáže
        r = self.run_svc(s, {"web": [(t("2026-11-04 18:20"), t("2026-11-04 18:21"))]}, [ev])
        self.assertEqual(build.visible_events([ev], [r]), [ev])

    def test_security_dns_one_server(self):
        # restart jednoho NS během bezpečnostní údržby = omezený provoz, den „údržba“, nic se nezapočítá
        s = svc("kterakoli", ["ns1", "ns2"])
        r = self.run_svc(s, {"ns1": [(t("2026-11-06 16:47"), t("2026-11-06 16:49"))]},
                         [security("2026-11-06 16:37", "2026-11-06 17:00")])
        self.assertEqual(self.month(r, 2026, 11)["down"], 0)
        self.assertEqual(next(d for d in r["days"] if d["date"] == date(2026, 11, 6))["level"], "maint")

    def test_running_month_and_start(self):
        # listopad se počítá jen do „teď“, září jen od začátku měření
        s = svc("vse", ["web"], since=date(2026, 9, 16))
        r = self.run_svc(s, {})
        nov = self.month(r, 2026, 11)
        self.assertTrue(nov["running"])
        sep = self.month(r, 2026, 9)
        self.assertEqual(sep["partial_from"], t("2026-09-16 00:00"))
        self.assertFalse(any(m["month"] == 8 for m in r["months"]))

    def test_backup_checks(self):
        now = t("2026-11-15 12:00")
        h = 3600
        # ok, ok, chyba, chyba, ok → výpadek od první chyby do další úspěšné kontroly
        data = {"kontroly": [[now - 60 * h, 1, 45, 0], [now - 48 * h, 1, 45, 0], [now - 36 * h, 0, 45, 2],
                             [now - 24 * h, 0, 45, 1], [now - 12 * h, 1, 45, 0]]}
        self.assertEqual(build.checks_to_part(data, now), ("up", [(now - 36 * h, now - 12 * h)]))
        # poslední kontrola neúspěšná → výpadek trvá
        data["kontroly"].append([now - 1 * h, 0, 45, 3])
        self.assertEqual(build.checks_to_part(data, now), ("down", [(now - 36 * h, now - 12 * h), (now - h, now)]))
        # mgmt neposílá přes 30 h → stav neznámý, žádný výpadek navíc
        self.assertEqual(build.checks_to_part({"kontroly": [[now - 40 * h, 1, 45, 0]]}, now), ("unknown", []))
        self.assertEqual(build.checks_to_part({"kontroly": []}, now), ("unknown", []))

    def test_dst_day_has_25_hours(self):
        c = self.clock
        self.assertEqual(c.epoch(date(2026, 10, 26)) - c.epoch(date(2026, 10, 25)), 25 * 3600)


if __name__ == "__main__":
    unittest.main()
