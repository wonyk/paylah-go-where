#!/usr/bin/env python3
"""Simple HTML front-end + API for the DBS PayLah! merchants parser.

Provides:
  * Re-indexing with hash-based change detection (PDF sha256 vs previous run).
  * Querying by postal code.
  * Display via a self-contained HTML page.

Run:
  python3 server.py [--port 8000] [--host 127.0.0.1]

Endpoints:
  GET  /                    -> HTML page
  GET  /api/status          -> current index status (hash, counts, timestamps)
  POST /api/reindex         -> re-download PDF, compare hash, re-parse if changed
  GET  /api/search?postal=X -> search merchants by 6-digit postal code
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import parse_merchants as pm

_DATA_DIR = os.environ.get("APP_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
META_PATH = os.path.join(_DATA_DIR, "index_meta.json")
INDEX_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_meta():
    if not os.path.exists(META_PATH):
        return {}
    with open(META_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_meta(meta):
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def download_pdf():
    print("Downloading PDF ...", file=sys.stderr)
    req = urllib.request.Request(pm.PDF_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(pm.CACHE_PATH, "wb") as f:
        f.write(resp.read())


def reindex(force=False):
    """Download the PDF, compare its sha256 with the previous extract, and
    re-parse only when the content has changed (or when forced)."""
    download_pdf()
    pdf_hash = sha256_file(pm.CACHE_PATH)
    meta = load_meta()
    previous_hash = meta.get("pdf_hash")

    if not force and previous_hash == pdf_hash:
        records = pm.load_records(pm.CACHE_PATH)
        return {
            "changed": False,
            "pdf_hash": pdf_hash,
            "previous_hash": previous_hash,
            "message": "PDF unchanged since the previous extract. No re-parse needed.",
            "record_count": len(records),
        }

    records = pm.load_records(pm.CACHE_PATH, refresh=True)
    meta = {
        "pdf_hash": pdf_hash,
        "indexed_at": int(__import__("time").time()),
        "record_count": len(records),
        "pdf_bytes": os.path.getsize(pm.CACHE_PATH),
        "force": bool(force),
    }
    save_meta(meta)
    return {
        "changed": True,
        "pdf_hash": pdf_hash,
        "previous_hash": previous_hash,
        "message": "PDF changed; re-indexed successfully."
        if previous_hash else "Initial index created.",
        "record_count": len(records),
    }


def status():
    meta = load_meta()
    records = pm.load_records(pm.CACHE_PATH) if os.path.exists(pm.JSON_CACHE_PATH) else []
    return {
        "pdf_exists": os.path.exists(pm.CACHE_PATH),
        "json_exists": os.path.exists(pm.JSON_CACHE_PATH),
        "pdf_bytes": os.path.getsize(pm.CACHE_PATH) if os.path.exists(pm.CACHE_PATH) else None,
        "record_count": len(records),
        "indexed_at": meta.get("indexed_at"),
        "pdf_hash": meta.get("pdf_hash"),
        "last_force": meta.get("force", False),
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self):
        with open(INDEX_HTML_PATH, encoding="utf-8") as f:
            body = f.read().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            return self._send_html()
        if path == "/api/status":
            try:
                return self._send_json(status())
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
        if path == "/api/search":
            qs = parse_qs(parsed.query)
            postal = (qs.get("postal") or [""])[0].strip()
            name = (qs.get("name") or [""])[0].strip()
            address = (qs.get("address") or [""])[0].strip()
            query = (qs.get("q") or [""])[0].strip()
            # Default is substring matching; whole-word is requested via
            # whole_word=1 (or contains=0 for backwards compatibility).
            whole_word = (qs.get("whole_word") or [""])[0].lower() in ("1", "true", "yes") \
                or (qs.get("contains") or [""])[0].lower() in ("0", "false", "no")
            contains = not whole_word
            try:
                records = pm.load_records(pm.CACHE_PATH)
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
            if query:
                if len(query.strip()) < 2:
                    return self._send_json({"error": "query must be at least 2 characters"}, 400)
                matches = pm.search_by_keyword(records, query, contains=contains)
                return self._send_json({"query": query, "count": len(matches), "results": [r.__dict__ for r in matches]})
            if postal or name or address:
                try:
                    matches = pm.filter_records(records, postal=postal, name=name, address=address, contains=contains)
                except ValueError as e:
                    return self._send_json({"error": str(e)}, 400)
                return self._send_json({
                    "postal": postal, "name": name, "address": address, "contains": contains,
                    "count": len(matches), "results": [r.__dict__ for r in matches],
                })
            return self._send_json({"error": "provide 'postal', 'name', 'address', and/or 'q'"}, 400)
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/reindex":
            qs = parse_qs(parsed.query)
            force = (qs.get("force") or [""])[0].lower() in ("1", "true", "yes")
            try:
                return self._send_json(reindex(force=force))
            except Exception as e:
                return self._send_json({"error": str(e)}, 500)
        return self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


def main(argv=None):
    ap = argparse.ArgumentParser(description="DBS PayLah! merchants web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)

    if not os.path.exists(INDEX_HTML_PATH):
        sys.exit("index.html not found next to server.py")

    pm.ensure_records_available()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", file=sys.stderr)


if __name__ == "__main__":
    main()