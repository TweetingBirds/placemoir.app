#!/usr/bin/env python3
"""Sweep every Apple Maps listing a Field Guide links to and flag places to recheck.

For each place with an appleMapsID in guides/src/<slug>.maps.json, this fetches
the public place page (https://maps.apple.com/place?place-id=<id>), reads the
name, coordinates, address, phone, website and closure text, and flags:
  - no Apple listing (and the match.skip reason, if any); a match without a
    place ID (trails, islands) isn't flagged
  - Apple says permanently/temporarily closed
  - our pin more than 300 m from Apple's
  - name similarity under 0.6
  - with --sites: the official website doesn't answer 2xx/3xx
    (403, 429 and 999 bot blocks are ignored)

Flagged places still need a person to check the Apple page and the official
site; this only narrows the list. Stdlib only. Output goes to --out (default
build/apple-sweep/, which is not committed): apple-pages.json (a cache, so a
rerun resumes) and places.json, plus a short summary on stdout.

  python3 tools/apple-listing-sweep.py [--src guides/src] [--out build/apple-sweep] [--sites]
"""
import argparse, collections, concurrent.futures as cf, difflib, glob, html as H
import json, math, os, re, time, urllib.error, urllib.request

UA = 'Mozilla/5.0 (Macintosh) Safari/605'


def fetch(pid):
    url = f'https://maps.apple.com/place?place-id={pid}'
    err = ''
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-US'})
            return urllib.request.urlopen(req, timeout=25).read().decode('utf-8', 'replace')
        except Exception as e:
            err = str(e)
            time.sleep(2 * (a + 1))
    return 'ERROR ' + err


def parse(h):
    if h.startswith('ERROR'):
        return {'error': h}
    r = {}
    m = re.search(r'og:title" content="([^"]*)', h)
    r['name'] = H.unescape(m.group(1)) if m else None
    m = re.search(r'center=(-?[\d.]+),(-?[\d.]+)', h)
    r['lat'], r['lon'] = (float(m.group(1)), float(m.group(2))) if m else (None, None)
    m = re.search(r'href="([^"]+)"[^>]*id="axUnifiedActionRowItem-Website"', h)
    r['website'] = H.unescape(m.group(1)) if m else None
    t = re.sub(r'<script.*?</script>|<style.*?</style>', '', h, flags=re.S)
    lines = [l.strip() for l in H.unescape(re.sub(r'<[^>]+>', '\n', t)).split('\n') if l.strip()]
    r['flags'] = sorted({l for l in lines if re.fullmatch(r'(?i)(permanently|temporarily) closed', l)})
    r['address'] = r['phone'] = None
    if 'Address' in lines:
        i = len(lines) - 1 - lines[::-1].index('Address')
        addr = []
        for l in lines[i + 1:i + 6]:
            addr.append(l)
            if l in ('United States', 'Canada', 'Spain', 'France', 'Germany', 'Japan'):
                break
        r['address'] = ', '.join(addr)
    if 'Phone' in lines:
        i = lines.index('Phone')
        r['phone'] = lines[i + 1] if i + 1 < len(lines) else None
    return r


def check_site(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': UA, 'Accept': 'text/html'})
        with urllib.request.urlopen(req, timeout=20) as r:
            return u, r.status
    except urllib.error.HTTPError as e:
        return u, e.code
    except Exception as e:
        return u, 'ERR ' + type(e).__name__


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--src', default='guides/src')
    ap.add_argument('--out', default='build/apple-sweep')
    ap.add_argument('--sites', action='store_true', help='also check each official website')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cache_path = os.path.join(a.out, 'apple-pages.json')

    lookups = {}
    for f in glob.glob(os.path.join(a.src, '*.maps.json')):
        lookups[os.path.basename(f)[:-len('.maps.json')]] = json.load(open(f))
    ids = {v['appleMapsID'] for m in lookups.values() for v in m.values() if v.get('appleMapsID')}
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    todo = sorted(i for i in ids if i not in cache or 'error' in cache[i])
    print(f'{len(ids)} Apple IDs, {len(todo)} to fetch', flush=True)
    with cf.ThreadPoolExecutor(4) as ex:
        for n, (pid, h) in enumerate(zip(todo, ex.map(fetch, todo)), 1):
            cache[pid] = parse(h)
            if n % 100 == 0:
                json.dump(cache, open(cache_path, 'w'), indent=1, ensure_ascii=False)
                print(n, flush=True)
    json.dump(cache, open(cache_path, 'w'), indent=1, ensure_ascii=False)

    rows = []
    for gf in sorted(glob.glob(os.path.join(a.src, '*.guide.geojson'))):
        slug = os.path.basename(gf)[:-len('.guide.geojson')]
        g = json.load(open(gf))
        m = lookups.get(slug, {})
        for f in g['features']:
            p = f.get('properties') or {}
            geo = f.get('geometry') or {}
            if geo.get('type') != 'Point' or not p.get('name'):
                continue
            lon, lat = geo['coordinates'][:2]
            look = m.get(f['id']) or {}
            pid = look.get('appleMapsID')
            ap_ = cache.get(pid, {}) if pid else {}
            issues = []
            skip = (p.get('match') or {}).get('skip')
            if not look:
                issues.append('no Apple listing' + (f' (skip: {skip})' if skip else ''))
            # Trails, islands and other natural features match without a place ID,
            # so there is no place page to check; that is not a problem by itself.
            closed = [x for x in ap_.get('flags', []) if re.fullmatch(r'(?i)(permanently|temporarily) closed', x)]
            if closed:
                issues.append('Apple says: ' + ', '.join(closed))
            if ap_.get('lat') is not None:
                k = math.cos(math.radians(lat))
                d = math.dist((lat, lon * k), (ap_['lat'], ap_['lon'] * k)) * 111000
                if d > 300:
                    issues.append(f'our pin {round(d)} m from Apple')
            if ap_.get('name') and difflib.SequenceMatcher(None, p['name'].lower(), ap_['name'].lower()).ratio() < 0.6:
                issues.append(f'name differs: Apple "{ap_["name"]}"')
            rows.append({'guide': slug, 'status': g['guide'].get('status'), 'key': f['id'], 'name': p['name'],
                         'address': p.get('address'), 'lat': lat, 'lon': lon, 'match': look.get('name'), 'appleMapsID': pid,
                         'apple': ap_, 'issues': issues})

    if a.sites:
        urls = sorted({r['apple']['website'] for r in rows if r['apple'].get('website')})
        with cf.ThreadPoolExecutor(12) as ex:
            res = dict(ex.map(check_site, urls))
        for r in rows:
            w = r['apple'].get('website')
            if w:
                s = res[w]
                r['site_status'] = s
                if not (isinstance(s, int) and s < 400) and s not in (403, 429, 999):
                    r['issues'].append(f'website {w} -> {s}')

    json.dump(rows, open(os.path.join(a.out, 'places.json'), 'w'), indent=1, ensure_ascii=False)
    flagged = [r for r in rows if r['issues']]
    print(f'{len(rows)} places, {len(flagged)} flagged')
    c = collections.Counter('pin far' if i.startswith('our pin') else i.split(':')[0].split(' (')[0]
                            for r in flagged for i in r['issues'])
    for k, v in c.most_common():
        print(f'  {k}: {v}')
    for r in flagged:
        print(f"{r['guide']}/{r['key']}: {'; '.join(r['issues'])}")


if __name__ == '__main__':
    main()
