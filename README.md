# placemoir.app

The marketing site for **Placemoir**, a private journal for the places you visit. Plain static HTML, CSS and a little JavaScript, published with GitHub Pages from the root of `main`. There's no build step.

## Preview

```bash
python3 -m http.server 8811
```

Then open http://127.0.0.1:8811. GitHub Pages serves `404.html` for missing paths; the Python server doesn't.

## Layout

| Path | What's there |
| --- | --- |
| `index.html` | Landing page |
| `support.html`, `privacy.html`, `terms.html`, `404.html` | Inner pages. They copy the header and footer from `index.html`, so change all of them together |
| `css/site.css` | Everything visual. Tokens at the top mirror `brand/tokens.css` in placemoir-marketing |
| `css/fonts.css`, `fonts/` | Newsreader Italic, Karla and IBM Plex Mono, self-hosted as Latin subsets (SIL OFL) |
| `js/theme.js` | Light and dark toggle, including the inline “light” and “dark” links. Follows the system until the visitor picks one, then remembers it in localStorage and pins each `<picture>` to that theme so only its images load |
| `js/gallery.js` | The iPhone, iPad and iPhone Duo tabs over the gallery. Without JavaScript every device row shows |
| `images/screens/` | Gallery screens named `<iphone|ipad|duo>-<screen>-<light|dark>.jpg`, made from the full-size PNGs in placemoir-marketing/website/screenshots at twice their display width (520, 840 and 1120 px) |
| `images/devices/` | The framed iPhone for the hero, light and dark, cut out of the `website/framed` images in placemoir-marketing so it sits on the map backdrop |
| `images/brand/` | Logo mark and wordmark SVGs from placemoir-marketing |
| `CNAME` | `placemoir.app` |

## Conventions

- Brand: Field Journal. Paper and ink, moss accent, plum used sparingly, Newsreader italic for titles (never roman), Karla for text, Plex Mono for dates and small eyebrows.
- Liquid Glass (`.glass`, `.glass-tint`) is for controls and floating chips only. Content sits on Page cards. Glass falls back to solid Page under Reduce Transparency.
- Every color is a token with a light and a dark value. Screenshots are `<picture>`s with a `(prefers-color-scheme: dark)` source marked `data-theme-dark`; the logo SVGs use `.only-light` / `.only-dark`.
- The Content Security Policy allows only same-origin scripts, styles, fonts and images, with no inline styles or scripts. The one exception is Cloudflare Web Analytics, which Cloudflare injects because the site is proxied (static.cloudflareinsights.com and cloudflareinsights.com). No cookies or other third-party requests.

- Cloudflare caches CSS and JS for four hours, so stylesheets and scripts are linked with `?v=<date>`. When you change `css/` or `js/`, bump that value in every page (`grep -l '?v=' *.html`), or the live site mixes new HTML with old CSS.

## Before launch

- Add an App Store link (`apple-itunes-app` meta and the store buttons).
- Legal pages are drafts adapted from quipsapp.com. Open items are marked `TODO` in HTML comments: the legal entity and address, the contact mailbox (`feedback@placemoir.app`), the weather provider, and pricing.
- Add a press kit page if wanted.
