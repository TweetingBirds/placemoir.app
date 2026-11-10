// Run with `node --test workers/placemoir-com/worker.test.mjs` (Node 20 or later, no packages).
import { test } from "node:test";
import assert from "node:assert/strict";
import { handle, guideSlug, TEAM_ID } from "./worker.js";

const get = (url, method = "GET") => handle(new Request(url, { method }));

test("serves the apple-app-site-association file without a redirect", async () => {
  for (const path of ["/.well-known/apple-app-site-association", "/apple-app-site-association"]) {
    const res = await get(`https://placemoir.com${path}`);
    assert.equal(res.status, 200);
    assert.equal(res.headers.get("Content-Type"), "application/json");
    const body = await res.json();
    const [detail] = body.applinks.details;
    assert.deepEqual(detail.appIDs, [
      `${TEAM_ID}.com.tweetingbirds.placemoir`,
      `${TEAM_ID}.com.tweetingbirds.placemoir.dev`,
    ]);
    assert.deepEqual(detail.components, [{ "/": "/g/*", comment: "Field Guides" }]);
  }
});

test("sends guide links to the guide page", async () => {
  const cases = {
    "https://placemoir.com/g/atlanta": "https://placemoir.app/guides/atlanta/",
    "https://www.placemoir.com/g/salt-lake-city/": "https://placemoir.app/guides/salt-lake-city/",
    "https://placemoir.com/g/Atlanta?ref=x": "https://placemoir.app/guides/atlanta/",
  };
  for (const [link, page] of Object.entries(cases)) {
    const res = await get(link);
    assert.equal(res.status, 302, link);
    assert.equal(res.headers.get("Location"), page, link);
  }
});

test("sends bad guide links to the guides list", async () => {
  for (const path of ["/g", "/g/", "/g/a%2Fb", "/g/-atlanta", "/g/at--lanta",
                      "/g/caf%C3%A9", "/g/atlanta/extra", "/g/%E0%A4%A", `/g/${"a".repeat(65)}`]) {
    const res = await get(`https://placemoir.com${path}`);
    assert.equal(res.status, 302, path);
    assert.equal(res.headers.get("Location"), "https://placemoir.app/guides/", path);
  }
});

test("redirects everything else to placemoir.app", async () => {
  let res = await get("https://placemoir.com/");
  assert.equal(res.status, 301);
  assert.equal(res.headers.get("Location"), "https://placemoir.app/");
  res = await get("https://www.placemoir.com/support.html?x=1");
  assert.equal(res.headers.get("Location"), "https://placemoir.app/support.html?x=1");
  res = await get("https://placemoir.com/guides/atlanta/");
  assert.equal(res.headers.get("Location"), "https://placemoir.app/guides/atlanta/");
});

test("answers HEAD and refuses other methods", async () => {
  const head = await get("https://placemoir.com/.well-known/apple-app-site-association", "HEAD");
  assert.equal(head.status, 200);
  assert.equal(head.body, null);
  const post = await get("https://placemoir.com/g/atlanta", "POST");
  assert.equal(post.status, 405);
});

test("slug rule matches the app", () => {
  assert.equal(guideSlug("/g/new-york"), "new-york");
  assert.equal(guideSlug("/g/new-york/"), "new-york");
  assert.equal(guideSlug("/guides/new-york"), null);
  assert.equal(guideSlug(`/g/${"a".repeat(64)}`), "a".repeat(64));
});
