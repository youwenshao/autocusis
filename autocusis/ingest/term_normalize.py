"""Normalize academic term strings from community data sources."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Term

_TERM_CODE_MAP = {
    "2380": (1, None),
    "2390": (2, None),
    "2400": (3, None),
}

_YEAR_TERM_RE = re.compile(
    r"(\d{4})-(\d{2})\s*(?:Term\s*)?([123]|T1|T2|Summer|S)",
    re.IGNORECASE,
)
_COMPACT_RE = re.compile(r"(\d{4})-(\d{2})-T([123])", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedTerm:
    year_label: str  # e.g. "2025-26"
    term: Term
    term_label: str  # e.g. "2025-26 Term 2"

    @property
    def compact(self) -> str:
        return f"{self.year_label}-T{int(self.term)}"


def normalize_term(name: str, *, term_code: str | None = None) -> NormalizedTerm | None:
    """Parse a community term string into a normalized form."""
    text = (name or "").strip()
    if not text and term_code and term_code in _TERM_CODE_MAP:
        return None
    if not text:
        return None

    m = _COMPACT_RE.search(text)
    if m:
        y1, y2, t = m.group(1), m.group(2), m.group(3)
        term = Term(int(t))
        year_label = f"{y1}-{y2}"
        return NormalizedTerm(
            year_label=year_label,
            term=term,
            term_label=f"{year_label} {term.label}",
        )

    m = _YEAR_TERM_RE.search(text)
    if m:
        y1, y2, raw = m.group(1), m.group(2), m.group(3).upper()
        year_label = f"{y1}-{y2}"
        if raw in ("1", "T1"):
            term = Term.TERM1
        elif raw in ("2", "T2"):
            term = Term.TERM2
        else:
            term = Term.SUMMER
        return NormalizedTerm(
            year_label=year_label,
            term=term,
            term_label=f"{year_label} {term.label}",
        )

    if term_code and term_code in _TERM_CODE_MAP:
        tnum, _ = _TERM_CODE_MAP[term_code]
        return None

    return None


def term_matches_filter(term_label: str, filter_text: str) -> bool:
    """Return True if ``term_label`` matches the user filter (substring)."""
    return filter_text.strip().lower() in term_label.lower()
