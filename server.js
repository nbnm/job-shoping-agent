// JOP backend: node:http + node:sqlite, no dependencies. Run: node server.js
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";
import { SEED, SEED_VERSION } from "./seed.js";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(ROOT, "public");
const DATA = path.resolve(process.env.JOP_DATA_DIR || path.join(ROOT, "data"));
const UPLOADS = path.join(DATA, "uploads");
const PORT = Number(process.env.PORT) || 3000;
const HOST = "127.0.0.1"; // personal data, no auth: never listen on the network
const CATEGORIES = ["Outerwear", "Tops", "Knitwear", "Bottoms", "Shoes", "Accessories"];
// active: in rotation. disliked: still owned, no longer loved. unavailable: lost, damaged or no longer fits. disposed: donated, sold or binned.
const STATUSES = ["active", "disliked", "unavailable", "disposed"];

process.umask(0o077);
fs.mkdirSync(UPLOADS, { recursive: true, mode: 0o700 });
const db = new DatabaseSync(path.join(DATA, "jop.db"));
const itemsSchema = (table) => `
  CREATE TABLE IF NOT EXISTS ${table} (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    brand        TEXT NOT NULL DEFAULT '',
    category     TEXT NOT NULL,
    size         TEXT NOT NULL DEFAULT '',
    color        TEXT NOT NULL DEFAULT '#2b2d33',
    price        REAL CHECK (price >= 0),
    purchased_on TEXT,
    store        TEXT NOT NULL DEFAULT '',
    material     TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    wears        INTEGER NOT NULL DEFAULT 0 CHECK (wears >= 0),
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disliked','unavailable','disposed')),
    status_note  TEXT NOT NULL DEFAULT '',
    favourite    INTEGER NOT NULL DEFAULT 0 CHECK (favourite IN (0,1)),
    disliked     INTEGER CHECK (disliked IN (0,1)),
    photo        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
  );`;
db.exec(`
  PRAGMA journal_mode = WAL;
  ${itemsSchema("items")}
  CREATE INDEX IF NOT EXISTS idx_items_purchased_on ON items (purchased_on);
  -- Kept separately so the email importer can rebuild its purchase rows without
  -- clearing favourites. It reuses the same item IDs for unchanged purchases.
  CREATE TABLE IF NOT EXISTS wardrobe_purchase_favourites (
    item_id TEXT PRIMARY KEY NOT NULL
  );
  CREATE TABLE IF NOT EXISTS wardrobe_purchase_state (
    item_id TEXT PRIMARY KEY NOT NULL,
    disliked INTEGER NOT NULL DEFAULT 0 CHECK (disliked IN (0,1)),
    in_wardrobe INTEGER NOT NULL DEFAULT 1 CHECK (in_wardrobe IN (0,1)),
    ownership_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (ownership_confirmed IN (0,1))
  );
  -- Verified replacements survive an email reimport under the same purchase ID.
  CREATE TABLE IF NOT EXISTS wardrobe_purchase_photo_matches (
    item_id TEXT PRIMARY KEY NOT NULL,
    photo_url TEXT NOT NULL,
    source_url TEXT NOT NULL
  );
`);
// databases created before statuses existed
const have = new Set(db.prepare("PRAGMA table_info(items)").all().map((c) => c.name));
if (!have.has("status")) db.exec("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disliked','unavailable','disposed'))");
if (!have.has("status_note")) db.exec("ALTER TABLE items ADD COLUMN status_note TEXT NOT NULL DEFAULT ''");
if (!have.has("favourite")) db.exec("ALTER TABLE items ADD COLUMN favourite INTEGER NOT NULL DEFAULT 0 CHECK (favourite IN (0,1))");
if (!have.has("disliked")) db.exec("ALTER TABLE items ADD COLUMN disliked INTEGER CHECK (disliked IN (0,1))");
// Older databases required both fields. Copy by column name and retain the ID sequence.
if (db.prepare("PRAGMA table_info(items)").all().some(c => c.notnull && ["price", "purchased_on"].includes(c.name))) {
  db.exec("BEGIN IMMEDIATE");
  try {
    const sequence = db.prepare("SELECT seq FROM sqlite_sequence WHERE name='items'").get()?.seq;
    const schemaObjects = db.prepare("SELECT sql FROM sqlite_schema WHERE tbl_name='items' AND type IN ('index','trigger') AND sql IS NOT NULL").all();
    db.exec(itemsSchema("items_new"));
    const columns = db.prepare("PRAGMA table_info(items_new)").all().map(c => c.name).join(",");
    db.exec(`INSERT INTO items_new (${columns}) SELECT ${columns} FROM items;
      DROP TABLE items; ALTER TABLE items_new RENAME TO items;`);
    if (sequence !== undefined && !db.prepare("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='items'").run(sequence).changes) {
      db.prepare("INSERT INTO sqlite_sequence (name,seq) VALUES ('items',?)").run(sequence);
    }
    for (const {sql} of schemaObjects) db.exec(sql);
    db.exec("COMMIT");
  } catch (e) { db.exec("ROLLBACK"); throw e; }
}
if (!db.prepare("SELECT 1 FROM pragma_table_info('wardrobe_purchase_state') WHERE name = 'ownership_confirmed'").get()) {
  db.exec("ALTER TABLE wardrobe_purchase_state ADD COLUMN ownership_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (ownership_confirmed IN (0,1))");
}

// ---------- data layer ----------
const COLS = ["name", "brand", "category", "size", "color", "price", "purchased_on", "store", "material", "notes", "wears", "status", "status_note", "photo"];
const insertItem = db.prepare(`INSERT INTO items (${COLS.join(",")}) VALUES (${COLS.map(() => "?").join(",")})`);
const DEFAULTS = { brand: "", size: "", color: "#2b2d33", store: "", material: "", notes: "", wears: 0, status: "active", status_note: "", photo: null };
const getItem = db.prepare("SELECT * FROM items WHERE id = ?");

// Sample data is versioned: a database seeded at version N only receives rows added in later versions,
// once, so deleting a sample item never brings it back on restart.
db.exec("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)");
const getPreferences = db.prepare("SELECT value FROM meta WHERE key = 'user_preferences'");
const savePreferences = db.prepare("INSERT INTO meta (key, value) VALUES ('user_preferences', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value");
function seed(from = 0) {
  db.exec("BEGIN");
  try {
    for (const r of SEED) if (r.since > from) insertItem.run(...COLS.map((c) => r[c] ?? null));
    db.prepare("INSERT INTO meta (key, value) VALUES ('seed_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").run(String(SEED_VERSION));
    db.exec("COMMIT");
  } catch (e) { db.exec("ROLLBACK"); throw e; }
}
{
  const stored = db.prepare("SELECT value FROM meta WHERE key = 'seed_version'").get();
  const empty = db.prepare("SELECT COUNT(*) AS n FROM items").get().n === 0;
  const version = stored ? Number(stored.value) : empty ? 0 : 1; // databases from before versioning hold the first 21 rows
  if (!stored && empty && process.env.JOP_SEED === "empty") {
    db.prepare("INSERT INTO meta (key, value) VALUES ('seed_version', ?)").run(String(SEED_VERSION));
  } else if (version < SEED_VERSION) seed(version);
}

// DB row -> JSON the frontend uses
const toApi = (r) => ({
  id: r.id, name: r.name, brand: r.brand, cat: r.category, size: r.size, color: r.color,
  price: r.price, date: r.purchased_on, store: r.store, material: r.material, notes: r.notes,
  wears: r.wears, status: r.status, statusNote: r.status_note, photo: r.photo ? `/${r.photo}?v=${Date.parse(r.updated_at + "Z") || 0}` : "",
  ...inventoryState(r),
});

// Old inventory rows and the sample data can still encode dislike in status.
const inventoryState = r => ({
  favourite: Boolean(r.favourite),
  disliked: Boolean(r.disliked ?? (r.status === "disliked")),
  inWardrobe: r.status !== "unavailable" && r.status !== "disposed",
});
function applyWardrobePatch(current, patch) {
  const next = { ...current, ...patch };
  if (patch.favourite) next.disliked = false;
  if (patch.disliked) next.favourite = false;
  return next;
}
const hasPurchaseHistory = db.prepare("SELECT 1 FROM sqlite_schema WHERE type = 'view' AND name = 'wardrobe_purchase_timeline'");
function purchaseHistory() {
  // A fresh app database has inventory but no imported email history yet.
  if (!hasPurchaseHistory.get()) return [];
  const photo = db.prepare("SELECT 1 FROM pragma_table_info('wardrobe_purchase_timeline') WHERE name = 'photo_url'").get()
    ? "p.photo_url" : "NULL";
  const currencyAssumed = db.prepare("SELECT 1 FROM pragma_table_info('wardrobe_purchase_items') WHERE name = 'currency_assumed'").get()
    ? "(SELECT currency_assumed FROM wardrobe_purchase_items i WHERE i.item_id = p.item_id)" : "0";
  return db.prepare(`
    SELECT p.item_id AS id, order_id AS orderId, order_number AS orderNumber, merchant,
           name, brand, category AS cat, size, color, quantity,
           reported_price AS price, currency, ${currencyAssumed} AS currencyAssumed, price_basis AS priceBasis,
           timeline_date AS date, timeline_date_basis AS dateBasis,
           p.purchased_on AS orderDate, p.purchase_date_basis AS orderDateBasis,
           (SELECT MIN(e.sent_at) FROM wardrobe_order_emails e
             WHERE e.order_id = p.order_id AND e.event_type = 'confirmation') AS confirmationSentAt,
           return_status AS returnStatus, affected_quantity AS affectedQuantity,
           (return_status = 'returned' OR (return_status = 'refunded' AND EXISTS (
             SELECT 1 FROM wardrobe_item_emails ie JOIN wardrobe_order_emails oe
             ON oe.order_id = ie.order_id AND oe.source_seq = ie.source_seq
             WHERE ie.item_id = p.item_id AND oe.event_type = 'returned'
           ))) AS reportedReturned,
           order_return_status AS orderReturnStatus, unassigned_return_emails AS unassignedReturnEmails,
           COALESCE(m.photo_url, ${photo}) AS photo, f.item_id IS NOT NULL AS favourite,
           COALESCE(s.disliked, 0) AS disliked, COALESCE(s.in_wardrobe, 1) AS inWardrobe,
           COALESCE(s.ownership_confirmed, 0) AS ownershipConfirmed
    FROM wardrobe_purchase_timeline p
    LEFT JOIN wardrobe_purchase_favourites f ON p.item_id = f.item_id
    LEFT JOIN wardrobe_purchase_photo_matches m ON p.item_id = m.item_id
    LEFT JOIN wardrobe_purchase_state s ON p.item_id = s.item_id
    ORDER BY timeline_date DESC, order_id, p.item_id
  `).all().map(p => ({...p, currencyAssumed: Boolean(p.currencyAssumed), favourite: Boolean(p.favourite),
    disliked: Boolean(p.disliked), inWardrobe: Boolean(p.inWardrobe), ownershipConfirmed: Boolean(p.ownershipConfirmed),
    reportedReturned: Boolean(p.reportedReturned)}));
}

function savePurchaseWardrobe(id, patch) {
  if (!hasPurchaseHistory.get()) throw new HttpError(404, "No such purchase");
  // Likes and wardrobe state live in separate tables. Save both as one change.
  db.exec("BEGIN IMMEDIATE");
  try {
    const row = db.prepare(`
      SELECT f.item_id IS NOT NULL AS favourite, COALESCE(s.disliked, 0) AS disliked,
             COALESCE(s.in_wardrobe, 1) AS inWardrobe, COALESCE(s.ownership_confirmed, 0) AS ownershipConfirmed
      FROM wardrobe_purchase_items p
      LEFT JOIN wardrobe_purchase_favourites f ON f.item_id = p.item_id
      LEFT JOIN wardrobe_purchase_state s ON s.item_id = p.item_id
      WHERE p.item_id = ?
    `).get(id);
    if (!row) throw new HttpError(404, "No such purchase");
    const { favourite, disliked, inWardrobe } = applyWardrobePatch({
      favourite: Boolean(row.favourite), disliked: Boolean(row.disliked), inWardrobe: Boolean(row.inWardrobe),
    }, patch);
    const ownershipConfirmed = inWardrobe && (patch.ownershipConfirmed ?? (patch.inWardrobe === true || Boolean(row.ownershipConfirmed)));
    if (Number(disliked) !== row.disliked || Number(inWardrobe) !== row.inWardrobe || Number(ownershipConfirmed) !== row.ownershipConfirmed) {
      db.prepare(`INSERT INTO wardrobe_purchase_state (item_id, disliked, in_wardrobe, ownership_confirmed) VALUES (?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET disliked = excluded.disliked, in_wardrobe = excluded.in_wardrobe,
          ownership_confirmed = excluded.ownership_confirmed`)
        .run(id, Number(disliked), Number(inWardrobe), Number(ownershipConfirmed));
    }
    if (Number(favourite) !== row.favourite) {
      if (favourite) db.prepare("INSERT INTO wardrobe_purchase_favourites(item_id) VALUES (?)").run(id);
      else db.prepare("DELETE FROM wardrobe_purchase_favourites WHERE item_id = ?").run(id);
    }
    db.exec("COMMIT");
    return {favourite, disliked, inWardrobe, ownershipConfirmed};
  } catch (e) { db.exec("ROLLBACK"); throw e; }
}

// ---------- validation ----------
class HttpError extends Error { constructor(status, message) { super(message); this.status = status; } }
const text = (max) => (v) => {
  if (typeof v !== "string") throw new Error("must be text");
  v = v.trim(); if (v.length > max) throw new Error(`max ${max} characters`); return v;
};
const PREFERENCE_FIELDS = { colours: 160, location: 120, notes: 2000 };
const EMPTY_PREFERENCES = { colours: "", location: "", notes: "" };
function validatePreferences(body) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new HttpError(400, "JSON object expected");
  const clean = {};
  for (const [key, max] of Object.entries(PREFERENCE_FIELDS)) {
    try { clean[key] = text(max)(body[key]); }
    catch (e) { throw new HttpError(400, `${key}: ${e.message}`); }
  }
  return clean;
}
const FIELDS = {
  name: { col: "name", required: true, parse: (v) => { v = text(120)(v); if (!v) throw new Error("required"); return v; } },
  brand: { col: "brand", parse: text(80) },
  cat: { col: "category", required: true, parse: (v) => { if (!CATEGORIES.includes(v)) throw new Error(`one of ${CATEGORIES.join(", ")}`); return v; } },
  size: { col: "size", parse: text(40) },
  color: { col: "color", parse: (v) => { if (!/^#[0-9a-f]{6}$/i.test(v)) throw new Error("must be #rrggbb"); return v.toLowerCase(); } },
  price: { col: "price", nullable: true, parse: (v) => { if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1e7) throw new Error("must be 0 to 10,000,000"); return v; } },
  date: { col: "purchased_on", nullable: true, parse: (v) => {
    if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v) || new Date(v).toJSON()?.slice(0,10) !== v) throw new Error("must be a valid date (YYYY-MM-DD)"); return v; } },
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
      if (f.nullable && body[key] === null) out[f.col] = null;
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
async function readFavourite(req) {
  if (req.method !== "PUT") throw new HttpError(405, "Method not allowed");
  const body = await readJson(req);
  if (typeof body?.favourite !== "boolean") throw new HttpError(400, "favourite must be true or false");
  return body.favourite;
}
async function readWardrobePatch(req, purchase = false) {
  if (req.method !== "PUT") throw new HttpError(405, "Method not allowed");
  const body = await readJson(req);
  const keys = ["favourite", "disliked", "inWardrobe"];
  if (purchase) keys.push("ownershipConfirmed");
  if (!body || typeof body !== "object" || Array.isArray(body) || !Object.keys(body).length ||
      Object.entries(body).some(([key, value]) => !keys.includes(key) || typeof value !== "boolean")) {
    throw new HttpError(400, `Provide one or more boolean fields: ${keys.join(", ")}`);
  }
  if (body.favourite && body.disliked) throw new HttpError(400, "An item cannot be both liked and disliked");
  return body;
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
  const m = pathname.match(/^\/api\/items(?:\/(\d+))?(?:\/(photo|wear|favourite|wardrobe))?$/);
  const method = req.method;

  if (pathname === "/api/preferences") {
    if (method === "GET") {
      const stored = getPreferences.get();
      return send(res, 200, stored ? JSON.parse(stored.value) : EMPTY_PREFERENCES);
    }
    if (method === "PUT") {
      const preferences = validatePreferences(await readJson(req));
      savePreferences.run(JSON.stringify(preferences));
      return send(res, 200, preferences);
    }
    throw new HttpError(405, "Method not allowed");
  }
  if (pathname === "/api/purchases") {
    if (method !== "GET") throw new HttpError(405, "Method not allowed");
    return send(res, 200, purchaseHistory());
  }
  const purchaseAction = pathname.match(/^\/api\/purchases\/([a-f\d]{24})\/(favourite|wardrobe)$/);
  if (purchaseAction) {
    const [, id, action] = purchaseAction;
    const patch = action === "wardrobe" ? await readWardrobePatch(req, true) : { favourite: await readFavourite(req) };
    return send(res, 200, savePurchaseWardrobe(id, patch));
  }
  if (pathname === "/api/items" && method === "GET") {
    return send(res, 200, db.prepare("SELECT * FROM items ORDER BY purchased_on, id").all().map(toApi));
  }
  if (pathname === "/api/items" && method === "POST") {
    const v = validate(await readJson(req), false);
    const id = Number(insertItem.run(...COLS.map((c) => v[c] ?? DEFAULTS[c] ?? null)).lastInsertRowid);
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

  if (m[2] === "wardrobe" || m[2] === "favourite") {
    const patch = m[2] === "wardrobe" ? await readWardrobePatch(req) : {favourite: await readFavourite(req)};
    const current = getItem.get(id);
    if (!current) throw new HttpError(404, "No such item");
    const previous = inventoryState(current);
    const { favourite, disliked, inWardrobe } = applyWardrobePatch(previous, patch);
    const status = inWardrobe ? disliked ? "disliked" : "active"
      : previous.inWardrobe ? "disposed" : current.status;
    db.prepare("UPDATE items SET status = ?, favourite = ?, disliked = ? WHERE id = ?").run(status, Number(favourite), Number(disliked), id);
    return send(res, 200, {status, favourite, disliked, inWardrobe});
  }
  if (!m[2] && method === "GET") return send(res, 200, toApi(row));
  if (!m[2] && method === "PUT") {
    const v = validate(await readJson(req), true);
    if (v.status) {
      const current = getItem.get(id);
      if (!current) throw new HttpError(404, "No such item");
      v.disliked = Number(v.status === "disliked" || (v.status !== "active" && inventoryState(current).disliked));
      if (v.disliked) v.favourite = 0;
    }
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
    if (!["GET", "HEAD"].includes(req.method) && req.headers.origin &&
        ![...allowedHosts].some(host => req.headers.origin === `http://${host}`)) {
      throw new HttpError(403, "Cross-origin writes are not allowed");
    }
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
