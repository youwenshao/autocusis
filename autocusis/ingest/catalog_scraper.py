"""Ingest non-CSE course details from the public Course Catalog browser.

Non-CSE subjects are not published as PDFs on the CSE host, but the public
Course Catalog browser (rgsntl.rgs.cuhk.edu.hk/aqs_prd_applx/Public/
tt_dsp_crse_catalog.aspx) renders the same "Print Course Catalog Details"
content (Course:, Units:, Pre-requisite:, etc.). Since the field labels match
the PDF layout, we strip the HTML to text and reuse the PDF parser.

Acquisition is the same story as the timetable: the page is CAPTCHA-gated for
public users but open to a logged-in CUSIS session. Save the detail page and
feed it here, or drive an authenticated browser via the cursor-ide-browser MCP.
"""

from __future__ import annotations

import re

from ..models import Course
from .pdf_parser import parse_course_text

CATALOG_URL = (
    "http://rgsntl.rgs.cuhk.edu.hk/aqs_prd_applx/Public/tt_dsp_crse_catalog.aspx"
)

_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(html: str) -> str:
    """Convert catalog-detail HTML to layout-ish text the PDF parser expects."""
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    # Preserve row/line structure before dropping tags.
    text = re.sub(r"(?i)<(br|/tr|/p|/div|/td)\s*/?>", "\n", text)
    text = _TAG_RE.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
    )
    # Collapse intra-line runs of spaces, keep newlines.
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _enrich_browser_catalog(code: str, text: str, course: Course) -> None:
    """Fill gaps when the HTML comes from the live catalog browser, not a PDF."""
    subj = re.match(r"^([A-Z]{2,5})(\d{3,4}[A-Z]?)$", code.upper())
    if subj and not course.title_en:
        m = re.search(
            rf"{subj.group(1)}\s*{subj.group(2)}\s*-\s*(.+)",
            text,
        )
        if m:
            course.title_en = m.group(1).strip()

    m = re.search(r"Units\s+([\d.]+)", text)
    if m:
        course.units = float(m.group(1))

    if course.prerequisite.kind == "none":
        m = re.search(
            r"Enrollment Requirement\s+(.+?)(?=\nDescription\b|\nGrade Descriptor\b|$)",
            text,
            re.DOTALL,
        )
        if m:
            from .enrollment import resolve_prerequisite_fields

            raw = re.sub(r"\s+", " ", m.group(1)).strip()
            if raw:
                prereq_raw, prereq, exclusion_raw, exclusion_codes = resolve_prerequisite_fields(
                    course_code=code,
                    enrollment=raw,
                )
                course.prerequisite_raw = prereq_raw
                course.prerequisite = prereq
                course.exclusion_raw = exclusion_raw
                course.exclusion_codes = exclusion_codes


def parse_catalog_html(code: str, html: str, source_url: str | None = None) -> Course:
    """Parse a saved catalog-detail HTML page into a :class:`Course`."""
    text = html_to_text(html)
    course = parse_course_text(code, text, source_url=source_url or CATALOG_URL)
    _enrich_browser_catalog(code, text, course)
    course.source = "catalog"
    return course
