// Frequencies picture links (build 128): a Cloudflare Worker in front of
// an R2 bucket. The app uploads a station picture somebody chose from
// their own files when they share it, and the QR code carries the link,
// so the other end gets the full picture instead of the code's 64 px copy.
//
//   POST /upload        body = the picture (PNG, JPEG, GIF or WebP, at most
//                       2 MB) -> {"url": "https://<this worker>/a/<sha256>.<ext>"}
//   GET  /a/<name>      the picture
//
// Setup (Cloudflare dashboard): an R2 bucket bound to this Worker as ART.
// Optional: a rate limit binding named UPLOAD_LIMIT (uploads per person
// per minute). To take a picture down, delete it from the bucket.

const MAX_BYTES = 2 * 1024 * 1024;

const TYPES = [
  { ext: "png", type: "image/png", test: (b) => b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4e && b[3] === 0x47 },
  { ext: "jpg", type: "image/jpeg", test: (b) => b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff },
  { ext: "gif", type: "image/gif", test: (b) => b[0] === 0x47 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x38 },
  { ext: "webp", type: "image/webp",
    test: (b) => b[0] === 0x52 && b[1] === 0x49 && b[2] === 0x46 && b[3] === 0x46 &&
                 b[8] === 0x57 && b[9] === 0x45 && b[10] === 0x42 && b[11] === 0x50 },
];

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });
}

async function readLimited(request) {
  const declared = Number(request.headers.get("Content-Length") || 0);
  if (declared > MAX_BYTES) return null;
  const reader = request.body.getReader();
  const parts = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > MAX_BYTES) return null;
    parts.push(value);
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.length; }
  return bytes;
}

async function upload(request, env, origin) {
  if (env.UPLOAD_LIMIT) {
    const who = request.headers.get("CF-Connecting-IP") || "unknown";
    const { success } = await env.UPLOAD_LIMIT.limit({ key: who });
    if (!success) return json({ error: "too many uploads, try again later" }, 429);
  }
  if (!request.body) return json({ error: "no picture" }, 400);
  const bytes = await readLimited(request);
  if (bytes === null) return json({ error: "picture too big (2 MB at most)" }, 413);
  if (bytes.length < 12) return json({ error: "no picture" }, 400);
  const kind = TYPES.find((t) => t.test(bytes));
  if (!kind) return json({ error: "not a PNG, JPEG, GIF or WebP picture" }, 415);

  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  const hex = [...digest].map((b) => b.toString(16).padStart(2, "0")).join("");
  const name = `${hex}.${kind.ext}`;
  // The same picture always lands at the same address: nothing new to store.
  if (!(await env.ART.head(name))) {
    await env.ART.put(name, bytes, { httpMetadata: { contentType: kind.type } });
  }
  return json({ url: `${origin}/a/${name}` });
}

async function picture(name, env) {
  if (!/^[0-9a-f]{64}\.(png|jpg|gif|webp)$/.test(name)) return new Response("Not found", { status: 404 });
  const object = await env.ART.get(name);
  if (!object) return new Response("Not found", { status: 404 });
  return new Response(object.body, {
    headers: {
      "Content-Type": (object.httpMetadata && object.httpMetadata.contentType) || "application/octet-stream",
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/upload") {
      if (request.method !== "POST") return json({ error: "use POST" }, 405);
      return upload(request, env, url.origin);
    }
    if (url.pathname.startsWith("/a/") && (request.method === "GET" || request.method === "HEAD")) {
      return picture(url.pathname.slice(3), env);
    }
    return new Response("Frequencies picture links.", { status: 200 });
  },
};
