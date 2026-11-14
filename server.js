// JOP backend: node:http + node:sqlite, no dependencies. Run: node server.js
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { SEED } from "./seed.js";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, "public");
const DATA = path.join(ROOT, "data");
const UPLOADS = path.join(DATA, "uploads");
const PORT = Number(process.env.PORT) || 3000;
const HOST = "127.0.0.1"; // personal data, no auth: never listen on the network
const CATEGORIES = ["Outerwear", "Tops", "Knitwear", "Bottoms", "Shoes", "Accessories"];
// active: in rotation. disliked: still owned, no longer loved. unavailable: lost, damaged or no longer fits. disposed: donated, sold or binned.
const STATUSES = ["active", "disliked", "unavailable", "disposed"];

fs.mkdirSync(UPLOADS, { recursive: true });
const db = new DatabaseSync(path.join(DATA, "jop.db"));
db.exec(`
  PRAGMA journal_mode = WAL;
  CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    brand        TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL,
    size         TEXT NOT NULL DEFAULT '',
    color        TEXT NOT NULL DEFAULT '#2b2d33',
    price        REAL NOT NULL CHECK (price >= 0),
    purchased_on TEXT NOT NULL,
    store        TEXT NOT NULL DEFAULT '',
    material     TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    wears        INTEGER NOT NULL DEFAULT 0 CHECK (wears >= 0),
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disliked','unavailable','disposed')),
    status_note  TEXT NOT NULL DEFAULT '',
    photo        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE INDEX IF NOT EXISTS idx_items_purchased_on ON items (purchased_on);
`);
// databases created before statuses existed
const have = new Set(db.prepare("PRAGMA table_info(items)").all().map((c) => c.name));
if (!have.has("status")) db.exec("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disliked','unavailable','disposed'))");
if (!have.has("status_note")) db.exec("ALTER TABLE items ADD COLUMN status_note TEXT NOT NULL DEFAULT ''");

// ---------- data layer ----------
const COLS = ["name", "brand", "category", "size", "color", "price", "purchased_on", "store", "material", "notes", "wears", "status", "status_note", "photo"];
const insertItem = db.prepare(`INSERT INTO items (${COLS.join(",")}) VALUES (${COLS.map(() => "?").join(",")})`);
const DEFAULTS = { brand: "", size: "", color: "#2b2d33", store: "", material: "", notes: "", wears: 0, status: "active", status_note: "", photo: null };
const getItem = db.prepare("SELECT * FROM items WHERE id = ?");

function seed() {
  db.exec("BEGIN");
  try {
    for (const r of SEED) insertItem.run(...COLS.map((c) => r[c] ?? null));
    db.exec("COMMIT");
  } catch (e) { db.exec("ROLLBACK"); throw e; }
}
if (db.prepare("SELECT COUNT(*) AS n FROM items").get().n === 0) seed();

// DB row -> JSON the frontend uses
const toApi = (r) => ({
  id: r.id, name: r.name, brand: r.brand, cat: r.category, size: r.size, color: r.color,
  price: r.price, date: r.purchased_on, store: r.store, material: r.material, notes: r.notes,
  wears: r.wears, status: r.status, statusNote: r.status_note, photo: r.photo ? `/${r.photo}?v=${Date.parse(r.updated_at + "Z") || 0}` : "",
});

// ---------- validation ----------
class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
const text = (max) => (v) => {
  if (typeof v !== "string") throw new Error("must be text");
  v = v.trim(); if (v.length > max) throw new Error(`max ${max} characters`); return v;
};
const FIELDS = {
  name: { col: "name", required: true, parse: (v) => { v = text(120)(v); if (!v) throw new Error("required"); return v; } },
  brand: { col: "brand", parse: text(80) },
  cat: { col: "category", required: true, parse: (v) => { if (!CATEGORIES.includes(v)) throw new Error(`one of ${CATEGORIES.join(", ")}`); return v; } },
  size: { col: "size", parse: text(40) },
  color: { col: "color", parse: (v) => { if (!/^#[0-9a-f]{6}$/i.test(v)) throw new Error("must be #rrggbb"); return v.toLowerCase(); } },
  price: { col: "price", required: true, parse: (v) => { v = Number(v); if (!Number.isFinite(v) || v < 0 || v > 1e7) throw new Error("must be 0 to 10,000,000"); return v; } },
  date: { col: "purchased_on", required: true, parse: (v) => {
    if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v) || Number.isNaN(Date.parse(v))) throw new Error("must be YYYY-MM-DD"); return v; } },
  store: { col: "store", parse: text(80) },
  material: { col: "material", parse: text(80) },
  notes: { col: "notes", parse: text(300) },
  status: { col: "status", parse: (v) => { if (!STATUSES.includes(v)) throw new Error(`one of ${STATUSES.join(", ")}`); return v; } },
  statusNote: { col: "status_note", parse: text(120) },
  wears: { col: "wears", parse: (v) => { if (!Number.isInteger(v) || v < 0) throw new Error("must be a whole number >= 0"); return v; } },
};
function validate(body, partial) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new HttpError(400, "JSON object expected");
  const out = {}, errors = [];
  for (const [key, f] of Object.entries(FIELDS)) {
    if (body[key] === undefined || body[key] === null) {
      if (f.required && !partial) errors.push(`${key}: required`);
      continue;
    }
    try { out[f.col] = f.parse(body[key]); } catch (e) { errors.push(`${key}: ${e.message}`); }
  }
  if (errors.length) throw new HttpError(400, errors.join("; "));
  return out;
}

// ---------- http helpers ----------
const send = (res, status, body, headers = {}) => {
  const json = body !== undefined && !Buffer.isBuffer(body);
  res.writeHead(status, { ...(json ? { "Content-Type": "application/json; charset=utf-8" } : {}), "Cache-Control": "no-store", ...headers });
  res.end(json ? JSON.stringify(body) : body);
};
async function readBody(req, limit) {
  const chunks = []; let size = 0;
  for await (const c of req) {
    size += c.length;
    if (size > limit) throw new HttpError(413, "Body too large");
    chunks.push(c);
  }
  return Buffer.concat(chunks);
}
async function readJson(req) {
  // Requiring application/json forces a CORS preflight for cross-site requests, which we never answer.
  if (!(req.headers["content-type"] || "").startsWith("application/json")) throw new HttpError(415, "Content-Type must be application/json");
  try { return JSON.parse((await readBody(req, 64 * 1024)).toString("utf8")); }
  catch (e) { if (e instanceof HttpError) throw e; throw new HttpError(400, "Invalid JSON"); }
}
function imageExt(buf) {
  if (buf.length > 12 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return "jpg";
  if (buf.length > 12 && buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) return "png";
  if (buf.length > 12 && buf.toString("ascii", 0, 4) === "RIFF" && buf.toString("ascii", 8, 12) === "WEBP") return "webp";
  return null;
}
const removeUpload = (photo) => {
  if (photo?.startsWith("uploads/")) fs.rmSync(path.join(DATA, photo), { force: true });
};

// ---------- API ----------
async function api(req, res, pathname) {
  const m = pathname.match(/^\/api\/items(?:\/(\d+))?(?:\/(photo|wear))?$/);
  const method = req.method;

  if (pathname === "/api/items" && method === "GET") {
    return send(res, 200, db.prepare("SELECT * FROM items ORDER BY purchased_on, id").all().map(toApi));
  }
  if (pathname === "/api/items" && method === "POST") {
    const v = validate(await readJson(req), false);
    const id = Number(insertItem.run(...COLS.map((c) => v[c] ?? DEFAULTS[c])).lastInsertRowid);
    return send(res, 201, toApi(getItem.get(id)));
  }
  if (pathname === "/api/reset" && method === "POST") {
    await readJson(req);
    for (const r of db.prepare("SELECT photo FROM items").all()) removeUpload(r.photo);
    db.exec("DELETE FROM items; DELETE FROM sqlite_sequence WHERE name = 'items'");
    seed();
    return send(res, 200, { ok: true });
  }
  if (!m || !m[1]) throw new HttpError(404, "Not found");

  const id = Number(m[1]);
  const row = getItem.get(id);
  if (!row) throw new HttpError(404, "No such item");

  if (!m[2] && method === "GET") return send(res, 200, toApi(row));
  if (!m[2] && method === "PUT") {
    const v = validate(await readJson(req), true);
    const keys = Object.keys(v);
    if (keys.length) {
      db.prepare(`UPDATE items SET ${keys.map((k) => `${k} = ?`).join(", ")}, updated_at = datetime('now') WHERE id = ?`).run(...keys.map((k) => v[k]), id);
    }
    return send(res, 200, toApi(getItem.get(id)));
  }
  if (!m[2] && method === "DELETE") {
    db.prepare("DELETE FROM items WHERE id = ?").run(id);
    removeUpload(row.photo);
    return send(res, 204, undefined);
  }
  if (m[2] === "wear" && method === "POST") {
    await readJson(req);
    db.prepare("UPDATE items SET wears = wears + 1, updated_at = datetime('now') WHERE id = ?").run(id);
    return send(res, 200, toApi(getItem.get(id)));
  }
  if (m[2] === "photo" && method === "POST") {
    const buf = await readBody(req, 5 * 1024 * 1024);
    const ext = imageExt(buf);
    if (!ext) throw new HttpError(415, "Expected a JPEG, PNG or WebP image");
    const file = `uploads/${id}.${ext}`;
    fs.writeFileSync(path.join(DATA, file), buf);
    if (row.photo && row.photo !== file) removeUpload(row.photo);
    db.prepare("UPDATE items SET photo = ?, updated_at = datetime('now') WHERE id = ?").run(file, id);
    return send(res, 200, toApi(getItem.get(id)));
  }
  throw new HttpError(405, "Method not allowed");
}

// ---------- static files ----------
const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon" };
function serveStatic(res, pathname) {
  const [base, rel] = pathname.startsWith("/uploads/") ? [UPLOADS, pathname.slice(9)] : [PUBLIC, pathname === "/" ? "index.html" : pathname.slice(1)];
  const file = path.resolve(base, rel);
  if (path.relative(base, file).startsWith("..") || !fs.existsSync(file) || !fs.statSync(file).isFile()) throw new HttpError(404, "Not found");
  res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream", "Cache-Control": "no-cache" });
  fs.createReadStream(file).pipe(res);
}

// ---------- server ----------
const allowedHosts = new Set([`127.0.0.1:${PORT}`, `localhost:${PORT}`]);
http.createServer(async (req, res) => {
  try {
    if (!allowedHosts.has(req.headers.host)) throw new HttpError(403, "Bad host"); // blocks DNS rebinding
    const { pathname } = new URL(req.url, "http://localhost");
    let decoded;
    try { decoded = decodeURIComponent(pathname); } catch { throw new HttpError(400, "Bad path"); }
    if (pathname.startsWith("/api/")) return await api(req, res, pathname);
    if (req.method !== "GET" && req.method !== "HEAD") throw new HttpError(405, "Method not allowed");
    serveStatic(res, decoded);
  } catch (e) {
    if (!(e instanceof HttpError)) console.error(e);
    if (res.headersSent) return res.end();
    const status = e instanceof HttpError ? e.status : 500;
    send(res, status, { error: e instanceof HttpError ? e.message : "Server error" });
  }
}).listen(PORT, HOST, () => console.log(`jop listening on http://${HOST}:${PORT}  (db: ${path.relative(process.cwd(), path.join(DATA, "jop.db")) || "data/jop.db"})`));
