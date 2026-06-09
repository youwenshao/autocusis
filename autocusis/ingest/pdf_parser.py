"""Parse the layout text of a CUHK 'Print Course Catalog Details' PDF.

These PDFs share a stable structure. We extract: course code/id, EN/ZH title,
units (academic-progress credits), description, prerequisite (as a boolean
AST), exclusions, components, learning outcomes, academic org/subject, and
grading basis. Term/semester availability is NOT present in these PDFs.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import Course
from .enrollment import resolve_prerequisite_fields, split_enrollment, strip_enrollment_noise

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")


def _first_cjk_index(text: str) -> int:
    m = _CJK_RE.search(text)
    return m.start() if m else -1


def _split_en_zh(text: str) -> tuple[Optional[str], Optional[str]]:
    """Split a mixed EN/ZH string at the first CJK character."""
    text = text.strip()
    if not text:
        return None, None
    idx = _first_cjk_index(text)
    if idx <= 0:
        return (None, text) if idx == 0 else (text, None)
    return text[:idx].strip() or None, text[idx:].strip() or None


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _clean_lines(block: str) -> str:
    """Join wrapped lines in a block, dropping page headers/footers."""
    lines = []
    for ln in block.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("CU_CURR") or s.startswith("Page ") or "THE CHINESE UNIVERSITY" in s:
            continue
        if "Print Course Catalog Details" in s:
            continue
        lines.append(s)
    return " ".join(lines)


def parse_course_text(code: str, text: str, source_url: Optional[str] = None) -> Course:
    """Parse extracted PDF text into a structured :class:`Course`."""
    code = code.upper()
    course = Course(code=code, source="pdf", source_url=source_url)

    # --- Course id + effective line ---
    m = re.search(r"Course:\s*([A-Z]{2,5}\d{3,4}[A-Z]?)\s+Course ID:\s*(\S+)", text)
    if m:
        course.course_id = m.group(2)
        # Title is on the line immediately following the "Course:" line. The
        # match ends mid-line, so drop the rest of the current line first.
        after = text[m.end():]
        nl = after.find("\n")
        after = after[nl + 1:] if nl != -1 else after
        first_line = next((ln.strip() for ln in after.splitlines() if ln.strip()), "")
        if first_line:
            en, zh = _split_en_zh(first_line)
            course.title_en, course.title_zh = en, zh

    # --- Academic Org / Subject ---
    m = re.search(r"Academic Org:\s*(.+?)\s*[–-]\s*Subject:\s*(.+)", text)
    if m:
        course.academic_org = _collapse_ws(m.group(1))
        course.subject = _collapse_ws(m.group(2).splitlines()[0])

    # --- Units: use the Academic Progress value (credits) ---
    m = re.search(
        r"Units:\s*([\d.]+)\s*\(Min\)\s*/\s*([\d.]+)\s*\(Max\)\s*/\s*([\d.]+)\s*\(Acad",
        text,
    )
    if m:
        course.units = float(m.group(3))
    else:
        m2 = re.search(r"Units:\s*([\d.]+)", text)
        if m2:
            course.units = float(m2.group(1))

    # --- Grading basis ---
    m = re.search(r"Grading Basis:\s*(.+)", text)
    if m:
        course.grading_basis = _collapse_ws(m.group(1).splitlines()[0])

    # --- Description: between the title line and "Grade Descriptor:" ---
    course.description_en, course.description_zh = _extract_description(text, course)

    # --- Prerequisite / exclusion (ENROLMENT REQUIREMENTS section) ---
    enrollment_blob = _extract_enrolment_blob(text)
    prereq_raw, prereq, excl_raw, exclusion_codes = resolve_prerequisite_fields(
        course_code=code,
        enrollment=enrollment_blob,
    )
    course.prerequisite_raw = prereq_raw
    course.prerequisite = prereq
    course.exclusion_raw = excl_raw
    course.exclusion_codes = exclusion_codes

    # --- Components (LEC/LAB/TUT/...) ---
    course.components = _extract_components(text)

    # --- Learning outcomes ---
    course.learning_outcomes = _extract_outcomes(text)

    return course


def _extract_description(text: str, course: Course) -> tuple[Optional[str], Optional[str]]:
    # Description sits after the title line and before "Grade Descriptor:".
    title = course.title_en or course.title_zh
    start = 0
    if title:
        ti = text.find(title)
        if ti != -1:
            # Skip to the line after the title line (the title line also holds
            # the ZH title, which we don't want in the description block).
            nl = text.find("\n", ti)
            start = nl + 1 if nl != -1 else ti + len(title)
    gd = text.find("Grade Descriptor:")
    block = text[start:gd] if gd != -1 else text[start:start + 4000]
    block = _clean_lines(block)
    if not block:
        return None, None
    return _split_en_zh(block)


def _extract_enrolment_blob(text: str) -> Optional[str]:
    """Return the cleaned ENROLMENT REQUIREMENTS block as one string."""
    sec = _section(text, "ENROLMENT REQUIREMENTS", ["CAF", "eLearning", "Research components"])
    if not sec:
        return None
    blob = strip_enrollment_noise(_clean_lines(_collapse_ws(sec)))
    if blob.lower() in ("no change", "none", "nil", "n/a", "no change."):
        return None
    return blob or None


def _extract_components(text: str) -> list[str]:
    sec = _section(text, "COMPONENTS", ["ENROLMENT REQUIREMENTS", "OFFERINGS"])
    if not sec:
        return []
    comps: list[str] = []
    for m in re.finditer(r"\b([A-Z]{3})\s*:\s*Size=", sec):
        c = m.group(1)
        if c not in comps:
            comps.append(c)
    return comps


def _extract_outcomes(text: str) -> list[str]:
    sec = _section(text, "Learning Outcomes:", ["Course Syllabus", "Assessment", "Feedback", "Required Readings", "Recommended Readings", "COURSE SYLLABUS"])
    if not sec:
        return []
    outcomes: list[str] = []
    for m in re.finditer(r"^\s*(\d+)\.\s*(.+)$", sec, re.MULTILINE):
        item = _collapse_ws(m.group(2))
        if item:
            outcomes.append(item)
    return outcomes


def _section(text: str, start_marker: str, end_markers: list[str]) -> Optional[str]:
    """Return the text between ``start_marker`` and the earliest end marker."""
    si = text.find(start_marker)
    if si == -1:
        return None
    si += len(start_marker)
    end = len(text)
    for em in end_markers:
        ei = text.find(em, si)
        if ei != -1:
            end = min(end, ei)
    return text[si:end]
