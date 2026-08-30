#!/usr/bin/env python3
"""Parse the DBS PayLah! Saturdays participating merchants PDF and search
merchants / stalls by postal code.

The PDF has two distinct formats:
  1. "Heartland Merchants" tables (Stall Name | Stall Address | Unit No. | Postal Code)
  2. "Hawker, Wet Markets, Coffeeshops" lists where each venue has an address
     line ending in a postal code, followed by its individual stalls.

Usage:
  python3 parse_merchants.py 730888          # search by postal code
  python3 parse_merchants.py --refresh 730888 # force re-download of the PDF
  python3 parse_merchants.py --dump json     # dump all parsed records as JSON
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.request
from dataclasses import dataclass

PDF_URL = "https://www.dbs.com.sg/iwov-resources/media/pdf/deposits/promotions/paylah/saturdays/dbs-paylah-saturdays-participating-merchants.pdf"

_DATA_DIR = os.environ.get("APP_DATA_DIR") or os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(_DATA_DIR, "dbs-paylah-merchants.pdf")
JSON_CACHE_PATH = os.path.join(_DATA_DIR, "dbs-paylah-merchants.json")
META_PATH = os.path.join(_DATA_DIR, "index_meta.json")

# Detailed sections may use these headers; they map to the output category.
SECTION_ALIASES = {
    "Wet Market": "wet_markets",
    "Wet Markets": "wet_markets",
    "Hawker Centres": "hawker_centres",
    "Coffeeshops": "coffeeshops",
    "Industrial Canteens": "industrial_canteens",
}

# Matches a venue address line ending in a Singapore postal code.
# e.g. "888 WOODLANDS DRIVE 50, S730888" or "469 BUKIT BATOK WEST AVE 9, SINGAPORE 650469"
VENUE_ADDR_RE = re.compile(r"^(?P<addr>.+?),\s*(?:S(?P<postal>\d{5,6})|SINGAPORE\s+(?P<postal2>\d{6}))\s*$")


def _normalize_postal(code):
    """Normalize a postal code to 6 digits (PDF has an occasional 5-digit typo)."""
    if len(code) == 5:
        return "0" + code
    return code

# Matches a stall line, e.g. "AH KEAT KWAY CHAP #01-733" or "SHUN FA FRESH EGG"
STALL_RE = re.compile(r"^(?P<name>.+?)(?:\s+(?P<unit>#?\s?\d{2,3}-\d{2,4}[A-Za-z]?(?:/\d{2,4}[A-Za-z]?)*))?\s*$")


@dataclass
class Merchant:
    name: str
    address: str
    unit: str
    postal_code: str
    category: str
    # kind: "merchant" (heartland row), "venue" (hawker/coffeeshop title),
    #       "stall" (individual stall inside a venue)
    kind: str = "merchant"
    # For stalls: the name of the parent venue (e.g. "BAI SHENG FOOD COURT").
    venue: str = ""


def download_pdf(url: str, path: str) -> None:
    print(f"Downloading {url} ...", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp, open(path, "wb") as f:
        f.write(resp.read())


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_index_meta(pdf_path: str, record_count: int, source_url: str) -> None:
    import time
    with open(META_PATH, "w", encoding="utf-8") as target:
        json.dump({
            "pdf_hash": sha256_file(pdf_path),
            "indexed_at": int(time.time()),
            "record_count": record_count,
            "pdf_bytes": os.path.getsize(pdf_path),
            "source_url": source_url,
            "force": True,
        }, target, ensure_ascii=False, indent=2)


def load_index_meta() -> dict:
    try:
        with open(META_PATH, encoding="utf-8") as source:
            return json.load(source)
    except FileNotFoundError:
        return {}


def resolve_refresh_source(args, parser):
    """Return candidate path, temporary path, and human-readable source."""
    if not args.local_pdf:
        temporary = args.pdf + ".download"
        download_pdf(args.url, temporary)
        return temporary, temporary, args.url

    candidate = os.path.abspath(args.local_pdf)
    if not os.path.isfile(candidate):
        parser.error(f"local PDF not found: {args.local_pdf}")
    return candidate, None, f"file:{os.path.basename(candidate)}"


def validate_pdf(path, parser):
    with open(path, "rb") as source:
        if source.read(5) != b"%PDF-":
            parser.error("selected source is not a PDF")


def install_pdf(candidate, destination, move):
    if os.path.abspath(candidate) == os.path.abspath(destination):
        return
    (os.replace if move else shutil.copyfile)(candidate, destination)


def refresh_records(args, parser):
    """Hash first; parse and replace caches only when the PDF changed."""
    candidate, temporary, source_label = resolve_refresh_source(args, parser)
    try:
        validate_pdf(candidate, parser)
        changed = (
            sha256_file(candidate) != load_index_meta().get("pdf_hash")
            or not os.path.exists(JSON_CACHE_PATH)
        )
        if not changed:
            return load_records(args.pdf), False, source_label

        install_pdf(candidate, args.pdf, move=bool(temporary))
        temporary = None
        records = load_records(args.pdf, refresh=True)
        save_index_meta(args.pdf, len(records), source_label)
        return records, True, source_label
    finally:
        if temporary and os.path.exists(temporary):
            os.remove(temporary)


def _clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())


def _group_words_by_line(words, tolerance=4.0):
    """Group extracted words into text lines ordered by their vertical position."""
    lines = {}
    for w in words:
        key = round(w["top"] / tolerance) * tolerance
        lines.setdefault(key, []).append(w)
    result = []
    for key in sorted(lines):
        ws = sorted(lines[key], key=lambda w: w["x0"])
        result.append({
            "x0": ws[0]["x0"],
            "text": " ".join(w["text"] for w in ws),
        })
    return result


def parse_heartland_tables(pdf):
    """Parse the tabular 'Heartland Merchants' section (pages 4-207).

    Each merchant row has four columns: Stall Name | Stall Address | Unit No. |
    Postal Code. Rows are validated by checking the last non-empty cell is a
    6-digit postal code.
    """
    records = []
    for i in range(3, 207):
        page = pdf.pages[i]
        for table in page.extract_tables():
            for row in table:
                non_empty = [c for c in row if c and c.strip()]
                if len(non_empty) < 2:
                    continue
                postal = _clean(non_empty[-1])
                if not re.fullmatch(r"\d{6}", postal):
                    continue
                name = _clean(non_empty[0])
                if not name or name in ("Stall Name", "Postal Code Starting with"):
                    continue
                # Columns: name, address, unit, postal. Some rows have unit blank.
                if len(non_empty) >= 4:
                    unit = _clean(non_empty[-2])
                    if not unit or not re.search(r"[#0-9]", unit):
                        unit = ""
                    address = " ".join(_clean(c) for c in non_empty[1:-2])
                else:
                    unit = ""
                    address = " ".join(_clean(c) for c in non_empty[1:-1])
                records.append(Merchant(
                    name=name, address=address, unit=unit,
                    postal_code=postal, category="heartland", kind="merchant"))
    return records


def parse_hawker_sections(pdf):
    """Parse the 'Hawker, Wet Markets, Coffeeshops' lists (pages 208-418).

    Two sub-formats exist:
      * Summary pages (208-226): a table with venue name + address (postal in
        the address cell). No individual stalls.
      * Detailed pages (227-418): venue name (centered) on one line, address
        (centered, ends in postal code) on the next, then left-aligned stall
        lines each with an optional unit number.
    """
    records = []
    current_section = None

    # ---- Summary pages 208-226 (tabular) ----
    for i in range(207, 226):
        page = pdf.pages[i]
        text = page.extract_text() or ""
        for line in text.splitlines():
            if line.strip() in SECTION_ALIASES:
                current_section = SECTION_ALIASES[line.strip()]
        for table in page.extract_tables():
            for row in table:
                cells = [_clean(c) for c in row if c]
                if not cells:
                    continue
                joined = " ".join(cells)
                m = VENUE_ADDR_RE.search(joined)
                if not m:
                    continue
                name = cells[0]
                if not name or name == "Hawker Type":
                    continue
                postal = m.group("postal") or m.group("postal2")
                # The address is the cell that actually contains the postal code.
                addr_cell = next((c for c in cells if re.search(r"S\d{6}|SINGAPORE\s+\d{6}", c)), "")
                addr = VENUE_ADDR_RE.match(addr_cell)
                addr = _clean(addr.group("addr")) if addr else m.group("addr")
                records.append(Merchant(
                    name=name,
                    address=addr,
                    unit="",
                    postal_code=_normalize_postal(postal),
                    category=current_section or "hawker",
                    kind="venue",
                ))

    # ---- Detailed pages 227-418 (position-based) ----
    current_venue = None
    pending_name = None

    def flush_venue():
        nonlocal current_venue
        if current_venue:
            records.append(Merchant(
                name=current_venue["name"],
                address=current_venue["address"],
                unit="",
                postal_code=current_venue["postal_code"],
                category=current_venue["category"],
                kind="venue",
            ))
            current_venue = None

    for i in range(226, 418):
        page = pdf.pages[i]
        words = page.extract_words()
        lines = _group_words_by_line(words)

        for line in lines:
            text = line["text"].strip()
            x0 = line["x0"]
            if not text:
                continue
            if text in SECTION_ALIASES or text == "Hawker Type":
                flush_venue()
                current_section = SECTION_ALIASES.get(text, current_section)
                pending_name = None
                continue
            if text == "Back to top":
                continue
            if re.fullmatch(r"[0-9A-Z\s]+", text) and len(text) < 60 and " " not in text.strip():
                continue  # alphabetical index row, e.g. "12345678ABCDEFGHJ..."
            if re.fullmatch(r"[\d\s]+", text):
                continue

            m = VENUE_ADDR_RE.match(text)
            if m and x0 >= 70:
                # Address line: the venue name is the preceding centered line.
                flush_venue()
                name = pending_name or _clean(m.group("addr"))
                current_venue = {
                    "name": name,
                    "address": _clean(m.group("addr")),
                    "postal_code": _normalize_postal(m.group("postal") or m.group("postal2")),
                    "category": current_section or "hawker",
                }
                pending_name = None
                continue

            if x0 >= 70:
                # Centered line without postal -> venue name (or its wrap).
                # Ignore unit-only continuation lines such as "#01-12".
                if re.search(r"#\s?\d{1,3}-\d{1,4}", text):
                    continue
                if pending_name:
                    pending_name = pending_name + " " + text
                else:
                    pending_name = text
                continue

            # Left-aligned stall line belonging to current_venue.
            if current_venue:
                sm = STALL_RE.match(text)
                if sm and sm.group("name"):
                    records.append(Merchant(
                        name=_clean(sm.group("name")),
                        address=current_venue["address"],
                        unit=_clean(sm.group("unit") or ""),
                        postal_code=current_venue["postal_code"],
                        category=current_section or "hawker",
                        kind="stall",
                        venue=current_venue["name"],
                    ))
        flush_venue()
        pending_name = None
    return records


def parse_pdf(path: str):
    import pdfplumber

    records = []
    with pdfplumber.open(path) as pdf:
        records.extend(parse_heartland_tables(pdf))
        records.extend(parse_hawker_sections(pdf))
    return dedupe(records)


def load_records(pdf_path: str, refresh: bool = False):
    """Return parsed records, using a JSON cache to avoid re-parsing the PDF."""
    import json

    cache = JSON_CACHE_PATH
    if not refresh and os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            data = json.load(f)
        return [Merchant(**d) for d in data]
    records = parse_pdf(pdf_path)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump([r.__dict__ for r in records], f, ensure_ascii=False, indent=2)
    return records


def ensure_records_available():
    """Install a local copy of the PDF if missing (no third-party deps needed
    for cached searches; pdfplumber is only needed the first time)."""
    if not os.path.exists(CACHE_PATH):
        download_pdf(PDF_URL, CACHE_PATH)
    if not os.path.exists(JSON_CACHE_PATH):
        try:
            load_records(CACHE_PATH, refresh=True)
        except ImportError:
            sys.exit(
                "pdfplumber is required to parse the PDF the first time.\n"
                "Install it with:  pip install pdfplumber\n"
                "or copy dbs-paylah-merchants.json next to this script."
            )


def dedupe(records):
    """Remove exact duplicates (same name, address, unit, postal, category)."""
    seen = set()
    out = []
    for r in records:
        key = (r.name, r.address, r.unit, r.postal_code, r.category, r.kind, r.venue)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def search(records, postal_code: str):
    postal_code = postal_code.strip()
    if not re.fullmatch(r"\d{6}", postal_code):
        raise ValueError("postal code must be 6 digits")
    return [r for r in records if r.postal_code == postal_code]


def search_by_keyword(records, query: str, fields=("postal_code", "name", "address"), limit=500, contains=True):
    """Search across postal code, shop name, and/or address (case-insensitive).

    Substring matching by default (``contains=True``), with whole-word matches
    ranked first. Set ``contains=False`` to restrict to whole-word matches only.
    A venue match also pulls in its stalls so the venue header can be rendered
    with everything inside it.
    """
    q = query.strip().lower()
    if not q:
        return []
    direct = [r for r in records
              if any(_text_has(getattr(r, f), q, contains) for f in fields)]
    direct.sort(key=lambda r: _keyword_rank(r, q, fields), reverse=True)
    return _expand_venues(records, direct, limit)


def search_by_name(records, query: str, limit: int = 200, contains=True):
    """Case-insensitive search on merchant/stall names (substring by default,
    whole-word matches ranked first)."""
    return search_by_keyword(records, query, fields=("name",), limit=limit, contains=contains)


def _match_level(text, query_lower):
    """0 = no match, 1 = substring match, 2 = whole-word match."""
    text = (text or "").lower()
    if not text or not query_lower:
        return 0
    if re.search(r"(?<!\w)" + re.escape(query_lower) + r"(?!\w)", text):
        return 2
    if query_lower in text:
        return 1
    return 0


def _text_has(text, query_lower, contains):
    """Match ``query_lower`` against ``text``, case-insensitively.

    Whole-word mode uses word boundaries so a query like "ntuc" does not match
    the middle of "kentucrky".
    """
    if contains:
        return _match_level(text, query_lower) > 0
    return _match_level(text, query_lower) == 2


# Weights put name matches above address/postal matches when ranking.
_FIELD_WEIGHT = {"postal_code": 1, "name": 5, "address": 2}


def _keyword_rank(record, query_lower, fields):
    """Rank a record for keyword search: whole-word beats substring, and name
    matches rank above address/postal matches."""
    best = 0
    for f in fields:
        level = _match_level(getattr(record, f), query_lower)
        if level:
            best = max(best, level * _FIELD_WEIGHT.get(f, 1))
    return best


def filter_records(records, postal=None, name=None, address=None, limit=500, contains=True):
    """Fine-grained AND filter across postal code, shop name, and address.

    Only the criteria that are non-empty are applied; empty ones are ignored.
    Name/address matching is substring by default (``contains=True``), with
    whole-word matches ranked first. Set ``contains=False`` to restrict to
    whole-word matches only. A venue match also pulls in its stalls so the
    venue header can be rendered with everything inside it.
    """
    criteria = {}
    if postal is not None and postal.strip():
        p = postal.strip()
        if not re.fullmatch(r"\d{6}", p):
            raise ValueError("postal code must be 6 digits")
        criteria["postal"] = p
    if name is not None and name.strip():
        criteria["name"] = name.strip().lower()
    if address is not None and address.strip():
        criteria["address"] = address.strip().lower()
    if not criteria:
        return []

    matchers = {
        "postal": lambda record, value: record.postal_code == value,
        "name": lambda record, value: _text_has(record.name, value, contains),
        "address": lambda record, value: _text_has(record.address, value, contains),
    }

    def matches(record):
        return all(matchers[field](record, value) for field, value in criteria.items())

    direct = [r for r in records if matches(r)]
    # Whole-word name matches first, then substring name, then address levels.
    direct.sort(key=lambda r: _filter_rank(r, criteria), reverse=True)
    return _expand_venues(records, direct, limit)


def _filter_rank(record, criteria):
    """Rank for AND filtering: name whole-word > name substring > address
    whole-word > address substring."""
    name_level = _match_level(record.name, criteria.get("name", "")) if "name" in criteria else 0
    address_level = _match_level(record.address, criteria.get("address", "")) if "address" in criteria else 0
    return (name_level, address_level)


def _expand_venues(records, direct, limit=500):
    """Attach each venue's stalls so the venue header can be shown with its
    contents. Preserves the order of the direct matches."""
    stalls_by_key = {}
    for r in records:
        if r.kind == "stall" and r.venue:
            stalls_by_key.setdefault((r.postal_code, r.venue), []).append(r)

    results = []
    for r in direct:
        if r not in results:
            results.append(r)
        if r.kind == "venue":
            for s in stalls_by_key.get((r.postal_code, r.name), []):
                if s not in results:
                    results.append(s)
        if len(results) >= limit:
            break
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Filter DBS PayLah! Saturdays merchants by postal code, name, and/or address. "
                    "Empty criteria are ignored; provided criteria are combined with AND.")
    ap.add_argument("--postal", "-p", help="6-digit Singapore postal code")
    ap.add_argument("--name", "-n", help="merchant/stall name (case-insensitive)")
    ap.add_argument("--address", "-a", help="address substring (case-insensitive)")
    ap.add_argument("--query", "-q", help="quick search: any of postal, name, or address (case-insensitive)")
    ap.add_argument("--whole-word", action="store_true",
                    help="restrict to whole-word matches only (default is substring matching)")
    ap.add_argument("--refresh", action="store_true", help="download, hash-check, and re-parse the PDF when changed")
    ap.add_argument("--url", default=PDF_URL, help="PDF URL used with --refresh")
    ap.add_argument("--local-pdf", help="local PDF to hash-check and index instead of downloading")
    ap.add_argument("--dump", choices=["json"], help="dump all parsed records as JSON instead of searching")
    ap.add_argument("--pdf", default=CACHE_PATH, help="path to the PDF (default: cached copy next to this script)")
    args = ap.parse_args(argv)

    refresh_requested = args.refresh or bool(args.local_pdf)
    if refresh_requested:
        records, changed, source_label = refresh_records(args, ap)
    else:
        ensure_records_available()
        records = load_records(args.pdf)
        changed, source_label = False, args.url

    if args.dump == "json":
        print(json.dumps([r.__dict__ for r in records], ensure_ascii=False, indent=2))
        return

    if refresh_requested and not (args.query or args.postal or args.name or args.address):
        action = "Re-indexed" if changed else "PDF unchanged; kept"
        print(f"{action} {len(records)} records from {source_label}")
        return

    if args.query:
        matches = search_by_keyword(records, args.query, contains=not args.whole_word)
        label = f"query '{args.query}'"
    elif args.postal or args.name or args.address:
        matches = filter_records(records, postal=args.postal, name=args.name,
                                 address=args.address, contains=not args.whole_word)
        parts = []
        if args.postal:
            parts.append(f"postal {args.postal}")
        if args.name:
            parts.append(f"name '{args.name}'")
        if args.address:
            parts.append(f"address '{args.address}'")
        label = " + ".join(parts)
    else:
        ap.error("provide --postal, --name, --address, and/or --query")

    if not matches:
        print(f"No merchants found for {label}.")
        return

    print(f"Found {len(matches)} merchant/stall record(s) for {label}:\n")
    _print_results(matches)


def _print_results(matches):
    by_cat = {}
    for r in matches:
        by_cat.setdefault(r.category, []).append(r)
    for cat in sorted(by_cat):
        print(f"[{cat}]")
        # Preserve the ranked order from the search, but keep venues first.
        for r in sorted(enumerate(by_cat[cat]), key=lambda x: (x[1].kind != "venue", x[0])):
            r = r[1]
            unit = f" {r.unit}" if r.unit else ""
            address = f"  ({r.address})" if r.address else ""
            tag = {"venue": " (venue)", "stall": " (stall)"}.get(r.kind, "")
            print(f"  - {r.name}{tag}{unit}{address}")
        print()


if __name__ == "__main__":
    sys.exit(main())
