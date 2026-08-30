const DEFAULT_URL = "https://www.dbs.com.sg/iwov-resources/media/pdf/deposits/promotions/paylah/saturdays/dbs-paylah-saturdays-participating-merchants.pdf";
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

async function setting(db, key, fallback = "") {
  const row = await db.prepare("SELECT value FROM settings WHERE key = ?").bind(key).first();
  return row?.value ?? fallback;
}
async function active(db) {
  return db.prepare("SELECT s.* FROM snapshots s JOIN settings x ON x.key='active_snapshot_id' AND x.value=s.id LIMIT 1").first();
}
async function bundledRecords(env, requestUrl) {
  const assetUrl = new URL("/dbs-paylah-merchants.json", requestUrl);
  const response = await env.ASSETS.fetch(new Request(assetUrl));
  if (!response.ok) return [];
  const data = await response.json();
  return Array.isArray(data) ? data : (data.records || []);
}
async function bundledMeta(env, requestUrl) {
  const response = await env.ASSETS.fetch(new Request(new URL("/index-meta.json", requestUrl)));
  return response.ok ? response.json() : {};
}
function sourceUrl(value) {
  const url = new URL(value);
  if (url.protocol !== "https:") throw new Error("Only HTTPS PDF URLs are allowed");
  return url;
}

async function api(request, env, url) {
  if (!env.DB) return json({ error: "Database binding DB is not configured" }, 503);
  const db = env.DB;

  if (url.pathname === "/api/status" && request.method === "GET") {
    const current = await active(db);
    const bundled = current ? [] : await bundledRecords(env, url);
    const meta = current ? {} : await bundledMeta(env, url);
    return json({
      pdf_exists: Boolean(current), json_exists: Boolean(current),
      pdf_bytes: current?.pdf_bytes ?? null, record_count: current?.record_count ?? bundled.length,
      indexed_at: current?.created_at ?? meta.indexed_at ?? null, pdf_hash: current?.pdf_hash ?? meta.pdf_hash ?? null,
      source_url: await setting(db, "source_url", DEFAULT_URL), database: true, bundled_fallback: !current,
    });
  }

  if (url.pathname === "/api/pdf" && request.method === "GET") {
    try {
      const target = sourceUrl(url.searchParams.get("url") || await setting(db, "source_url", DEFAULT_URL));
      const response = await fetch(target, { headers: { "user-agent": "Mozilla/5.0" } });
      if (!response.ok) return json({ error: `PDF download failed (${response.status})` }, 502);
      const length = Number(response.headers.get("content-length") || 0);
      if (length > 40 * 1024 * 1024) return json({ error: "PDF exceeds the 40 MB limit" }, 413);
      const headers = { "content-type": "application/pdf", "cache-control": "no-store" };
      if (length) headers["content-length"] = String(length);
      return new Response(response.body, { headers });
    } catch (error) { return json({ error: error.message }, 400); }
  }

  if (url.pathname === "/api/refresh/check" && request.method === "POST") {
    const body = await request.json();
    if (!/^[a-f0-9]{64}$/.test(body.hash || "")) return json({ error: "Invalid SHA-256 hash" }, 400);
    const current = await active(db);
    const bundled = current ? [] : await bundledRecords(env, url);
    return json({ changed: current?.pdf_hash !== body.hash, current_hash: current?.pdf_hash ?? null, current_count: current?.record_count ?? bundled.length });
  }

  if (url.pathname === "/api/refresh/begin" && request.method === "POST") {
    const body = await request.json();
    if (!/^[a-f0-9]{64}$/.test(body.hash || "")) return json({ error: "Invalid SHA-256 hash" }, 400);
    const current = await active(db);
    if (current?.pdf_hash === body.hash) return json({ changed: false, message: "PDF unchanged; extraction skipped." });
    const previous = await db.prepare("SELECT id FROM snapshots WHERE pdf_hash=? AND status!='active'").bind(body.hash).first();
    if (previous) await db.prepare("DELETE FROM snapshots WHERE id=?").bind(previous.id).run();
    const id = crypto.randomUUID();
    const source = body.source_url ? sourceUrl(body.source_url).toString() : null;
    await db.prepare("INSERT INTO snapshots(id,pdf_hash,source_url,pdf_bytes,status,created_at) VALUES(?,?,?,?, 'staging', ?)")
      .bind(id, body.hash, source, Number(body.pdf_bytes || 0), Math.floor(Date.now() / 1000)).run();
    return json({ changed: true, upload_id: id });
  }

  const chunk = url.pathname.match(/^\/api\/refresh\/([^/]+)\/chunk$/);
  if (chunk && request.method === "POST") {
    const body = await request.json();
    const records = Array.isArray(body.records) ? body.records : [];
    if (!records.length || records.length > 100) return json({ error: "Each chunk must contain 1-100 records" }, 400);
    const staging = await db.prepare("SELECT id FROM snapshots WHERE id=? AND status='staging'").bind(chunk[1]).first();
    if (!staging) return json({ error: "Staging refresh not found" }, 404);
    const offset = Number(body.offset || 0);
    const insert = db.prepare("INSERT INTO merchants(snapshot_id,seq,name,address,unit,postal_code,category,kind,venue) VALUES(?,?,?,?,?,?,?,?,?)");
    await db.batch(records.map((r, i) => insert.bind(chunk[1], offset + i, String(r.name || ""), String(r.address || ""), String(r.unit || ""), String(r.postal_code || ""), String(r.category || "heartland"), String(r.kind || "merchant"), String(r.venue || ""))));
    return json({ stored: records.length });
  }

  const commit = url.pathname.match(/^\/api\/refresh\/([^/]+)\/commit$/);
  if (commit && request.method === "POST") {
    const snapshot = await db.prepare("SELECT * FROM snapshots WHERE id=? AND status='staging'").bind(commit[1]).first();
    if (!snapshot) return json({ error: "Staging refresh not found" }, 404);
    const count = await db.prepare("SELECT COUNT(*) count FROM merchants WHERE snapshot_id=?").bind(snapshot.id).first();
    if (!count?.count) return json({ error: "Cannot activate an empty refresh" }, 400);
    const statements = [
      db.prepare("UPDATE snapshots SET status='archived' WHERE status='active'"),
      db.prepare("UPDATE snapshots SET status='active', record_count=? WHERE id=?").bind(count.count, snapshot.id),
      db.prepare("INSERT INTO settings(key,value) VALUES('active_snapshot_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").bind(snapshot.id),
    ];
    if (snapshot.source_url) statements.push(db.prepare("INSERT INTO settings(key,value) VALUES('source_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value").bind(snapshot.source_url));
    await db.batch(statements);
    return json({ changed: true, record_count: count.count, message: "PDF changed; database index activated." });
  }

  if (url.pathname === "/api/search" && request.method === "GET") {
    const current = await active(db);
    const postal = (url.searchParams.get("postal") || "").trim();
    const name = (url.searchParams.get("name") || "").trim();
    const address = (url.searchParams.get("address") || "").trim();
    if (!postal && !name && !address) return json({ error: "provide postal, name, and/or address" }, 400);
    if (!current) {
      const records = await bundledRecords(env, url);
      const results = records.filter((record) =>
        (!postal || record.postal_code === postal) &&
        (!name || String(record.name || "").toLowerCase().includes(name.toLowerCase())) &&
        (!address || String(record.address || "").toLowerCase().includes(address.toLowerCase()))
      ).slice(0, 500);
      return json({ count: results.length, results });
    }
    const clauses = ["snapshot_id=?"]; const values = [current.id];
    if (postal) { clauses.push("postal_code=?"); values.push(postal); }
    if (name) { clauses.push("LOWER(name) LIKE ?"); values.push(`%${name.toLowerCase()}%`); }
    if (address) { clauses.push("LOWER(address) LIKE ?"); values.push(`%${address.toLowerCase()}%`); }
    const result = await db.prepare(`SELECT name,address,unit,postal_code,category,kind,venue FROM merchants WHERE ${clauses.join(" AND ")} LIMIT 500`).bind(...values).all();
    return json({ count: result.results.length, results: result.results });
  }
  return json({ error: "not found" }, 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try { return await api(request, env, url); }
      catch (error) { return json({ error: error.message || String(error) }, 500); }
    }
    const response = await env.ASSETS.fetch(request);
    const acceptsHtml = request.headers.get("accept")?.includes("text/html");
    if (response.status !== 404 || !acceptsHtml || !["GET", "HEAD"].includes(request.method)) return response;
    const indexUrl = new URL(request.url); indexUrl.pathname = "/index.html"; indexUrl.search = "";
    return env.ASSETS.fetch(new Request(indexUrl, request));
  },
};
