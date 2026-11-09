#!/usr/bin/env python3
"""Build status.wdd.systems -> dist/ (statický web, bez cookies a bez externích zdrojů).

Vstupy: sluzby.toml (služby a monitory), incidenty/*.md, udrzba/*.md, data/vypadky.json (uložené výpadky)
a s --fetch aktuální data z UptimeRobotu (UPTIMEROBOT_API_KEY, volitelně UPTIMEROBOT_API=v2).

    python3 build.py            # bez UptimeRobotu: uložená historie, aktuální stav „neznámý“
    python3 build.py --fetch    # Actions: načte UptimeRobot, doplní data/vypadky.json, postaví web
    python3 build.py --sample   # ukázková data pro kontrolu vzhledu (nic neukládá)
"""
import argparse
import base64
import calendar
import html
import json
import math
import os
import random
import re
import shutil
import sys
import time
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import markdown

import uptimerobot

ROOT = Path(__file__).resolve().parent
SRC, OUT = ROOT / "src", ROOT / "dist"
STORE = ROOT / "data" / "vypadky.json"
BACKUPS = ROOT / "data" / "zalohy.json"   # kontroly záloh, zapisuje status-zalohy na mgmt (repo wdd-ops)
SITE_URL = "https://status.wdd.systems"
DOCS_URL = "https://docs.wdd.systems"

PROVIDER = "<b>WD&amp;D</b> · Dmitry Meiner · IČO 71492780 · Na Domovině 1081/2, 142 00 Praha – Libuš · plátce DPH"
CONTACT = "support@wdd.systems · +420 727 943 449"

HISTORY_DAYS = 90
SLA_TARGET = 99.5     # SLA 2.1
SLA_MONTHS = 12
KEEP_DAYS = 400       # jak dlouho držíme výpadky v data/vypadky.json
NOTICE_DAYS = 3       # SLA 3.2: plánovaná údržba ohlášená nejméně 3 dny předem
RESTART_MAX_MIN = 15  # SLA 3.3: krátký restart při bezpečnostní údržbě se nezapočítá jednotlivě do 15 min
RESTART_MONTH_MIN = 60  # SLA 3.3: … a v součtu nejvýše 60 min za měsíc; doba nad limit se započítá
BAD_DAY_MIN = 30      # den s výpadkem od 30 min je červený
LONG_OUTAGE_MIN = 60  # SLA 5.6: výpadek nad 60 min = e-mail klientům, chceme k němu incident
STALE_MIN = 30        # po kolika minutách bez aktualizace stránka upozorní, že data jsou stará
CHECK_STALE_H = 30    # kontrola záloh z mgmt starší než tohle = stav záloh neověřený (ne výpadek)

COLORS = {"ok": "#1f9d57", "warn": "#d48806", "bad": "#d93025", "maint": "#2e4bdb", "unknown": "#8f99a8", "paused": "#8f99a8"}
MONTHS = ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]
MONTHS_GEN = ["ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září", "října", "listopadu", "prosince"]
WEEKDAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]
NB = " "


def warn(msg):
    """Varování pro log Actions (zobrazí se jako anotace běhu)."""
    print(f"::warning::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"VAROVÁNÍ: {msg}")


def fail(msg):
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"CHYBA: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------- intervaly [start, end) v epoch sekundách ----------

def union(iv):
    out = []
    for s, e in sorted((s, e) for s, e in iv if e > s):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def intersect(a, b):
    a, b, out, i, j = union(a), union(b), [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a, b):
    out = []
    b = union(b)
    for s, e in union(a):
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def clip(iv, lo, hi):
    return [(max(s, lo), min(e, hi)) for s, e in iv if e > lo and s < hi]


def measure(iv):
    return sum(e - s for s, e in union(iv))


# ---------- formátování ----------

class Clock:
    def __init__(self, tz, now):
        self.tz, self.now = tz, now

    def local(self, epoch):
        return datetime.fromtimestamp(epoch, self.tz)

    def epoch(self, d, hh=0, mm=0):
        return int(datetime(d.year, d.month, d.day, hh, mm, tzinfo=self.tz).timestamp())

    def parse(self, text, field, path):
        text = (text or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=self.tz)
                return int(dt.timestamp())
            except ValueError:
                pass
        fail(f"{path}: {field} „{text}“ není ve tvaru 2026-11-05 21:00")

    def date(self, epoch, year=True):
        d = self.local(epoch)
        return f"{d.day}.{NB}{d.month}.{NB}{d.year}" if year else f"{d.day}.{NB}{d.month}."

    def time(self, epoch):
        return self.local(epoch).strftime("%H:%M")

    def dt(self, epoch):
        return f"{self.date(epoch)} {self.time(epoch)}"

    def range(self, start, end):
        if end is None:
            return f"od {self.dt(start)}"
        if self.local(start).date() == self.local(end).date():
            return f"{self.date(start)} {self.time(start)}–{self.time(end)}"
        return f"{self.dt(start)} – {self.dt(end)}"


def plural(n, one, few, many):
    return one if n == 1 else few if 2 <= n <= 4 else many


def dur(seconds):
    m = math.ceil(seconds / 60)
    if m < 1:
        return f"méně než 1{NB}min"
    if m < 60:
        return f"{m}{NB}min"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}{NB}h {m}{NB}min" if m else f"{h}{NB}h"
    d, h = divmod(h, 24)
    return f"{d}{NB}{plural(d, 'den', 'dny', 'dní')} {h}{NB}h" if h else f"{d}{NB}{plural(d, 'den', 'dny', 'dní')}"


def pct(value):
    """Procenta zaokrouhlená dolů na setiny (99,4999 nesmí vypadat jako 99,50)."""
    if value >= 100:
        return f"100{NB}%"
    v = math.floor(value * 100) / 100
    return f"{v:.2f}".replace(".", ",") + f"{NB}%"


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- konfigurace a události ----------

def load_config(sample=False):
    cfg = tomllib.loads((ROOT / "sluzby.toml").read_text(encoding="utf-8"))
    tz = ZoneInfo(cfg.get("casove_pasmo", "Europe/Prague"))
    services = []
    for s in cfg.get("sluzba", []):
        if s.get("pravidlo") not in ("vse", "kterakoli"):
            fail(f"sluzby.toml: služba {s.get('klic')}: pravidlo musí být „vse“ nebo „kterakoli“")
        if not isinstance(s.get("od"), date):
            fail(f"sluzby.toml: služba {s.get('klic')}: od musí být datum, např. od = 2026-11-05")
        parts = []
        for p in s.get("cast", []):
            ref = p.get("monitor", 0)
            ref = ref.strip() if isinstance(ref, str) else int(ref)
            source = p.get("zdroj", "uptimerobot")
            if source not in ("uptimerobot", "mgmt"):
                fail(f"sluzby.toml: {s['klic']}/{p['klic']}: zdroj musí být „uptimerobot“ nebo „mgmt“")
            parts.append({"key": f"{s['klic']}/{p['klic']}", "name": p["nazev"], "source": source,
                          "monitor": ref if source == "uptimerobot" else None})
        if sample:  # ukázka funguje i bez vyplněných monitorů
            for i, p in enumerate(parts):
                if p["source"] == "uptimerobot" and not p["monitor"]:
                    p["monitor"] = 900000 + len(services) * 10 + i
        active = [p for p in parts if p["monitor"] or p["source"] == "mgmt"]
        if not active:
            warn(f"služba „{s['nazev']}“ nemá v sluzby.toml žádný monitor, na stránce se nezobrazí")
            continue
        if len(active) < len(parts):
            warn(f"služba „{s['nazev']}“: části bez monitoru se nezobrazí ({', '.join(p['name'] for p in parts if p not in active)})")
        services.append({
            "key": s["klic"], "name": s["nazev"], "desc": s.get("popis", ""), "rule": s["pravidlo"], "sla": bool(s.get("sla")),
            "since_date": s["od"], "parts": active,
            "word_down": s.get("vypadek", "výpadek"), "label_ok": s.get("stav_ok", "V provozu"), "label_bad": s.get("stav_vypadek", "Výpadek"), "show_pct": bool(s.get("procenta", True)), "show_bar": bool(s.get("pruh", True)),
        })
    return tz, services, cfg.get("oprava", [])


def parse_front(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if not m:
        fail(f"{path.relative_to(ROOT)}: chybí hlavička mezi ---")
    meta = {}
    for line in m.group(1).splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            if ":" not in line:
                fail(f"{path.relative_to(ROOT)}: řádek hlavičky „{line}“ nemá tvar klíč: hodnota")
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():].strip()


def md(text):
    if not text:
        return ""
    out = markdown.markdown(text, extensions=["sane_lists"])
    return re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" rel="noopener"', out)


def load_events(clock, service_keys):
    events = []
    for kind, folder in (("incident", "incidenty"), ("udrzba", "udrzba")):
        for path in sorted((ROOT / folder).glob("*.md")):
            if path.name.startswith("_") or path.name.lower() == "readme.md":
                continue
            rel = path.relative_to(ROOT)
            meta, body = parse_front(path)
            for req in ("nazev", "zacatek", "sluzby"):
                if not meta.get(req):
                    fail(f"{rel}: chybí {req}")
            start = clock.parse(meta["zacatek"], "zacatek", rel)
            end = clock.parse(meta.get("konec"), "konec", rel)
            if end is not None and end <= start:
                fail(f"{rel}: konec je dřív než začátek")
            svcs = [x.strip() for x in meta["sluzby"].split(",") if x.strip()]
            unknown = [x for x in svcs if x not in service_keys]
            if unknown:
                fail(f"{rel}: neznámá služba {', '.join(unknown)} (možné: {', '.join(sorted(service_keys))})")
            ev = {"kind": kind, "slug": path.stem, "title": meta["nazev"], "start": start, "end": end, "services": svcs,
                  "body": md(body), "summary": re.sub(r"\s+", " ", re.sub(r"[#*_`>\[\]]", "", body)).strip()[:300]}
            if kind == "incident":
                ev["sla_counts"] = meta.get("sla", "ano").lower() not in ("ne", "no", "false")
                ev["sla_reason"] = meta.get("duvod_sla", "")
                if not ev["sla_counts"] and not ev["sla_reason"]:
                    fail(f"{rel}: sla: ne potřebuje duvod_sla (veřejně se ukáže, proč se výpadek nezapočítává)")
                if not ev["sla_counts"] and end is None:
                    fail(f"{rel}: sla: ne jen pro ukončený incident (s koncem)")
                ev["state"] = meta.get("stav") or ("Vyřešeno" if end else "Řešíme")
            else:
                if end is None:
                    fail(f"{rel}: plánovaná údržba musí mít konec")
                notice = clock.parse(meta.get("oznameno"), "oznameno", rel)
                ev["impact"] = meta.get("dopad", "")
                ev["notice"] = notice
                deadline = clock.epoch(clock.local(start).date() - timedelta(days=NOTICE_DAYS))
                ev["valid"] = notice is not None and notice <= deadline
                # SLA 3.3: bezpečnostní údržba bez oznámení, krátké restarty se nezapočítají jen v limitu
                ev["security"] = meta.get("bezpecnostni", "ne").lower() in ("ano", "yes", "true")
                if not ev["valid"] and not ev["security"] and start > clock.now - 86400 * 40:
                    warn(f"{rel}: údržba není ohlášená {NOTICE_DAYS} dny předem (oznameno), výpadek během ní se započítá do SLA (SLA 2.5 a 3.2)")
            events.append(ev)
    return events


# ---------- data z monitoringu ----------

def load_store():
    if STORE.exists():
        return json.loads(STORE.read_text(encoding="utf-8"))
    return {"casti": {}}


def dump_store(store):
    """Jeden interval na řádek, aby diffy v gitu byly čitelné."""
    lines = ["{", '  "popis": "Ukončené výpadky částí služeb (UTC epoch sekundy, [začátek, konec]). Doplňuje build.py --fetch.",', '  "casti": {']
    keys = sorted(store["casti"])
    for i, k in enumerate(keys):
        iv = store["casti"][k]
        rows = ",\n".join(f"      [{s}, {e}]" for s, e in iv)
        body = f"[\n{rows}\n    ]" if iv else "[]"
        lines.append(f'    "{k}": {body}' + ("," if i < len(keys) - 1 else ""))
    lines += ["  }", "}", ""]
    return "\n".join(lines)


def sample_live(services, now, down_now=()):
    """Ukázková data: náhodné krátké výpadky za 90 dní; části v down_now mají výpadek právě teď."""
    rnd = random.Random(7)
    live = {}
    for svc in services:
        for p in svc["parts"]:
            if p["source"] != "uptimerobot":
                continue
            down = []
            for _ in range(rnd.randint(0, 4)):
                s = now - rnd.randint(1, 88) * 86400 - rnd.randint(0, 86000)
                down.append((s, s + rnd.choice([120, 300, 540, 1260, 2400, 5400])))
            if p["key"] in down_now:
                down.append((now - 1500, None))
            live[p["monitor"]] = {"id": p["monitor"], "status": "down" if p["key"] in down_now else "up", "interval": 60,
                                  "heartbeat": False, "down": down}
    return live


def sample_checks(now, down_now):
    """Ukázkové kontroly záloh: dvakrát denně, jedna zpožděná noc před třemi týdny."""
    out = []
    t = now - 89 * 86400
    while t < now:
        bad = now - 21 * 86400 < t < now - 20 * 86400
        out.append([t, 0 if bad else 1, 45, 2 if bad else 0])
        t += 12 * 3600
    if down_now:
        out.append([now - 1800, 0, 45, 3])
    return {"kontroly": out}


def checks_to_part(data, now):
    """Kontroly záloh [čas, ok, webů, problémů] -> stav a intervaly výpadku.

    Výpadek trvá od první neúspěšné kontroly do další úspěšné. Když poslední kontrola je starší než
    CHECK_STALE_H, stav je neznámý (mgmt neposílá), ale nic se nepočítá jako výpadek.
    """
    rows = sorted((int(r[0]), bool(r[1])) for r in data.get("kontroly", []) if len(r) >= 2)
    down, start = [], None
    for t, ok in rows:
        if t > now:
            continue
        if not ok and start is None:
            start = t
        elif ok and start is not None:
            down.append((start, t))
            start = None
    last = max((t for t, _ in rows if t <= now), default=None)
    if last is None or now - last > CHECK_STALE_H * 3600:
        if start is not None:
            down.append((start, last))
        return "unknown", down
    if start is not None:
        down.append((start, now))
        return "down", down
    return "up", down


def gather(services, args, clock, corrections):
    now = clock.now
    store = load_store()
    refs = {p["monitor"] for s in services for p in s["parts"] if p["source"] == "uptimerobot"}
    live, interval = None, None
    down_now = set(filter(None, (args.sample_down or "").split(",")))
    if args.sample:
        live = sample_live(services, now, down_now)
    elif args.fetch:
        key = os.environ.get("UPTIMEROBOT_API_KEY", "").strip()
        if not key:
            fail("--fetch potřebuje UPTIMEROBOT_API_KEY (v repozitáři: Settings → Secrets → Actions)")
        since = max(min(clock.epoch(s["since_date"]) for s in services) - 86400, now - KEEP_DAYS * 86400)
        api = os.environ.get("UPTIMEROBOT_API", "v3").strip() or "v3"
        try:
            live = uptimerobot.fetch(key, refs, since, api) if refs else {}
        except uptimerobot.FetchError as e:
            fail(f"UptimeRobot ({api}): {e}")
        for ref, mon in sorted(live.items(), key=lambda x: str(x[0])):
            hint = f" (ID {mon['id']})" if isinstance(ref, str) else ""
            print(f"monitor {ref}{hint}: {mon['status']}, výpadků {len(mon['down'])}, interval {mon.get('interval')}")
    else:
        print("Bez --fetch: aktuální stav neznámý, historie jen z data/vypadky.json")

    if live:
        checks = [int(m["interval"]) for m in live.values() if m.get("interval") and not m.get("heartbeat")]
        interval = max(checks) if checks else None

    checks = None
    if any(p["source"] == "mgmt" for s in services for p in s["parts"]):
        if args.sample:
            checks = sample_checks(now, any(k.startswith("zalohy/") for k in down_now))
        elif BACKUPS.exists():
            checks = json.loads(BACKUPS.read_text(encoding="utf-8"))
        else:
            checks = {"kontroly": []}
            print("data/zalohy.json zatím není (první kontrola z mgmt ho založí)")

    parts = {}
    changed = False
    for svc in services:
        for p in svc["parts"]:
            if p["source"] == "mgmt":
                status, down = checks_to_part(checks, now)
                rows = sorted((r for r in checks.get("kontroly", []) if len(r) >= 4 and int(r[0]) <= now), key=lambda r: int(r[0]))
                parts[p["key"]] = {"status": status, "down": down, "last": rows[-1] if rows else None}
                continue
            stored = [tuple(x) for x in store["casti"].get(p["key"], [])]
            mon = live.get(p["monitor"]) if live else None
            ongoing = []
            if mon:
                resolved = [(s, e) for s, e in mon["down"] if e is not None]
                ongoing = [(s, now) for s, e in mon["down"] if e is None]
                merged = [iv for iv in union(stored + resolved) if iv[1] > now - KEEP_DAYS * 86400]
                if merged != stored:
                    changed = True
                store["casti"][p["key"]] = [list(iv) for iv in merged]
            else:
                merged = stored
            down = union(merged + ongoing)
            for c in corrections:
                if c.get("cast") == p["key"]:
                    down = subtract(down, [(clock.parse(c["od"], "oprava od", "sluzby.toml"), clock.parse(c["do"], "oprava do", "sluzby.toml"))])
            status = mon["status"] if mon else "unknown"
            if ongoing and status != "down":
                status = "down"
            parts[p["key"]] = {"status": status, "down": down}

    if args.fetch and changed:
        STORE.parent.mkdir(exist_ok=True)
        STORE.write_text(dump_store(store), encoding="utf-8")
        print("data/vypadky.json: doplněny nové ukončené výpadky")
    return parts, interval, live is not None


# ---------- výpočty ----------

def restart_exclusions(outage, windows, clock):
    """SLA 3.3: z výpadků během bezpečnostní údržby se nezapočítá prvních 15 min každého výpadku,
    za kalendářní měsíc (pražský čas) nejvýše 60 min. Zbytek se do dostupnosti započítá."""
    budget, out = {}, []
    for s, e in sorted(intersect(outage, union(windows))):
        d = clock.local(s).date()
        left = budget.get((d.year, d.month), RESTART_MONTH_MIN * 60)
        take = min(e - s, RESTART_MAX_MIN * 60, left)
        if take > 0:
            out.append((s, s + take))
            budget[(d.year, d.month)] = left - take
    return out


def analyse(svc, parts, events, clock):
    now = clock.now
    start = clock.epoch(svc["since_date"])
    downs = [parts[p["key"]]["down"] for p in svc["parts"]]
    if svc["rule"] == "vse":
        outage, degraded = union([iv for d in downs for iv in d]), []
    else:
        outage = downs[0]
        for d in downs[1:]:
            outage = intersect(outage, d)
        degraded = subtract(union([iv for d in downs for iv in d]), outage)
    outage, degraded = clip(outage, start, now), clip(degraded, start, now)

    mine = [e for e in events if svc["key"] in e["services"]]
    maint_valid = [(e["start"], e["end"]) for e in mine if e["kind"] == "udrzba" and e["valid"]]
    # plánovaná (ohlášená i neohlášená) údržba se ukazuje vždy; bezpečnostní (SLA 3.3) jen když vysvětluje výpadek
    maint_all = [(e["start"], e["end"]) for e in mine if e["kind"] == "udrzba" and not e.get("security")]
    excl_incident = [(e["start"], e["end"]) for e in mine if e["kind"] == "incident" and not e["sla_counts"]]
    full = union(maint_valid + excl_incident)
    security = [(e["start"], e["end"]) for e in mine if e["kind"] == "udrzba" and e.get("security")]
    exclusions = union(full + restart_exclusions(subtract(outage, full), security, clock))
    counted = subtract(outage, exclusions)
    sec_hit = intersect(union(outage + degraded), union(security))   # výpadky během bezpečnostní údržby

    # aktuální stav
    states = [parts[p["key"]]["status"] for p in svc["parts"]]
    live_states = [s for s in states if s != "paused"]
    in_maint = any(s <= now < e for s, e in maint_all)
    incident_open = any(e["kind"] == "incident" and e["end"] is None and e["start"] <= now for e in mine)
    n_down = live_states.count("down")
    if not live_states:
        level, label = "paused", "Neměří se"
    elif all(s == "unknown" for s in live_states):
        level, label = "unknown", "Stav neznámý"
    elif n_down and (svc["rule"] == "vse" or n_down == len(live_states)):
        level = "bad"
        label = svc["label_bad"] if n_down == len(live_states) else "Částečný výpadek"
    elif n_down:
        level, label = "warn", "Omezený provoz"
    else:
        level, label = "ok", svc["label_ok"]
    security_now = any(s <= now < e for s, e in security)
    if in_maint and level in ("ok", "bad", "warn"):
        level, label = "maint", "Plánovaná údržba"
    elif security_now and level in ("bad", "warn"):
        level, label = "maint", "Bezpečnostní údržba"
    elif incident_open and level == "ok":
        level, label = "warn", "Řešíme potíže"

    # 90 dní po dnech (místní čas)
    today = clock.local(now).date()
    days, measured_total, counted_total = [], 0, 0
    for i in range(HISTORY_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        lo, hi = clock.epoch(d), clock.epoch(d + timedelta(days=1))
        mlo, mhi = max(lo, start), min(hi, now)
        if mhi <= mlo:
            days.append({"date": d, "level": "nodata", "tip": [f"{WEEKDAYS[d.weekday()]} {d.day}.{NB}{d.month}.{NB}{d.year}", "zatím neměřeno"]})
            continue
        c = measure(clip(counted, lo, hi))
        x = measure(clip(intersect(outage, exclusions), lo, hi))
        g = measure(clip(degraded, lo, hi))
        g_rest = measure(clip(subtract(degraded, union(security)), lo, hi))   # omezení mimo bezpečnostní údržbu
        mt = [iv for iv in maint_all if iv[1] > lo and iv[0] < hi]
        if measure(clip(sec_hit, lo, hi)):
            mt.append((lo, hi))
        day_events = [e for e in mine if e["start"] < hi and (e["end"] or now) > lo
                      and (not e.get("security") or measure(clip(intersect(sec_hit, [(e["start"], e["end"])]), lo, hi)))]
        measured_total += mhi - mlo
        counted_total += c
        tip = [f"{WEEKDAYS[d.weekday()]} {d.day}.{NB}{d.month}.{NB}{d.year}"]
        if c:
            tip.append(f"{svc['word_down']} {dur(c)}")
        if x:
            tip.append(f"nezapočítáno do SLA {dur(x)}")
        if g:
            tip.append(f"omezený provoz {dur(g)}")
        for e in day_events:
            tip.append(("Údržba: " if e["kind"] == "udrzba" else "Incident: ") + e["title"])
        if len(tip) == 1:
            tip.append(f"bez {'výpadku' if svc['word_down'] == 'výpadek' else 'potíží'}")
        if c >= BAD_DAY_MIN * 60:
            lvl = "bad"
        elif c or g_rest or (x and not mt) or any(e["kind"] == "incident" for e in day_events):
            lvl = "warn"
        elif mt:
            lvl = "maint"
        else:
            lvl = "ok"
        days.append({"date": d, "level": lvl, "tip": tip, "events": [e["slug"] for e in day_events]})
    uptime90 = 100 * (measured_total - counted_total) / measured_total if measured_total else None

    # měsíce podle SLA 2.3
    months = []
    y, m = today.year, today.month
    for _ in range(SLA_MONTHS):
        lo = clock.epoch(date(y, m, 1))
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        hi = clock.epoch(date(ny, nm, 1))
        mlo, mhi = max(lo, start), min(hi, now)
        if mhi <= mlo:
            break
        c = measure(clip(counted, lo, hi))
        months.append({"year": y, "month": m, "pct": 100 * (mhi - mlo - c) / (mhi - mlo), "down": c,
                       "running": hi > now, "partial_from": mlo if mlo > lo else None})
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)

    # dlouhé výpadky bez incidentu (SLA 5.6)
    for s, e in counted:
        if e - s >= LONG_OUTAGE_MIN * 60 and e < now and not any(ev["kind"] == "incident" and ev["start"] < e and (ev["end"] or now) > s for ev in mine):
            warn(f"{svc['name']}: výpadek {clock.range(s, e)} ({dur(e - s)}) nemá incident v incidenty/ (SLA 5.6: e-mail klientům)")

    part_states = []
    for p in svc["parts"]:
        st = parts[p["key"]]["status"]
        part_states.append({"name": p["name"], "level": {"up": "ok", "down": "bad"}.get(st, st),
                            "label": {"up": "v provozu", "down": "výpadek", "paused": "neměří se"}.get(st, "neznámý"),
                            "last": parts[p["key"]].get("last")})
    return {"svc": svc, "level": level, "label": label, "days": days, "uptime90": uptime90, "months": months,
            "parts": part_states, "since": start, "sec_hit": sec_hit}


def visible_events(events, results):
    """Bezpečnostní údržbu (SLA 3.3) ukazujeme jen tehdy, když během ní něco vypadlo.
    Běžné kolo aktualizací bez dopadu na klienty stránku nezaplevelí."""
    hits = {r["svc"]["key"]: r["sec_hit"] for r in results}
    out = []
    for e in events:
        if e["kind"] == "udrzba" and e.get("security"):
            if not any(intersect(hits.get(k, []), [(e["start"], e["end"])]) for k in e["services"]):
                continue
        out.append(e)
    return out


def overall(results):
    """Červeně jen výpadek služby s garancí (weby, e-mail, DNS); zpožděná záloha nebo panel jsou „potíže“."""
    levels = [r["level"] for r in results]
    sla = [r for r in results if r["svc"]["sla"]]
    sla_bad = [r for r in sla if r["level"] == "bad"]
    if sla_bad:
        return "bad", "Služby mají výpadek" if len(sla_bad) == len(sla) > 1 else "Část služeb má výpadek"
    if "bad" in levels or "warn" in levels:
        return "warn", "Některé služby mají potíže"
    if "maint" in levels:
        security = any(r["label"] == "Bezpečnostní údržba" for r in results)
        return "maint", "Probíhá bezpečnostní údržba" if security else "Probíhá plánovaná údržba"
    if all(lv in ("unknown", "paused") for lv in levels):
        return "unknown", "Stav služeb se nepodařilo ověřit"
    return "ok", "Všechny služby fungují"


# ---------- HTML ----------

def favicon(level):
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='{COLORS.get(level, COLORS['unknown'])}'/>"
           "<path d='M6 17h5l3-7 4 13 3-6h5' fill='none' stroke='white' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'/></svg>")
    return "data:image/svg+xml," + svg.replace("<", "%3C").replace(">", "%3E").replace("#", "%23").replace('"', "'")


def page(title, desc, main, current, level, generated, base=""):
    nav = "".join(
        f'<a href="{href}"{" aria-current=\"page\"" if key == current else ""}>{label}</a>'
        for key, href, label in (("index", f"{base}index.html", "Stav služeb"), ("historie", f"{base}historie.html", "Historie"),
                                 ("docs", f"{DOCS_URL}/", "Smluvní dokumenty"))
    )
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{esc(desc)}">
<link rel="icon" href="{favicon(level)}">
<link rel="stylesheet" href="{base}style.css">
<link rel="alternate" type="application/atom+xml" title="Incidenty a údržba WD&amp;D" href="{base}feed.xml">
</head>
<body data-generated="{generated * 1000}">
<header class="bar"><div class="bar-in"><a class="brand" href="{base}index.html"><b>WD&amp;D</b><span>Stav služeb</span></a><nav aria-label="Navigace">{nav}</nav></div></header>
<main class="wrap">
{main}
</main>
<footer class="foot"><div class="foot-in"><p>{PROVIDER}</p><p>{CONTACT}</p><p>Stránka běží mimo naše servery, nepoužívá cookies a nenačítá nic odjinud.</p></div></footer>
<script src="{base}status.js" defer></script>
</body>
</html>
"""


def pill(level, label):
    return f'<span class="pill lv-{level}"><span class="dot" aria-hidden="true"></span>{esc(label)}</span>'


def services_label(keys, names):
    return ", ".join(names.get(k, k) for k in keys)


def event_card(ev, clock, names, *, full=False, anchor=True):
    now = clock.now
    if ev["kind"] == "incident":
        kicker = "Incident"
        state = ev["state"]
        lv = "ok" if ev["end"] else "bad"
    else:
        kicker = "Bezpečnostní údržba" if ev.get("security") else "Plánovaná údržba"
        state = "Proběhla" if ev["end"] <= now else ("Probíhá" if ev["start"] <= now else "Plánovaná")
        lv = "maint"
    title = esc(ev["title"])
    if not (full and anchor):
        title = f'<a href="historie.html#{esc(ev["slug"])}">{title}</a>'
    meta = [esc(clock.range(ev["start"], ev["end"])), esc(services_label(ev["services"], names))]
    if ev["end"] and ev["kind"] == "incident":
        meta.append(f"trvání {dur(ev['end'] - ev['start'])}")
    notes = []
    if ev["kind"] == "incident" and not ev["sla_counts"]:
        notes.append(f'<p class="ev-note">Nezapočítává se do dostupnosti: {esc(ev["sla_reason"])}</p>')
    if ev["kind"] == "udrzba":
        if ev.get("impact"):
            notes.append(f'<p class="ev-impact">{esc(ev["impact"])}</p>')
        if ev["notice"]:
            notes.append(f'<p class="ev-note">Oznámeno e-mailem {clock.date(ev["notice"])}.</p>')
        elif ev.get("security"):
            notes.append(f'<p class="ev-note">Krátké restarty při bezpečnostní údržbě (jednotlivě do {RESTART_MAX_MIN}{NB}min, za měsíc nejvýše {RESTART_MONTH_MIN}{NB}min) do dostupnosti nepočítáme, <a href="{DOCS_URL}/sla.html#udrzba">čl. 3.3 SLA</a>.</p>')
    body = f'<div class="ev-body">{ev["body"]}</div>' if full and ev["body"] else ""
    tag = "article" if full else "li"
    ident = f' id="{esc(ev["slug"])}"' if full and anchor else ""
    return (f'<{tag} class="ev ev-{ev["kind"]}"{ident}><div class="ev-head"><span class="ev-kicker">{kicker}</span>{pill(lv, state)}</div>'
            f'<h3 class="ev-title">{title}</h3><p class="ev-meta">{" · ".join(meta)}</p>{"".join(notes)}{body}</{tag}>')


def bars(r):
    days = r["days"]
    spans = []
    for d in days:
        tip = "\n".join(d["tip"])
        href = f'historie.html#{d["events"][0]}' if d.get("events") else None
        attrs = f'class="d lv-{d["level"]}" data-tip="{esc(tip)}" title="{esc(tip)}"'
        spans.append(f'<a {attrs} href="{href}" tabindex="-1"></a>' if href else f'<span {attrs}></span>')
    bad = sum(1 for d in days if d["level"] in ("bad", "warn"))
    summary = (f"{r['svc']['name']} za {HISTORY_DAYS} dní: " + (f"{bad} {plural(bad, 'den', 'dny', 'dní')} s potížemi" if bad else "bez potíží")
               + (f", dostupnost {pct(r['uptime90'])}" if r["uptime90"] is not None else ""))
    return f'<div class="bars" role="img" aria-label="{esc(summary)}">{"".join(spans)}</div>'


def last_check(r, clock):
    """„Poslední kontrola 7. 11. 2026 10:15 · 53 webů, bez problémů“ z kontrol na mgmt."""
    rows = [p["last"] for p in r["parts"] if p.get("last")]
    if not rows:
        return "Zatím bez kontroly."
    t, ok, total, problems = max(rows, key=lambda x: int(x[0]))[:4]
    n = int(total)
    word = "web" if n == 1 else "weby" if 2 <= n <= 4 else "webů"
    state = "bez problémů" if ok and not int(problems) else f"potíže: {int(problems)}"
    when = f'<time datetime="{datetime.fromtimestamp(int(t), timezone.utc).isoformat()}">{clock.dt(int(t))}</time>'
    return f"Poslední kontrola {when} · {n}{NB}{word}, {state}"


def render_index(results, events, clock, names, ov, interval, measured, generated):
    now = clock.now
    lvl, label = ov
    nm = [r["svc"]["name"] for r in results]
    nm = nm[:1] + [n if n[:2].isalpha() and n[:2].isupper() else n[0].lower() + n[1:] for n in nm[1:]]  # „DNS“ zůstane velkými
    listing = ", ".join(nm[:-1]) + " a " + nm[-1] if len(nm) > 1 else nm[0]
    measured_txt = ("každou minutu" if interval and interval <= 60 else f"každých {round(interval / 60)} minut" if interval else "nejméně každých 5 minut")
    upd = f'Aktualizováno <time datetime="{datetime.fromtimestamp(generated, timezone.utc).isoformat()}">{clock.dt(generated)}</time>'
    hero = f"""<section class="hero">
  <p class="eyebrow">Hosting WD&amp;D · stav služeb</p>
  <h1 class="overall lv-{lvl}"><span class="dot" aria-hidden="true"></span>{esc(label)}</h1>
  <p class="lead">{esc(listing)}. Služby měří externí monitoring {measured_txt}, mimo naše servery.</p>
  <p class="updated"><span class="js-updated">{upd}</span></p>
  <p class="stale" hidden>Údaje na stránce jsou starší než {STALE_MIN} minut. Pokud vám něco nefunguje, napište nám na <a href="mailto:support@wdd.systems">support@wdd.systems</a> nebo zavolejte na +420&nbsp;727&nbsp;943&nbsp;449.</p>
</section>"""

    active = [e for e in events if e["start"] <= now and (e["end"] is None or e["end"] > now)]
    active.sort(key=lambda e: (e["kind"] != "incident", -e["start"]))
    active_html = ""
    if active:  # probíhající incidenty a údržba nahoře, i s textem
        active_html = '<section class="active" aria-label="Právě probíhá">' + "".join(
            event_card(e, clock, names, full=True, anchor=False) for e in active) + "</section>"

    cards = []
    for r in results:
        s = r["svc"]
        parts = ""
        if len(r["parts"]) > 1:
            parts = '<ul class="parts">' + "".join(
                f'<li><span class="pdot lv-{p["level"]}" aria-hidden="true"></span>{esc(p["name"])}<span class="sr"> – {esc(p["label"])}</span></li>' for p in r["parts"]) + "</ul>"
        meta = []
        if r["since"] > now - (HISTORY_DAYS - 1) * 86400:
            meta.append(f'měříme od {clock.date(r["since"])}')
        if r["uptime90"] is not None and s["show_pct"]:
            meta.append(f'{pct(r["uptime90"])} za {HISTORY_DAYS} dní' if not meta else f'dostupnost {pct(r["uptime90"])}')
        if s["show_bar"]:
            body = f"""{bars(r)}
  <div class="bars-legend"><span><span class="l90">před {HISTORY_DAYS} dny</span><span class="l30">před 30 dny</span></span><span class="lm">{" · ".join(meta)}</span><span>dnes</span></div>"""
        else:  # bez pruhu: jen poslední kontrola (zálohy, historie se neuchovává)
            body = f'<p class="svc-last">{last_check(r, clock)}</p>'
        cards.append(f"""<article class="svc" id="sluzba-{esc(s['key'])}">
  <div class="svc-head"><h3>{esc(s['name'])}</h3>{pill(r['level'], r['label'])}</div>
  <p class="svc-desc">{esc(s['desc'])}</p>{parts}
  {body}
</article>""")
    services_html = f"""<div class="section-h"><h2>Služby</h2><p class="legend"><span><i class="lg lv-ok"></i>bez potíží</span><span><i class="lg lv-warn"></i>krátký výpadek nebo omezení</span><span><i class="lg lv-bad"></i>výpadek od {BAD_DAY_MIN}{NB}min</span><span><i class="lg lv-maint"></i>údržba</span></p></div>
<section class="svcs" aria-label="Služby">{''.join(cards)}</section>"""

    sla = [r for r in results if r["svc"]["sla"]]
    sla_html = ""
    if sla:
        all_months = sorted({(mo["year"], mo["month"]) for r in sla for mo in r["months"]}, reverse=True)
        head = "".join(f"<th>{esc(r['svc']['name'])}</th>" for r in sla)
        rows = []
        for y, m in all_months:
            cells = []
            note = ""
            for r in sla:
                mo = next((x for x in r["months"] if (x["year"], x["month"]) == (y, m)), None)
                if not mo:
                    cells.append('<td class="num-col muted">–</td>')
                    continue
                cls = " below" if mo["pct"] < SLA_TARGET else ""
                sub = f'<small>{svc_down(mo["down"])}</small>'
                cells.append(f'<td class="num-col{cls}">{pct(mo["pct"])}{sub}</td>')
                if mo["running"]:
                    note = f"k {clock.date(now, year=False)}"
                elif mo["partial_from"]:
                    note = f"měřeno od {clock.date(mo['partial_from'], year=False)}"
                if mo["running"] and mo["partial_from"]:
                    note = f"od {clock.date(mo['partial_from'], year=False)} do {clock.date(now, year=False)}"
            rows.append(f'<tr><td>{MONTHS[m - 1]} {y}{f"<small>{note}</small>" if note else ""}</td>{"".join(cells)}</tr>')
        sla_html = f"""<div class="split sla-split">
<section class="panel" aria-labelledby="sla-h">
  <h2 id="sla-h">Měsíční dostupnost podle SLA</h2>
  <div class="table"><table class="sla-table"><thead><tr><th>Měsíc</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  <p class="note">Počítáme podle <a href="{DOCS_URL}/sla.html#c2-3">čl. 2.3 SLA</a>: (minuty v měsíci − minuty výpadku) ÷ minuty v měsíci. Plánovanou údržbu ohlášenou předem ani krátké restarty při bezpečnostní údržbě v limitu čl. 3.3 do výpadků nepočítáme (čl. 2.5).</p>
</section>
<section class="panel accent" aria-labelledby="garance-h">
  <h2 id="garance-h">Garantujeme 99,5{NB}%</h2>
  <p>U webů, e-mailu a DNS garantujeme dostupnost 99,5{NB}% měsíčně, tedy nejvýše 3{NB}h 36{NB}min výpadků za třicetidenní měsíc.</p>
  <ol>
    <li><strong>Pod 99,5{NB}%</strong> vám vrátíme 10 až 50{NB}% měsíční ceny dotčené služby (<a href="{DOCS_URL}/sla.html#kompenzace">čl. 7 SLA</a>). Stačí nám napsat do 30 dní od konce měsíce.</li>
    <li><strong>Výpadek nad 60 minut</strong> vám popíšeme e-mailem nejpozději další pracovní den: co se stalo a co jsme udělali.</li>
  </ol>
</section>
</div>"""

    upcoming = sorted([e for e in events if e["kind"] == "udrzba" and e["start"] > now], key=lambda e: e["start"])
    recent = sorted([e for e in events if e["start"] > now - HISTORY_DAYS * 86400 and e["start"] <= now and not (e["end"] is None or e["end"] > now)],
                    key=lambda e: -e["start"])
    up_html = ("<ul class=\"evs\">" + "".join(event_card(e, clock, names) for e in upcoming) + "</ul>") if upcoming else \
        f'<p class="empty">Žádnou údržbu teď neplánujeme. Údržbu s výpadkem nad 15{NB}minut ohlašujeme e-mailem nejméně 3 dny předem a provádíme ji přednostně mezi 20:00 a 7:00.</p>'
    rec_html = ("<ul class=\"evs\">" + "".join(event_card(e, clock, names) for e in recent[:6]) + "</ul>") if recent else \
        f'<p class="empty">Za posledních {HISTORY_DAYS} dní bez incidentů a údržby.</p>'
    more = f'<p class="more-link"><a href="historie.html">Celá historie</a></p>' if recent or events else ""
    events_html = f"""<div class="split ev-split">
<section class="panel" aria-labelledby="up-h"><h2 id="up-h">Plánovaná údržba</h2>{up_html}</section>
<section class="panel" aria-labelledby="rec-h"><h2 id="rec-h">Poslední události</h2>{rec_html}{more}</section>
</div>"""

    main = hero + active_html + services_html + sla_html + events_html
    return page("Stav služeb · WD&amp;D", "Aktuální stav webů, e-mailu, DNS, záloh a ovládacího panelu hostingu WD&D, historie dostupnosti a plánovaná údržba.",
                main, "index", lvl, generated)


def svc_down(seconds):
    return "bez výpadku" if not seconds else dur(seconds)


def render_history(events, clock, names, lvl, generated):
    items = sorted(events, key=lambda e: -e["start"])
    groups = {}
    for e in items:
        d = clock.local(e["start"])
        groups.setdefault((d.year, d.month), []).append(e)
    if groups:
        body = "".join(f'<section class="hist-month"><h2>{MONTHS[m - 1]} {y}</h2>' + "".join(event_card(e, clock, names, full=True) for e in evs) + "</section>"
                       for (y, m), evs in groups.items())
    else:
        body = '<p class="empty">Zatím bez incidentů a plánované údržby.</p>'
    main = f"""<header class="doc-head">
  <p class="crumb"><a href="index.html">Stav služeb</a></p>
  <h1>Historie incidentů a údržby</h1>
  <p class="lead">Všechny incidenty a plánované údržby, které jsme na stránce stavu služeb ohlásili. Novinky můžete odebírat přes <a href="feed.xml">RSS (Atom)</a>.</p>
</header>
<div class="hist">{body}</div>"""
    return page("Historie · Stav služeb WD&amp;D", "Historie incidentů a plánované údržby hostingu WD&D.", main, "historie", lvl, generated)


def render_404(lvl, generated):
    main = """<header class="doc-head">
  <p class="crumb"><a href="/">Stav služeb</a></p>
  <h1>Stránka nenalezena</h1>
  <p class="lead">Tahle adresa tu neexistuje. Aktuální stav služeb je na <a href="/">hlavní stránce</a>.</p>
</header>"""
    return page("Stránka nenalezena · WD&amp;D", "Stránka nenalezena.", main, "", lvl, generated, base="/")


def render_feed(events, clock, names, generated):
    def iso(t):
        return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for e in sorted(events, key=lambda e: -e["start"])[:50]:
        kind = "Incident" if e["kind"] == "incident" else ("Bezpečnostní údržba" if e.get("security") else "Plánovaná údržba")
        updated = max(e["start"], e["end"] or e["start"]) if e["kind"] == "incident" else e["start"]
        summary = f"{kind}: {clock.range(e['start'], e['end'])} · {services_label(e['services'], names)}"
        if e["kind"] == "incident":
            summary += f" · {e['state']}"
        elif e.get("impact"):
            summary += f" · {e['impact']}"
        entries.append(f"""  <entry>
    <id>tag:status.wdd.systems,2026:{esc(e['slug'])}</id>
    <title>{esc(kind + ': ' + e['title'])}</title>
    <link href="{SITE_URL}/historie.html#{esc(e['slug'])}"/>
    <updated>{iso(updated)}</updated>
    <summary>{esc(summary.replace(NB, ' '))}</summary>
    <content type="html">{esc(e['body'])}</content>
  </entry>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="cs">
  <id>{SITE_URL}/</id>
  <title>WD&amp;D · incidenty a údržba</title>
  <link href="{SITE_URL}/"/>
  <link rel="self" href="{SITE_URL}/feed.xml"/>
  <updated>{iso(generated)}</updated>
  <author><name>WD&amp;D</name><email>support@wdd.systems</email></author>
{chr(10).join(entries)}
</feed>
"""


def render_json(results, events, ov, generated, clock):
    now = clock.now
    return json.dumps({
        "generated": datetime.fromtimestamp(generated, timezone.utc).isoformat(),
        "status": {"level": ov[0], "label": ov[1]},
        "services": [{"key": r["svc"]["key"], "name": r["svc"]["name"], "level": r["level"], "label": r["label"],
                      "uptime90": round(r["uptime90"], 4) if r["uptime90"] is not None else None} for r in results],
        "active": [{"slug": e["slug"], "kind": e["kind"], "title": e["title"]} for e in events if e["start"] <= now and (e["end"] is None or e["end"] > now)],
    }, ensure_ascii=False, indent=1) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="načíst UptimeRobot (UPTIMEROBOT_API_KEY) a doplnit data/vypadky.json")
    ap.add_argument("--monitory", action="store_true", help="jen diagnostika: kdy monitory vznikly a pauzy (pro zpětnou historii), nic nestaví")
    ap.add_argument("--sample", action="store_true", help="ukázková data pro kontrolu vzhledu")
    ap.add_argument("--sample-down", help="s --sample: části s výpadkem právě teď, např. email/imap,dns/ns2")
    ap.add_argument("--now", help="jiný „teď“ pro zkoušky, např. 2026-11-20 10:00")
    ap.add_argument("--preview", action="store_true", help="navíc .preview-style.css s písmy vloženými jako data URI")
    args = ap.parse_args()

    tz, services, corrections = load_config(sample=args.sample)
    now = int(time.time())
    clock = Clock(tz, now)
    if args.now:
        clock.now = now = clock.parse(args.now, "--now", "příkaz")
    if not services:
        fail("v sluzby.toml není žádná služba s monitorem")
    names = {s["key"]: s["name"] for s in services}
    if args.monitory:
        key = os.environ.get("UPTIMEROBOT_API_KEY", "")
        if not key:
            fail("--monitory potřebuje UPTIMEROBOT_API_KEY")
        refs = {p["monitor"]: p["key"] for s in services for p in s["parts"] if p["source"] == "uptimerobot"}
        for ref, d in uptimerobot.describe(key, set(refs)).items():
            print(f"{refs[ref]:<14} od služby {next(s['since_date'] for s in services if s['key'] == refs[ref].split('/')[0])} | "
                  f"v3 {d.get('v3') or '-'} | v2 vznik {d.get('v2_created') or d.get('v2_error') or '-'} | "
                  f"pauzy {d.get('pause_log') or 'žádné'} | stav {d.get('status')}")
        since = clock.epoch(date(2026, 7, 31))
        live = uptimerobot.fetch(key, set(refs), since, os.environ.get("UPTIMEROBOT_API") or "v3")
        for ref, mon in live.items():
            for s, e in sorted(mon["down"]):
                print(f"výpadek {refs[ref]:<14} {clock.local(s):%Y-%m-%d %H:%M} {((e or now) - s):>6} s")
        return
    events = load_events(clock, set(names))
    parts, interval, measured = gather(services, args, clock, corrections)
    results = [analyse(s, parts, events, clock) for s in services]
    events = visible_events(events, results)
    ov = overall(results)
    generated = now

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(render_index(results, events, clock, names, ov, interval, measured, generated), encoding="utf-8")
    (OUT / "historie.html").write_text(render_history(events, clock, names, ov[0], generated), encoding="utf-8")
    (OUT / "404.html").write_text(render_404(ov[0], generated), encoding="utf-8")
    (OUT / "feed.xml").write_text(render_feed(events, clock, names, generated), encoding="utf-8")
    (OUT / "status.json").write_text(render_json(results, events, ov, generated, clock), encoding="utf-8")
    shutil.copy(SRC / "style.css", OUT / "style.css")
    shutil.copy(SRC / "status.js", OUT / "status.js")
    shutil.copytree(SRC / "fonts", OUT / "fonts")
    if args.preview:
        css = (OUT / "style.css").read_text(encoding="utf-8")
        for f in (SRC / "fonts").glob("*.woff2"):
            css = css.replace(f'url("fonts/{f.name}")', f'url("data:font/woff2;base64,{base64.b64encode(f.read_bytes()).decode()}")')
        (ROOT / ".preview-style.css").write_text(css, encoding="utf-8")

    # kontrola: kotvy do historie existují
    ids = set(re.findall(r'id="([\w-]+)"', (OUT / "historie.html").read_text(encoding="utf-8")))
    bad = [h for f in ("index.html", "historie.html") for h in re.findall(r'href="historie\.html#([\w-]+)"', (OUT / f).read_text(encoding="utf-8")) if h not in ids]
    print(f"stav: {ov[1]} | služeb {len(results)} | událostí {len(events)} | broken anchors: {sorted(set(bad)) or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
