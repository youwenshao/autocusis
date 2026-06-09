#!/usr/bin/env python3
"""Save scraped catalog HTML from a CDP JSON dump and ingest into the catalog DB."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import re

from autocusis.db import open_catalog
from autocusis.ingest.catalog_scraper import html_to_text, parse_catalog_html
from autocusis.paths import html_cache_dir

_CODE_HDR = re.compile(r"([A-Z]{2,5})\s*(\d{3,4}[A-Z]?)\s*-")


def _code_from_html(html: str) -> str | None:
    text = html_to_text(html)
    m = _CODE_HDR.search(text)
    return f"{m.group(1)}{m.group(2)}" if m else None


def main(path: str) -> None:
    data = json.loads(Path(path).read_text())
    payload = data.get("result", {}).get("value")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise SystemExit(f"Unexpected payload in {path}")

    cache = html_cache_dir()
    ok = 0
    with open_catalog() as db:
        for code, html in sorted(payload.items()):
            if code.startswith("_"):
                continue
            code = code.upper()
            actual = _code_from_html(html)
            if actual and actual != code:
                print(f"SKIP {code} (page is {actual})")
                continue
            out = cache / f"{code}.html"
            out.write_text(html, encoding="utf-8")
            course = parse_catalog_html(code, html)
            db.upsert_course(course)
            title = course.title_en or "(no title)"
            print(f"OK {code} {course.units:g}cr {title}")
            ok += 1
    print(f"Ingested {ok}/{len(payload)} from {path}")


if __name__ == "__main__":
    main(sys.argv[1])
