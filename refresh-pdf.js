import * as pdfjs from "pdfjs-dist/legacy/build/pdf.mjs";
import pdfWorker from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

const SECTION_ALIASES = {
  "Wet Market": "wet_markets", "Wet Markets": "wet_markets",
  "Hawker Centres": "hawker_centres", Coffeeshops: "coffeeshops",
  "Industrial Canteens": "industrial_canteens",
};
const addressRe = /^(.+?),\s*(?:S(\d{5,6})|SINGAPORE\s+(\d{6}))\s*$/i;
const unitRe = /#?\s?\d{1,3}-\d{1,4}[A-Za-z]?(?:\/\d{1,4}[A-Za-z]?)*$/;
const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
const postal = (value) => value.length === 5 ? `0${value}` : value;

function pageLines(content) {
  const groups = [];
  for (const item of content.items) {
    const text = clean(item.str);
    if (!text) continue;
    const x = item.transform[4];
    const y = item.transform[5];
    let line = groups.find((candidate) => Math.abs(candidate.y - y) <= 2.5);
    if (!line) { line = { y, items: [] }; groups.push(line); }
    line.items.push({ x, text });
  }
  return groups.sort((a, b) => b.y - a.y).map((line) => {
    line.items.sort((a, b) => a.x - b.x);
    return { ...line, x: line.items[0].x, text: clean(line.items.map((item) => item.text).join(" ")) };
  });
}

function parseHeartland(lines, state) {
  const records = [];
  const header = lines.find((line) => line.text.includes("Stall Name") && line.text.includes("Postal Code"));
  if (header) {
    const locate = (needles, fallback) => header.items.find((item) => needles.some((needle) => item.text.includes(needle)))?.x ?? fallback;
    state.heartlandColumns = {
      addressX: locate(["Stall Address", "Address"], 210),
      unitX: locate(["Unit No.", "Unit"], 420),
      postalX: locate(["Postal Code", "Postal"], 500),
    };
  }
  if (!state.heartlandColumns) return records;
  const { addressX, unitX } = state.heartlandColumns;
  const postalX = Math.max(state.heartlandColumns.postalX, unitX + 25);
  for (const line of lines) {
    const postalItem = [...line.items].reverse().find((item) => /^\d{6}$/.test(item.text));
    if (!postalItem || postalItem.x < postalX - 30) continue;
    const name = clean(line.items.filter((item) => item.x < addressX - 4).map((item) => item.text).join(" "));
    const address = clean(line.items.filter((item) => item.x >= addressX - 4 && item.x < unitX - 4).map((item) => item.text).join(" "));
    const unit = clean(line.items.filter((item) => item.x >= unitX - 4 && item.x < postalItem.x - 4).map((item) => item.text).join(" "));
    if (name && name !== "Stall Name") records.push({ name, address, unit, postal_code: postalItem.text, category: "heartland", kind: "merchant", venue: "" });
  }
  return records;
}

function parseSummary(lines, state) {
  const records = [];
  for (const line of lines) {
    if (SECTION_ALIASES[line.text]) { state.section = SECTION_ALIASES[line.text]; continue; }
    const match = line.text.match(/^(.*?)\s+(.+?,\s*(?:S\d{5,6}|SINGAPORE\s+\d{6}))$/i);
    if (!match || match[1] === "Hawker Type") continue;
    const address = match[2].match(addressRe);
    if (!address) continue;
    records.push({ name: clean(match[1]), address: clean(address[1]), unit: "", postal_code: postal(address[2] || address[3]), category: state.section || "hawker", kind: "venue", venue: "", _summary: true });
  }
  return records;
}

function parseDetails(lines, state) {
  const records = [];
  let pending = "";
  const flush = () => {
    if (!state.venue) return;
    records.push({ name: state.venue.name, address: state.venue.address, unit: "", postal_code: state.venue.postal_code, category: state.venue.category, kind: "venue", venue: "", _summary: false });
    state.venue = null;
  };
  for (const line of lines) {
    const text = line.text;
    if (SECTION_ALIASES[text] || text === "Hawker Type") { flush(); state.section = SECTION_ALIASES[text] || state.section; pending = ""; continue; }
    if (!text || text === "Back to top" || /^\d+$/.test(text)) continue;
    const address = text.match(addressRe);
    if (address && line.x >= 65) {
      flush();
      state.venue = { name: pending || clean(address[1]), address: clean(address[1]), postal_code: postal(address[2] || address[3]), category: state.section || "hawker" };
      pending = "";
      continue;
    }
    if (line.x >= 65) {
      if (!unitRe.test(text)) pending = clean(`${pending} ${text}`);
      continue;
    }
    if (state.venue) {
      const unit = text.match(unitRe)?.[0] || "";
      const name = clean(unit ? text.slice(0, text.length - unit.length) : text);
      if (name) records.push({ name, address: state.venue.address, unit: clean(unit), postal_code: state.venue.postal_code, category: state.venue.category, kind: "stall", venue: state.venue.name });
    }
  }
  flush();
  return records;
}

export async function extractPdf(bytes, progress) {
  const document = await pdfjs.getDocument({ data: bytes }).promise;
  const records = [];
  const state = { section: null, venue: null, heartlandColumns: null };
  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);
    const lines = pageLines(await page.getTextContent());
    if (pageNumber >= 4 && pageNumber <= 207) records.push(...parseHeartland(lines, state));
    else if (pageNumber >= 208 && pageNumber <= 226) records.push(...parseSummary(lines, state));
    else if (pageNumber >= 227 && pageNumber <= 418) records.push(...parseDetails(lines, state));
    if (pageNumber % 5 === 0 || pageNumber === document.numPages) progress?.(`Extracting page ${pageNumber}/${document.numPages}`);
    page.cleanup();
  }
  await document.cleanup();
  const detailedVenuePostals = new Set(records.filter((record) => record.kind === "venue" && record._summary === false).map((record) => record.postal_code));
  const seen = new Set();
  return records.filter((record) => !(record._summary && detailedVenuePostals.has(record.postal_code))).filter((record) => {
    const key = [record.name, record.address, record.unit, record.postal_code, record.category, record.kind, record.venue].join("\u001f");
    if (seen.has(key)) return false;
    seen.add(key); return true;
  }).map(({ _summary, ...record }) => record);
}

async function requestJson(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

export async function refreshPdfIndex({ file, url, force = false, onProgress }) {
  onProgress?.(file ? "Reading uploaded PDF" : "Downloading PDF");
  const response = file ? null : await fetch(`/api/pdf?url=${encodeURIComponent(url)}`, { cache: "no-store" });
  if (response && !response.ok) {
    let message = `PDF download failed (${response.status})`;
    try { message = (await response.json()).error || message; } catch {}
    throw new Error(message);
  }
  const bytes = new Uint8Array(await (file || response).arrayBuffer());
  if (bytes.length < 5 || String.fromCharCode(...bytes.slice(0, 5)) !== "%PDF-") throw new Error("Selected source is not a PDF");
  onProgress?.("Checking SHA-256 hash");
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hash = [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
  const check = await requestJson("/api/refresh/check", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ hash }) });
  if (!check.changed && !force) return { changed: false, message: "PDF unchanged; extraction skipped.", pdf_hash: hash };
  const records = await extractPdf(bytes, onProgress);
  if (records.length < 100) throw new Error(`Extraction produced only ${records.length} records; existing data was kept`);
  if (check.current_count && (records.length < check.current_count * 0.8 || records.length > check.current_count * 1.2)) {
    throw new Error(`Extraction count ${records.length.toLocaleString()} is outside the safe range for the current ${check.current_count.toLocaleString()} records; existing data was kept`);
  }
  const begin = await requestJson("/api/refresh/begin", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ hash, pdf_bytes: bytes.length, source_url: file ? null : url }) });
  if (!begin.changed) return begin;
  for (let offset = 0; offset < records.length; offset += 100) {
    onProgress?.(`Storing ${Math.min(offset + 100, records.length).toLocaleString()}/${records.length.toLocaleString()} records`);
    await requestJson(`/api/refresh/${begin.upload_id}/chunk`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ offset, records: records.slice(offset, offset + 100) }) });
  }
  return requestJson(`/api/refresh/${begin.upload_id}/commit`, { method: "POST" });
}

window.refreshPdfIndex = refreshPdfIndex;
