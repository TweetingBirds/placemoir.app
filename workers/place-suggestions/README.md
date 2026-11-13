# Field Guide suggestions Worker

The **Suggest a place** form at [placemoir.app/guides/suggest/](https://placemoir.app/guides/suggest/) posts to this Cloudflare Worker, which turns each suggestion into an issue labelled `field-guide-suggestion` in the private repo **TweetingBirds/placemoir-suggestions**. The repo is private because suggestions can hold a person's name, where they live, or their handle, and nothing there should be public until it's been reviewed and used.

The Worker answers one path, `POST https://placemoir.app/api/field-guide-suggestions`. Everything else on placemoir.app stays on GitHub Pages. A suggestion only becomes an issue when it:

1. comes from `https://placemoir.app` (the `Origin` header) as form data under 16 KB
2. leaves the hidden `website` field empty (a honeypot: bots that fill it get a fake success)
3. fits the rate limits: 3 a minute from one IP address, 20 a minute from everyone, per Cloudflare location
4. passes the field checks (required fields, lengths, a real `http(s)` link, the fixed lists)
5. carries a Cloudflare Turnstile token that Cloudflare confirms for `placemoir.app` and the `suggest-place` action

What people type is shown in the issue exactly as typed: every punctuation mark is escaped, `@`, `#`, `://` and `www.` get an invisible word joiner so nothing becomes a mention, an issue link or a web link, and the place's link is shown as code. The visitor's IP address is used for the rate limit and the Turnstile check only, and never written to the issue. The form asks for no email address.

## Test

```bash
node --test workers/place-suggestions/worker.test.mjs
```

Node 20 or later, no packages. `.github/workflows/worker.yml` runs it on every change here.

## Set it up (once)

1. **Create the repo.** On GitHub, create **TweetingBirds/placemoir-suggestions**, private, with a README. Under Issues › Labels add `field-guide-suggestion` (the Worker's token can't create labels). If you'd rather use another repo, change `GITHUB_REPO` in `wrangler.toml`.
2. **Make the GitHub token.** GitHub › Settings › Developer settings › Personal access tokens › Fine-grained tokens › Generate new token. Resource owner **TweetingBirds**, Repository access **Only select repositories › placemoir-suggestions**, Repository permissions **Issues: Read and write** (Metadata: Read-only is added for you), nothing else. Pick an expiry and put a reminder in your calendar; when it expires the form says "Something went wrong" until you put in a new one. If the organization requires approval for fine-grained tokens, approve it under the organization's Settings › Personal access tokens.
3. **Make the Turnstile widget.** Cloudflare dashboard › Turnstile › Add widget. Name "Placemoir suggestions", hostname `placemoir.app`, mode **Managed**, no pre-clearance. Copy the **site key** (public) and the **secret key**.
4. **Deploy the Worker.** From this folder, signed in to the Cloudflare account with the placemoir.app zone:

   ```bash
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN       # paste the token from step 2
   npx wrangler secret put TURNSTILE_SECRET   # paste the secret key from step 3
   npx wrangler deploy
   ```

   `wrangler deploy` adds the route `placemoir.app/api/field-guide-suggestions`. placemoir.app's DNS record must be proxied (orange cloud), which it already is for Cloudflare Web Analytics. The `[[ratelimits]]` blocks need wrangler 4.36 or later (`npx` fetches the latest).
5. **Turn the form on.** Set `TURNSTILE_SITE_KEY` in `tools/guides.py` to the site key from step 3, run `python3 tools/guides.py build` and commit. Until then the page says suggestions aren't open yet, is `noindex`, and isn't linked from the guides list.

Keep the token and the secret key out of the repo. They live only in the Worker's secrets.

## Check it

```bash
curl -s -X POST https://placemoir.app/api/field-guide-suggestions   # 403: no Origin
```

Then send a real suggestion from the page and look for the issue in placemoir-suggestions. To see errors as they happen: `npx wrangler tail` from this folder.

## Using the suggestions

The suggestion form tells people that Tweeting Birds may use and edit what they send for Field Guides and in posts about them, and the privacy policy says how. A credit goes only in posts and on a guide page's Thanks line, never in place notes, and only when they chose one; the default is no credit. There are two kinds: **by name**, a name (letters only, first name and last initial suggested) plus an optional location, used as “Nate B. from Canton, GA” or just “Nate B.”; and **by social handle**. The issue's Credit section shows the line ready to use. Close or delete an issue once it's been used or turned down, and delete it if the person asks.
