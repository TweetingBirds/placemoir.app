// placemoir.com: the short-link domain for Field Guides. It isn't a site of its own.
//
// - /.well-known/apple-app-site-association: the file iOS reads to open
//   placemoir.com/g/<slug> links in Placemoir (universal links). It must be served
//   from placemoir.com itself, with no redirect.
// - /g/<slug>: for anyone without the app (or on a computer), a 302 to the guide's
//   page, placemoir.app/guides/<slug>/.
// - Everything else: a 301 to the same path on placemoir.app, as the old Cloudflare
//   redirect did.
//
// The paths and the slug rule match GuideLink in placemoir-app
// (Placemoir/Services/Guides/FieldGuide.swift) and docs/field-guides-links.md there.

export const SITE = "https://placemoir.app";

// The Apple developer Team ID that signs Placemoir (DEVELOPMENT_TEAM in the Xcode
// project). Not a secret: every AASA file is public.
export const TEAM_ID = "7AL8RCKX3J";

export const AASA = {
  applinks: {
    details: [
      {
        appIDs: [
          `${TEAM_ID}.com.tweetingbirds.placemoir`,
          `${TEAM_ID}.com.tweetingbirds.placemoir.dev`,
        ],
        components: [{ "/": "/g/*", comment: "Field Guides" }],
      },
    ],
  },
};

const MAX_SLUG_LENGTH = 64;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** The guide slug in a /g/ path, or null. Same rule as GuideLink.isValidSlug. */
export function guideSlug(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length !== 2 || parts[0] !== "g") return null;
  let slug;
  try {
    slug = decodeURIComponent(parts[1]).toLowerCase();
  } catch {
    return null;
  }
  return slug.length <= MAX_SLUG_LENGTH && SLUG.test(slug) ? slug : null;
}

function redirect(location, status) {
  return new Response(null, {
    status,
    headers: { Location: location, "Cache-Control": "public, max-age=300" },
  });
}

export function handle(request) {
  const url = new URL(request.url);
  const path = url.pathname;

  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }

  if (path === "/.well-known/apple-app-site-association" || path === "/apple-app-site-association") {
    return new Response(request.method === "HEAD" ? null : JSON.stringify(AASA), {
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  if (path === "/g" || path.startsWith("/g/")) {
    const slug = guideSlug(path);
    // 302, not 301: browsers cache a 301 for good, and a guide may move or be renamed.
    return redirect(slug ? `${SITE}/guides/${slug}/` : `${SITE}/guides/`, 302);
  }

  return redirect(`${SITE}${path}${url.search}`, 301);
}

export default {
  fetch(request) {
    return handle(request);
  },
};
