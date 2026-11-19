#!/usr/bin/env python3
"""
Read-only helpers for District's location features - the same requests
district.in makes in your browser:

  search_locations("madurai")  -> address suggestions (like District's location search)
  resolve_location(lat, lng)   -> District's city/location data for that spot
  movie_sessions(loc, ...)     -> showtimes near that location

They only read listings. Nothing here books, holds or reserves anything.
These are District's private, undocumented APIs: they can change at any time
and using them may be against District's terms of service.

  python3 district_api.py --probe madurai   # try location search + showtimes
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import district_tracker as T
from seatmap import device_id

GW = "https://www.district.in/gw"
GUEST = "1212"   # the visitor token district.in itself sends when not logged in


def loc_headers(loc):
    """District's location headers, from a location dict (see resolve_location)."""
    if not loc:
        return {}
    h = {"x-user-lat": loc.get("lat"), "x-user-lng": loc.get("long"),
         "x-gps-lat": loc.get("lat"), "x-gps-lng": loc.get("long"),
         "x-city-id": loc.get("cityId"), "x-pcity-id": loc.get("pCityId"),
         "x-pcity-key": loc.get("pCityKey"), "x-pstate-key": loc.get("pStateKey"),
         "x-country-id": loc.get("countryId") or "1", "x-place-type": loc.get("placeType"),
         "x-place-id": loc.get("placeId"), "x-subzone-id": loc.get("subzoneId"),
         "x-available-tabs": "movies"}
    return {k: str(v) for k, v in h.items() if v not in (None, "")}


def _req(method, path, body=None, loc=None, query=None):
    url = GW + path + (("&" if "?" in path else "?") + urllib.parse.urlencode(query) if query else "")
    h = {"User-Agent": T.UA, "Content-Type": "application/json", "Accept": "application/json",
         "Origin": "https://www.district.in", "Referer": "https://www.district.in/movies/",
         "x-client-id": "district-web", "x-app-type": "web", "x-device-id": device_id(),
         "x-guest-token": GUEST, **loc_headers(loc)}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20, context=T.ssl_context()) as r:
            return r.status, json.loads(r.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "ignore") or "{}")
        except ValueError:
            return e.code, {}


def search_locations(text):
    status, data = _req("POST", "/web/get_location_search", {"searched_text": text})
    if status != 200:
        raise RuntimeError(f"location search HTTP {status}: {str(data)[:160]}")
    return data


def resolve_location(lat, lng):
    status, data = _req("POST", "/web/pre_home",
                        {"gps_lat": lat, "user_lng": lng, "gps_lng": lng, "user_lat": lat},
                        loc={"lat": lat, "long": lng})
    if status != 200:
        raise RuntimeError(f"pre_home HTTP {status}: {str(data)[:160]}")
    return data


def movie_sessions(loc, city_key, movie_code, date=None):
    q = {"meta": 1, "reqData": 1, "city": city_key, "movieCode": movie_code}
    if date:
        q["date"] = date
    status, data = _req("GET", "/consumer/movies/v3/movie", loc=loc, query=q)
    if status != 200:
        raise RuntimeError(f"movie sessions HTTP {status}: {str(data)[:160]}")
    return data


TYPE_LABEL = {"CITY": "City", "SUBZONE": "Area", "DSZ": "Area", "POI": "Landmark", "GOOGLE_PLACE": "Place"}


def location_suggestions(text):
    """Address suggestions for the Settings search box (only ones with coordinates)."""
    out = []
    for e in (search_locations(text) or {}).get("entities") or []:
        if e.get("lat") is None or e.get("long") is None:
            continue
        kind = (e.get("entity_type") or "").replace("LOCATION_ENTITY_TYPE_", "")
        out.append({"title": e.get("title") or "", "subtitle": e.get("subtitle") or "",
                    "type": TYPE_LABEL.get(kind, "Place"), "lat": e["lat"], "lng": e["long"]})
    return out


def city_for(lat, lng):
    """District's city for a point: {key, name, state, area}."""
    ld, loc = location_from_pre_home(lat, lng)
    if not loc.get("pCityKey"):
        raise ValueError("District doesn't cover this location")
    return {"key": loc["pCityKey"], "name": ld.get("p_city_name") or ld.get("city_name") or loc["pCityKey"],
            "state": ld.get("p_state_name") or "", "area": ld.get("display_title") or ""}


def _shape(o, depth=0, maxd=3):
    """Short structural summary of a JSON value, for the probe."""
    if depth >= maxd:
        return type(o).__name__
    if isinstance(o, dict):
        return {k: _shape(v, depth + 1, maxd) for k, v in list(o.items())[:14]}
    if isinstance(o, list):
        return [_shape(o[0], depth + 1, maxd), f"...x{len(o)}"] if o else []
    return o if isinstance(o, (int, float, bool)) or o is None else str(o)[:60]


def probe(text):
    res = search_locations(text)
    print("== location search shape:")
    print(json.dumps(_shape(res, maxd=5), indent=1)[:2500])
    with open(os.path.join(T.STATE_DIR, "location_search_sample.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print("saved", os.path.join(T.STATE_DIR, "location_search_sample.json"))


def location_from_pre_home(lat, lng):
    """pre_home -> location dict usable by loc_headers()/movie_sessions()."""
    data = resolve_location(lat, lng)
    ld = (data.get("data") or {}).get("location_data") or data.get("location_data") or {}
    return ld, {"lat": lat, "long": lng, "cityId": ld.get("city_id"), "pCityId": ld.get("p_city_id") or ld.get("city_id"),
                "pCityKey": ld.get("p_city_key") or ld.get("city_key"), "pStateKey": ld.get("p_state_key"),
                "countryId": ld.get("country_id") or 1}


def probe_sessions(lat, lng, movie_code="mm5dpqtqjz"):
    ld, loc = location_from_pre_home(lat, lng)
    print("== pre_home location_data:", json.dumps(_shape(ld, maxd=2))[:700])
    city = loc["pCityKey"] or "madurai"
    data = movie_sessions(loc, city, movie_code)
    pd = data.get("pageData") or {}
    meta = data.get("meta") or {}
    print("== sessions: city", city, "| top keys", list(data.keys())[:10], "| showDates", meta.get("showDates"))
    cin = (pd.get("nearbyCinemas") or []) + (pd.get("farCinemas") or [])
    cmap = data.get("cinemasMap") or pd.get("cinemasMap") or {}
    sess = pd.get("sessions") or {}
    print("   nearby/far cinemas:", [(c.get("cinemaInfo") or c).get("name", "")[:40] for c in cin][:8])
    print("   sessions by cinema id:", {k: len(v) for k, v in list(sess.items())[:8]} if isinstance(sess, dict) else type(sess))
    print("   cinemasMap:", [v.get("name", "")[:40] for v in list(cmap.values())[:8]] if isinstance(cmap, dict) else type(cmap))
    with open(os.path.join(T.STATE_DIR, "sessions_sample.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--probe":
        if sys.argv[2] == "sessions":
            probe_sessions(float(sys.argv[3]), float(sys.argv[4]), *(sys.argv[5:6]))
        else:
            probe(" ".join(sys.argv[2:]))
