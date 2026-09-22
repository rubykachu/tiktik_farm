/*
 * Keyword search on TikTok's web app, driven through a headless Chromium.
 *
 * TikTok has no public search API and yt-dlp ships no search extractor for
 * it: a plain request to /api/search/general/full/ answers 200 with an
 * empty body, and the server-rendered HTML of /search carries no results
 * (they are fetched client-side). A real browser that has first visited the
 * home page — which is what mints the ttwid/msToken cookies the search call
 * is signed with — does get the full JSON back without any login. So this
 * script opens the page and reads the response the page itself made.
 *
 * Protocol: one JSON request object on stdin, one JSON result on stdout.
 *   {"mode":"search","keywords":["a","b"],"language":"en"}
 *     -> {"results":{"a":[creator,...],"b":[...]},"errors":{...}}
 *   {"mode":"suggest","seed":"dance","language":"en"}
 *     -> {"suggestions":["...","..."]}
 * A creator is {channel_id, unique_id, sec_uid, nickname, follower_count,
 * signature}. sec_uid is what yt-dlp's `tiktokuser:` input takes, which is
 * the cheaper way to list that creator's feed — see tiktok.py.
 * Failure for one keyword is reported in `errors` and leaves the others
 * intact; a failure of the whole run exits non-zero with {"error": "..."}.
 *
 * playwright-chromium is not a dependency of this file's own directory — it
 * is resolved from tiktok_uploader/tiktok-signature/node_modules via
 * NODE_PATH, which api/services/discovery_sources/tiktok.py sets.
 */
const { chromium } = require("playwright-chromium");

const HOME = "https://www.tiktok.com/";
const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
// The search XHR lands a few seconds after navigation; the warm-up wait is
// what lets the home page set the cookies that call is signed with.
const WARMUP_MS = 5000;
const RESPONSE_TIMEOUT_MS = 30000;
const NAV_TIMEOUT_MS = 60000;
// TikTok sometimes answers a search navigation with a page that never fires
// the search request at all — no error, nothing to catch, just a keyword that
// looks like it found nobody. Observed mid-recording: one attempt failed and
// took the whole scan down with it. Each retry goes back through the home page
// first, because that is what re-mints the tokens the search call is signed
// with; retrying the search URL alone repeats the same refusal.
const SEARCH_ATTEMPTS = 3;
const RETRY_BACKOFF_MS = 4000;

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (buf += c));
    process.stdin.on("end", () => resolve(buf));
    process.stdin.on("error", reject);
  });
}

function searchUrl(keyword, language) {
  const params = new URLSearchParams({ q: keyword, lang: language || "en" });
  return `https://www.tiktok.com/search?${params.toString()}`;
}

/** Navigate to a search page and return the JSON bodies of the API calls the
 *  page made, keyed by the endpoint fragment asked for. */
async function collectOnce(page, keyword, language, fragments) {
  const waits = fragments.map((f) =>
    page
      .waitForResponse((r) => r.url().includes(f) && r.status() === 200, {
        timeout: RESPONSE_TIMEOUT_MS,
      })
      .then((r) => r.json())
      .catch(() => null),
  );
  await page.goto(searchUrl(keyword, language), {
    waitUntil: "domcontentloaded",
    timeout: NAV_TIMEOUT_MS,
  });
  return Promise.all(waits);
}

/** Navigate to a search page and return the JSON bodies of the API calls the
 *  page made, keyed by the endpoint fragment asked for. Retries through the
 *  home page while the first fragment — the one the caller actually needs —
 *  comes back empty. */
async function collect(page, keyword, language, fragments) {
  let bodies = [];
  for (let attempt = 0; attempt < SEARCH_ATTEMPTS; attempt += 1) {
    if (attempt > 0) {
      await page.waitForTimeout(RETRY_BACKOFF_MS * attempt);
      await warmUp(page);
    }
    bodies = await collectOnce(page, keyword, language, fragments);
    if (bodies[0]) return bodies;
  }
  return bodies;
}

async function warmUp(page) {
  await page.goto(HOME, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT_MS });
  await page.waitForTimeout(WARMUP_MS);
}

function creatorsFrom(payload) {
  const byId = new Map();
  for (const entry of (payload && payload.data) || []) {
    const item = entry && entry.item;
    const author = item && item.author;
    if (!author || !author.id || !author.uniqueId) continue;
    if (byId.has(author.id)) continue;
    const stats = item.authorStats || {};
    byId.set(author.id, {
      channel_id: String(author.id),
      unique_id: author.uniqueId,
      sec_uid: author.secUid || "",
      nickname: author.nickname || author.uniqueId,
      follower_count: Number(stats.followerCount || 0),
      signature: author.signature || "",
    });
  }
  return [...byId.values()];
}

function suggestionsFrom(preview, guide) {
  const out = [];
  for (const s of (preview && preview.sug_list) || []) {
    if (s && s.content) out.push(s.content);
  }
  for (const w of (guide && guide.data) || []) {
    if (w && w.word) out.push(w.word);
  }
  return [...new Set(out)];
}

async function main() {
  const request = JSON.parse((await readStdin()) || "{}");
  const language = request.language || "en";
  const browser = await chromium.launch({
    headless: true,
    args: ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
  });
  try {
    const context = await browser.newContext({
      userAgent: UA,
      viewport: { width: 1280, height: 900 },
      locale: `${language}-US`,
    });
    const page = await context.newPage();
    await warmUp(page);

    if (request.mode === "suggest") {
      const [preview, guide] = await collect(page, request.seed || "", language, [
        "/api/search/general/preview/",
        "/api/search/suggest/guide/",
      ]);
      process.stdout.write(JSON.stringify({ suggestions: suggestionsFrom(preview, guide) }));
      return;
    }

    const results = {};
    const errors = {};
    for (const keyword of request.keywords || []) {
      try {
        const [payload] = await collect(page, keyword, language, ["/api/search/general/full/"]);
        results[keyword] = creatorsFrom(payload);
        if (!payload) errors[keyword] = "TikTok did not answer the search request";
      } catch (e) {
        results[keyword] = [];
        errors[keyword] = String((e && e.message) || e);
      }
    }
    process.stdout.write(JSON.stringify({ results, errors }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String((e && e.message) || e) }));
  process.exit(1);
});
