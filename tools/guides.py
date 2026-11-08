#!/usr/bin/env python3
"""Builds the Want to go guides from guides/src/<slug>.json.

Each source file is one guide: its title, sections and places, written by hand. This
script turns it into, for every guide:

    guides/<slug>/index.html               the page people read
    guides/<slug>/guide.json               the library file Placemoir 1.1 downloads
    guides/<slug>/<slug>.placemoirarchive  the same file zipped, which Placemoir 1.0 opens from Files

plus guides/index.html (the list of guides) and the guides block in sitemap.xml.

    python3 tools/guides.py build          # write everything (standard library only)
    python3 tools/guides.py check          # validate the sources; fail if the built files are stale
    python3 tools/guides.py maps [slug…]   # look every place up in Apple Maps (see below)
    python3 tools/guides.py token <origin> [days]  # write guides/mapkit-token.json for the map

A guide with "status": "draft" is built and served with noindex, so it can be reviewed on the
live site, but stays off the guides list and the sitemap until it is "published".

The guide files follow the library format at archive-format.html, with its `guide` block.
Place ids are `guide:<slug>/<key>`, not UUIDs, so importing a guide twice adds nothing and a
place already in someone's journal is matched by name within 150 m.

`maps` needs APPLE_MAPS_KEY (the .p8 contents), APPLE_MAPS_KEY_ID and APPLE_TEAM_ID in the
environment, and `pip install pyjwt cryptography`. It searches Apple Maps for each place near
its coordinates and writes what it found to guides/src/<slug>.maps.json, which `build` reads
for the Apple Maps place ID and the exact position, so the source's coordinates only need to be
close. A place matches when its name is alike and Apple Maps has the same street number and
street; a place without a street number (a trail, a big park) needs a near-identical name. It
exits 1 if any place isn't found. Set "mapsQuery" on a place to search for a different name, or
"mapsSkip": "<reason>" to leave it out of the check.

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
import urllib.parse
import urllib.request
import zipfile

SITE = pathlib.Path(__file__).resolve().parent.parent
SRC = SITE / "guides" / "src"
OUT = SITE / "guides"
BASE_URL = "https://placemoir.app"
APP_STORE = "https://apps.apple.com/app/placemoir/id6829224969"

# Turn on with Placemoir 1.1: the button then opens the app through the universal link on
# placemoir.com, and the file download becomes the small-print fallback.
ONE_TAP = False
SHORT_LINK = "https://placemoir.com/g/{slug}"

CATEGORIES = {"restaurant": "Restaurant", "cafe": "Café", "bar": "Bar", "hotel": "Hotel",
              "park": "Park", "museum": "Museum", "attraction": "Attraction",
              "airport": "Airport", "other": "Place"}
PLACE_KEYS = {"key", "name", "category", "section", "address", "locality", "latitude",
              "longitude", "notes", "mapsQuery", "mapsSkip"}
MOVED_METRES = 300
SEARCH_RADIUS_METRES = 2000
STREET_RADIUS_METRES = 5000
NOTES_MAX = 300


# MARK: Sources

def load_sources():
    guides = []
    for path in sorted(SRC.glob("*.json")):
        if path.name.endswith(".maps.json"):
            continue
        guide = json.loads(path.read_text(encoding="utf-8"))
        problems = validate(guide, path.stem)
        if problems:
            sys.exit(f"{path.relative_to(SITE)} has {len(problems)} problem(s):\n"
                     + "\n".join(f"• {p}" for p in problems))
        lock = SRC / f"{guide['slug']}.maps.json"
        guide["_maps"] = json.loads(lock.read_text(encoding="utf-8")) if lock.exists() else {}
        guides.append(guide)
    return guides


def validate(guide, stem):
    problems = []
    for field in ("slug", "status", "title", "summary", "author", "language", "updatedAt", "sections", "places"):
        if not guide.get(field):
            problems.append(f"{field}: required")
    if problems:
        return problems
    if guide["slug"] != stem:
        problems.append(f"slug: “{guide['slug']}” must match the file name “{stem}”")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", guide["slug"]):
        problems.append("slug: lowercase letters, digits and - only")
    if guide["status"] not in ("draft", "published"):
        problems.append("status: draft or published")
    try:
        datetime.date.fromisoformat(guide["updatedAt"])
    except ValueError:
        problems.append("updatedAt: a day, like 2026-11-08")
    keys = set()
    for i, place in enumerate(guide["places"]):
        where = f"places[{i}] ({place.get('key') or place.get('name') or '?'})"
        for field in ("key", "name", "category", "section", "address", "notes"):
            if not str(place.get(field, "")).strip():
                problems.append(f"{where}.{field}: required")
        unknown = set(place) - PLACE_KEYS
        if unknown:
            problems.append(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
        key = place.get("key", "")
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", key):
            problems.append(f"{where}.key: lowercase letters, digits and - only")
        if key in keys:
            problems.append(f"{where}.key: “{key}” is used twice")
        keys.add(key)
        if place.get("category") not in CATEGORIES:
            problems.append(f"{where}.category: one of {', '.join(CATEGORIES)}")
        if place.get("section") not in guide["sections"]:
            problems.append(f"{where}.section: one of {', '.join(guide['sections'])}")
        lat, lon = place.get("latitude"), place.get("longitude")
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and -90 <= lat <= 90 and -180 <= lon <= 180):
            problems.append(f"{where}: latitude and longitude are required numbers")
        if len(place.get("notes", "")) > NOTES_MAX:
            problems.append(f"{where}.notes: keep it under {NOTES_MAX} characters")
    for section in guide["sections"]:
        if not any(p.get("section") == section for p in guide["places"]):
            problems.append(f"sections: “{section}” has no places")
    return problems


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
            "country": guide.get("country", ""),
            "latitude": found.get("latitude", place["latitude"]),
            "longitude": found.get("longitude", place["longitude"]),
            "wantToGo": True,
            "notes": place["notes"],
            "section": place["section"],
        }
        if found.get("appleMapsID"):
            record["appleMapsID"] = found["appleMapsID"]
        places.append({k: v for k, v in record.items() if v != ""})
    return {
        "format": "placemoir-library",
        "formatVersion": 1,
        "exportedAt": guide["updatedAt"],
        "generator": "Placemoir Guides",
        "guide": {
            "slug": guide["slug"],
            "title": guide["title"],
            "author": guide["author"],
            "language": guide["language"],
            "summary": guide["summary"],
            "sections": guide["sections"],
            "updatedAt": guide["updatedAt"],
            "url": page_url(guide),
        },
        "places": places,
    }


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


def page(title, description, canonical, body, prefix, noindex, app_argument=None, image=None, with_map=False):
    head, foot = template()
    if with_map:
        head = with_mapkit_csp(head)
        head = head.replace("</head>", '    <script src="js/guide-map.js?v=20261108.1" defer></script>\n</head>', 1)
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
    head, foot = relink(head, prefix), relink(foot, prefix)
    return f"{head}<main id=\"main\">\n        <!-- Generated by tools/guides.py from guides/src/. Don't edit here. -->\n{body}\n    </main>{foot}"


# Apple's MapKit JS, on guide pages only: its script, tiles and data come from Apple. The rest of
# the site keeps its stricter policy. Styles stay 'self': MapKit sets styles through the DOM.
MAPKIT_CSP = {
    "script-src": ["https://cdn.apple-mapkit.com"],
    "img-src": ["https://*.apple-mapkit.com", "https://*.ls.apple.com", "blob:"],
    "connect-src": ["https://*.apple-mapkit.com", "https://*.ls.apple.com", "https://*.apple.com"],
    "worker-src": ["blob:"],
}


def with_mapkit_csp(head):
    match = re.search(r'(<meta http-equiv="Content-Security-Policy" content=")([^"]*)(")', head)
    directives = [d.strip() for d in match.group(2).split(";") if d.strip()]
    names = [d.split()[0] for d in directives]
    for name, sources in MAPKIT_CSP.items():
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
                <figcaption><ul class="guide-legend">{keys}</ul></figcaption>
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
    if ONE_TAP:
        actions = f'''<a class="btn btn-moss" href="{SHORT_LINK.format(slug=slug)}">Add to Placemoir</a>
                    <p class="cta-note">Opens Placemoir, where you pick the places you want. <a href="{archive}" download="{e(archive_name)}">Download the file</a> instead.</p>'''
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
                        <h3>{e(place['name'])}</h3>
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
                <p class="eyebrow moss"><a href="../">Want to go guides</a></p>
                <h1>{e(guide['title'])}</h1>
                <p>{e(guide['summary'])}</p>
                <p class="meta">{count} places · Picked by {e(guide['author'])} · Checked {updated}</p>
                <div class="cta-row guide-actions">
                    {actions}
                </div>
            </header>
        </div>
        <div class="wrap page-body">
{chr(10).join(sections)}
            <p class="press-lede guide-fineprint">Every place here was picked for the guide, not paid for. Places that are already in your journal are left as they are when you import.</p>
        </div>'''
    return page(guide["title"], guide["summary"], page_url(guide), body, "../../",
                noindex=guide["status"] != "published", app_argument=page_url(guide), with_map=True)


def index_page(guides):
    e = html.escape
    published = [g for g in guides if g["status"] == "published"]
    if published:
        cards = "\n".join(f'''                <article class="prompt-card card">
                    <p class="meta">{len(g['places'])} places</p>
                    <h3><a href="{g['slug']}/">{e(g['title'])}</a></h3>
                    <p>{e(g['summary'])}</p>
                </article>''' for g in published)
        listing = f'            <div class="prompt-grid">\n{cards}\n            </div>'
    else:
        listing = '            <p class="press-lede">The first guides are on their way.</p>'
    body = f'''        <div class="wrap">
            <header class="page-head">
                <p class="eyebrow moss">Want to go guides</p>
                <h1>Places worth the trip.</h1>
                <p>A week of places to stay, eat, see and walk in a city, picked by people who've been. Add a guide to your Want to go list in Placemoir, then rate each place once you've gone.</p>
            </header>
        </div>
        <div class="wrap page-body">
{listing}
        </div>'''
    return page("Want to go guides", "City guides you can add to your Want to go list in Placemoir.",
                f"{BASE_URL}/guides/", body, "../", noindex=not published)


def sitemap(guides, current):
    published = [g for g in guides if g["status"] == "published"]
    text = re.sub(r"\n  <!-- guides:sitemap -->.*?<!-- /guides:sitemap -->", "", current, flags=re.S)
    if not published:
        return text
    newest = max(g["updatedAt"] for g in published)
    urls = [f"  <url><loc>{BASE_URL}/guides/</loc><lastmod>{newest}</lastmod></url>"]
    urls += [f"  <url><loc>{page_url(g)}</loc><lastmod>{g['updatedAt']}</lastmod></url>" for g in published]
    block = "  <!-- guides:sitemap -->\n" + "\n".join(urls) + "\n  <!-- /guides:sitemap -->\n"
    return text.replace("</urlset>", block + "</urlset>", 1)


# MARK: Commands

def outputs(guides):
    """Every built text file, by path. The archives are compared by their library.json."""
    files = {OUT / "index.html": index_page(guides)}
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
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("results", [])


def one_line(lines):
    """Apple's address lines, sometimes with line breaks inside them, as one line."""
    text = ", ".join(lines)
    return re.sub(r"\s*\n\s*", ", ", re.sub(r"[ \t]+", " ", text)).strip()


def street(address):
    """("659", "peachtree") from "659 Peachtree St NE, …", or None without a street number."""
    match = re.match(r"\s*(\d+[a-z]?)\s+([\w.']+)", address.lower())
    return (match.group(1), match.group(2).replace(".", "")) if match else None


def judge(guide, place, result):
    """How well an Apple Maps result fits a place: (fits, score, metres)."""
    coordinate = result["coordinate"]
    metres = distance(place["latitude"], place["longitude"], coordinate["latitude"], coordinate["longitude"])
    score = similarity(place.get("mapsQuery") or place["name"], result.get("name", ""),
                       city=guide["slug"].split("-"))
    if score < 0.6:
        return False, score, metres
    ours = street(place["address"])
    if ours and metres <= STREET_RADIUS_METRES:
        # A street address decides it: the same number on the same street, wherever the pin is.
        # This keeps "Sweet Auburn Curb Market" from matching a train station of a similar name.
        return street(one_line(result.get("formattedAddressLines", []))) == ours, score, metres
    # No street number (a trail, a big park): only a close name, near where the source says.
    return not ours and score >= 0.9 and metres <= SEARCH_RADIUS_METRES, score, metres


def maps(slugs):
    guides = [g for g in load_sources() if not slugs or g["slug"] in slugs]
    token = maps_token()
    failures, report = [], []
    for guide in guides:
        found = {}
        for place in guide["places"]:
            label = f"{guide['slug']}/{place['key']}"
            if place.get("mapsSkip"):
                report.append(f"SKIP  {label}: {place['mapsSkip']}")
                continue
            best, seen = None, []
            for result in search(token, place, guide.get("countryCode", "US")):
                if "latitude" not in (result.get("coordinate") or {}):
                    continue
                fits, score, metres = judge(guide, place, result)
                seen.append(f"{result.get('name', '?')} ({one_line(result.get('formattedAddressLines', []))}, "
                            f"{round(metres)} m, name {score:.2f})")
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
    print("\n".join(report))
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
