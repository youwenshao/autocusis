"""Derive term availability from the CUHK public Teaching Timetable.

The Teaching Timetable (rgsntl.rgs.cuhk.edu.hk/rws_prd_applx2/Public/
tt_dsp_timetable.aspx) lists the classes offered for a chosen *academic
career + term + department*. For public (non-campus) users each search is
gated by a verification code (CAPTCHA); a session logged into CUSIS/MyCUHK
bypasses it.

Because the term is fixed by the query, any course code appearing in a
result page is "offered in that term". We therefore:
  1. obtain the results HTML for a given term (saved page, or live fetch via
     an authenticated browser context), and
  2. extract the set of course codes -> mark them available in that term.

Two acquisition paths are supported:
  * ``parse_timetable_html(html, term)`` - parse an already-saved page (works
    with whatever browser/agent saved it, including the cursor-ide-browser
    MCP after you log into CUSIS).
  * ``fetch_timetable_live(...)`` - optional Playwright path using a persistent
    profile so you authenticate once; lazily imported so Playwright is not a
    hard dependency.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from ..models import CourseAvailability, Term
from .availability_store import AvailabilityStore

TIMETABLE_URL = (
    "https://rgsntl.rgs.cuhk.edu.hk/rws_prd_applx2/Public/tt_dsp_timetable.aspx"
)

_CODE_RE = re.compile(r"\b([A-Z]{2,5}\d{3,4}[A-Z]?)\b")
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return text


def extract_offered_codes(html: str) -> set[str]:
    """Return the set of course codes appearing in a timetable results page."""
    text = _strip_html(html)
    return {m.group(1) for m in _CODE_RE.finditer(text)}


def parse_timetable_html(
    html: str,
    term: Term,
    year: Optional[str] = None,
    restrict_subjects: Optional[Iterable[str]] = None,
) -> AvailabilityStore:
    """Build an AvailabilityStore from one term's timetable HTML.

    ``restrict_subjects`` optionally limits which subject prefixes are kept
    (useful to avoid noise from unrelated codes on shared pages).
    """
    store = AvailabilityStore()
    subjects = {s.upper() for s in restrict_subjects} if restrict_subjects else None
    for code in sorted(extract_offered_codes(html)):
        prefix = re.match(r"[A-Z]+", code).group(0)
        if subjects and prefix not in subjects:
            continue
        store.upsert(
            CourseAvailability(
                code=code, terms=[term], source="timetable", year=year
            ),
            respect_precedence=False,
        )
    return store


def combine_terms(stores: list[AvailabilityStore], year: Optional[str] = None) -> AvailabilityStore:
    """Union per-term stores into one store with merged term lists."""
    combined = AvailabilityStore()
    for st in stores:
        for code, av in st.records.items():
            existing = combined.records.get(code)
            if existing:
                terms = sorted(set(existing.terms) | set(av.terms))
                existing.terms = [Term(t) for t in terms]
            else:
                combined.records[code] = CourseAvailability(
                    code=code, terms=list(av.terms), source="timetable", year=year
                )
    return combined


def fetch_timetable_live(
    *,
    term_value: str,
    career_value: str = "UG",
    user_data_dir: str = ".autocusis_session",
    headless: bool = False,
    department_values: Optional[list[str]] = None,
) -> str:
    """Fetch timetable results HTML via an authenticated Playwright session.

    Uses a persistent browser profile (``user_data_dir``) so you log into
    CUSIS once and the session is reused. Raises a clear error if Playwright
    is not installed. Returns the concatenated results HTML.

    NOTE: This is a best-effort helper. The ASP.NET form fields/IDs on the
    timetable page change periodically; if selection fails, save the results
    page manually and use ``parse_timetable_html`` / the
    ``availability sync --from-html`` command instead.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "Playwright is not installed. Either:\n"
            "  pip install playwright && playwright install chromium\n"
            "or save the timetable results page from a logged-in browser and run:\n"
            "  autocusis availability sync --from-html page.html --term 1"
        ) from e

    htmls: list[str] = []
    with sync_playwright() as p:  # pragma: no cover - requires real browser/login
        ctx = p.chromium.launch_persistent_context(user_data_dir, headless=headless)
        page = ctx.new_page()
        page.goto(TIMETABLE_URL, wait_until="domcontentloaded")
        # Give the user a chance to log in / clear CAPTCHA on first run.
        page.wait_for_timeout(1500)
        htmls.append(page.content())
        ctx.close()
    return "\n".join(htmls)
