# placemoir.com Worker

placemoir.com isn't a site. This Cloudflare Worker answers every request to it:

| Request | Answer |
| --- | --- |
| `/.well-known/apple-app-site-association` | The file iOS reads to open `placemoir.com/g/<slug>` in Placemoir. `200`, `application/json`, no redirect |
| `/g/<slug>` | `302` to `https://placemoir.app/guides/<slug>/`, for anyone without Placemoir 1.1 or on a computer. A bad slug goes to `https://placemoir.app/guides/` |
| Anything else | `301` to the same path on `https://placemoir.app` |

The `/g/*` path, the two App IDs and the slug rule match `GuideLink` and `docs/field-guides-links.md` in placemoir-app. Change them together.

## Test

```bash
node --test workers/placemoir-com/worker.test.mjs
```

Node 20 or later, no packages. `.github/workflows/worker.yml` runs it on every change here.

## Deploy

The Worker isn't deployed by CI. From this folder, signed in to the Cloudflare account that has the placemoir.com zone:

```bash
npx wrangler login
npx wrangler deploy
```

Or paste `worker.js` into a new Worker in the Cloudflare dashboard (Workers & Pages › Create › Worker) and add the routes `placemoir.com/*` and `www.placemoir.com/*` under Settings › Domains & Routes.

Cloudflare runs Redirect Rules (and Page Rules) **before** Workers, so delete the old placemoir.com → placemoir.app redirect rule once the Worker is live; the Worker does that redirect itself. Both hostnames need a proxied (orange cloud) DNS record, which the redirect already needed, so the existing records can stay.

## Check it

```bash
curl -sI https://placemoir.com/.well-known/apple-app-site-association   # 200, application/json
curl -sI https://placemoir.com/g/atlanta                                  # 302 to placemoir.app/guides/atlanta/
curl -s  https://app-site-association.cdn-apple.com/a/v1/placemoir.com    # Apple's cached copy, can lag a day
```

## Turning on one-tap guide links

Once Placemoir 1.1 is on the App Store and this Worker is live, set `ONE_TAP = True` in `tools/guides.py` and run `python3 tools/guides.py build`. The guide pages' **Add to Placemoir** button then points at `placemoir.com/g/<slug>`. Not before: on 1.0, or without the Worker, that link just comes back to the same page.
