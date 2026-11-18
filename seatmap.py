#!/usr/bin/env python3
"""
Read-only seat map for one District show.

Uses the same request District's own seat-layout page makes
(POST /gw/consumer/movies/v1/select-seat). It only READS the layout - it never
calls add-seat / set-seat, so no seats are ever held or reserved.

Auth: District needs a token. We first use a visitor ("guest") token, the way
the website does for people who aren't logged in. If District insists on a
logged-in user, put your own `x-access-token` cookie value from district.in
into config.json as "district_access_token" (Settings in the dashboard).

  python3 seatmap.py --probe   # fetch one seat map and print a summary
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

import district_tracker as T

GW = "https://www.district.in/gw"
DEVICE_FILE = os.path.join(T.STATE_DIR, "device_id")
_token = {"value": None, "kind": None, "at": 0.0}
TOKEN_TTL = 30 * 60


def device_id():
    try:
        with open(DEVICE_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        os.makedirs(T.STATE_DIR, exist_ok=True)
        d = str(uuid.uuid4())
        with open(DEVICE_FILE, "w", encoding="utf-8") as f:
            f.write(d)
        return d


def _post(path, body, headers=None):
    h = {"User-Agent": T.UA, "Content-Type": "application/json",
         "Origin": "https://www.district.in", "Referer": "https://www.district.in/movies/",
         "x-client-id": "district-web", "x-app-type": "web", "x-device-id": device_id()}
    h.update(headers or {})
    req = urllib.request.Request(GW + path, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8", "ignore") or "{}")
        except ValueError:
            payload = {}
        return e.code, dict(e.headers), payload


def _find_token(obj):
    """Pull a token string out of the skip_login response, whatever its shape."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and "token" in k.lower() and len(v) > 20:
                return v
            t = _find_token(v)
            if t:
                return t
    elif isinstance(obj, list):
        for v in obj:
            t = _find_token(v)
            if t:
                return t
    return None


def auth_headers(cfg, refresh=False):
    user_tok = (cfg.get("district_access_token") or "").strip()
    if user_tok:
        return {"x-access-token": user_tok}
    if refresh or not _token["value"] or time.time() - _token["at"] > TOKEN_TTL:
        status, headers, body = _post("/auth/skip_login", {})
        tok = _find_token(body)
        if not tok:  # maybe only set as a cookie
            for k, v in headers.items():
                if k.lower() == "set-cookie" and "token=" in v:
                    tok = v.split("token=", 1)[1].split(";", 1)[0]
        if not tok:
            raise RuntimeError(f"couldn't get a visitor token (HTTP {status})")
        _token.update(value=tok, at=time.time())
    return {"x-guest-token": _token["value"], "x-access-token": _token["value"]}


def fetch_raw(cfg, sess):
    """sess: the raw District session dict (cid, sid, pid, scrnFmt, mid)."""
    body = {"cinemaId": sess["cid"], "sessionId": str(sess["sid"]), "providerId": sess["pid"],
            "screenOnTop": True, "freeSeating": False, "screenFormat": sess.get("scrnFmt") or "",
            "moviecode": sess.get("mid") or "", "config": {"socialDistancing": 1},
            "contentId": cfg["movie_url"].rsplit("-MV", 1)[-1]}
    city = (re.search(r"-in-([a-z-]+)-MV\d+$", cfg["movie_url"]) or [None, "chennai"])[1]
    for attempt in range(3):
        status, _, data = _post("/consumer/movies/v1/select-seat", body,
                                {**auth_headers(cfg, refresh=attempt == 1), "paytmCinemaId": str(sess["cid"])})
        if status == 200:
            return data
        if status == 412 and "sessionDate" not in body:
            # the site adds these for some shows (e.g. opening-day sessions)
            body = {**body, "cityKey": city, "sessionDate": sess.get("date") or "",
                    "fetchSessions": True, "movieFormatCode": str(sess.get("fid") or "").lower()}
            continue
        if status not in (401, 403):
            break
    msg = (data or {}).get("message") or (data or {}).get("msg") or json.dumps(data)[:160]
    if status == 412:
        raise RuntimeError("District isn't sharing this show's seat map yet")
    if status in (401, 403) and not cfg.get("district_access_token"):
        msg += " - District needs a logged-in token: add your x-access-token in Settings"
    raise RuntimeError(f"seat map HTTP {status}: {msg}")


def layout(cfg, sess, classes=()):
    """Compact seat layout: sections -> rows -> seats with grid positions.
    SeatStatus "0" = free; anything else = booked/blocked."""
    data = fetch_raw(cfg, sess)
    by_price = {round(float(c.get("price") or 0)): c for c in classes}
    areas = []
    for a in ((data.get("seatLayout") or {}).get("colAreas") or {}).get("objArea") or []:
        cls = by_price.get(round(float(a.get("AreaPrice") or 0)), {})
        rows = []
        for r in a.get("objRow") or []:
            seats = [{"c": s.get("GridSeatNum"), "n": s.get("displaySeatNumber") or str(s.get("seatNumber") or ""),
                      "f": str(s.get("SeatStatus")) == "0"}
                     for s in r.get("objSeat") or [] if s.get("GridSeatNum") is not None]
            rows.append({"r": r.get("GridRowId"), "id": r.get("PhyRowId") or "", "seats": seats})
        areas.append({"label": cls.get("label") or a.get("AreaDesc") or a.get("AreaCode") or "",
                      "price": a.get("AreaPrice"), "front": bool(cls.get("front")), "rows": rows,
                      "free": sum(s["f"] for r in rows for s in r["seats"]),
                      "total": sum(len(r["seats"]) for r in rows)})
    return {"areas": areas, "fetched_at": time.time()}


def probe():
    cfg = T.load_config()
    base = T.movie_sessions(T.fetch(cfg["movie_url"]))
    langs = T.languages(base)
    key = next(v for k, v in langs.items() if k.lower() == cfg["language"].lower())
    d = base["meta"]["showDates"][-1]
    ms = T.movie_sessions(T.fetch(f"{cfg['movie_url']}?frmtid={key}&fromdate={d}"))
    cin = (ms["pageData"].get("nearbyCinemas") or []) + (ms["pageData"].get("farCinemas") or [])
    sess = next(s for c in cin for s in c["sessions"] if s.get("avail"))
    print("session", sess["encSessionId"], T.to_ist(sess["showTime"]))
    data = fetch_raw(cfg, sess)
    out = os.path.join(T.STATE_DIR, "seatmap_sample.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print("ok, top-level keys:", list(data.keys())[:30])
    print("saved to", out)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
