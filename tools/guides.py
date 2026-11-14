#!/usr/bin/env python3
"""Builds the Field Guides (city, park and trip guides for the Want to go list) from guides/src/<slug>.guide.geojson.

Each source file is one guide in the Field Guide format 0.1 (spec, schema and tools in the
private repo natebird/placemoir-guide-spec): a GeoJSON FeatureCollection with a `guide`
object (id, status, kind, name, description, author, inLanguage, dateModified, countries,
sections, and optional region, ordered, season, related, sources, license), one Point feature
per place (name, category, section, notes, address, and optional position, locality,
countryCode, ids, match, sources, lastVerified), and optional route lines
(`"featureType": "route"`). `validate` checks the spec's rules with the standard library, and
`from_source` turns a guide into what the rest of this script builds. Guide categories are
richer than the app's 9; SPEC_CATEGORIES maps each one down for the library file. This script
turns each source into:

    guides/<slug>/index.html               the page people read
    guides/<slug>/guide.json               the library file Placemoir 1.1 downloads
    guides/<slug>/<slug>.placemoirarchive  the same file zipped, which Placemoir 1.0 opens from Files

plus guides/index.html (the list of guides), guides/index.json (the same list for the app's
Guides list), guides/suggest/index.html (the Suggest a place form, see TURNSTILE_SITE_KEY) and
the guides block in sitemap.xml.

    python3 tools/guides.py build          # write everything (standard library only)
    python3 tools/guides.py check          # validate the sources; fail if the built files are stale
    python3 tools/guides.py maps [slug…]   # look every place up in Apple Maps (see below)
    python3 tools/guides.py token <origin> [days]  # write guides/mapkit-token.json for the map

A guide's kind and region go into guide.json and index.json (kind only when it isn't city).
Guides that aren't city guides get a not-affiliated and check-before-you-go line, and the guides
list groups by kind once more than one kind is published. An ordered guide numbers its places;
a season line shows under the title.

A guide with "status": "draft" (or "retired") is built and served with noindex, so it can be
reviewed on the live site, but stays off the guides list and the sitemap until it is "published".

The guide files follow the library format at archive-format.html, with its `guide` block.
Place ids are `guide:<slug>/<key>`, not UUIDs, so importing a guide twice adds nothing and a
place already in someone's journal is matched by name within 150 m.

`maps` needs APPLE_MAPS_KEY (the .p8 contents), APPLE_MAPS_KEY_ID and APPLE_TEAM_ID in the
environment, and `pip install pyjwt cryptography`. It searches Apple Maps for each place near
its coordinates and writes what it found to guides/src/<slug>.maps.json, which `build` reads
for the Apple Maps place ID and the exact position, so the source's coordinates only need to be
close. A place matches when its name is alike and Apple Maps has the same street number and
street; a place without a street number (a trail, a big park) needs a near-identical name. It
exits 1 if any place isn't found. Set "match": {"query": …} on a place to search for a different
name, or "match": {"skip": "<reason>"} to leave it out of the check.

Each guide page has an Apple Maps map (MapKit JS, js/guide-map.js) with a pin per place,
coloured by section. It needs guides/mapkit-token.json, which the Pages deploy writes with
`token https://placemoir.app`; without it the map stays hidden and the page is just the list.
Only guide pages allow Apple's map hosts in their Content Security Policy.
"""
import datetime
import difflib
import html
import io
import json
import math
import os
import pathlib
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SITE = pathlib.Path(__file__).resolve().parent.parent
SRC = SITE / "guides" / "src"
OUT = SITE / "guides"
BASE_URL = "https://placemoir.app"
APP_STORE = "https://apps.apple.com/app/placemoir/id6829224969"

# On: the button opens Placemoir through the universal link on placemoir.com (served by
# workers/placemoir-com/), and the file download is the small-print fallback. The link opens
# the app only with Placemoir 1.1 or later installed and the Worker live; otherwise the
# Worker sends it back to the guide page, where the download link still works.
ONE_TAP = True
SHORT_LINK = "https://placemoir.com/g/{slug}"

# The "Suggest a place" form (guides/suggest/), which posts to workers/place-suggestions/.
# Paste the Turnstile widget's site key here (it's public) once that Worker is deployed, then
# build. Until then the page says suggestions aren't open yet, has noindex and isn't linked.
TURNSTILE_SITE_KEY = "0x4AAAAAAF-W8noK39RV6gFc"
SUGGEST_ENDPOINT = "/api/field-guide-suggestions"
SUGGEST_URL = f"{BASE_URL}/guides/suggest/"
# Cloudflare Turnstile, on the suggestion page only: its script and its check run in a frame.
TURNSTILE_CSP = {
    "script-src": ["https://challenges.cloudflare.com"],
    "frame-src": ["https://challenges.cloudflare.com"],
}

CATEGORIES = {"restaurant": "Restaurant", "cafe": "Café", "bar": "Bar", "hotel": "Hotel",
              "park": "Park", "museum": "Museum", "attraction": "Attraction",
              "airport": "Airport", "other": "Place"}
# What a guide covers. "city" is the default when a source leaves "kind" out; the guides
# list groups by kind, in this order, once more than one kind is published.
KINDS = {"city": "Cities", "park": "National parks", "state-parks": "State parks", "route": "Trips",
         "food": "Food", "theme": "Themed lists", "season": "Seasons", "weekend": "Weekends and day trips"}
MOVED_METRES = 300
SEARCH_RADIUS_METRES = 2000
STREET_RADIUS_METRES = 5000
NOTES_MAX = 300


# MARK: Sources

# Field Guide format 0.1: each source is guides/src/<slug>.guide.geojson, a GeoJSON
# FeatureCollection with a `guide` object, one Point feature per place and optional route
# lines. The spec lives in the private repo natebird/placemoir-guide-spec; this file checks the
# same rules with the standard library and turns each source into the shape the rest of the
# script builds from.
FORMAT_VERSION = "0.1"
STATUSES = ("draft", "published", "retired")
# Guide categories, and the Placemoir category each one imports as (category-map.json in the spec).
SPEC_CATEGORIES = {
    "restaurant": "restaurant", "food-truck": "restaurant", "barbecue": "restaurant",
    "cafe": "cafe", "bakery": "cafe", "dessert": "cafe",
    "bar": "bar", "brewery": "bar", "winery": "bar", "distillery": "bar",
    "hotel": "hotel", "inn": "hotel", "lodge": "hotel", "hostel": "hotel", "albergue": "hotel", "campground": "hotel",
    "park": "park", "national-park": "park", "state-park": "park", "trailhead": "park", "viewpoint": "park",
    "beach": "park", "garden": "park", "waterfall": "park",
    "museum": "museum", "gallery": "museum",
    "attraction": "attraction", "landmark": "attraction", "historic-site": "attraction",
    "visitor-center": "attraction", "stadium": "attraction", "theater": "attraction", "market": "attraction",
    "shop": "attraction", "zoo": "attraction",
    "airport": "airport",
    "station": "other", "other": "other",
}
ROUTE_MODES = ("drive", "walk", "hike", "bike", "transit", "ferry")
# Country names for the library file's `country`, by the code in `guide.countries`.
COUNTRY_NAMES = {"US": "United States", "CA": "Canada", "MX": "Mexico", "ES": "Spain", "FR": "France",
                 "PT": "Portugal", "DE": "Germany", "AT": "Austria", "IT": "Italy", "GB": "United Kingdom",
                 "IE": "Ireland", "JP": "Japan"}
GUIDE_FIELDS = {"id", "status", "kind", "name", "description", "author", "inLanguage", "dateModified",
                "datePublished", "region", "countries", "ordered", "season", "sections", "related", "sources",
                "license"}
SECTION_FIELDS = {"id", "name", "description", "distanceKm"}
SOURCE_FIELDS = {"id", "name", "url", "license", "credit"}
PLACE_FIELDS = {"featureType", "name", "category", "section", "position", "notes", "address", "locality",
                "countryCode", "ids", "match", "sources", "lastVerified"}
ROUTE_FIELDS = {"featureType", "name", "mode", "section", "stops", "distanceKm", "notes", "sources",
                "lastVerified"}
SLUG = re.compile(r"(?=.{1,80}\Z)[a-z0-9]+(-[a-z0-9]+)*")
DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def load_sources():
    guides = []
    for path in sorted(SRC.glob("*.guide.geojson")):
        stem = path.name[: -len(".guide.geojson")]
        doc = json.loads(path.read_text(encoding="utf-8"))
        problems = validate(doc, stem)
        if problems:
            sys.exit(f"{path.relative_to(SITE)} has {len(problems)} problem(s):\n"
                     + "\n".join(f"• {p}" for p in problems))
        guide = from_source(doc)
        lock = SRC / f"{guide['slug']}.maps.json"
        guide["_maps"] = json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}
        guides.append(guide)
    return guides


def is_day(value):
    if not isinstance(value, str) or not DAY.fullmatch(value):
        return False
    try:
        datetime.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def unknown(obj, allowed):
    return sorted(k for k in obj if k not in allowed and not k.startswith("x-"))


def text(value, limit=None):
    return isinstance(value, str) and value.strip() != "" and (limit is None or len(value) <= limit)


def is_position(value):
    return (isinstance(value, list) and len(value) in (2, 3) and all(isinstance(n, (int, float)) for n in value)
            and -180 <= value[0] <= 180 and -90 <= value[1] <= 90)


def validate(doc, stem):
    """The Field Guide 0.1 rules: the spec's JSON Schema plus the checks a schema can't express."""
    problems = []
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        return ["type: must be \"FeatureCollection\""]
    if doc.get("placemoirGuide") != FORMAT_VERSION:
        problems.append(f"placemoirGuide: must be \"{FORMAT_VERSION}\"")
    for key in unknown(doc, {"type", "placemoirGuide", "guide", "features", "bbox"}):
        problems.append(f"{key}: unknown field (extensions start with x-)")
    guide, features = doc.get("guide"), doc.get("features")
    if not isinstance(guide, dict):
        return problems + ["guide: required"]
    if not isinstance(features, list) or not features:
        return problems + ["features: at least one place"]
    g = "guide"
    for key in unknown(guide, GUIDE_FIELDS):
        problems.append(f"{g}.{key}: unknown field")
    missing = [f"{g}.{field}: required" for field in ("id", "status", "kind", "name", "description", "author",
                                                       "inLanguage", "dateModified", "countries", "sections")
               if field not in guide]
    if missing:
        return problems + missing
    if not isinstance(guide["id"], str) or not SLUG.fullmatch(guide["id"]):
        problems.append(f"{g}.id: lowercase letters, digits and - only")
    elif guide["id"] != stem:
        problems.append(f"{g}.id: “{guide['id']}” must match the file name “{stem}.guide.geojson”")
    if guide["status"] not in STATUSES:
        problems.append(f"{g}.status: one of {', '.join(STATUSES)}")
    if guide["kind"] not in KINDS:
        problems.append(f"{g}.kind: one of {', '.join(KINDS)}")
    if not text(guide["name"], 120):
        problems.append(f"{g}.name: 1 to 120 characters")
    if not text(guide["description"], 400):
        problems.append(f"{g}.description: 1 to 400 characters")
    author = guide["author"]
    if not isinstance(author, dict) or not text(author.get("name")) or unknown(author, {"name", "url"}):
        problems.append(f"{g}.author: {{\"name\": …}} with an optional url")
    if not isinstance(guide["inLanguage"], str) or not re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})*", guide["inLanguage"]):
        problems.append(f"{g}.inLanguage: a BCP 47 tag, like en or pt-BR")
    for field in ("dateModified", "datePublished"):
        if field in guide and not is_day(guide[field]):
            problems.append(f"{g}.{field}: a day, like 2026-11-14")
    countries = guide["countries"]
    if (not isinstance(countries, list) or not countries
            or not all(isinstance(c, str) and re.fullmatch(r"[A-Z]{2}", c) for c in countries)
            or len(set(countries)) != len(countries)):
        problems.append(f"{g}.countries: a list of two-letter country codes, like [\"US\"]")
    elif countries[0] not in COUNTRY_NAMES:
        problems.append(f"{g}.countries: add {countries[0]} to COUNTRY_NAMES in tools/guides.py")
    if "region" in guide and not text(guide["region"]):
        problems.append(f"{g}.region: leave it out, or name the states or provinces")
    if "ordered" in guide and not isinstance(guide["ordered"], bool):
        problems.append(f"{g}.ordered: true or false")
    if "season" in guide:
        season = guide["season"]
        months = season.get("months", []) if isinstance(season, dict) else None
        if (not isinstance(season, dict) or not text(season.get("text"), 120) or unknown(season, {"text", "months"})
                or not isinstance(months, list)
                or not all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in months)):
            problems.append(f"{g}.season: {{\"text\": …}} with optional months 1 to 12")
    if "license" in guide and not text(guide["license"]):
        problems.append(f"{g}.license: text")
    if "related" in guide and not (isinstance(guide["related"], list)
                                   and all(isinstance(s, str) and SLUG.fullmatch(s) for s in guide["related"])):
        problems.append(f"{g}.related: a list of guide ids")
    source_ids = set()
    for i, source in enumerate(guide.get("sources", [])):
        where = f"{g}.sources[{i}]"
        if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not SLUG.fullmatch(source["id"]):
            problems.append(f"{where}.id: lowercase letters, digits and - only")
            continue
        if not text(source.get("name")):
            problems.append(f"{where}.name: required")
        for key in unknown(source, SOURCE_FIELDS) + [k for k in source if k.startswith("x-")]:
            problems.append(f"{where}.{key}: unknown field")
        if source["id"] in source_ids:
            problems.append(f"{where}.id: “{source['id']}” is used twice")
        source_ids.add(source["id"])
    section_ids = []
    if not isinstance(guide["sections"], list) or not guide["sections"]:
        problems.append(f"{g}.sections: at least one section")
    for i, section in enumerate(guide["sections"] if isinstance(guide["sections"], list) else []):
        where = f"{g}.sections[{i}]"
        if not isinstance(section, dict) or not isinstance(section.get("id"), str) or not SLUG.fullmatch(section["id"]):
            problems.append(f"{where}.id: lowercase letters, digits and - only")
            continue
        if not text(section.get("name"), 80):
            problems.append(f"{where}.name: 1 to 80 characters")
        if "description" in section and not (isinstance(section["description"], str) and len(section["description"]) <= 300):
            problems.append(f"{where}.description: up to 300 characters")
        if "distanceKm" in section and not (isinstance(section["distanceKm"], (int, float)) and section["distanceKm"] >= 0):
            problems.append(f"{where}.distanceKm: a number, 0 or more")
        for key in unknown(section, SECTION_FIELDS):
            problems.append(f"{where}.{key}: unknown field")
        if section["id"] in section_ids:
            problems.append(f"{where}.id: “{section['id']}” is used twice")
        section_ids.append(section["id"])
    ids, places, routes = set(), [], []
    for i, feature in enumerate(features):
        fid = feature.get("id") if isinstance(feature, dict) else None
        where = f"features[{i}] ({fid or '?'})"
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            problems.append(f"{where}.type: must be \"Feature\"")
            continue
        for key in unknown(feature, {"type", "id", "geometry", "properties"}) + [k for k in feature if k.startswith("x-")]:
            problems.append(f"{where}.{key}: unknown field")
        if not isinstance(fid, str) or not SLUG.fullmatch(fid):
            problems.append(f"{where}.id: lowercase letters, digits and - only")
        elif fid in ids:
            problems.append(f"{where}.id: “{fid}” is used twice")
        ids.add(fid)
        props, geometry = feature.get("properties"), feature.get("geometry", "missing")
        if not isinstance(props, dict):
            problems.append(f"{where}.properties: required")
            continue
        is_route = props.get("featureType") == "route"
        (routes if is_route else places).append((where, feature))
        if "section" in props and props["section"] not in section_ids:
            problems.append(f"{where}.section: one of {', '.join(section_ids)}")
        sources = props.get("sources", [])
        if not isinstance(sources, list) or len(set(map(str, sources))) != len(sources):
            problems.append(f"{where}.sources: a list of source ids, each once")
        else:
            for s in sources:
                if s not in source_ids:
                    problems.append(f"{where}.sources: “{s}” isn't in guide.sources")
        if "lastVerified" in props and not is_day(props["lastVerified"]):
            problems.append(f"{where}.lastVerified: a day, like 2026-11-14")
        if is_route:
            for key in unknown(props, ROUTE_FIELDS):
                problems.append(f"{where}.properties.{key}: unknown field")
            if not text(props.get("name"), 120):
                problems.append(f"{where}.name: 1 to 120 characters")
            if props.get("mode") not in ROUTE_MODES:
                problems.append(f"{where}.mode: one of {', '.join(ROUTE_MODES)}")
            if "notes" in props and not (isinstance(props["notes"], str) and len(props["notes"]) <= NOTES_MAX):
                problems.append(f"{where}.notes: keep it under {NOTES_MAX} characters")
            if "distanceKm" in props and not (isinstance(props["distanceKm"], (int, float)) and props["distanceKm"] >= 0):
                problems.append(f"{where}.distanceKm: a number, 0 or more")
            if geometry is None:
                if not isinstance(props.get("stops"), list) or len(props["stops"]) < 2:
                    problems.append(f"{where}.stops: at least two place ids when geometry is null")
            elif not (isinstance(geometry, dict) and (
                    (geometry.get("type") == "LineString" and isinstance(geometry.get("coordinates"), list)
                     and len(geometry["coordinates"]) >= 2 and all(map(is_position, geometry["coordinates"])))
                    or (geometry.get("type") == "MultiLineString" and isinstance(geometry.get("coordinates"), list)
                        and geometry["coordinates"] and all(isinstance(line, list) and len(line) >= 2
                                                            and all(map(is_position, line))
                                                            for line in geometry["coordinates"])))):
                problems.append(f"{where}.geometry: a LineString, a MultiLineString, or null with stops")
            continue
        for key in unknown(props, PLACE_FIELDS):
            problems.append(f"{where}.properties.{key}: unknown field")
        if props.get("featureType", "place") != "place":
            problems.append(f"{where}.featureType: \"place\" or \"route\"")
        if not (isinstance(geometry, dict) and geometry.get("type") == "Point" and is_position(geometry.get("coordinates"))
                and not unknown(geometry, {"type", "coordinates"})):
            problems.append(f"{where}.geometry: a Point at [longitude, latitude]")
        for field, limit in (("name", 120), ("notes", NOTES_MAX), ("address", None)):
            if not text(props.get(field), limit):
                problems.append(f"{where}.{field}: required" + (f", up to {limit} characters" if limit else ""))
        if props.get("category") not in SPEC_CATEGORIES:
            problems.append(f"{where}.category: one of {', '.join(SPEC_CATEGORIES)}")
        if "section" not in props:
            problems.append(f"{where}.section: required")
        if "locality" in props and not isinstance(props["locality"], str):
            problems.append(f"{where}.locality: text")
        if "countryCode" in props and not (isinstance(props["countryCode"], str) and re.fullmatch(r"[A-Z]{2}", props["countryCode"])):
            problems.append(f"{where}.countryCode: a two-letter country code")
        elif "countryCode" in props and props["countryCode"] not in COUNTRY_NAMES:
            problems.append(f"{where}.countryCode: add {props['countryCode']} to COUNTRY_NAMES in tools/guides.py")
        place_ids_ = props.get("ids", {})
        if not isinstance(place_ids_, dict) or unknown(place_ids_, {"apple", "wikidata", "overture", "osm"}) \
                or any(k.startswith("x-") for k in place_ids_) or not all(text(v) for v in place_ids_.values()):
            problems.append(f"{where}.ids: apple, wikidata, overture and osm, each text")
        else:
            if "wikidata" in place_ids_ and not re.fullmatch(r"Q[0-9]+", place_ids_["wikidata"]):
                problems.append(f"{where}.ids.wikidata: like Q42")
            if "osm" in place_ids_ and not re.fullmatch(r"(node|way|relation)/[0-9]+", place_ids_["osm"]):
                problems.append(f"{where}.ids.osm: like node/123")
        match = props.get("match", {})
        if not isinstance(match, dict) or unknown(match, {"query", "skip"}) or any(k.startswith("x-") for k in match) \
                or not all(text(v) for v in match.values()):
            problems.append(f"{where}.match: query and skip, each text")
        if "position" in props and not (isinstance(props["position"], int) and not isinstance(props["position"], bool)
                                        and props["position"] >= 1):
            problems.append(f"{where}.position: a whole number from 1")
    place_ids = {f["id"] for _, f in places}
    if guide.get("ordered") is True:
        positions = []
        for where, feature in places:
            if "position" not in feature["properties"]:
                problems.append(f"{where}.position: required, because the guide is ordered")
            else:
                positions.append(feature["properties"]["position"])
        if positions and sorted(positions) != list(range(1, len(positions) + 1)):
            problems.append("positions: must run 1 to N with no gaps or repeats")
    for where, feature in routes:
        for stop in feature["properties"].get("stops", []) if isinstance(feature["properties"].get("stops"), list) else []:
            if stop not in place_ids:
                problems.append(f"{where}.stops: “{stop}” isn't a place in this guide")
    if not places:
        problems.append("features: at least one place")
    for section in section_ids:
        if not any(f["properties"].get("section") == section for _, f in places):
            problems.append(f"{g}.sections: “{section}” has no places")
    if problems:
        return problems
    # A city, park or food guide fits in about 300 km, so a place far from the rest is a typo. Wider
    # guides (routes, seasons, themes, weekends) only flag a place whose swapped coordinates would fit.
    if len(places) > 2:
        lons = sorted(f["geometry"]["coordinates"][0] for _, f in places)
        lats = sorted(f["geometry"]["coordinates"][1] for _, f in places)
        mid_lon, mid_lat = lons[len(lons) // 2], lats[len(lats) // 2]
        compact = guide["kind"] in ("city", "park", "food")
        for where, f in places:
            lon, lat = f["geometry"]["coordinates"][:2]
            far = abs(lon - mid_lon) > 3 or abs(lat - mid_lat) > 3
            if far and compact:
                problems.append(f"{where}: more than ~300 km from the guide's other places (latitude and longitude swapped?)")
            elif far and abs(lat - mid_lon) <= 3 and abs(lon - mid_lat) <= 3:
                problems.append(f"{where}: latitude and longitude look swapped")
    return problems


def from_source(doc):
    """A 0.1 source as the dict the pages, library files and Apple Maps check are built from."""
    g = doc["guide"]
    names = {s["id"]: s["name"] for s in g["sections"]}
    guide = {
        "slug": g["id"], "status": g["status"], "kind": g["kind"], "title": g["name"],
        "summary": g["description"], "author": g["author"]["name"], "language": g["inLanguage"],
        "country": COUNTRY_NAMES[g["countries"][0]], "countryCode": g["countries"][0],
        "updatedAt": g["dateModified"], "sections": [s["name"] for s in g["sections"]],
        "ordered": g.get("ordered", False), "places": [], "routes": [],
    }
    for key in ("region", "season"):
        if key in g:
            guide[key] = g[key]["text"] if key == "season" else g[key]
    for feature in doc["features"]:
        p = feature["properties"]
        if p.get("featureType") == "route":
            guide["routes"].append({"key": feature["id"], **p, "geometry": feature["geometry"]})
            continue
        lon, lat = feature["geometry"]["coordinates"][:2]
        place = {"key": feature["id"], "name": p["name"], "category": SPEC_CATEGORIES[p["category"]],
                 "guideCategory": p["category"], "section": names[p["section"]], "address": p["address"],
                 "locality": p.get("locality", ""), "latitude": lat, "longitude": lon, "notes": p["notes"],
                 "countryCode": p.get("countryCode", g["countries"][0])}
        if "position" in p:
            place["position"] = p["position"]
        if p.get("match", {}).get("query"):
            place["mapsQuery"] = p["match"]["query"]
        if p.get("match", {}).get("skip"):
            place["mapsSkip"] = p["match"]["skip"]
        if p.get("ids", {}).get("apple"):
            place["appleMapsID"] = p["ids"]["apple"]
        guide["places"].append(place)
    if guide["ordered"]:
        guide["places"].sort(key=lambda place: place["position"])
    return guide


# MARK: Library file

def page_url(guide):
    return f"{BASE_URL}/guides/{guide['slug']}/"


def library(guide):
    places = []
    for place in guide["places"]:
        found = guide["_maps"].get(place["key"], {})
        record = {
            "id": f"guide:{guide['slug']}/{place['key']}",
            "name": place["name"],
            "category": place["category"],
            "address": place["address"],
            "locality": place.get("locality", ""),
            "country": COUNTRY_NAMES.get(place["countryCode"], guide.get("country", "")),
            "latitude": found.get("latitude", place["latitude"]),
            "longitude": found.get("longitude", place["longitude"]),
            "wantToGo": True,
            "notes": place["notes"],
            "section": place["section"],
        }
        if found.get("appleMapsID") or place.get("appleMapsID"):
            record["appleMapsID"] = found.get("appleMapsID") or place["appleMapsID"]
        places.append({k: v for k, v in record.items() if v != ""})
    return {
        "format": "placemoir-library",
        "formatVersion": 1,
        "exportedAt": guide["updatedAt"],
        "generator": "Placemoir Field Guides",
        "guide": {
            "slug": guide["slug"],
            "title": guide["title"],
            "author": guide["author"],
            "language": guide["language"],
            "summary": guide["summary"],
            "sections": guide["sections"],
            "updatedAt": guide["updatedAt"],
            "url": page_url(guide),
            **optional_fields(guide),
        },
        "places": places,
    }


def optional_fields(guide):
    """The guide's kind and region, only when the source gives them, so city guides stay as they were."""
    return {key: guide[key] for key in ("kind", "region")
            if guide.get(key) and not (key == "kind" and guide[key] == "city")}


def library_json(guide):
    return json.dumps(library(guide), indent=2, ensure_ascii=False) + "\n"


def archive_bytes(guide):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # A fixed timestamp, so the same guide always makes the same file.
        info = zipfile.ZipInfo("library.json", date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, library_json(guide))
    return buffer.getvalue()


# MARK: Pages

def template():
    """privacy.html's head, header and footer, relinked for a page two folders down."""
    text = (SITE / "privacy.html").read_text(encoding="utf-8")
    head, _, rest = text.partition('<main id="main">')
    _, _, foot = rest.partition("</main>")
    return head, foot


def relink(text, prefix):
    def fix(match):
        attr, url = match.group(1), match.group(2)
        if url.startswith(("#", "/", "http:", "https:", "mailto:", "data:", "tel:")):
            return match.group(0)
        if url in ("./", ""):
            url = ""
        return f'{attr}="{prefix}{url}"' if url else f'{attr}="{prefix or "./"}"'
    return re.sub(r'\b(href|src)="([^"]*)"', fix, text)


def page(title, description, canonical, body, prefix, noindex, app_argument=None, image=None, with_map=False,
         with_turnstile=False, with_list=False):
    head, foot = template()
    if with_list:
        head = head.replace("</head>", '    <script src="js/guides-list.js?v=20261114.1" defer></script>\n</head>', 1)
    if with_turnstile:
        head = with_csp(head, TURNSTILE_CSP)
        head = head.replace("</head>", '    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>\n'
                                       '    <script src="js/suggest.js?v=20261114.1" defer></script>\n</head>', 1)
    if with_map:
        head = with_csp(head, MAPKIT_CSP)
        head = head.replace("</head>", '    <script src="js/guide-map.js?v=20261108.3" defer></script>\n</head>', 1)
    head = head.replace("<title>Privacy Policy · Placemoir</title>", f"<title>{html.escape(title)} · Placemoir</title>")
    head = head.replace('href="https://placemoir.app/privacy.html"', f'href="{canonical}"')
    head = head.replace('content="Privacy Policy · Placemoir"', f'content="{html.escape(title)} · Placemoir"')
    head = re.sub(r'(<meta (?:name|property)="(?:og:)?description" content=")[^"]*"',
                  lambda m: m.group(1) + html.escape(description) + '"', head)
    if app_argument:
        head = head.replace('content="app-id=6829224969"',
                            f'content="app-id=6829224969, app-argument={html.escape(app_argument)}"')
    if noindex:
        head = head.replace("<title>", '<meta name="robots" content="noindex">\n    <title>', 1)
    head = head.replace('<a href="guides/">Guides</a>', '<a href="guides/" aria-current="page">Guides</a>', 1)
    head, foot = relink(head, prefix), relink(foot, prefix)
    return f"{head}<main id=\"main\">\n        <!-- Generated by tools/guides.py from guides/src/. Don't edit here. -->\n{body}\n    </main>{foot}"


# Apple's MapKit JS, on guide pages only: its script, tiles and data come from Apple. The rest of
# the site keeps its stricter policy. MapKit injects its own style sheet, which needs
# 'unsafe-inline' for styles (scripts stay strict).
MAPKIT_CSP = {
    "script-src": ["https://cdn.apple-mapkit.com"],
    "style-src": ["'unsafe-inline'"],
    "img-src": ["https://*.apple-mapkit.com", "https://*.ls.apple.com", "blob:"],
    "connect-src": ["https://*.apple-mapkit.com", "https://*.ls.apple.com", "https://*.apple.com"],
    "worker-src": ["blob:"],
}


def with_csp(head, extra):
    match = re.search(r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(")', head)
    directives = [d.strip() for d in match.group(2).split(";") if d.strip()]
    names = [d.split()[0] for d in directives]
    for name, sources in extra.items():
        if name in names:
            i = names.index(name)
            directives[i] += " " + " ".join(sources)
        else:
            directives.append(f"{name} " + " ".join(sources))
    return head[:match.start(2)] + "; ".join(directives) + ";" + head[match.end(2):]


def map_section(guide):
    keys = "".join(f'<li><span class="guide-key guide-key-{i % 4}" aria-hidden="true"></span>{html.escape(s)}</li>'
                   for i, s in enumerate(guide["sections"]))
    # Hidden until js/guide-map.js has drawn the map, so a page without JavaScript, without a
    # MapKit token (a local preview) or with Apple Maps unreachable just shows the list.
    return f'''            <figure class="guide-map card" id="guide-map" hidden>
                <div class="guide-map-canvas" role="region" aria-label="Map of the places in this guide"></div>
                <figcaption>
                    <div class="guide-map-picked" aria-live="polite" hidden>
                        <p class="meta guide-map-section"></p>
                        <p class="guide-map-name"></p>
                        <p class="guide-map-notes"></p>
                        <a href="#">See it in the list</a>
                    </div>
                    <ul class="guide-legend">{keys}</ul>
                </figcaption>
            </figure>'''


def maps_link(guide, place):
    found = guide["_maps"].get(place["key"], {})
    lat = found.get("latitude", place["latitude"])
    lon = found.get("longitude", place["longitude"])
    query = urllib.parse.urlencode({"q": place["name"], "ll": f"{lat},{lon}"})
    return f"https://maps.apple.com/?{query}"


def guide_page(guide):
    slug, e = guide["slug"], html.escape
    count = len(guide["places"])
    archive = f"{slug}.placemoirarchive"
    archive_name = f"{guide['title']}.placemoirarchive"
    updated = datetime.date.fromisoformat(guide["updatedAt"]).strftime("%B %-d, %Y")
    region = f"{e(guide['region'])} · " if guide.get("region") else ""
    season = f'\n                <p class="meta">{e(guide["season"])}</p>' if guide.get("season") else ""
    outdoors = ""
    if guide.get("kind", "city") != "city":
        outdoors = ('\n            <p class="press-lede guide-fineprint">Placemoir isn’t affiliated with the National Park Service, '
                    'Parks Canada or any park agency. Roads, trails and campgrounds close for weather and repairs, and some '
                    'need a permit or reservation, so check the park’s own site before you go.</p>')
    if ONE_TAP:
        actions = f'''<a class="btn btn-moss" href="{SHORT_LINK.format(slug=slug)}">Add to Placemoir</a>
                    <p class="cta-note">Opens Placemoir, where you pick the places you want. If it doesn’t open, <a href="{archive}" download="{e(archive_name)}">download the guide</a> and open it from Files.</p>'''
    else:
        actions = f'''<a class="btn btn-moss" href="{archive}" download="{e(archive_name)}">Add to Placemoir</a>
                    <p class="cta-note">Downloads the guide. Open it from Files, or in Placemoir under You › Import an archive.</p>'''
    sections = [map_section(guide)]
    for section in guide["sections"]:
        cards = []
        for place in (p for p in guide["places"] if p["section"] == section):
            kind = CATEGORIES[place["category"]]
            where = f" · {e(place['locality'])}" if place.get("locality") else ""
            cards.append(f'''                    <article class="prompt-card card guide-place" id="{place['key']}">
                        <p class="meta">{kind}{where}</p>
                        <h3>{f"{place['position']}. " if guide.get("ordered") else ""}{e(place['name'])}</h3>
                        <p>{e(place['notes'])}</p>
                        <p class="meta guide-address"><a href="{e(maps_link(guide, place))}">{e(place['address'])}</a></p>
                    </article>''')
        sections.append(f'''            <h2 class="press-h2" id="{e(section.lower().replace(' ', '-'))}"><span class="guide-key guide-key-{guide['sections'].index(section) % 4}" aria-hidden="true"></span>{e(section)}</h2>
            <div class="prompt-grid">
{chr(10).join(cards)}
            </div>''')
    ld = {
        "@context": "https://schema.org", "@type": "ItemList", "name": guide["title"],
        "description": guide["summary"], "url": page_url(guide), "numberOfItems": count,
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "item": {"@type": "Place", "name": p["name"], "address": p["address"]}}
            for i, p in enumerate(guide["places"])],
    }
    ld_json = json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
    body = f'''        <script type="application/ld+json">{ld_json}</script>
        <div class="wrap">
            <header class="page-head">
                <p class="eyebrow moss"><a href="../">Field Guides</a></p>
                <h1>{e(guide['title'])}</h1>
                <p>{e(guide['summary'])}</p>
                <p class="meta">{region}{count} places · Picked by {e(guide['author'])} · Checked {updated}</p>{season}
                <div class="cta-row guide-actions">
                    {actions}
                </div>
            </header>
        </div>
        <div class="wrap page-body">
{chr(10).join(sections)}
            <p class="press-lede guide-fineprint">Every place here was picked for the guide, not paid for. Places that are already in your journal are left as they are when you import.</p>{outdoors}
        </div>'''
    return page(guide["title"], guide["summary"], page_url(guide), body, "../../",
                noindex=guide["status"] != "published", app_argument=page_url(guide), with_map=True)


def search_text(guide):
    """What the guides list's search looks in: title, summary, region and place names, lowercased
    and without accents, the same way js/guides-list.js folds what's typed."""
    words = [guide["title"], guide["summary"], guide.get("region", "")] + [p["name"] for p in guide["places"]]
    text = unicodedata.normalize("NFD", " ".join(w for w in words if w).lower())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


def index_page(guides):
    e = html.escape
    published = [g for g in guides if g["status"] == "published"]
    kinds = [k for k in KINDS if any(g.get("kind", "city") == k for g in published)]
    controls = ""
    if published:
        def grid(group):
            cards = "\n".join(f'''                <article class="prompt-card card" data-kind="{e(g.get('kind', 'city'))}" data-search="{e(search_text(g))}">
                    <p class="meta">{len(g['places'])} places</p>
                    <h3><a href="{g['slug']}/">{e(g['title'])}</a></h3>
                    <p>{e(g['summary'])}</p>
                </article>''' for g in group)
            return f'            <div class="prompt-grid">\n{cards}\n            </div>'
        if len(kinds) > 1:
            listing = "\n".join(f'''            <section class="guide-group" data-kind="{k}" aria-labelledby="{k}">
            <h2 class="press-h2" id="{k}">{KINDS[k]}</h2>
{grid([g for g in published if g.get("kind", "city") == k])}
            </section>''' for k in kinds)
        else:
            listing = grid(published)
        # Rendered hidden: js/guides-list.js shows them, so without JavaScript the list is as it was.
        filters = ""
        if len(kinds) > 1:
            buttons = "\n".join(f'                    <button class="filter-chip" type="button" data-kind="{k}" aria-pressed="false">{KINDS[k]}</button>'
                                for k in kinds)
            filters = f'''
                <div class="guide-filters" role="group" aria-label="Show guides by type">
                    <button class="filter-chip" type="button" data-kind="" aria-pressed="true">All</button>
{buttons}
                </div>'''
        no_match = ('No guides match. Try another word, or <a href="suggest/">suggest a place</a>.' if TURNSTILE_SITE_KEY
                    else "No guides match. Try another word.")
        controls = f'''
            <div class="guide-controls" id="guideControls" hidden>
                <div class="field guide-search">
                    <label for="guideSearch">Search guides</label>
                    <input id="guideSearch" type="search" autocomplete="off" spellcheck="false">
                </div>{filters}
                <p class="guide-count" id="guideCount" aria-live="polite"></p>
                <template id="guideNoMatch">{no_match}</template>
            </div>'''
    else:
        listing = '            <p class="press-lede">The first guides are on their way.</p>'
    intro = ("A week of places to stay, eat, see and walk in a city" if kinds in ([], ["city"])
             else "Places to stay, eat, see and walk, in a city or out on the trail")
    body = f'''        <div class="wrap">
            <header class="page-head">
                <p class="eyebrow moss">Field Guides</p>
                <h1>Places worth the trip.</h1>
                <p>{intro}, picked by people who've been. Add a Field Guide to your Want to go list in Placemoir, then rate each place once you've gone.</p>
            </header>
        </div>
        <div class="wrap page-body">{controls}
            <div id="guideList">
{listing}
            </div>{suggest_card()}
        </div>'''
    return page("Field Guides", "Placemoir Field Guides: city guides you can add to your Want to go list.",
                f"{BASE_URL}/guides/", body, "../", noindex=not published, with_list=bool(published))


def suggest_card(prefix=""):
    if not TURNSTILE_SITE_KEY:
        return ""
    return f'''
            <div class="contact-card card">
                <p class="eyebrow plum">Suggest a place</p>
                <h2>Know a place that belongs here?</h2>
                <p>Tell us about a place you'd send a friend to, for one of these guides or a new one. We read every suggestion.</p>
                <a class="btn btn-moss" href="{prefix}suggest/">Suggest a place</a>
            </div>'''


def suggest_page(guides):
    """guides/suggest/index.html: the form that sends a place to workers/place-suggestions/."""
    e = html.escape
    head = f'''        <div class="wrap">
            <header class="page-head">
                <p class="eyebrow moss">Field Guides</p>
                <h1>Suggest a place.</h1>
                <p>Know a place that belongs in a Field Guide? A restaurant you'd send a friend to, a park worth the drive, a hotel you'd stay at again. Tell us about it, and if we add it, you can be thanked by name.</p>
            </header>
        </div>'''
    if not TURNSTILE_SITE_KEY:
        body = f'''{head}
        <div class="wrap page-body">
            <div class="contact-card card">
                <h2>Suggestions open soon.</h2>
                <p>The form isn't open yet. Until it is, you can email a place to <a href="mailto:feedback@placemoir.app?subject=Field%20Guide%20suggestion">feedback@placemoir.app</a>.</p>
                <a class="btn btn-moss" href="../">See the Field Guides</a>
            </div>
        </div>'''
        return page("Suggest a place", "Suggest a place for a Placemoir Field Guide.", SUGGEST_URL, body, "../../",
                    noindex=True)

    # The guide's title is what the Worker files the suggestion under, so the issue names the guide.
    published = [g for g in guides if g["status"] == "published"]
    groups = []
    for kind, label in KINDS.items():
        titles = sorted(g["title"] for g in published if g.get("kind", "city") == kind)
        if titles:
            options = "\n".join(f'                                <option>{e(t)}</option>' for t in titles)
            groups.append(f'                            <optgroup label="{e(label)}">\n{options}\n                            </optgroup>')
    city_options = "\n".join(groups)
    kinds = [("restaurant", "Restaurant"), ("cafe", "Café or bakery"), ("bar", "Bar"), ("hotel", "Place to stay"),
             ("museum", "Museum"), ("attraction", "Something to see or do"), ("park", "Park or outdoors"),
             ("trail", "Trail or hike"), ("other", "Something else")]
    kind_options = "\n".join(f'                            <option value="{k}">{e(label)}</option>' for k, label in kinds)
    went = [("month", "In the last month"), ("6-months", "In the last 6 months"), ("year", "In the last year"),
            ("2-years", "1 to 2 years ago"), ("older", "More than 2 years ago")]
    went_options = "\n".join(f'                            <option value="{k}">{e(label)}</option>' for k, label in went)
    body = f'''{head}
        <div class="wrap page-body">
            <form class="suggest-form card" id="suggestForm" action="{SUGGEST_ENDPOINT}" method="post" novalidate>
                <noscript><p class="form-note">This form needs JavaScript. You can also email a place to <a href="mailto:feedback@placemoir.app?subject=Field%20Guide%20suggestion">feedback@placemoir.app</a>.</p></noscript>
                <fieldset>
                    <legend>The place</legend>
                    <div class="field">
                        <label for="city">Which guide?</label>
                        <select id="city" name="city" required aria-describedby="city-error">
                            <option value="" selected disabled>Choose a guide</option>
{city_options}
                            <option value="other">A new guide idea</option>
                        </select>
                        <p class="field-error" id="city-error" hidden></p>
                    </div>
                    <div class="field" id="otherCityField" hidden>
                        <label for="otherCity">Which city, park or trip?</label>
                        <input id="otherCity" name="otherCity" type="text" maxlength="60" autocomplete="off" aria-describedby="otherCity-error">
                        <p class="field-error" id="otherCity-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="place">Name of the place</label>
                        <input id="place" name="place" type="text" required maxlength="120" aria-describedby="place-error">
                        <p class="field-error" id="place-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="kind">What kind of place?</label>
                        <select id="kind" name="kind" required aria-describedby="kind-error">
                            <option value="" selected disabled>Choose one</option>
{kind_options}
                        </select>
                        <p class="field-error" id="kind-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="address">Address <span class="optional">or a link below</span></label>
                        <input id="address" name="address" type="text" maxlength="200" autocomplete="off" aria-describedby="address-hint address-error">
                        <p class="field-hint" id="address-hint">So we find the right one. A street and neighborhood is plenty.</p>
                        <p class="field-error" id="address-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="link">Link <span class="optional">optional</span></label>
                        <input id="link" name="link" type="url" inputmode="url" maxlength="500" placeholder="https://" autocomplete="off" aria-describedby="link-hint link-error">
                        <p class="field-hint" id="link-hint">Its Apple Maps or Google Maps link, or its own website.</p>
                        <p class="field-error" id="link-error" hidden></p>
                    </div>
                </fieldset>
                <fieldset>
                    <legend>Why it belongs</legend>
                    <div class="field">
                        <label for="tip">What should people get, see or do there?</label>
                        <textarea id="tip" name="tip" rows="3" required maxlength="500" aria-describedby="tip-error"></textarea>
                        <p class="field-error" id="tip-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="why">Why does it belong in a Field Guide?</label>
                        <textarea id="why" name="why" rows="5" required minlength="10" maxlength="1500" aria-describedby="why-error"></textarea>
                        <p class="field-error" id="why-error" hidden></p>
                    </div>
                    <div class="field">
                        <label for="lastWent">When did you last go?</label>
                        <select id="lastWent" name="lastWent" required aria-describedby="lastWent-error">
                            <option value="" selected disabled>Choose one</option>
{went_options}
                        </select>
                        <p class="field-error" id="lastWent-error" hidden></p>
                    </div>
                </fieldset>
                <fieldset>
                    <legend>Thanks</legend>
                    <p class="field-hint">If we add your place, we can thank you in our posts about the guide and on the guide's page. Never in the place's notes.</p>
                    <div class="radio-list" role="radiogroup" aria-label="How to thank you">
                        <label class="radio"><input type="radio" name="credit" value="none" checked> Don't name me</label>
                        <label class="radio"><input type="radio" name="credit" value="name"> Thank me by name</label>
                        <label class="radio"><input type="radio" name="credit" value="handle"> Thank me by social handle</label>
                    </div>
                    <div class="field" id="creditNameField" hidden>
                        <label for="creditName" id="creditNameLabel" data-label-name="Name" data-label-handle="Your handle, and where (e.g. @name on Instagram)">Name</label>
                        <input id="creditName" name="creditName" type="text" maxlength="40" autocomplete="off" aria-describedby="creditName-hint creditName-error">
                        <p class="field-hint" id="creditName-hint" data-hint-name="Your first name and last initial is plenty, like Dana R." data-hint-handle="We'll use it to thank you in our posts about the guide.">Your first name and last initial is plenty, like Dana R.</p>
                        <p class="field-error" id="creditName-error" hidden></p>
                    </div>
                    <div class="field" id="creditLocationField" hidden>
                        <label for="creditLocation">Location <span class="optional">optional</span></label>
                        <input id="creditLocation" name="creditLocation" type="text" maxlength="60" autocomplete="off" aria-describedby="creditLocation-hint creditLocation-error">
                        <p class="field-hint" id="creditLocation-hint">Where you live, like Decatur, GA. We'd thank you as “Dana R. from Decatur, GA”, which tells readers a real person picked it.</p>
                        <p class="field-error" id="creditLocation-error" hidden></p>
                    </div>
                </fieldset>
                <div class="hp" aria-hidden="true">
                    <label for="website">Leave this empty</label>
                    <input id="website" name="website" type="text" tabindex="-1" autocomplete="off">
                </div>
                <div class="cf-turnstile" data-sitekey="{e(TURNSTILE_SITE_KEY)}" data-action="suggest-place" data-theme="auto" data-size="flexible" data-language="en"></div>
                <p class="form-note">Please don't include anyone's email or phone number. By sending, you agree that Tweeting Birds may use, edit and publish your suggestion in Field Guides and in posts about them. We don't ask for your email. See the <a href="../../privacy.html#suggestions">Privacy Policy</a>.</p>
                <p class="form-status" id="formStatus" role="status" aria-live="polite"></p>
                <button class="btn btn-moss" type="submit">Send suggestion</button>
            </form>
            <div class="contact-card card" id="suggestThanks" hidden tabindex="-1">
                <p class="eyebrow plum">Thank you</p>
                <h2>Got it. Thanks for the tip.</h2>
                <p>We read every suggestion. If it makes it into a Field Guide, you'll see it there.</p>
                <div class="thanks-actions">
                    <button class="btn btn-moss" type="button" id="suggestAnother">Suggest another place</button>
                    <a class="btn btn-soft" href="../">See the Field Guides</a>
                </div>
            </div>
        </div>'''
    return page("Suggest a place", "Suggest a place for a Placemoir Field Guide: a restaurant, park, hotel or sight you'd send a friend to.",
                SUGGEST_URL, body, "../../", noindex=False, with_turnstile=True)


def sitemap(guides, current):
    published = [g for g in guides if g["status"] == "published"]
    text = re.sub(r"\n  <!-- guides:sitemap -->.*?<!-- /guides:sitemap -->", "", current, flags=re.S)
    if not published:
        return text
    newest = max(g["updatedAt"] for g in published)
    urls = [f"  <url><loc>{BASE_URL}/guides/</loc><lastmod>{newest}</lastmod></url>"]
    urls += [f"  <url><loc>{page_url(g)}</loc><lastmod>{g['updatedAt']}</lastmod></url>" for g in published]
    if TURNSTILE_SITE_KEY:
        urls.append(f"  <url><loc>{SUGGEST_URL}</loc><lastmod>{newest}</lastmod></url>")
    block = "  <!-- guides:sitemap -->\n" + "\n".join(urls) + "\n  <!-- /guides:sitemap -->\n"
    return text.replace("</urlset>", block + "</urlset>", 1)


def catalog_json(guides):
    """guides/index.json: the published Field Guides, for the Guides list in the app.

    The app fetches this one static file when someone opens the list, and nothing else until
    they pick a guide, so the list needs no account and tells the site nothing about them.
    """
    published = sorted((g for g in guides if g["status"] == "published"), key=lambda g: g["slug"])
    entries = []
    for guide in published:
        points = [(guide["_maps"].get(p["key"], {}).get("latitude", p["latitude"]),
                   guide["_maps"].get(p["key"], {}).get("longitude", p["longitude"])) for p in guide["places"]]
        entries.append({
            "slug": guide["slug"],
            "title": guide["title"],
            "summary": guide["summary"],
            "author": guide["author"],
            "language": guide["language"],
            "country": guide.get("country", ""),
            **optional_fields(guide),
            "sections": guide["sections"],
            "placeCount": len(guide["places"]),
            "latitude": round(sum(p[0] for p in points) / len(points), 4),
            "longitude": round(sum(p[1] for p in points) / len(points), 4),
            "updatedAt": guide["updatedAt"],
            "url": page_url(guide),
            "guideURL": f"{page_url(guide)}guide.json",
            "archiveURL": f"{page_url(guide)}{guide['slug']}.placemoirarchive",
        })
    catalog = {"format": "placemoir-guides", "formatVersion": 1,
               "updatedAt": max((g["updatedAt"] for g in published), default=""), "guides": entries}
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


# MARK: Commands

def outputs(guides):
    """Every built text file, by path. The archives are compared by their library.json."""
    files = {OUT / "index.html": index_page(guides), OUT / "index.json": catalog_json(guides),
             OUT / "suggest" / "index.html": suggest_page(guides)}
    for guide in guides:
        folder = OUT / guide["slug"]
        files[folder / "index.html"] = guide_page(guide)
        files[folder / "guide.json"] = library_json(guide)
    sitemap_path = SITE / "sitemap.xml"
    files[sitemap_path] = sitemap(guides, sitemap_path.read_text(encoding="utf-8"))
    return files


def build():
    guides = load_sources()
    for path, text in outputs(guides).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for guide in guides:
        (OUT / guide["slug"] / f"{guide['slug']}.placemoirarchive").write_bytes(archive_bytes(guide))
    print(f"Built {len(guides)} guide(s): " + ", ".join(f"{g['slug']} ({g['status']})" for g in guides))


def check():
    guides = load_sources()
    stale = [str(p.relative_to(SITE)) for p, text in outputs(guides).items()
             if not p.exists() or p.read_text(encoding="utf-8") != text]
    for guide in guides:
        path = OUT / guide["slug"] / f"{guide['slug']}.placemoirarchive"
        try:
            with zipfile.ZipFile(path) as archive:
                if archive.read("library.json").decode("utf-8") != library_json(guide):
                    stale.append(str(path.relative_to(SITE)))
        except (OSError, KeyError, zipfile.BadZipFile):
            stale.append(str(path.relative_to(SITE)))
    built = {p.parent.name for p in OUT.glob("*/guide.json")}
    orphans = sorted(built - {g["slug"] for g in guides})
    if stale or orphans:
        lines = [f"• {p} is out of date" for p in stale] + [f"• guides/{s}/ has no source" for s in orphans]
        sys.exit("Run python3 tools/guides.py build and commit the result:\n" + "\n".join(lines))
    print(f"OK — {len(guides)} guide(s) valid and built.")


# MARK: Apple Maps

def maps_token():
    try:
        import jwt  # pyjwt
    except ImportError:
        sys.exit("maps needs: pip install pyjwt cryptography")
    missing = [n for n in ("APPLE_MAPS_KEY", "APPLE_MAPS_KEY_ID", "APPLE_TEAM_ID") if not os.environ.get(n)]
    if missing:
        sys.exit("maps needs " + ", ".join(missing) + " in the environment")
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    signed = jwt.encode({"iss": os.environ["APPLE_TEAM_ID"].strip(), "iat": now, "exp": now + 1800},
                        os.environ["APPLE_MAPS_KEY"].strip() + "\n", algorithm="ES256",
                        headers={"kid": os.environ["APPLE_MAPS_KEY_ID"].strip(), "typ": "JWT"})
    request = urllib.request.Request("https://maps-api.apple.com/v1/token",
                                     headers={"Authorization": f"Bearer {signed}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["accessToken"]


def distance(a_lat, a_lon, b_lat, b_lon):
    r = 6371000
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def simplify(name, city):
    name = name.lower().replace("&", "and")
    name = re.sub(r"[^\w\s]", " ", name)
    return " ".join(w for w in name.split() if w not in {"the", "a", "of", *city})


def similarity(a, b, city=()):
    """How alike two names are, 0 to 1, ignoring "the" and the guide's city."""
    a, b = simplify(a, city), simplify(b, city)
    if a and b and (a in b or b in a):
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def search(token, place, country_code):
    query = {"q": place.get("mapsQuery") or place["name"],
             "searchLocation": f"{place['latitude']},{place['longitude']}", "lang": "en-US"}
    if country_code:
        query["limitToCountries"] = country_code
    request = urllib.request.Request("https://maps-api.apple.com/v1/search?" + urllib.parse.urlencode(query),
                                     headers={"Authorization": f"Bearer {token}"})
    # Apple sometimes answers slowly or with 429 when asked many times in a row: wait and try
    # again a few times, so one slow answer doesn't fail (or hold up) the whole check.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response).get("results", [])
        except (urllib.error.URLError, TimeoutError) as error:
            status = getattr(error, "code", None)
            if attempt == 3 or (status is not None and status != 429 and status < 500):
                raise
            time.sleep(2 ** attempt)


def one_line(lines):
    """Apple's address lines, sometimes with line breaks inside them, as one line."""
    text = ", ".join(lines)
    return re.sub(r"\s*\n\s*", ", ", re.sub(r"[ \t]+", " ", text)).strip()


ORDINALS = {"first": "1st", "second": "2nd", "third": "3rd", "fourth": "4th", "fifth": "5th", "sixth": "6th",
            "seventh": "7th", "eighth": "8th", "ninth": "9th", "tenth": "10th", "eleventh": "11th",
            "twelfth": "12th", "thirteenth": "13th", "fourteenth": "14th", "fifteenth": "15th",
            "sixteenth": "16th", "seventeenth": "17th", "eighteenth": "18th", "nineteenth": "19th",
            "twentieth": "20th"}
DIRECTIONS = {"n", "s", "e", "w", "ne", "nw", "se", "sw", "north", "south", "east", "west"}


def street(address):
    """("659", "peachtree") from "659 Peachtree St NE, …", or None without a street number.

    Looks at each part of the address, so "The Ferry Building, 1 Ferry Building" works, and
    reads "1622.5 N 45th St" and "1000 Fifth Ave" as ("1622", "45th") and ("1000", "5th").
    """
    for part in address.lower().split(","):
        match = re.match(r"\s*(\d+)[\d./-]*[a-z]?\s+(.+)", part)
        if not match:
            continue
        words = [re.sub(r"[.'’]", "", w) for w in match.group(2).split()]
        words = [w for w in words if w not in DIRECTIONS and w not in {"1/2", "&", "and"} and not w.isdigit()] or words
        return match.group(1), ORDINALS.get(words[0], words[0])
    return None


def judge(guide, place, result):
    """How well an Apple Maps result fits a place: (fits, score, metres)."""
    coordinate = result["coordinate"]
    metres = distance(place["latitude"], place["longitude"], coordinate["latitude"], coordinate["longitude"])
    score = similarity(place.get("mapsQuery") or place["name"], result.get("name", ""),
                       city=guide["slug"].split("-"))
    if score < 0.6:
        return False, score, metres
    ours = street(place["address"])
    theirs = street(one_line(result.get("formattedAddressLines", [])))
    if ours and theirs and metres <= STREET_RADIUS_METRES:
        # A street address decides it: the same number on the same street, wherever the pin is.
        # This keeps "Sweet Auburn Curb Market" from matching a train station of a similar name.
        return theirs == ours, score, metres
    # No street number on one side (a trail, a big park, a station Apple lists without one):
    # only a close name, near where the source says.
    return score >= 0.9 and metres <= SEARCH_RADIUS_METRES, score, metres


def maps(slugs):
    guides = [g for g in load_sources() if not slugs or g["slug"] in slugs]
    unknown = slugs - {g["slug"] for g in load_sources()}
    if unknown:
        sys.exit("No guide called " + ", ".join(sorted(unknown)))
    failures, report = [], []
    for guide in guides:
        # A fresh token per guide, so a long run never outlives one (they last 30 minutes).
        token = maps_token()
        found = {}
        start = len(report)
        for place in guide["places"]:
            label = f"{guide['slug']}/{place['key']}"
            if place.get("mapsSkip"):
                report.append(f"SKIP  {label}: {place['mapsSkip']}")
                continue
            best, seen = None, []
            for result in search(token, place, place.get("countryCode") or guide.get("countryCode", "US")):
                if "latitude" not in (result.get("coordinate") or {}):
                    continue
                fits, score, metres = judge(guide, place, result)
                coordinate = result["coordinate"]
                seen.append(f"{result.get('name', '?')} ({one_line(result.get('formattedAddressLines', []))}, "
                            f"{round(metres)} m, name {score:.2f}, at {coordinate['latitude']:.4f},{coordinate['longitude']:.4f})")
                if fits and (best is None or (score, -metres) > best[0]):
                    best = ((score, -metres), result, metres)
            if best is None:
                failures.append(f"{label}: “{place['name']}” at {place['address']} isn't in Apple Maps. "
                                "Check it's open and the address is right")
                report.append(f"MISS  {label}. Apple Maps offered: " + ("; ".join(seen[:4]) or "nothing"))
                continue
            _, result, metres = best
            entry = {"name": result.get("name", ""),
                     "latitude": round(result["coordinate"]["latitude"], 6),
                     "longitude": round(result["coordinate"]["longitude"], 6),
                     "address": one_line(result.get("formattedAddressLines", []))}
            if result.get("id"):
                entry["appleMapsID"] = result["id"]
            found[place["key"]] = entry
            moved = f", {round(metres)} m from the source position" if metres > MOVED_METRES else ""
            report.append(f"OK    {label} → {entry['name']} ({entry['address']}{moved})")
        lock = SRC / f"{guide['slug']}.maps.json"
        lock.write_text(json.dumps(found, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        # Print as each guide finishes, so a long run shows its progress.
        print("\n".join(report[start:]), flush=True)
    if failures:
        print(f"\n{len(failures)} place(s) need a look:\n" + "\n".join(f"• {f}" for f in failures))
        sys.exit(1)
    print("\nOK — every place found in Apple Maps.")


# MARK: MapKit JS token

def mapkit_token(origin, days):
    """Writes guides/mapkit-token.json: a MapKit JS token that only works on pages from `origin`.

    The token is public by design (the browser sends it to Apple), and the origin claim stops
    other sites using it. The Pages deploy writes a fresh one; it isn't committed.
    """
    try:
        import jwt  # pyjwt
    except ImportError:
        sys.exit("token needs: pip install pyjwt cryptography")
    missing = [n for n in ("APPLE_MAPS_KEY", "APPLE_MAPS_KEY_ID", "APPLE_TEAM_ID") if not os.environ.get(n)]
    if missing:
        sys.exit("token needs " + ", ".join(missing) + " in the environment")
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    token = jwt.encode({"iss": os.environ["APPLE_TEAM_ID"].strip(), "iat": now, "exp": now + days * 86400,
                        "origin": origin},
                       os.environ["APPLE_MAPS_KEY"].strip() + "\n", algorithm="ES256",
                       headers={"kid": os.environ["APPLE_MAPS_KEY_ID"].strip(), "typ": "JWT"})
    (OUT / "mapkit-token.json").write_text(json.dumps({"token": token}) + "\n", encoding="utf-8")
    print(f"Wrote guides/mapkit-token.json for {origin}, valid {days} days")


if __name__ == "__main__":
    command, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if command == "build" and not args:
        build()
    elif command == "check" and not args:
        check()
    elif command == "maps":
        maps(set(args))
    elif command == "token" and len(args) in (1, 2):
        mapkit_token(args[0], int(args[1]) if len(args) == 2 else 90)
    else:
        sys.exit(__doc__)
