#!/usr/bin/env python3
"""
Ticket tracker server: checks District/TicketNew in the background, sends
macOS notifications, and serves a browser dashboard with a Settings panel.

  python3 tracker_server.py              # http://127.0.0.1:8765
  python3 tracker_server.py --port 9000

All tracking options (theatres, websites, language, time window, min seats,
check interval) live in config.json and can be edited from the dashboard.
Stdlib only. Reuses district_tracker.py (must sit in the same folder).
"""

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import district_tracker as T
import seatmap
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
UI_FILE = os.path.join(HERE, "tracker_ui.html")
DIRECTORY_REFRESH = 6 * 3600   # re-read theatre logos/links every 6h
MAX_BACKOFF = 300              # seconds, after repeated failures
SEAT_CACHE_TTL = 30            # seconds a fetched seat map is reused


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.cfg = T.load_config()
        self.directory, self.directory_at = {}, 0.0
        self.result = None
        self.last_check = self.last_ok = self.next_check = None
        self.error, self.checking, self.check_count = None, False, 0
        self.fresh_ids = set()
        self.seat_cache, self.seat_lock = {}, threading.Lock()

    # ---- config ----
    def set_config(self, raw):
        cfg = T.validate_config(raw)        # raises ValueError on bad input
        T.save_config(cfg)
        with self.lock:
            self.cfg = cfg
            self.result = None              # old results used old filters
            self.fresh_ids.clear()
        self.wake.set()                     # re-check right away
        return cfg

    # ---- background loop ----
    def loop(self):
        delay = None
        while True:
            self.check_once()
            interval = self.cfg["interval_seconds"]
            delay = interval if not self.error else min(max((delay or interval) * 2, 30), MAX_BACKOFF)
            with self.lock:
                self.next_check = time.time() + delay
            self.wake.wait(delay)
            self.wake.clear()

    def refresh_directory(self, force=False):
        if force or not self.directory or time.time() - self.directory_at > DIRECTORY_REFRESH:
            try:
                d = T.fetch_cinema_directory()
                with self.lock:
                    self.directory, self.directory_at = d, time.time()
            except Exception as e:  # noqa: BLE001  (logos are nice-to-have)
                T.log(f"WARN: cinema directory: {e}")

    def check_once(self):
        with self.lock:
            self.checking = True
            cfg = self.cfg
        st = T.load_state()
        try:
            self.refresh_directory()
            res = T.collect_all(cfg)
            fresh = T.process_alerts(cfg, st, res["shows"], extra_hint=" · see dashboard")
            m = T.matching(res["shows"])
            T.log(f"OK #{self.check_count + 1}: {len(m)} matching shows, "
                  f"{sum(1 for s in m if s['avail'] >= cfg['min_seats'])} with seats, {len(fresh)} new")
            with self.lock:
                if cfg is self.cfg:             # ignore results from a superseded config
                    self.result, self.error = res, None
                    self.last_ok = time.time()
                    self.fresh_ids |= {s["id"] for s in fresh}
        except Exception as e:  # noqa: BLE001
            T.log(f"ERROR: {e}")
            T.record_failure(st, e)
            with self.lock:
                self.error = str(e)
        finally:
            with self.lock:
                self.checking = False
                self.last_check = time.time()
                self.check_count += 1

    # ---- seat map (on demand, when the user hovers a show) ----
    def seat_map(self, show_id):
        with self.lock:
            res, cfg = self.result, self.cfg
            hit = self.seat_cache.get(show_id)
        if hit and time.time() - hit["fetched_at"] < SEAT_CACHE_TTL:
            return hit
        show = next((s for s in (res or {}).get("shows", []) if s["id"] == show_id), None)
        if not show:
            raise KeyError("unknown show")
        with self.seat_lock:   # one request to District at a time
            lay = seatmap.layout(cfg, show["raw"], show.get("classes") or [])
        with self.lock:
            self.seat_cache[show_id] = lay
            if len(self.seat_cache) > 200:
                self.seat_cache.pop(next(iter(self.seat_cache)))
        return lay

    # ---- data for the UI ----
    def config_payload(self):
        with self.lock:
            cfg, d, res = self.cfg, dict(self.directory), self.result
        cinemas = {cid: {"id": cid, "name": v["name"], "address": v["address"], "logo": v["logo"]}
                   for cid, v in d.items()}
        if res:  # cinemas with shows that aren't in the directory page
            for cid, v in res["cinemas"].items():
                cinemas.setdefault(cid, {"id": cid, "name": v["name"], "address": v["address"],
                                         "logo": v["logo"]})
        return {
            "config": cfg,
            "sites": T.SITES,
            "languages": (res or {}).get("languages") or [cfg["language"]],
            "class_labels": sorted(set((res or {}).get("class_labels") or [])
                                   | {c.title() for c in cfg["front_classes"]}),
            "cinemas": sorted(cinemas.values(), key=lambda c: c["name"].lower()),
        }

    def snapshot(self):
        with self.lock:
            res, d, cfg = self.result, self.directory, self.cfg
            base = {
                "movie": cfg["movie_name"], "language": cfg["language"],
                "window": [cfg["window_start"], cfg["window_end"]],
                "min_seats": cfg["min_seats"], "interval": cfg["interval_seconds"],
                "front_classes": cfg["front_classes"],
                "sites": {k: v for k, v in cfg["sites"].items() if v},
                "open_links_on": cfg["open_links_on"],
                "checking": self.checking, "check_count": self.check_count,
                "error": self.error, "last_check": self.last_check,
                "last_ok": self.last_ok, "next_check": self.next_check,
                "now": time.time(), "movie_url": cfg["movie_url"],
            }
            fresh_ids = set(self.fresh_ids)
        if not res:
            return {**base, "dates": [], "theatres": [], "others": [], "warnings": [],
                    "loading": True}

        def card(cid, theatre):
            info = d.get(cid, {})
            seen = res["cinemas"].get(cid, {})
            shows = sorted((s for s in res["shows"] if s["cinema_id"] == cid),
                           key=lambda s: (s["date"], s["time"]))
            return {
                "cinema_id": cid, "name": theatre or seen.get("name") or info.get("name") or str(cid),
                "full_name": info.get("name") or seen.get("name") or "",
                "address": info.get("address") or seen.get("address") or "",
                "logo": info.get("logo") or seen.get("logo") or "",
                "photo": info.get("photo") or "",
                "cinema_url": info.get("url") or "",
                "target": bool(theatre),
                "shows": [{**s, "new": s["id"] in fresh_ids} for s in shows],
            }

        theatres = []
        for t in cfg["theatres"]:
            ids = [cid for cid, c in res["cinemas"].items() if c["theatre"] == t["name"]]
            if not ids and t["id"]:
                ids = [t["id"]]
            theatres += [card(cid, t["name"]) for cid in ids]
        others = sorted((card(cid, None) for cid, c in res["cinemas"].items() if not c["theatre"]),
                        key=lambda c: c["name"])
        return {**base, "dates": res["dates"], "theatres": theatres, "others": others,
                "warnings": res["warnings"], "loading": False}


def make_handler(tracker):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(UI_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/seatmap"):
                sid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
                try:
                    self._json(200, tracker.seat_map(sid))
                except KeyError:
                    self._json(404, {"error": "show not found - refresh the page"})
                except Exception as e:  # noqa: BLE001
                    T.log(f"WARN: seat map {sid}: {e}")
                    self._json(502, {"error": str(e)})
            elif self.path == "/api/status":
                self._json(200, tracker.snapshot())
            elif self.path == "/api/config":
                if not tracker.directory:
                    tracker.refresh_directory()
                self._json(200, tracker.config_payload())
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            # only accept requests from our own page (blocks other sites posting to localhost)
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}",
                                         f"http://localhost:{self.server.server_port}"):
                return self._json(403, {"error": "forbidden origin"})
            if self.path == "/api/check":
                tracker.wake.set()
                self._json(200, {"ok": True})
            elif self.path == "/api/test-notify":
                T.notify("🎬 Ticket tracker test", "Notifications from the dashboard work.")
                self._json(200, {"ok": True})
            elif self.path == "/api/config":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    cfg = tracker.set_config(json.loads(self.rfile.read(n) or b"{}"))
                    T.log(f"Config updated: {len(cfg['theatres'])} theatres, sites="
                          f"{[k for k, v in cfg['sites'].items() if v]}, {cfg['language']} "
                          f"{cfg['window_start']}-{cfg['window_end']}, every {cfg['interval_seconds']}s")
                    self._json(200, {"ok": True, "config": cfg})
                except (ValueError, KeyError, TypeError) as e:
                    self._json(400, {"error": str(e)})
            else:
                self._send(404, "not found", "text/plain")

        def log_message(self, *a):  # keep the log clean
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    tracker = Tracker()
    threading.Thread(target=tracker.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(tracker))
    T.log(f"Dashboard: http://127.0.0.1:{a.port}  (config: {T.CONFIG_FILE})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
