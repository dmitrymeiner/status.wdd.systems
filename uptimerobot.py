"""Stav a výpadky monitorů z UptimeRobotu. API v3 (výchozí) nebo v2, jen standardní knihovna.

Monitor se v sluzby.toml zadává číslem (ID) nebo přesným názvem z dashboardu.
Výstup je normalizovaný a bez adres a důvodů z UptimeRobotu (repozitář i logy Actions jsou veřejné):
    {ref: {"id": int, "status": "up|down|paused|unknown", "interval": s|None, "heartbeat": bool,
           "down": [(start, end|None), ...]}}   # ref = ID nebo název ze sluzby.toml; časy v UTC epoch sekundách, end None = trvá
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API3 = "https://api.uptimerobot.com/v3"
API2 = "https://api.uptimerobot.com/v2/getMonitors"
UA = "status.wdd.systems"


class FetchError(Exception):
    pass


def _request(url, *, data=None, headers=None, tries=3):
    """GET/POST s opakováním. Chybová hláška nese jen cestu a kód, nikdy klíč ani obsah odpovědi."""
    path = urllib.parse.urlsplit(url).path
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", "User-Agent": UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = e.headers.get("Retry-After") or ""
                time.sleep(min(int(wait) if wait.isdigit() else 5 * (attempt + 1), 60))
                continue
            raise FetchError(f"HTTP {e.code} z {path}") from None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(5 * (attempt + 1))
    raise FetchError(f"{path}: bez odpovědi ani po {tries} pokusech")


def parse_time(v):
    """ISO 8601, epoch v sekundách nebo milisekundách -> epoch sekundy (UTC). None/'' -> None."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = float(v)
        return int(n / 1000 if n > 1e12 else n)
    s = str(v).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _items(resp):
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("data", "monitors", "incidents", "items"):
            if isinstance(resp.get(k), list):
                return resp[k]
    return []


def _next(resp):
    link = resp.get("nextLink") if isinstance(resp, dict) else None
    if not link:
        return None
    if link.startswith("/"):
        return "https://api.uptimerobot.com" + link
    if not link.startswith("http"):
        return f"{API3}/{link.lstrip('/')}"
    return link


# ---------- API v3 ----------

def _status3(m):
    s = str(m.get("status", "")).upper()
    if s == "UP" or s == "LOOKS_DOWN":  # LOOKS_DOWN = první neúspěšné měření, ještě nepotvrzený výpadek
        return "up"
    if s == "DOWN":
        return "down"
    if s == "PAUSED":
        return "paused"
    return "unknown"


def _norm(name):
    """Název pro porovnání: bez ohledu na velikost písmen, mezery a druh pomlčky."""
    for dash in "\u2010\u2011\u2012\u2013\u2014\u2212":
        name = name.replace(dash, "-")
    return " ".join(name.split()).casefold()


def _match(refs, monitors, name_key):
    """ref (ID nebo název) -> monitor."""
    by_id = {m.get("id"): m for m in monitors}
    by_name = {}
    for m in monitors:
        by_name.setdefault(_norm(str(m.get(name_key, ""))), []).append(m)
    out, missing = {}, []
    for ref in refs:
        if isinstance(ref, int):
            m = by_id.get(ref)
        else:
            hits = by_name.get(_norm(ref), [])
            if len(hits) > 1:
                raise FetchError(f"název „{ref}“ má v účtu {len(hits)} monitory, v sluzby.toml zadejte ID")
            m = hits[0] if hits else None
        if m is None:
            missing.append(ref)
        else:
            out[ref] = m
    if missing:
        raise FetchError("v účtu chybí monitory: " + ", ".join(map(str, missing)))
    return out


def fetch_v3(key, refs, since):
    h = {"Authorization": f"Bearer {key}"}
    monitors = []
    url = f"{API3}/monitors?limit=200"
    for _ in range(20):
        r = _request(url, headers=h)
        monitors += _items(r)
        url = _next(r)
        if not url:
            break
    found = _match(refs, monitors, "friendlyName")

    now = int(time.time())
    out, by_id = {}, {}
    for ref, m in found.items():
        typ = str(m.get("type", "")).lower()
        out[ref] = {"id": m.get("id"), "status": _status3(m), "interval": m.get("interval"), "heartbeat": "heart" in typ, "down": []}
        by_id[m.get("id")] = out[ref]

    url = f"{API3}/incidents?" + urllib.parse.urlencode({"started_after": _iso(since)})
    for _ in range(200):
        r = _request(url, headers=h)
        for inc in _items(r):
            mon = inc.get("monitor") or {}
            mid = mon.get("id", inc.get("monitorId"))
            if mid not in by_id:
                continue
            typ = str(inc.get("type", "")).lower()
            if "slow" in typ:
                continue
            start = parse_time(inc.get("startedAt"))
            if start is None:
                continue
            end = parse_time(inc.get("resolvedAt"))
            st = str(inc.get("status", "")).lower()
            if end is None and st in ("resolved", "closed") and inc.get("duration"):
                end = start + int(inc["duration"])
            by_id[mid]["down"].append((start, end))
        url = _next(r)
        if not url:
            break
    for ref, m in found.items():
        _reconcile(out[ref], m.get("currentStateDuration"), now)
    return out


def _reconcile(mon, state_duration, now):
    """Sladí probíhající výpadky s aktuálním stavem monitoru.

    Monitor je dole a otevřený incident chybí -> výpadek od změny stavu (nebo od teď).
    Monitor je nahoře a incident je pořád otevřený (zpoždění API) -> zavřít ho v okamžiku změny stavu;
    když ho neznáme, incident vynechat, dokud ho UptimeRobot neuzavře (jinak by se výpadek natahoval do „teď“).
    """
    known = isinstance(state_duration, (int, float)) and state_duration > 0
    since = now - int(state_duration) if known else now
    open_ = [d for d in mon["down"] if d[1] is None]
    if mon["status"] == "down" and not open_:
        mon["down"].append((min(since, now), None))
    elif mon["status"] != "down" and open_:
        closed = [d for d in mon["down"] if d[1] is not None]
        mon["down"] = closed + ([(s, max(s, since)) for s, _ in open_] if known else [])


# ---------- API v2 (záloha, kdyby v3 neodpovídala tvarem) ----------

V2_STATUS = {0: "paused", 1: "unknown", 2: "up", 8: "up", 9: "down"}


def fetch_v2(key, refs, since):
    now = int(time.time())
    form = urllib.parse.urlencode({"api_key": key, "format": "json", "limit": "50"}).encode()
    r = _request(API2, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.get("stat") != "ok":
        raise FetchError("v2 getMonitors: stat=" + str(r.get("stat")))
    found = _match(refs, r.get("monitors", []), "friendly_name")
    ref_of = {m["id"]: ref for ref, m in found.items()}
    out = {}
    step = 90 * 86400
    lo = since
    while lo < now:
        hi = min(lo + step, now)
        form = urllib.parse.urlencode({
            "api_key": key, "format": "json", "monitors": "-".join(map(str, sorted(ref_of))),
            "logs": "1", "logs_start_date": str(lo), "logs_end_date": str(hi), "logs_limit": "1000",
        }).encode()
        r = _request(API2, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if r.get("stat") != "ok":
            raise FetchError("v2 getMonitors: stat=" + str(r.get("stat")))
        for m in r.get("monitors", []):
            mid = m.get("id")
            if mid not in ref_of:
                continue
            mon = out.setdefault(ref_of[mid], {"id": mid, "status": V2_STATUS.get(m.get("status"), "unknown"), "interval": m.get("interval"),
                                               "heartbeat": m.get("type") == 5, "down": []})
            logs = sorted(m.get("logs", []), key=lambda x: x.get("datetime", 0))
            for i, log in enumerate(logs):
                if log.get("type") != 1:
                    continue
                s = int(log["datetime"])
                last = i == len(logs) - 1
                end = None if (last and mon["status"] == "down" and hi == now) else s + int(log.get("duration") or 0)
                mon["down"].append((s, end))
        lo = hi
    for ref, m in found.items():  # monitor bez záznamů v logu
        out.setdefault(ref, {"id": m["id"], "status": V2_STATUS.get(m.get("status"), "unknown"), "interval": m.get("interval"),
                             "heartbeat": m.get("type") == 5, "down": []})
    for mon in out.values():
        mon["down"] = sorted(set(mon["down"]))
        _reconcile(mon, None, now)
    return out


def describe(key, refs):
    """Diagnostika pro zpětné doplnění historie: kdy monitory vznikly a jestli byly pozastavené.
    Jen data, která můžou do veřejného logu Actions (název ze sluzby.toml, ID, data)."""
    out = {}
    h = {"Authorization": f"Bearer {key}"}
    monitors, url = [], f"{API3}/monitors?limit=200"
    for _ in range(20):
        r = _request(url, headers=h)
        monitors += _items(r)
        url = _next(r)
        if not url:
            break
    for ref, m in _match(set(refs), monitors, "friendlyName").items():
        dates = {k: parse_time(v) for k, v in m.items() if "creat" in k.lower() and isinstance(v, (str, int, float))}
        out[ref] = {"id": m.get("id"), "v3": {k: _iso(v) for k, v in dates.items() if v}, "status": _status3(m)}
    now = int(time.time())
    form = urllib.parse.urlencode({"api_key": key, "format": "json", "limit": "50", "logs": "1", "log_types": "98-99",
                                   "logs_start_date": str(now - 366 * 86400), "logs_end_date": str(now), "logs_limit": "500"}).encode()
    try:
        r = _request(API2, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"})
        by_id = {m.get("id"): m for m in r.get("monitors", [])}
        for ref, d in out.items():
            m = by_id.get(d["id"]) or {}
            d["v2_created"] = _iso(int(m["create_datetime"])) if m.get("create_datetime") else None
            d["pause_log"] = [(("paused" if lg.get("type") == 99 else "started"), _iso(int(lg["datetime"])))
                              for lg in sorted(m.get("logs", []), key=lambda x: x.get("datetime", 0)) if lg.get("type") in (98, 99)]
    except FetchError as e:
        for d in out.values():
            d["v2_error"] = str(e)
    return out


def fetch(key, refs, since, api="v3"):
    """refs: množina ID (int) nebo názvů (str) monitorů."""
    return (fetch_v2 if api == "v2" else fetch_v3)(key, set(refs), since)
