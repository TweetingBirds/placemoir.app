#!/usr/bin/env python3
"""Build the translated pages of placemoir.app from the English pages and i18n/<lang>.json.

The English pages at the root stay hand-written and are the source of truth. This script
finds their translatable text on its own, gettext style: every run of text (with any inline
tags inside it, such as <strong> or <a>) and a short list of attributes (alt, aria-label,
title, placeholder, the description and social meta tags, and the data-label-* strings the
scripts read). The English text itself is the key in each language file, so there is
nothing to mark up when you add or change English copy.

    python3 tools/i18n.py build            # write de/index.html, fr/index.html, ...
    python3 tools/i18n.py check            # list missing and unused translations, exit 1 if any
    python3 tools/i18n.py sync             # add new English strings (as null) to every language file
    python3 tools/i18n.py review DIR       # write side-by-side review pages for translators into DIR

`build` also refreshes two generated regions in the English pages: the hreflang links in
<head> (between <!-- i18n:alternates --> markers) and the language list in the footer
(between <!-- i18n:languages --> markers). Don't edit inside those markers by hand, and
don't edit the files in the language folders: change the English page or i18n/<lang>.json
and run `build` again. A string with no translation falls back to English, and `check`
says so. Standard library only.
"""

import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "i18n"
SITE = "https://placemoir.app"

# Folder, hreflang, <html lang>, Open Graph locale, name in its own language,
# and the label for the language list in that language.
LANGS = [
    {"code": "en", "dir": "", "hreflang": "en", "lang": "en", "og": "en_US", "name": "English", "label": "Language"},
    {"code": "de", "dir": "de", "hreflang": "de", "lang": "de", "og": "de_DE", "name": "Deutsch", "label": "Sprache"},
    {"code": "es", "dir": "es", "hreflang": "es", "lang": "es-ES", "og": "es_ES", "name": "Español (España)", "label": "Idioma"},
    {"code": "es-419", "dir": "es-419", "hreflang": "es-419", "lang": "es-419", "og": "es_LA", "name": "Español (Latinoamérica)", "label": "Idioma"},
    {"code": "fr", "dir": "fr", "hreflang": "fr", "lang": "fr", "og": "fr_FR", "name": "Français", "label": "Langue"},
    {"code": "it", "dir": "it", "hreflang": "it", "lang": "it", "og": "it_IT", "name": "Italiano", "label": "Lingua"},
    {"code": "pt-BR", "dir": "pt-br", "hreflang": "pt-BR", "lang": "pt-BR", "og": "pt_BR", "name": "Português (Brasil)", "label": "Idioma"},
    {"code": "ja", "dir": "ja", "hreflang": "ja", "lang": "ja", "og": "ja_JP", "name": "日本語", "label": "言語"},
    {"code": "zh-Hant", "dir": "zh-hant", "hreflang": "zh-Hant", "lang": "zh-Hant", "og": "zh_TW", "name": "繁體中文", "label": "語言"},
]
ENGLISH = LANGS[0]
TRANSLATED = LANGS[1:]

# English pages that have translations. Every other page links to its English original.
PAGES = ["index.html"]
# English pages that only get the generated language list (no translations yet, or never:
# the legal pages and the archive spec stay in English).
ENGLISH_ONLY = ["support.html", "whats-new.html", "ai-import.html", "press.html", "pronunciation.html",
                "privacy.html", "terms.html", "archive-format.html", "404.html"]

# Kept as they are in every language, without a warning.
KEEP = {"placemoir", "Placemoir", "iPhone", "iPad", "iPhone Duo", "© 2026 Tweeting Birds"}

INLINE = {"a", "abbr", "b", "bdi", "br", "button", "cite", "code", "em", "i", "kbd", "mark", "q", "s",
          "small", "span", "strong", "sub", "sup", "time", "u", "wbr"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
SKIP = {"script", "style", "svg", "template", "noscript"}
ATTRS = ("alt", "aria-label", "title", "placeholder", "data-label", "data-label-light", "data-label-dark",
         "data-valuetexts")
META_CONTENT = {("name", "description"), ("property", "og:title"), ("property", "og:description"),
                ("name", "twitter:title"), ("name", "twitter:description")}

REGION = re.compile(r"(<!-- i18n:(alternates|globe|languages) -->)(.*?)(<!-- /i18n:\2 -->)", re.S)
HAS_LETTER = re.compile(r"[^\W\d_]", re.U)


def norm(text):
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------- parsing

class Node:
    def __init__(self, tag, attrs, start, start_end, parent):
        self.tag, self.attrs, self.start, self.start_end, self.parent = tag, attrs, start, start_end, parent
        self.end_start = self.end = start_end
        self.children = []


class Text:
    tag = None

    def __init__(self, start, end):
        self.start, self.end = start, end


class Tree(HTMLParser):
    """A tiny DOM that remembers where every tag and text run sits in the source."""

    def __init__(self, source):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        for match in re.finditer("\n", source):
            self.lines.append(match.end())
        self.root = Node("#root", [], 0, 0, None)
        self.current = self.root
        self.feed(source)
        self.close()
        self.root.end_start = self.root.end = len(source)

    def _pos(self):
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._pos()
        node = Node(tag, attrs, start, start + len(self.get_starttag_text()), self.current)
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        start = self._pos()
        node = Node(tag, attrs, start, start + len(self.get_starttag_text()), self.current)
        self.current.children.append(node)

    def handle_endtag(self, tag):
        start = self._pos()
        end = self.source.index(">", start) + 1
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is self.root:
            return
        node.end_start, node.end = start, end
        self.current = node.parent

    def _text(self, start, length):
        parent = self.current
        if parent.children and isinstance(parent.children[-1], Text) and parent.children[-1].end == start:
            parent.children[-1].end = start + length
        else:
            parent.children.append(Text(start, start + length))

    def handle_data(self, data):
        self._text(self._pos(), len(data))

    def handle_entityref(self, name):
        self._text(self._pos(), len(name) + 2)

    def handle_charref(self, name):
        self._text(self._pos(), len(name) + 3)


def all_inline(node):
    if isinstance(node, Text):
        return True
    return node.tag in INLINE and all(all_inline(child) for child in node.children)


def units(tree):
    """Yield (kind, start, end, english) for every translatable piece of the page.
    kind is "text" for element content and "attr:<name>" for an attribute value."""
    src = tree.source

    def attr_units(node):
        attrs = dict(node.attrs)
        if node.tag == "meta":
            names = ("content",) if any(attrs.get(k) == v for k, v in META_CONTENT) else ()
        else:
            names = ATTRS
        text = src[node.start:node.start_end]
        for name in [n for n, _ in node.attrs if n in names]:
            value = attrs.get(name)
            if not value or not HAS_LETTER.search(value):
                continue
            match = re.search(r'\s%s="([^"]*)"' % re.escape(name), text)
            if match:
                yield ("attr:" + name, node.start + match.start(1), node.start + match.end(1), html.unescape(match.group(1)))

    def walk(node):
        if isinstance(node, Text):
            return
        if node.tag != "#root":
            yield from attr_units(node)
        if node.tag in SKIP:
            return
        run = []

        def flush():
            content = [c for c in run if isinstance(c, Text) and src[c.start:c.end].strip()]
            if not content:
                # Only elements: each one is its own piece, so <b>Title</b><span>Text</span>
                # becomes two strings without tags.
                for child in run:
                    if not isinstance(child, Text):
                        yield from walk_inline(child)
            else:
                start, end = run[0].start, run[-1].end
                raw = src[start:end]
                lead = len(raw) - len(raw.lstrip())
                trail = len(raw) - len(raw.rstrip())
                text = raw.strip()
                # Attributes on tags inside the run are part of the string.
                if HAS_LETTER.search(text):
                    yield ("text", start + lead, end - trail, text)
            run.clear()

        for child in node.children:
            if all_inline(child) and not (not isinstance(child, Text) and child.tag in SKIP):
                run.append(child)
            else:
                yield from flush()
                yield from walk(child)
        yield from flush()

    def walk_inline(node):
        # An inline element on its own: translate what's inside it, not the tag.
        yield from attr_units(node)
        inner = Node("#inner", [], node.start_end, node.start_end, None)
        inner.children = node.children
        yield from walk(inner)

    yield from walk(tree.root)


def strip_regions(source):
    """The generated regions don't hold strings to translate; empty them before reading."""
    return REGION.sub(lambda m: m.group(1) + m.group(4), source)


def english_strings(page):
    source = strip_regions((ROOT / page).read_text(encoding="utf-8"))
    seen, result = set(), []
    for kind, start, end, text in units(Tree(source)):
        key = norm(text)
        if key in KEEP or key in seen:
            continue
        seen.add(key)
        result.append(key)
    jsonld = jsonld_description(source)
    if jsonld and jsonld not in seen:
        result.append(jsonld)
    return result


def jsonld_description(source):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', source, re.S)
    if not match:
        return None
    data = json.loads(match.group(1))
    return data.get("description")


# ---------------------------------------------------------------- language files

def load_file(lang):
    path = I18N / f"{lang['code']}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load(lang):
    return load_file(lang).get("strings", {})


def published():
    """Languages a native speaker has reviewed ("status": "reviewed" in i18n/<lang>.json).
    Only these are listed in the language menu, hreflang and the sitemap. Drafts are still
    built, with noindex, so reviewers can read them on the live site."""
    return [lang for lang in TRANSLATED if load_file(lang).get("status") == "reviewed"]


def save(lang, strings):
    old = load_file(lang)
    data = {"language": lang["code"], "name": lang["name"], "status": old.get("status", "draft"),
            "notes": old.get("notes", ""), "strings": strings}
    path = I18N / f"{lang['code']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def all_english():
    keys = []
    for page in PAGES:
        for key in english_strings(page):
            if key not in keys:
                keys.append(key)
    return keys


# ---------------------------------------------------------------- output

def url_for(lang, page):
    folder = f"/{lang['dir']}/" if lang["dir"] else "/"
    return SITE + folder + ("" if page == "index.html" else page)


def path_for(lang, page):
    folder = f"/{lang['dir']}/" if lang["dir"] else "/"
    return folder + ("" if page == "index.html" else page)


def alternates(page, indent):
    live = published()
    if page not in PAGES or not live:
        return ""
    lines = [f'{indent}<link rel="alternate" hreflang="{lang["hreflang"]}" href="{url_for(lang, page)}">'
             for lang in [ENGLISH] + live]
    lines.append(f'{indent}<link rel="alternate" hreflang="x-default" href="{url_for(ENGLISH, page)}">')
    return "\n" + "\n".join(lines) + "\n" + indent


def menu_languages(current):
    live = published()
    shown = [ENGLISH] + [lang for lang in TRANSLATED if lang in live or lang is current]
    return shown if len(shown) > 1 else []


def languages(current, page, indent):
    shown = menu_languages(current)
    if not shown:
        return ""
    target = page if page in PAGES else "index.html"
    items = []
    for lang in shown:
        here = ' aria-current="true"' if lang is current else ""
        items.append(f'{indent}    <li><a href="{path_for(lang, target)}" hreflang="{lang["hreflang"]}" '
                     f'lang="{lang["lang"]}"{here}>{lang["name"]}</a></li>')
    return ("\n" + f'{indent}<nav class="lang-switch" id="languages" aria-label="{current["label"]}">\n'
            + f'{indent}  <ul>\n' + "\n".join(items) + f"\n{indent}  </ul>\n{indent}</nav>\n{indent}")


def globe(current, indent):
    if not menu_languages(current):
        return ""
    return (f'\n{indent}<a class="icon-btn lang-btn" href="#languages" aria-label="{html.escape(current["label"])}">\n'
            f'{indent}    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 '
            f'6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z"/></svg>\n{indent}</a>\n{indent}')


def fill_regions(source, lang, page):
    def replace(match):
        line_start = source.rfind("\n", 0, match.start()) + 1
        indent = re.match(r"\s*", source[line_start:]).group(0)
        kind = match.group(2)
        if kind == "alternates":
            body = alternates(page, indent)
        elif kind == "globe":
            body = globe(lang, indent)
        else:
            body = languages(lang, page, indent)
        return match.group(1) + body + match.group(4)
    return REGION.sub(replace, source)


def splice(source, edits):
    edits.sort(key=lambda e: e[0])
    out, pos = [], 0
    for start, end, text in edits:
        if start < pos:
            raise SystemExit(f"overlapping edits at {start}")
        out.append(source[pos:start])
        out.append(text)
        pos = end
    out.append(source[pos:])
    return "".join(out)


BADGE = "images/app-store-badge.svg"


def badge(lang):
    """Apple's localized "Download on the App Store" badge, if images/badges/ has one."""
    path = f"images/badges/app-store-{lang['code']}.svg"
    return path if (ROOT / path).exists() else None


def badge_width(path):
    match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', (ROOT / path).read_text(encoding="utf-8"))
    return round(float(match.group(1)) / float(match.group(2)) * 40)


def relink(url, lang):
    """Make a root-relative link work from inside a language folder."""
    if url == BADGE and badge(lang):
        return "../" + badge(lang)
    if not url or url.startswith(("#", "/", "http:", "https:", "mailto:", "data:", "tel:", "../")):
        return url
    path, sep, frag = url.partition("#")
    if path in ("", "./", "index.html"):
        return "./" + sep + frag
    if path in PAGES:
        return path + sep + frag
    return "../" + url


def translate_page(page, lang, strings, missing):
    # Regions are filled last: they hold absolute links and the language names, untranslated.
    source = strip_regions((ROOT / page).read_text(encoding="utf-8"))
    tree = Tree(source)
    live = published()
    edits = []

    def lookup(text):
        key = norm(text)
        if key in KEEP:
            return text
        value = strings.get(key)
        if value is None:
            missing.add(key)
            return text
        return value

    for kind, start, end, text in units(tree):
        value = lookup(text)
        if kind != "text":
            value = html.escape(value, quote=True).replace("&#x27;", "'")
        edits.append((start, end, value))

    # <html lang>, canonical and og:url, og:locale.
    def head(node):
        if isinstance(node, Text):
            return
        attrs = dict(node.attrs)
        tag = source[node.start:node.start_end]
        new = tag
        if node.tag == "html":
            new = tag.replace('lang="en"', f'lang="{lang["lang"]}"')
        elif node.tag == "link" and attrs.get("rel") == "canonical":
            new = re.sub(r'href="[^"]*"', f'href="{url_for(lang, page)}"', tag)
        elif node.tag == "meta" and attrs.get("property") == "og:url":
            new = re.sub(r'content="[^"]*"', f'content="{url_for(lang, page)}"', tag)
        elif node.tag == "meta" and attrs.get("property") == "og:type":
            new = tag + f'\n    <meta property="og:locale" content="{lang["og"]}">'
        elif node.tag == "meta" and attrs.get("name") == "viewport" and lang not in live:
            new = tag + '\n    <meta name="robots" content="noindex"><!-- draft until a native speaker reviews it -->'
        if new != tag:
            edits.append((node.start, node.start_end, new))
        for child in node.children:
            head(child)

    head(tree.root)

    # The JSON-LD description, string for string.
    english = jsonld_description(source)
    if english:
        quoted = json.dumps(english, ensure_ascii=False)
        at = source.find(quoted)
        if at >= 0:
            edits.append((at, at + len(quoted), json.dumps(lookup(english), ensure_ascii=False)))

    out = splice(source, edits)

    # Second pass: fix relative links now that the page lives one folder down.
    tree = Tree(out)
    links = []

    def fix(node):
        if isinstance(node, Text):
            return
        tag = out[node.start:node.start_end]
        new = tag
        for name in ("href", "src", "srcset"):
            def repl(m):
                value = html.unescape(m.group(2))
                if name == "srcset":
                    parts = [p.strip() for p in value.split(",")]
                    value = ", ".join(" ".join([relink(p.split()[0], lang)] + p.split()[1:]) for p in parts)
                else:
                    value = relink(value, lang)
                return f'{m.group(1)}="{html.escape(value, quote=True)}"'
            if node.tag == "link" and dict(node.attrs).get("rel") in ("canonical", "alternate"):
                continue
            new = re.sub(r'(\s%s)="([^"]*)"' % name, repl, new)
        if node.tag == "img" and dict(node.attrs).get("src") == BADGE and badge(lang):
            # Localized badges differ in width; keep the width attribute true to the art.
            new = re.sub(r'width="\d+"', f'width="{badge_width(badge(lang))}"', new)
        if new != tag:
            links.append((node.start, node.start_end, new))
        for child in node.children:
            fix(child)

    fix(tree.root)
    out = fill_regions(splice(out, links), lang, page)
    header = (f"<!-- Generated by tools/i18n.py from /{page} and i18n/{lang['code']}.json. "
              "Don't edit this file: change those and run `python3 tools/i18n.py build`. -->\n")
    return out.replace("<!DOCTYPE html>\n", "<!DOCTYPE html>\n" + header, 1)


def build():
    missing_by_lang = {}
    for page in PAGES + ENGLISH_ONLY:
        path = ROOT / page
        source = path.read_text(encoding="utf-8")
        filled = fill_regions(source, ENGLISH, page)
        if filled != source:
            path.write_text(filled, encoding="utf-8")
    for lang in TRANSLATED:
        strings = load(lang)
        missing = set()
        for page in PAGES:
            out = translate_page(page, lang, strings, missing)
            target = ROOT / lang["dir"] / page
            target.parent.mkdir(exist_ok=True)
            target.write_text(out, encoding="utf-8")
        missing_by_lang[lang["code"]] = missing
    for code, missing in missing_by_lang.items():
        if missing:
            print(f"{code}: {len(missing)} strings fall back to English (run check for the list)")
    live = published()
    print("Built " + ", ".join(f"{l['dir']}/" + ("" if l in live else " (draft)") for l in TRANSLATED))
    write_sitemap()


def write_sitemap():
    """List the reviewed translations of each page, each with its alternates. While a page
    has none, its plain English entry stays as it is."""
    path = ROOT / "sitemap.xml"
    text = path.read_text(encoding="utf-8")
    live = published()
    old = re.search(r"\n  <!-- i18n:sitemap -->.*?<!-- /i18n:sitemap -->", text, re.S)
    previous = old.group(0) if old else ""
    if old:
        text = text.replace(previous, "")
    blocks = []
    if not live:
        # Nothing reviewed (any more): put back the plain English entries the region replaced.
        for page in PAGES:
            english = url_for(ENGLISH, page)
            earlier = re.search(r"<loc>%s</loc><lastmod>([^<]+)</lastmod>" % re.escape(english), previous)
            if earlier and f"<loc>{english}</loc>" not in text:
                text = text.replace("</urlset>", f"  <url><loc>{english}</loc><lastmod>{earlier.group(1)}</lastmod></url>\n</urlset>")
    for page in PAGES if live else []:
        english = url_for(ENGLISH, page)
        plain = re.search(r"\n  <url><loc>%s</loc>(?:<lastmod>([^<]+)</lastmod>)?</url>" % re.escape(english), text)
        earlier = re.search(r"<loc>%s</loc><lastmod>([^<]+)</lastmod>" % re.escape(english), previous)
        lastmod = (plain.group(1) if plain else None) or (earlier.group(1) if earlier else "")
        if plain:
            text = text.replace(plain.group(0), "")
        langs = [ENGLISH] + live
        alts = "".join(f'\n    <xhtml:link rel="alternate" hreflang="{l["hreflang"]}" href="{url_for(l, page)}"/>' for l in langs)
        alts += f'\n    <xhtml:link rel="alternate" hreflang="x-default" href="{english}"/>'
        for lang in langs:
            blocks.append(f"  <url><loc>{url_for(lang, page)}</loc><lastmod>{lastmod}</lastmod>{alts}\n  </url>")
    if blocks:
        if "xmlns:xhtml" not in text:
            text = text.replace('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
                                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">')
        text = text.replace("</urlset>", "  <!-- i18n:sitemap -->\n" + "\n".join(blocks) + "\n  <!-- /i18n:sitemap -->\n</urlset>")
    path.write_text(text, encoding="utf-8")


def check():
    english = all_english()
    bad = False
    for lang in TRANSLATED:
        strings = load(lang)
        missing = [k for k in english if strings.get(k) is None]
        unused = [k for k in strings if k not in english]
        tags = [k for k in english if strings.get(k) and tag_shape(k) != tag_shape(strings[k])]
        if missing or unused or tags:
            bad = True
        print(f"{lang['code']}: {len(english) - len(missing)}/{len(english)} translated"
              + (f", {len(unused)} unused" if unused else "") + (f", {len(tags)} with different tags" if tags else ""))
        for k in missing:
            print(f"  missing: {k[:100]}")
        for k in unused:
            print(f"  unused:  {k[:100]}")
        for k in tags:
            print(f"  tags:    {k[:100]}")
    return 1 if bad else 0


def tag_shape(text):
    return sorted(re.findall(r"<(/?[a-z]+)", text))


def sync():
    english = all_english()
    for lang in TRANSLATED:
        strings = load(lang)
        ordered = {k: strings.get(k) for k in english}
        dropped = [k for k in strings if k not in ordered]
        for k in dropped:
            print(f"{lang['code']}: dropped unused {k[:80]!r}")
        save(lang, ordered)
    print(f"{len(english)} strings; language files updated")


def review(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    english = all_english()
    for lang in TRANSLATED:
        strings = load(lang)
        data = json.loads((I18N / f"{lang['code']}.json").read_text(encoding="utf-8")) if (I18N / f"{lang['code']}.json").exists() else {}
        rows = []
        for i, k in enumerate(english, 1):
            t = strings.get(k)
            rows.append(f"<tr><td>{i}</td><td>{html.escape(k)}</td><td lang=\"{lang['lang']}\">"
                        f"{html.escape(t) if t else '<em>missing</em>'}</td><td></td></tr>")
        notes = html.escape(data.get("notes", ""))
        page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Placemoir website · {lang['name']} review</title>
<style>body{{font:15px/1.5 -apple-system,sans-serif;margin:32px;color:#1D2733}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #D9DCD2;padding:8px;vertical-align:top;text-align:left}}th{{background:#F1F2EC}}
td:first-child{{color:#8A929A;width:2em}}td:last-child{{width:18%}}.notes{{background:#FBFBF7;border:1px solid #D9DCD2;padding:12px 16px;border-radius:8px;white-space:pre-wrap}}</style></head>
<body><h1>Placemoir website, {lang['name']} ({lang['code']})</h1>
<p>Home page draft at <a href="{url_for(lang, 'index.html')}">{url_for(lang, 'index.html')}</a> once merged.
Tags like &lt;strong&gt; and &lt;a&gt; must stay in the translation. Write corrections in the last column.</p>
<div class="notes">{notes}</div>
<table><tr><th>#</th><th>English</th><th>{lang['name']}</th><th>Correction</th></tr>
{chr(10).join(rows)}
</table></body></html>
"""
        (folder / f"{lang['code']}.html").write_text(page, encoding="utf-8")
    print(f"Wrote review pages to {folder}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    if command == "build":
        build()
    elif command == "check":
        sys.exit(check())
    elif command == "sync":
        sync()
    elif command == "review" and len(sys.argv) > 2:
        review(sys.argv[2])
    else:
        print(__doc__)
        sys.exit(2)
