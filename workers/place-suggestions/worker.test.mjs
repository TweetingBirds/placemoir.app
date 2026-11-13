// Run with `node --test workers/place-suggestions/worker.test.mjs` (Node 20 or later, no packages).
import { test } from "node:test";
import assert from "node:assert/strict";
import { handle, validate, issue, inert, inertTitle, clean, creditLine, PATH, LABEL, LIMITS } from "./worker.js";

const ENV = {
  GITHUB_TOKEN: "github-token",
  GITHUB_REPO: "TweetingBirds/placemoir-suggestions",
  TURNSTILE_SECRET: "turnstile-secret",
  ALLOWED_ORIGIN: "https://placemoir.app",
  TURNSTILE_HOSTNAME: "placemoir.app",
};

const GOOD = {
  city: "Atlanta",
  place: "Mary Mac's Tea Room",
  kind: "restaurant",
  address: "224 Ponce de Leon Ave NE, Atlanta, GA",
  link: "",
  tip: "Fried chicken and the pot likker with cornbread.",
  why: "It has been serving Southern cooking since 1945.",
  lastWent: "year",
  credit: "none",
  creditName: "",
  creditLocation: "",
  website: "",
  "cf-turnstile-response": "token",
};

/** A fake fetch for Turnstile and GitHub that records what it was asked. */
function fakeFetch({ turnstile = { success: true, action: "suggest-place", hostname: "placemoir.app" }, github = 201 } = {}) {
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push({ url, init });
    if (url.startsWith("https://challenges.cloudflare.com/")) return Response.json(turnstile);
    if (url.startsWith("https://api.github.com/")) return new Response("{}", { status: github });
    throw new Error(`unexpected fetch ${url}`);
  };
  return { fetcher, calls };
}

function post(fields = {}, { origin = "https://placemoir.app", path = PATH, type = "application/x-www-form-urlencoded", method = "POST" } = {}) {
  const body = method === "POST" ? new URLSearchParams({ ...GOOD, ...fields }).toString() : undefined;
  const headers = { "CF-Connecting-IP": "203.0.113.7" };
  if (origin) headers.Origin = origin;
  if (type) headers["Content-Type"] = type;
  return new Request(`https://placemoir.app${path}`, { method, headers, body });
}

test("creates a labelled issue in the configured repo", async () => {
  const { fetcher, calls } = fakeFetch();
  const res = await handle(post(), ENV, fetcher);
  assert.equal(res.status, 201);
  assert.deepEqual(await res.json(), { ok: true });

  const [verify, create] = calls;
  assert.equal(verify.init.body.get("secret"), "turnstile-secret");
  assert.equal(verify.init.body.get("response"), "token");
  assert.equal(verify.init.body.get("remoteip"), "203.0.113.7");

  assert.equal(create.url, "https://api.github.com/repos/TweetingBirds/placemoir-suggestions/issues");
  assert.equal(create.init.headers.Authorization, "Bearer github-token");
  const sent = JSON.parse(create.init.body);
  assert.deepEqual(sent.labels, [LABEL]);
  assert.equal(sent.title, "Mary Mac's Tea Room (Atlanta)");
  assert.match(sent.body, /\*\*Kind:\*\* Restaurant/);
  assert.match(sent.body, /\*\*Last went:\*\* In the last year/);
  assert.match(sent.body, /Don't credit \(the default\)\./);
  assert.doesNotMatch(sent.body, /203\.0\.113\.7/, "the IP address never goes in the issue");
});

test("only takes form posts from placemoir.app on its one path", async () => {
  const { fetcher, calls } = fakeFetch();
  assert.equal((await handle(post({}, { origin: "https://evil.example" }), ENV, fetcher)).status, 403);
  assert.equal((await handle(post({}, { origin: null }), ENV, fetcher)).status, 403);
  assert.equal((await handle(post({}, { path: "/api/other" }), ENV, fetcher)).status, 404);
  assert.equal((await handle(post({}, { method: "GET" }), ENV, fetcher)).status, 405);
  assert.equal((await handle(post({}, { type: "application/json" }), ENV, fetcher)).status, 415);
  assert.equal((await handle(post({ why: "x".repeat(17000) }), ENV, fetcher)).status, 413);
  assert.equal(calls.length, 0);
});

test("closes politely when a secret is missing", async () => {
  const { fetcher, calls } = fakeFetch();
  const res = await handle(post(), { ...ENV, GITHUB_TOKEN: "" }, fetcher);
  assert.equal(res.status, 503);
  assert.equal(calls.length, 0);
});

test("fakes success for the honeypot and creates nothing", async () => {
  const { fetcher, calls } = fakeFetch();
  const res = await handle(post({ website: "https://spam.example" }), ENV, fetcher);
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0);
});

test("rate limits per IP address and for everyone", async () => {
  const keys = [];
  const limiter = (ok) => ({ limit: async ({ key }) => (keys.push(key), { success: ok }) });
  const { fetcher, calls } = fakeFetch();
  let res = await handle(post(), { ...ENV, PER_IP: limiter(false), EVERYONE: limiter(true) }, fetcher);
  assert.equal(res.status, 429);
  assert.equal(res.headers.get("Retry-After"), "60");
  assert.deepEqual(keys, ["203.0.113.7"]);
  res = await handle(post(), { ...ENV, PER_IP: limiter(true), EVERYONE: limiter(false) }, fetcher);
  assert.equal(res.status, 429);
  assert.equal(calls.length, 0);
});

test("refuses a failed, reused or misdirected Turnstile check", async () => {
  for (const turnstile of [
    { success: false },
    { success: true, action: "something-else", hostname: "placemoir.app" },
    { success: true, action: "suggest-place", hostname: "evil.example" },
  ]) {
    const { fetcher, calls } = fakeFetch({ turnstile });
    assert.equal((await handle(post(), ENV, fetcher)).status, 403);
    assert.equal(calls.length, 1, "no issue is created");
  }
  const { fetcher, calls } = fakeFetch();
  assert.equal((await handle(post({ "cf-turnstile-response": "" }), ENV, fetcher)).status, 403);
  assert.equal(calls.length, 0);
});

test("reports field problems before spending the Turnstile token", async () => {
  const { fetcher, calls } = fakeFetch();
  const res = await handle(post({ place: "", why: "short", kind: "spaceship" }), ENV, fetcher);
  assert.equal(res.status, 400);
  const { fields } = await res.json();
  assert.deepEqual(Object.keys(fields).sort(), ["kind", "place", "why"]);
  assert.equal(calls.length, 0);
});

test("says when GitHub fails", async () => {
  const { fetcher } = fakeFetch({ github: 401 });
  assert.equal((await handle(post(), ENV, fetcher)).status, 502);
});

test("validates every field", () => {
  const check = (fields) => validate(new URLSearchParams({ ...GOOD, ...fields }));
  assert.ok(check({}).value);
  assert.ok(check({ address: "", link: "maps.apple.com/place?id=1" }).value);
  assert.equal(check({ address: "", link: "maps.apple.com/place?id=1" }).value.link, "https://maps.apple.com/place?id=1");
  assert.ok(check({ address: "", link: "" }).errors.address);
  for (const link of ["javascript:alert(1)", "ftp://example.com/x", "https://user:pw@example.com/", "not a link", "https://localhost/"]) {
    assert.ok(check({ link }).errors?.link, link);
  }
  assert.ok(check({ city: "" }).errors.city);
  assert.ok(check({ city: "other", otherCity: "" }).errors.otherCity);
  assert.equal(check({ city: "other", otherCity: "Savannah" }).value.hasGuide, false);
  assert.ok(check({ city: "other", otherCity: "x".repeat(LIMITS.city + 1) }).errors.otherCity);
  assert.ok(check({ place: "x".repeat(LIMITS.place + 1) }).errors.place);
  assert.ok(check({ place: "é".repeat(LIMITS.place) }).value, "limits count characters, not bytes");
  assert.ok(check({ tip: "" }).errors.tip);
  assert.ok(check({ why: "x".repeat(LIMITS.why + 1) }).errors.why);
  assert.ok(check({ lastWent: "tomorrow" }).errors.lastWent);
  assert.ok(check({ credit: "handle", creditName: "" }).errors.creditName);
  assert.equal(check({ credit: "handle", creditName: "@nate" }).value.creditName, "@nate");
  assert.equal(check({ credit: "none", creditName: "Someone" }).value.creditName, "", "a name without permission is dropped");
  assert.equal(check({ credit: "everyone" }).value.credit, "none");
});

test("cleans hidden and control characters", () => {
  assert.equal(clean("  Ma​ry‮  Mac\u0007's \n Tea  "), "Mary Mac's Tea");
  assert.equal(clean("one\r\n\r\n\r\n\r\ntwo  \n   three", { multiline: true }), "one\n\ntwo\nthree");
});

test("nothing typed can format the issue, mention anyone or link anything", () => {
  const nasty = [
    "@natebird @TweetingBirds/team see #1 and TweetingBirds/placemoir-app#2",
    "# Heading", "> quote", "- list", "1. list", "    code", "```js", "<img src=x onerror=alert(1)>",
    "[click](https://evil.example)", "![x](https://evil.example/x.png)", "https://evil.example www.evil.example",
    "**bold** _em_ ~~strike~~ $math$ | table |", "<!-- hidden -->", "&lt;b&gt;", "a\\", "===",
  ].join("\n");
  const out = inert(nasty);
  // Every ASCII punctuation mark is escaped, so CommonMark shows each one literally.
  for (const [i, ch] of [...out].entries()) {
    if (/[!-/:-@[-`{-~]/.test(ch) && ch !== "\\") {
      const before = [...out].slice(0, i);
      let slashes = 0;
      for (let j = before.length - 1; j >= 0 && before[j] === "\\"; j--) slashes++;
      assert.equal(slashes % 2, 1, `"${ch}" at ${i} isn't escaped`);
    }
  }
  assert.doesNotMatch(out, /@[A-Za-z]/, "no mentions");
  assert.doesNotMatch(out, /#\d/, "no issue references");
  assert.doesNotMatch(out, /:\\\/\\\//, "no scheme links");
  assert.doesNotMatch(out, /www\\\./i, "no www links");
  assert.equal(out.split("\n").length, nasty.split("\n").length, "one line per typed line");
  assert.ok(out.split("\n").slice(0, -1).every((line) => line.endsWith("\\")), "lines end in a hard break");

  assert.equal(inertTitle("Thanks @nate for #3"), "Thanks @⁠nate for #⁠3");
});

test("keeps the issue readable", () => {
  const { value } = validate(new URLSearchParams({
    ...GOOD, link: "https://maps.apple.com/place?q=Mary`Mac", credit: "first-name", creditName: "Dana",
    why: "Great.\n\nGo early.", city: "other", otherCity: "Decatur",
  }));
  const { title, body } = issue(value, new Date("2026-11-12T10:00:00Z"));
  assert.equal(title, "Mary Mac's Tea Room (Decatur)");
  assert.match(body, /\*\*City:\*\* Decatur \(no Field Guide yet\)/);
  assert.match(body, /\*\*Link:\*\* `https:\/\/maps\.apple\.com\/place\?q=Mary%60Mac`/);
  assert.match(body, /Great\\\.\n\nGo early\\\./);
  assert.match(body, /Thank them by name: Dana\n\nNo location given\./, "the old first-name choice still works");
  assert.match(body, /on 2026-11-12\./);
});

test("thanks by name, with a location when one is given", () => {
  const check = (fields) => validate(new URLSearchParams({ ...GOOD, ...fields }));
  const named = check({ credit: "name", creditName: " Nate  B. ", creditLocation: "Canton, GA" }).value;
  assert.equal(named.creditName, "Nate B.");
  assert.equal(named.creditLocation, "Canton, GA");
  assert.equal(creditLine(named), "Nate B. from Canton, GA");
  assert.match(issue(named).body, /Thank them by name: Nate B\\\. from Canton\\, GA/);

  const noPlace = check({ credit: "name", creditName: "Zoë O'Brien-Smith", creditLocation: "" }).value;
  assert.equal(creditLine(noPlace), "Zoë O'Brien-Smith");
  assert.ok(check({ credit: "name", creditName: "Ana", creditLocation: "São Paulo (Brasil)" }).value);

  assert.ok(check({ credit: "name", creditName: "" }).errors.creditName);
  assert.ok(check({ credit: "name", creditName: "@nate" }).errors.creditName, "a handle isn't a name");
  assert.ok(check({ credit: "name", creditName: "Nate https://x.example" }).errors.creditName);
  assert.ok(check({ credit: "name", creditName: "Nate 2" }).errors.creditName);
  assert.ok(check({ credit: "name", creditName: "x".repeat(LIMITS.personName + 1) }).errors.creditName);
  assert.ok(check({ credit: "name", creditName: "Nate", creditLocation: "@canton" }).errors.creditLocation);
  assert.ok(check({ credit: "name", creditName: "Nate", creditLocation: "x".repeat(LIMITS.creditLocation + 1) }).errors.creditLocation);

  assert.equal(check({ credit: "handle", creditName: "@nate", creditLocation: "Canton, GA" }).value.creditLocation, "",
    "a location only goes with a name");
  assert.equal(check({ credit: "none", creditLocation: "Canton, GA" }).value.creditLocation, "");
  assert.equal(creditLine(check({ credit: "handle", creditName: "@nate on Instagram" }).value), "@nate on Instagram");
});
