#!/usr/bin/env python3
"""
Movie ticket tracker (District / TicketNew) -> desktop notifications
(macOS Notification Center, or notify-send on Linux).

Everything you'd want to change lives in config.json (next to state.json in
the data folder - see default_state_dir()), editable from the dashboard's
Settings panel or by hand:

  theatres         which cinemas to alert on (District cinema ids)
  sites            which websites to check: district / ticketnew / bookmyshow
  open_links_on    which site a click opens
  language         e.g. "English"
  window_start/end show start-time window, "HH:MM"
  min_seats        alert only when at least this many seats are free
  front_classes    front-row seat classes (e.g. "budget"); a show with only
                   these left is flagged "Only front rows" (yellow)
  interval_seconds how often the server checks

District and TicketNew sell the same seats (every District cinema session is
tagged clientId "ticketnew"), so both are read from District's data.
BookMyShow blocks automated requests and is not supported.

Usage:
  python3 district_tracker.py               # one check
  python3 district_tracker.py --dry-run     # print everything, no notify, no state change
  python3 district_tracker.py --dry-run --all   # same, ignoring theatre/time filters
  python3 district_tracker.py --test-notify # fire a test macOS notification

For the browser UI + continuous checking, run tracker_server.py instead.
"""

import argparse
import copy
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

APP = "doomsday-tracker"


def default_state_dir():
    """macOS: ~/Library/Application Support/doomsday-tracker
    Linux: $XDG_DATA_HOME/doomsday-tracker (default ~/.local/share/doomsday-tracker)
    Override with the TRACKER_HOME environment variable."""
    if os.environ.get("TRACKER_HOME"):
        return os.path.expanduser(os.environ["TRACKER_HOME"])
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{APP}")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, APP)


STATE_DIR = default_state_dir()
STATE_FILE = os.path.join(STATE_DIR, "state.json")
CONFIG_FILE = os.path.join(STATE_DIR, "config.json")
DISTRICT = "https://www.district.in/movies"
CITY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MOVIE_URL_RE = re.compile(r"district\.in/movies/(?P<slug>.+?)-movie-tickets(?:-in-(?P<city>[a-z0-9-]+))?-MV(?P<id>\d+)")


def cinemas_url(city):
    return f"{DISTRICT}/cinemas-in-{city}"


def movie_url_for(slug, movie_id, city):
    return f"{DISTRICT}/{slug}-movie-tickets-in-{city}-MV{movie_id}"


def city_name(city):
    """'delhi-ncr' -> 'Delhi NCR'"""
    return " ".join(w.upper() if w in ("ncr",) else w.title() for w in city.split("-"))
FAIL_ALERT_AFTER = 3  # consecutive failed runs before a "tracker broken" alert

SITES = {
    "district":   {"label": "District", "supported": True,
                   "note": "Checked directly."},
    "ticketnew":  {"label": "TicketNew", "supported": True,
                   "note": "Same seats as District (cinemas tagged ticketnew); read via District."},
    "bookmyshow": {"label": "BookMyShow", "supported": False,
                   "note": "Blocks automated requests (HTTP 403) - can't be checked."},
}

DEFAULT_CONFIG = {
    "city": "chennai",
    "movie_name": "Avengers: Doomsday",
    "movie_url": "https://www.district.in/movies/avengers-doomsday-movie-tickets-in-chennai-MV176735",
    "language": "English",
    "window_start": "10:00",
    "window_end": "16:00",
    "min_seats": 1,
    # cheap front-row classes (₹62-112). Their seats still count, but a show
    # with ONLY these left is shown yellow ("Only front rows"), never green.
    "front_classes": ["budget", "classic", "normal"],
    "interval_seconds": 15,
    "sites": {"district": True, "ticketnew": True, "bookmyshow": False},
    "open_links_on": "district",
    "district_access_token": "",  # optional: your own district.in login token, only if seat maps need login
    "theatres": [
        {"id": 1022289, "name": "PVR Aerohub",              "keywords": ["aerohub"]},
        {"id": 1022276, "name": "PVR Grand Galada",         "keywords": ["grand galada"]},
        {"id": 1102122, "name": "Seven Screen's Cinemas",   "keywords": ["seven screen"]},
        {"id": 1022271, "name": "PVR Grand Mall Velachery", "keywords": ["grand mall", "velachery"]},
        {"id": 1020779, "name": "INOX Phoenix MarketCity",  "keywords": ["inox", "phoenix"]},
        {"id": 1022304, "name": "PVR VR Mall",              "keywords": ["vr mall"]},
    ],
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- config ---
def validate_config(raw):
    """Return a clean config (defaults filled in) or raise ValueError."""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    raw = raw or {}
    for k in ("movie_name", "movie_url", "language", "open_links_on"):
        if raw.get(k) not in (None, ""):
            cfg[k] = str(raw[k]).strip()
    for k in ("window_start", "window_end"):
        if raw.get(k) is not None:
            if not HHMM.match(str(raw[k])):
                raise ValueError(f"{k} must be HH:MM (24h)")
            cfg[k] = str(raw[k])
    if cfg["window_start"] > cfg["window_end"]:
        raise ValueError("window_start must be before window_end")
    if raw.get("min_seats") is not None:
        cfg["min_seats"] = max(1, int(raw["min_seats"]))
    fc = raw.get("front_classes", raw.get("ignore_classes"))  # old name migrated
    if isinstance(fc, list):
        cfg["front_classes"] = sorted({str(c).strip().lower() for c in fc if str(c).strip()})
    if isinstance(raw.get("district_access_token"), str):
        cfg["district_access_token"] = raw["district_access_token"].strip()
    if raw.get("interval_seconds") is not None:
        cfg["interval_seconds"] = max(10, min(3600, int(raw["interval_seconds"])))
    if isinstance(raw.get("sites"), dict):
        for s in SITES:
            if s in raw["sites"]:
                cfg["sites"][s] = bool(raw["sites"][s]) and SITES[s]["supported"]
    if cfg["open_links_on"] not in ("district", "ticketnew"):
        cfg["open_links_on"] = "district"
    if raw.get("city"):
        city = str(raw["city"]).strip().lower().replace(" ", "-")
        if not CITY_RE.match(city):
            raise ValueError("city must look like 'chennai' or 'delhi-ncr'")
        cfg["city"] = city
    m = MOVIE_URL_RE.search(cfg["movie_url"])
    if not m:
        raise ValueError("movie_url must be a District movie page ending in -MV<number>")
    # keep the movie link pointing at the chosen city
    cfg["movie_url"] = movie_url_for(m["slug"], m["id"], cfg["city"])
    if isinstance(raw.get("theatres"), list):
        out, seen = [], set()
        for t in raw["theatres"]:
            tid = int(t["id"]) if t.get("id") not in (None, "") else None
            kws = [str(k).lower().strip() for k in (t.get("keywords") or []) if str(k).strip()]
            if tid is None and not kws:
                continue
            key = tid or tuple(kws)
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": tid, "name": str(t.get("name") or "").strip() or f"Cinema {tid}",
                        "keywords": kws})
        cfg["theatres"] = out
    return cfg


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return validate_config(json.load(f))
    except FileNotFoundError:
        cfg = validate_config({})
        save_config(cfg)
        return cfg


def save_config(cfg):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def checking_enabled(cfg):
    return cfg["sites"].get("district") or cfg["sites"].get("ticketnew")


# ----------------------------------------------------------------- fetch ---
def fetch(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"fetch failed for {url}: {last}")


def next_data(page_html):
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', page_html, re.S)
    if not m:
        raise RuntimeError("no __NEXT_DATA__ in page (layout changed or blocked?)")
    return json.loads(m.group(1))


class NotOpenYet(Exception):
    """The movie page exists but has no showtimes in this city yet."""


def movie_sessions(page_html):
    state = next_data(page_html)["props"]["pageProps"]["data"]["serverState"]
    ms = state.get("movieSessions")
    if not ms:
        raise NotOpenYet()
    key, val = next(iter(ms.items()))  # key varies (e.g. 'mm5dpqtqjz2026-12-18')
    val["_key"] = re.sub(r"\d{4}-\d{2}-\d{2}$", "", key)
    return val


def languages(ms):
    for bucket in ms["meta"].get("filterBuckets") or []:
        if bucket.get("bucketKey") == "language":
            return {b["title"]: b["key"] for b in bucket.get("buckets", []) if b.get("title")}
    # only one language in this city: no filter, the page's own format is it
    lang = ms["meta"].get("movieLanguage") or "Default"
    return {lang: ms.get("_key")} if ms.get("_key") else {}


def ticketnew_url(cfg, frmt, date):
    m = re.search(r"/movies/(.+?)-movie-tickets(?:-in-[a-z-]+)?-MV(\d+)$", cfg["movie_url"])
    if not m:
        return None
    return (f"https://ticketnew.com/movies/{m.group(1)}-movie-detail-{m.group(2)}"
            f"?frmtid={frmt}&fromdate={date}")


def seat_layout_url(cfg, frmt, date, enc_session_id, host="www.district.in"):
    """Direct link to a show's seat map. TicketNew runs the same site, so
    host="ticketnew.com" gives the same page there."""
    m = re.search(r"-MV(\d+)$", cfg["movie_url"])
    if not (m and enc_session_id):
        return None
    return (f"https://{host}/movies/seat-layout/{frmt.lower()}?encsessionid={enc_session_id}"
            f"&fromdate={date}&freeseating=false&fromsessions=true&type=MOVIES&contentid={m.group(1)}")


# ---------------------------------------------------------------- filter ---
def theatre_for(cfg, cinema_id, name):
    low = name.lower()
    for t in cfg["theatres"]:
        if (t["id"] and cinema_id == t["id"]) or (t["keywords"] and all(k in low for k in t["keywords"])):
            return t["name"]
    return None


IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# District's statusColor codes -> the labels its website shows
STATUS = {"G": "Available", "Y": "Filling fast", "R": "Almost full", "D": "Sold out"}


def to_ist(show_time):
    """District's session showTime is UTC without a suffix (the website shows
    it +5:30, e.g. raw '2026-12-18T18:25' is the 11:55 PM show). Convert to IST."""
    utc = dt.datetime.fromisoformat(show_time[:16]).replace(tzinfo=dt.timezone.utc)
    return utc.astimezone(IST)


def in_window(cfg, hhmm):
    return cfg["window_start"] <= hhmm <= cfg["window_end"]


def site_allows(cfg, client_id):
    if cfg["sites"].get("district"):
        return True
    return cfg["sites"].get("ticketnew") and client_id == "ticketnew"


def collect_all(cfg):
    """Every session in the configured language, annotated with filter info."""
    if not checking_enabled(cfg):
        raise RuntimeError("no website enabled in Settings (turn on District or TicketNew)")
    try:
        base = movie_sessions(fetch(cfg["movie_url"]))
    except NotOpenYet:
        # District lists the movie but no cinema in this city has opened
        # bookings yet. Not an error: keep checking, alert when it opens.
        return {"dates": [], "shows": [], "cinemas": {}, "warnings": [], "languages": [],
                "class_labels": [], "not_open": True}
    langs = languages(base)
    lang_key = next((v for k, v in langs.items() if k.lower() == cfg["language"].lower()), None)
    if not lang_key:
        raise RuntimeError(f"language '{cfg['language']}' not offered; available: {', '.join(langs)}")
    dates = base["meta"].get("showDates") or []
    shows, cinemas_seen, warnings = [], {}, []
    front = set(cfg["front_classes"])
    for d in dates:
        url = f"{cfg['movie_url']}?frmtid={lang_key}&fromdate={d}"
        tn_url = ticketnew_url(cfg, lang_key, d)
        ms = movie_sessions(fetch(url))
        sel = ms["meta"].get("selectedShowDate")
        if sel != d:
            warnings.append(f"asked for {d}, server returned {sel}; skipped")
            continue
        pdata = ms.get("pageData") or {}
        cinemas = (pdata.get("nearbyCinemas") or []) + (pdata.get("farCinemas") or [])
        expected = (ms.get("nearbyCinemasCount") or 0) + (ms.get("farCinemasCount") or 0)
        if expected > len(cinemas):
            warnings.append(f"{d}: page listed {len(cinemas)} cinemas but reports {expected}")
        for c in cinemas:
            info = c["cinemaInfo"]
            client = info.get("clientId") or ""
            if not site_allows(cfg, client):
                continue
            cid = c.get("id")
            theatre = theatre_for(cfg, cid, info["name"])
            cinemas_seen[cid] = {
                "name": info["name"], "address": info.get("address") or "",
                "logo": (c.get("cinemaMeta") or {}).get("cinemaLogoUrl") or "",
                "theatre": theatre, "client": client,
            }
            for s in c.get("sessions") or []:
                links = {}
                if cfg["sites"].get("district"):
                    # same link District's own page uses: opens this show's seat map
                    links["district"] = seat_layout_url(cfg, lang_key, d, s.get("encSessionId")) or url
                if cfg["sites"].get("ticketnew") and client == "ticketnew":
                    links["ticketnew"] = seat_layout_url(cfg, lang_key, d, s.get("encSessionId"),
                                                         host="ticketnew.com") or tn_url
                primary = links.get(cfg["open_links_on"]) or next(iter(links.values()), url)
                local = to_ist(s["showTime"])
                hhmm = local.strftime("%H:%M")
                shows.append({
                    "id": s.get("encSessionId") or f'{cid}-{s.get("sid")}',
                    "cinema_id": cid,
                    "theatre": theatre,
                    "cinema": info["name"],
                    "date": local.date().isoformat(),
                    "time": hhmm,
                    "format": s.get("scrnFmt") or "",
                    "avail": int(s.get("avail") or 0),
                    "total": int(s.get("total") or 0),
                    "status": STATUS.get(s.get("statusColor"), s.get("seatStatus") or ""),
                    "color": s.get("statusColor") or "",
                    "classes": [{"label": (a.get("label") or "").title(),
                                 "price": a.get("price"),
                                 "avail": int(a.get("sAvail") or 0),
                                 "total": int(a.get("sTotal") or 0),
                                 "status": STATUS.get(a.get("statusColor"), ""),
                                 "color": a.get("statusColor") or "",
                                 "front": (a.get("label") or "").strip().lower() in front}
                                for a in s.get("areas") or []],
                    "in_window": in_window(cfg, hhmm),
                    "url": primary,
                    "links": links,
                    "raw": {"cid": s.get("cid") or cid, "sid": s.get("sid"), "pid": s.get("pid"),
                            "scrnFmt": s.get("scrnFmt"), "mid": s.get("mid"),
                            "fid": s.get("fid") or lang_key, "date": local.date().isoformat()},
                })
        time.sleep(1)
    for s in shows:
        add_good_seats(s)
    for w in warnings:
        log("WARN: " + w)
    uniq = {}
    for s in shows:  # a show can appear on two date pages around midnight
        uniq.setdefault(s["id"], s)
    shows = list(uniq.values())
    return {"dates": dates, "shows": shows, "cinemas": cinemas_seen,
            "warnings": warnings, "languages": list(langs),
            "class_labels": sorted({a["label"] for s in shows for a in s["classes"] if a["label"]})}


RANK = {"G": 3, "Y": 2, "R": 1, "D": 0}


def add_good_seats(s):
    """Split seats into front rows vs the rest and pick the display status.
    All seats count; but if only front-row seats are left the show is
    'Only front rows' (yellow), never 'Available' (green)."""
    back = [a for a in s["classes"] if not a["front"]]
    s["front_avail"] = sum(a["avail"] for a in s["classes"] if a["front"])
    s["good_avail"] = sum(a["avail"] for a in back) if back else s["avail"]
    open_back = [a for a in back if a["avail"] > 0]
    if s["avail"] == 0:
        color, status = "D", "Sold out"
    elif not back:
        color, status = s["color"] or "G", STATUS.get(s["color"], "Available")
    elif open_back:  # best status among the non-front classes that still have seats
        color = max((a["color"] or "G" for a in open_back), key=lambda c: RANK.get(c, 3))
        status = STATUS.get(color, "Available")
    else:
        color, status = "Y", "Only front rows"
    s["good_color"], s["good_status"] = color, status


def matching(shows):
    return [s for s in shows if s["theatre"] and s["in_window"]]


def fetch_cinema_directory(city="chennai"):
    """cinema_id -> {name, address, logo, photo, url} for a city's cinemas."""
    page = fetch(cinemas_url(city))
    urls = {int(cid): u for u, cid in
            re.findall(r'href="(https://www\.district\.in/movies/[a-z0-9-]+-CD(\d+))"', page)}
    out = {}

    def walk(o):
        if isinstance(o, dict):
            cd = o.get("CinemaData")
            if isinstance(cd, dict) and cd.get("cinema_id"):
                cid = int(cd["cinema_id"])
                out[cid] = {"name": cd.get("cinema_name", ""),
                            "address": cd.get("address", ""),
                            "logo": cd.get("logo", ""),
                            "photo": cd.get("logo_url_v2", ""),
                            "url": urls.get(cid, "")}
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(next_data(page))
    return out


def _movie_data(page_html):
    """movie_id -> MovieData objects found anywhere on a District listing page."""
    movies = {}

    def walk(o):
        if isinstance(o, dict):
            md = o.get("MovieData")
            if isinstance(md, dict) and md.get("movie_id") and md.get("name"):
                movies.setdefault(str(md["movie_id"]), md)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(next_data(page_html))
    return movies


def fetch_city_movies(city):
    """Movies for a city: those open for booking now plus upcoming ones, so you
    can track a movie before any cinema in the city opens bookings.
    Raises ValueError if District doesn't know the city."""
    try:
        now_page = fetch(f"{DISTRICT}/{city}-movie-tickets", tries=1)
    except RuntimeError as e:
        if "404" in str(e):
            raise ValueError(f"'{city}' isn't a District city") from None
        raise
    now = _movie_data(now_page)
    try:
        upcoming = _movie_data(fetch(f"{DISTRICT}/upcoming-movies-in-{city}", tries=1))
    except RuntimeError:
        upcoming = {}
    out = []
    for mid, md in {**upcoming, **now}.items():
        m = MOVIE_URL_RE.search(md.get("web_url") or "")
        if not m:
            continue
        try:
            release = dt.datetime.fromtimestamp(int(md.get("release_date") or 0), IST).date().isoformat()
        except (ValueError, OSError):
            release = ""
        out.append({"id": mid, "name": md["name"], "image": md.get("image") or "",
                    "languages": md.get("languages") or [], "censor": md.get("censor") or "",
                    "genres": md.get("genres") or [], "release": release,
                    "open": mid in now,           # bookings open in this city right now
                    "url": movie_url_for(m["slug"], mid, city)})
    # open for booking first (A-Z), then upcoming by release date
    out.sort(key=lambda x: (not x["open"], x["name"].lower() if x["open"] else (x["release"] or "9999"), x["name"].lower()))
    return out


def fetch_city_cinemas(city, max_movies=6, movies=None):
    """All District cinemas for a city: the city's cinema page (which only
    shows theatres "near you") plus every cinema showing the city's open
    movies. cinema_id -> {name, address, logo, photo, url}."""
    out = fetch_cinema_directory(city)
    try:
        movies = [m for m in (movies if movies is not None else fetch_city_movies(city)) if m["open"]][:max_movies]
    except (ValueError, RuntimeError):
        movies = []
    for m in movies:
        try:
            ms = movie_sessions(fetch(m["url"], tries=1))
        except (NotOpenYet, RuntimeError, KeyError, ValueError):
            continue
        for c in (ms["pageData"].get("nearbyCinemas") or []) + (ms["pageData"].get("farCinemas") or []):
            info = c.get("cinemaInfo") or {}
            if c.get("id") and c["id"] not in out:
                out[c["id"]] = {"name": info.get("name", ""), "address": info.get("address") or "",
                                "logo": (c.get("cinemaMeta") or {}).get("cinemaLogoUrl") or "",
                                "photo": "", "url": ""}
        time.sleep(0.3)
    return out


def fetch_popular_cities():
    """City keys District features on its movies home page."""
    page = fetch(f"{DISTRICT}/", tries=1)
    keys = sorted(set(re.findall(r'\\?"city_?[kK]ey\\?":\\?"([a-z0-9-]+)', page)) - {"all"})
    return [{"key": k, "name": city_name(k)} for k in keys]


# ---------------------------------------------------------------- notify ---
def notify(title, message, subtitle="", icon="video-display"):
    """Desktop notification: osascript on macOS, notify-send on Linux.
    Falls back to the log if neither is available."""
    try:
        if sys.platform == "darwin":
            def esc(s):
                return s.replace("\\", "\\\\").replace('"', '\\"')
            script = f'display notification "{esc(message)}" with title "{esc(title)}"'
            if subtitle:
                script += f' subtitle "{esc(subtitle)}"'
            script += ' sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, timeout=15)
            return
        if shutil.which("notify-send"):
            body = f"{subtitle}\n{message}" if subtitle else message
            subprocess.run(["notify-send", "--app-name=Ticket Tracker", "--urgency=critical",
                            f"--icon={icon}", title, body], check=False, timeout=15)
            return
    except (OSError, subprocess.SubprocessError) as e:
        log(f"WARN: notification failed: {e}")
    log(f"NOTIFY: {title} | {subtitle} | {message}")


def pretty_date(d):
    return dt.date.fromisoformat(d).strftime("%a %d %b")


# ----------------------------------------------------------------- state ---
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"seen": {}, "fails": 0, "broken_alerted": False}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_FILE)


def record_failure(st, err):
    st["fails"] = st.get("fails", 0) + 1
    if st["fails"] >= FAIL_ALERT_AFTER and not st.get("broken_alerted"):
        notify("Ticket tracker stopped working",
               f"{st['fails']} checks failed in a row: {str(err)[:120]}", icon="dialog-warning")
        st["broken_alerted"] = True
    save_state(st)


def process_alerts(cfg, st, shows, extra_hint=""):
    """Notify for newly-available matching shows; update + save state. Returns fresh list."""
    if st.get("broken_alerted"):
        notify("Ticket tracker is working again", "Checks are back to normal.", icon="emblem-default")
    st["fails"], st["broken_alerted"] = 0, False

    need = cfg["min_seats"]
    seen = st.setdefault("seen", {})
    fresh = []
    for s in matching(shows):
        prev = seen.get(s["id"])
        if s["avail"] >= need and (prev is None or prev.get("avail", 0) < need):
            fresh.append(s)
        seen[s["id"]] = {"avail": s["avail"], "date": s["date"], "time": s["time"],
                         "theatre": s["theatre"]}

    if fresh:
        movie = cfg["movie_name"]
        fresh.sort(key=lambda x: (x["date"], x["time"]))
        if len(fresh) == 1:
            s = fresh[0]
            notify(f"Tickets open: {movie}",
                   f"{s['format']} · {s['avail']} seats left · {s['good_status']}{extra_hint}",
                   f"{s['theatre']} · {pretty_date(s['date'])} · {s['time']}")
        else:
            lines = "; ".join(f"{x['theatre']} {pretty_date(x['date'])} {x['time']}"
                              for x in fresh[:4])
            more = f" +{len(fresh) - 4} more" if len(fresh) > 4 else ""
            notify(f"{len(fresh)} shows open: {movie}", lines + more + extra_hint,
                   f"{cfg['language']} · {cfg['window_start']}–{cfg['window_end']} · your theatres")
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "alerts.log"), "a") as f:
            for s in fresh:
                log(f"ALERT: {s['theatre']} {s['date']} {s['time']} {s['format']} "
                    f"{s['avail']} seats ({s['good_status']}) -> {s['url']}")
                f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}  {s['theatre']}  {s['date']} "
                        f"{s['time']}  {s['format']}  {s['avail']} seats  {s['good_status']}  {s['url']}\n")

    st["last_ok"] = dt.datetime.now().isoformat(timespec="seconds")
    save_state(st)
    return fresh


def print_shows(shows):
    for s in sorted(shows, key=lambda x: (x["date"], x["theatre"] or x["cinema"], x["time"])):
        log(f"  {s['date']} {s['time']}  {(s['theatre'] or s['cinema'])[:40]:<40} "
            f"{s['format']:<24} {s['avail']:>4} free ({s['front_avail']} front)  {s['good_status']}")


# ------------------------------------------------------------------ main ---
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print only, no notify/state")
    ap.add_argument("--all", action="store_true", help="ignore theatre/time filters (with --dry-run)")
    ap.add_argument("--test-notify", action="store_true", help="send a test notification")
    a = ap.parse_args()

    if a.test_notify:
        notify("Ticket tracker test", "If you can see this, notifications work.")
        print("Test notification sent.")
        return

    cfg = load_config()
    st = load_state()
    try:
        res = collect_all(cfg)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR: {e}")
        if not a.dry_run:
            record_failure(st, e)
        sys.exit(1)

    shows = res["shows"] if a.all else matching(res["shows"])
    log(f"OK: dates={res['dates']} matching shows={len(shows)} "
        f"(with seats: {sum(1 for s in shows if s['avail'] > 0)})")
    print_shows(shows)

    if not a.dry_run:
        process_alerts(cfg, st, res["shows"])


if __name__ == "__main__":
    main()
