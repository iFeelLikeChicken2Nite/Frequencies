// Frequencies thumbs-down reports (build 151, tasks 209/212): a Cloudflare
// Worker that files a GitHub issue on the reports repository for the app.
// The GitHub token lives here as a Worker secret, so no build of the app
// (AppImage, Windows installer, APK, F-Droid) carries one.
//
//   POST /report   JSON, at most 4 KB:
//                  {"artist": "...",          required, 1-200 characters
//                   "what": "bio" | "photo" | "both" | "test",   required
//                   "source": "...",          where the bio came from (200)
//                   "bio": "...",             the wrong bio, first 200 chars
//                   "photo": "...",           where the photo came from (500)
//                   "station": "...",         the station playing (200)
//                   "build": "151",           the app's build (20)
//                   "os": "linux" | "windows" | "android" | "other"}
//                  -> 201 {"ok": true, "url": "<the issue>"}
//                  -> 200 {"ok": true, "already": true}  (that artist was
//                     already reported today: nothing new is filed)
//                  -> 4xx/5xx {"error": "..."}; the app then opens the
//                     pre-filled GitHub page in the browser instead.
//   GET  anything  "Frequencies reports."
//
// Setup (Cloudflare dashboard, notes/cloudflare-art-setup.md part 2):
//   REPORT_TOKEN  secret: a fine-grained GitHub token with only
//                 Issues: Read and write on the reports repository.
//   REPORT_REPO   optional text variable, "<owner>/<repo>"; without it the
//                 repository below is used.
//   LIMITS        optional KV namespace binding. With it the limits below
//                 hold everywhere; without it each Cloudflare location
//                 counts on its own (still a limit, just a looser one).
//   DAILY_CAP     optional text variable: issues per day in all (300).

const DEFAULT_REPO = "iFeelLikeChicken2Nite/frequencies-reports";
const MAX_BODY = 4096;
const PER_PERSON_PER_DAY = 20;
const DAILY_CAP = 300;
const KINDS = { bio: "Bio", photo: "Photo", both: "Bio and photo", test: "Test" };
const SYSTEMS = ["linux", "windows", "android", "other"];
const LIMITS = { artist: 200, source: 200, bio: 200, photo: 500, station: 200, build: 20 };

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

// Plain text only: no control characters, one line, no @mentions (an issue
// that says "@someone" would notify them), trimmed to its limit.
function plain(value, limit) {
  if (value === undefined || value === null) return "";
  if (typeof value !== "string" && typeof value !== "number") return null;
  let text = String(value)
    .replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  text = text.replace(/@(?=[A-Za-z0-9])/g, "@\u200b");
  if (text.length > limit) text = text.slice(0, limit);
  return text;
}

// Markdown would turn "[x](y)" or "<img>" into a link or a picture; shown
// as typed instead.
function escapeMarkdown(text) {
  return text.replace(/[\\`*_{}\[\]()<>#|!~]/g, (c) => "\\" + c);
}

async function readBody(request) {
  const declared = Number(request.headers.get("Content-Length") || 0);
  if (declared > MAX_BODY) return null;
  if (!request.body) return "";
  const reader = request.body.getReader();
  const parts = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > MAX_BODY) return null;
    parts.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.length; }
  return new TextDecoder().decode(bytes);
}

function check(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return { error: "not a report" };
  const out = {};
  for (const [name, limit] of Object.entries(LIMITS)) {
    const text = plain(data[name], limit);
    if (text === null) return { error: `${name} must be text` };
    out[name] = text;
  }
  if (!out.artist) return { error: "artist is missing" };
  out.what = typeof data.what === "string" ? data.what.toLowerCase() : "";
  if (!KINDS[out.what]) return { error: "what must be bio, photo or both" };
  const os = typeof data.os === "string" ? data.os.toLowerCase() : "";
  out.os = SYSTEMS.includes(os) ? os : "other";
  return { report: out };
}

function issueText(r, now = new Date()) {
  const kind = KINDS[r.what];
  if (r.what === "test") {
    return {
      title: "Test report from Frequencies",
      body: `Sent by the dev build's "Send a test report" button (build ${escapeMarkdown(r.build || "?")}, ${r.os}). You can close this issue.`,
    };
  }
  const lines = [`Artist: ${escapeMarkdown(r.artist)}`, `What is wrong: ${kind}`];
  if (r.what === "bio" || r.what === "both") {
    lines.push(`Bio from: ${escapeMarkdown(r.source || "unknown")}`);
    lines.push(`Wrong info (first 200 characters):\n> ${escapeMarkdown(r.bio)}`);
  }
  if (r.what === "photo" || r.what === "both") {
    lines.push(`Photo shown: ${r.photo ? escapeMarkdown(r.photo) : "none shown"}`);
  }
  if (r.station) lines.push(`Station: ${escapeMarkdown(r.station)}`);
  lines.push(`App: build ${escapeMarkdown(r.build || "?")}, ${r.os}`);
  lines.push(`(${now.toISOString().slice(0, 16).replace("T", " ")} UTC)`);
  return { title: `Wrong artist info (${kind.toLowerCase()}): ${r.artist}`.slice(0, 250), body: lines.join("\n") };
}

// A small counter store: KV when bound, else this location's cache.
function counters(env) {
  if (env.LIMITS) {
    return {
      async get(key) { return Number((await env.LIMITS.get(key)) || 0); },
      async put(key, value) { await env.LIMITS.put(key, String(value), { expirationTtl: 2 * 86400 }); },
    };
  }
  const cache = typeof caches !== "undefined" ? caches.default : null;
  const address = (key) => `https://limits.frequencies.invalid/${encodeURIComponent(key)}`;
  return {
    async get(key) {
      if (!cache) return 0;
      const hit = await cache.match(address(key));
      return hit ? Number(await hit.text()) || 0 : 0;
    },
    async put(key, value) {
      if (!cache) return;
      await cache.put(address(key), new Response(String(value), {
        headers: { "Cache-Control": "max-age=172800" },
      }));
    },
  };
}

async function sha(text) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return [...digest].slice(0, 12).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function fileReport(request, env) {
  if (!env.REPORT_TOKEN) return json({ error: "reports are not set up yet" }, 503);
  if (env.REPORT_LIMIT) {
    const who = request.headers.get("CF-Connecting-IP") || "unknown";
    const { success } = await env.REPORT_LIMIT.limit({ key: who });
    if (!success) return json({ error: "too many reports, try again later" }, 429);
  }
  const type = request.headers.get("Content-Type") || "";
  if (!type.toLowerCase().startsWith("application/json")) return json({ error: "send JSON" }, 415);
  const text = await readBody(request);
  if (text === null) return json({ error: "report too big" }, 413);
  let data;
  try { data = JSON.parse(text); } catch (e) { return json({ error: "not JSON" }, 400); }
  const { report, error } = check(data);
  if (error) return json({ error }, 400);

  const day = new Date().toISOString().slice(0, 10);
  const store = counters(env);
  const who = await sha(request.headers.get("CF-Connecting-IP") || "unknown");
  const artistKey = `artist:${day}:${await sha(report.what === "test" ? "test" : report.artist.toLowerCase())}`;
  const personKey = `person:${day}:${who}`;
  const dayKey = `day:${day}`;
  if (await store.get(artistKey)) return json({ ok: true, already: true });
  const person = await store.get(personKey);
  if (person >= PER_PERSON_PER_DAY) return json({ error: "too many reports today, try again tomorrow" }, 429);
  const total = await store.get(dayKey);
  if (total >= (Number(env.DAILY_CAP) || DAILY_CAP)) return json({ error: "too many reports today, try again tomorrow" }, 429);

  const repo = /^[\w.-]+\/[\w.-]+$/.test(env.REPORT_REPO || "") ? env.REPORT_REPO : DEFAULT_REPO;
  const { title, body } = issueText(report);
  let answer;
  try {
    answer = await fetch(`https://api.github.com/repos/${repo}/issues`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.REPORT_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Frequencies reports (Cloudflare Worker)",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ title, body }),
    });
  } catch (e) {
    return json({ error: "couldn't reach GitHub" }, 502);
  }
  if (!answer.ok) {
    // GitHub's own words stay here (the Worker's log), never with the app.
    console.log(`GitHub answered ${answer.status}`);
    return json({ error: `GitHub answered ${answer.status}` }, 502);
  }
  const issue = await answer.json().catch(() => ({}));
  await store.put(artistKey, 1);
  await store.put(personKey, person + 1);
  await store.put(dayKey, total + 1);
  return json({ ok: true, url: String(issue.html_url || "") }, 201);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/report" && request.method === "POST") {
      return fileReport(request, env);
    }
    if (url.pathname === "/report") return json({ error: "use POST" }, 405);
    return new Response("Frequencies reports.", {
      status: 200, headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  },
};
