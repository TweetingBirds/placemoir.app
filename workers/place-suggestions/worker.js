// Field Guide suggestions: the "Suggest a place" form on placemoir.app/guides/suggest/
// posts here, and each suggestion that passes becomes an issue in a private GitHub repo.
//
// The Worker answers POST /api/field-guide-suggestions on placemoir.app itself, so the
// page needs no CORS and its Content Security Policy keeps connect-src 'self'.
//
// In order, a request must:
//   1. be a POST from https://placemoir.app (the Origin header), under 16 KB
//   2. leave the hidden "website" field empty (bots fill it; they get a fake success)
//   3. fit the rate limits (per IP address, and for everyone together)
//   4. pass the field checks below
//   5. carry a Cloudflare Turnstile token that Cloudflare confirms
// and only then is the issue created, with a token that can only write issues in one repo.
//
// Nothing typed into the form can format the issue, link anything or notify anyone:
// every value is escaped (see `inert`), and the link only ever appears as code.
//
// Environment (wrangler.toml and `wrangler secret put`):
//   GITHUB_TOKEN      secret: fine-grained token, Issues read and write on GITHUB_REPO only
//   TURNSTILE_SECRET  secret: the Turnstile widget's secret key
//   GITHUB_REPO       var: owner/name of the private repo that receives the issues
//   ALLOWED_ORIGIN    var: https://placemoir.app
//   TURNSTILE_HOSTNAME var (optional): the hostname Turnstile must report, placemoir.app
//   PER_IP, EVERYONE  rate limiting bindings (optional in tests)

export const PATH = "/api/field-guide-suggestions";
export const LABEL = "field-guide-suggestion";
export const TURNSTILE_ACTION = "suggest-place";
const MAX_BYTES = 16 * 1024;

/** The kinds of place, as the guides group them. The form shows the same list. */
export const KINDS = {
  restaurant: "Restaurant",
  cafe: "Café or bakery",
  bar: "Bar",
  hotel: "Place to stay",
  museum: "Museum",
  attraction: "Something to see or do",
  park: "Park or outdoors",
  other: "Something else",
};

export const LAST_WENT = {
  month: "In the last month",
  "6-months": "In the last 6 months",
  year: "In the last year",
  "2-years": "1 to 2 years ago",
  older: "More than 2 years ago",
};

export const CREDIT = {
  none: "Don't credit them",
  "first-name": "Credit them by first name",
  handle: "Credit them by social handle",
};

/** Longest value for each field, in characters. The form's maxlength values match. */
export const LIMITS = {
  city: 60,
  place: 120,
  address: 200,
  link: 500,
  tip: 500,
  why: 1500,
  creditName: 60,
};

// Invisible and direction-changing characters, which could hide or reorder text.
const INVISIBLE = /[­᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]/g;
// Control characters other than newline and tab.
const CONTROL = /[\u0000-\u0008\u000B-\u001F\u007F-\u009F]/g;

/** Tidies one form value: NFC, no hidden characters, trimmed. Single-line values lose newlines. */
export function clean(value, { multiline = false } = {}) {
  let text = String(value ?? "").normalize("NFC").replace(/\r\n?/g, "\n").replace(CONTROL, "").replace(INVISIBLE, "");
  if (!multiline) return text.replace(/\s+/g, " ").trim();
  return text
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trim())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const length = (text) => [...text].length;

/**
 * Text that GitHub shows exactly as typed: every ASCII punctuation mark is backslash-escaped,
 * so nothing becomes Markdown or HTML, and a word joiner (U+2060) goes after "@", "#" and
 * in "://" and "www.", so nothing becomes a mention, an issue reference or a link.
 * Line breaks stay as line breaks.
 */
export function inert(text) {
  return text
    .split("\n")
    .map((line) =>
      line
        .replace(/[!-/:-@[-`{-~]/g, (c) => `\\${c}`)
        .replace(/@/g, "@⁠")
        .replace(/#/g, "#⁠")
        .replace(/:\\\/\\\//g, ":⁠\\/\\/")
        .replace(/\bwww\\\./gi, (m) => `${m.slice(0, 3)}⁠\\.`),
    )
    .join("\\\n")
    .replace(/\\\n\\\n/g, "\n\n");
}

/** Issue titles aren't Markdown, but GitHub still links "#12" and "@name" in them. */
export function inertTitle(text) {
  return text.replace(/@/g, "@⁠").replace(/#/g, "#⁠");
}

/** A link as code, which GitHub never turns into a clickable link or anything else. */
export function codeLink(url) {
  // URL.href percent-encodes backticks, so the value can't close the code span.
  return "`" + url.replaceAll("`", "%60") + "`";
}

/** Checks the form. Returns { value } or { errors: { field: message } }. */
export function validate(form) {
  const get = (name, options) => clean(form.get(name), options);
  const errors = {};
  const value = {};

  const tooLong = (field, text, errorField = field) => {
    if (length(text) > LIMITS[field]) errors[errorField] = `Keep this under ${LIMITS[field]} characters.`;
  };

  // The city list holds the cities with a guide; "other" means the visitor typed one in.
  const cityChoice = get("city");
  const cityField = cityChoice === "other" ? "otherCity" : "city";
  value.city = cityChoice === "other" ? get("otherCity") : cityChoice;
  value.hasGuide = cityChoice !== "other";
  if (!value.city) errors[cityField] = "Pick the city, or type it in.";
  else tooLong("city", value.city, cityField);

  value.place = get("place");
  if (!value.place) errors.place = "Add the name of the place.";
  else tooLong("place", value.place);

  value.kind = get("kind");
  if (!Object.hasOwn(KINDS, value.kind)) errors.kind = "Pick what kind of place it is.";

  value.address = get("address");
  tooLong("address", value.address);
  const link = get("link");
  value.link = "";
  if (link) {
    try {
      const url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(link) ? link : `https://${link}`);
      if ((url.protocol !== "https:" && url.protocol !== "http:") || !url.hostname.includes(".") || url.username || url.password) {
        throw new Error("not a web link");
      }
      value.link = url.href;
      tooLong("link", value.link);
    } catch {
      errors.link = "That doesn't look like a web link.";
    }
  }
  if (!value.address && !link) errors.address = "Add the address or a link to the place, so we find the right one.";

  value.tip = get("tip", { multiline: true });
  if (!value.tip) errors.tip = "Tell us what to get, see or do there.";
  else tooLong("tip", value.tip);

  value.why = get("why", { multiline: true });
  if (length(value.why) < 10) errors.why = "Tell us a little more about why it belongs.";
  else tooLong("why", value.why);

  value.lastWent = get("lastWent");
  if (!Object.hasOwn(LAST_WENT, value.lastWent)) errors.lastWent = "Pick when you last went.";

  value.credit = get("credit") || "none";
  if (!Object.hasOwn(CREDIT, value.credit)) value.credit = "none";
  value.creditName = value.credit === "none" ? "" : get("creditName");
  if (value.credit !== "none") {
    if (!value.creditName) errors.creditName = "Add the name to credit, or choose not to be credited.";
    else tooLong("creditName", value.creditName);
  }

  return Object.keys(errors).length ? { errors } : { value };
}

/** The issue for a checked suggestion. */
export function issue(s, date = new Date()) {
  const title = [...inertTitle(`${s.place} (${s.city})`)].slice(0, 200).join("");
  const lines = [
    `**Place:** ${inert(s.place)}  `,
    `**City:** ${inert(s.city)}${s.hasGuide ? "" : " (no Field Guide yet)"}  `,
    `**Kind:** ${KINDS[s.kind]}  `,
  ];
  if (s.address) lines.push(`**Address:** ${inert(s.address)}  `);
  if (s.link) lines.push(`**Link:** ${codeLink(s.link)}  `);
  lines.push(`**Last went:** ${LAST_WENT[s.lastWent]}`);
  lines.push("", "### What to get, see or do", "", inert(s.tip));
  lines.push("", "### Why it belongs", "", inert(s.why));
  lines.push("", "### Credit", "");
  lines.push(s.credit === "none" ? "Don't credit (the default)." : `${CREDIT[s.credit]}: ${inert(s.creditName)}`);
  lines.push(
    "",
    "---",
    `<sub>Sent from the Suggest a place form on placemoir.app on ${date.toISOString().slice(0, 10)}. ` +
      "Everything above was typed by the visitor and is shown as plain text. Check it before using it. " +
      "Credit goes in posts and the guide page's Thanks line only, never in place notes.</sub>",
  );
  return { title, body: lines.join("\n"), labels: [LABEL] };
}

const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });

async function verifyTurnstile(token, ip, env, fetcher) {
  if (!token || token.length > 2048) return false;
  const body = new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token });
  if (ip) body.set("remoteip", ip);
  const res = await fetcher("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body });
  if (!res.ok) return false;
  const result = await res.json();
  if (!result.success) return false;
  if (result.action && result.action !== TURNSTILE_ACTION) return false;
  if (env.TURNSTILE_HOSTNAME && result.hostname !== env.TURNSTILE_HOSTNAME) return false;
  return true;
}

async function withinLimits(env, ip) {
  // Each binding is optional so tests and a first deploy work without them.
  if (env.PER_IP && !(await env.PER_IP.limit({ key: ip || "unknown" })).success) return false;
  if (env.EVERYONE && !(await env.EVERYONE.limit({ key: "everyone" })).success) return false;
  return true;
}

export async function handle(request, env, fetcher = fetch) {
  const url = new URL(request.url);
  if (url.pathname !== PATH) return json(404, { error: "Not found." });
  if (request.method !== "POST") return json(405, { error: "Use POST." }, { Allow: "POST" });

  if (request.headers.get("Origin") !== env.ALLOWED_ORIGIN) return json(403, { error: "Send suggestions from placemoir.app." });
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO || !env.TURNSTILE_SECRET) {
    console.error("place-suggestions: GITHUB_TOKEN, GITHUB_REPO or TURNSTILE_SECRET is missing");
    return json(503, { error: "Suggestions are closed right now. Please try again later." });
  }
  const type = (request.headers.get("Content-Type") || "").split(";")[0].trim();
  if (type !== "application/x-www-form-urlencoded") return json(415, { error: "Send the form as form data." });
  if (Number(request.headers.get("Content-Length") || 0) > MAX_BYTES) return json(413, { error: "That's too long to send." });
  const text = await request.text();
  if (text.length > MAX_BYTES) return json(413, { error: "That's too long to send." });
  const form = new URLSearchParams(text);

  // The honeypot: a field people never see. Pretend it worked so the bot moves on.
  if (form.get("website")) return json(200, { ok: true });

  const ip = request.headers.get("CF-Connecting-IP") || "";
  if (!(await withinLimits(env, ip))) {
    return json(429, { error: "That's a lot of suggestions at once. Please wait a minute and try again." }, { "Retry-After": "60" });
  }

  const checked = validate(form);
  if (checked.errors) return json(400, { error: "Check the highlighted fields.", fields: checked.errors });

  if (!(await verifyTurnstile(form.get("cf-turnstile-response") || "", ip, env, fetcher))) {
    return json(403, { error: "We couldn't confirm you're a person. Please try the check again." });
  }

  const res = await fetcher(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "placemoir-place-suggestions",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify(issue(checked.value)),
  });
  if (!res.ok) {
    // Log the status only: the response could echo what the visitor typed.
    console.error(`place-suggestions: GitHub answered ${res.status}`);
    return json(502, { error: "Something went wrong on our side. Please try again later." });
  }
  return json(201, { ok: true });
}

export default {
  fetch: (request, env) => handle(request, env),
};
