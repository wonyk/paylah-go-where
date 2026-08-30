#!/usr/bin/env python3
"""Build a static, self-contained site for GitHub Pages and similar hosts.

The result in ./public works with NO backend: the browser fetches
dbs-paylah-merchants.json and filters client-side. Re-indexing is
not available in this mode; regenerate the JSON by running:

    python3 parse_merchants.py --refresh
    python3 parse_merchants.py --dump json > public/dbs-paylah-merchants.json

or simply run this script after a refresh.

Usage:
    python3 build_static.py
"""

import json
import os
import shutil
import subprocess

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(BASE, "public")
INDEX_HTML = os.path.join(BASE, "index.html")
JSON_CACHE = os.path.join(BASE, "dbs-paylah-merchants.json")
INDEX_META = os.path.join(BASE, "index_meta.json")


def main():
    if not os.path.exists(JSON_CACHE):
        print("No cached JSON found; parsing the PDF (first run)...")
        subprocess.check_call([__import__("sys").executable, os.path.join(BASE, "parse_merchants.py")])

    os.makedirs(PUBLIC, exist_ok=True)

    # Copy the HTML front-end without the Sites-only PDF refresh module. The
    # generic static build remains read-only and works under a Pages subpath.
    with open(INDEX_HTML, encoding="utf-8") as source:
        html = source.read().replace('<script type="module" src="/refresh-pdf.js"></script>\n', "")
    with open(os.path.join(PUBLIC, "index.html"), "w", encoding="utf-8") as target:
        target.write(html)

    # Copy the parsed records as the client-side data file.
    shutil.copy(JSON_CACHE, os.path.join(PUBLIC, "dbs-paylah-merchants.json"))
    if os.path.exists(INDEX_META):
        shutil.copy(INDEX_META, os.path.join(PUBLIC, "index-meta.json"))

    records = json.load(open(JSON_CACHE, encoding="utf-8"))
    print(f"Built ./public with {len(records)} records.")
    print("Deploy the contents of ./public to any static host.")


if __name__ == "__main__":
    main()
