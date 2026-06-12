"""Split CUHK enrollment requirement text into prerequisite and exclusion clauses."""

from __future__ import annotations

import re

from ..models import CourseCode, PrereqExpr
from .prereq import extract_codes, parse_prerequisite

_COURSE_ATTRS_RE = re.compile(r"\s*Course Attributes\b.*$", re.IGNORECASE | re.DOTALL)
_FOOTER_RE = re.compile(
    r"\bCU_CURR\d+.*$|THE CHINESE UNIVERSITY OF HONG KONG.*$|Print Course Catalog Details.*$",
    re.IGNORECASE | re.DOTALL,
)
_SENIOR_WAIVED_RE = re.compile(
    r"[.\s]*For senior-year entrants, the prerequisite will be waived\.?$",
    re.IGNORECASE | re.DOTALL,
)
_APPROVAL_RE = re.compile(
    r"\s+with the approval of (?:the )?course instructor\.?$",
    re.IGNORECASE,
)
_EQUIVALENT_SUFFIX_RE = re.compile(r"\s+or equivalent\.?$", re.IGNORECASE)
_ALREADY_TAKEN_ONLY_RE = re.compile(
    r"For students who have (?:already )?taken\s+([A-Z]{2,5}\d{3,4}[A-Z]?)\s+only",
    re.IGNORECASE,
)
_CLAUSE_STOP = (
    r"(?:"
    r"[.;]\s*(?:Co-?requisite|Course Attributes|New Enrollment|Pre-?requisites?|Preprequisite)"
    r"|\s+Pre-?requisites?\s*:"
    r"|\.\s*\d+\.\s*"
    r"|\.\s+Not for"
    r"|\s+Not for students who have taken"
    r"|\s+New Enrollment Requirement"
    r"|\s+Additional Information"
    r"|\.$"
    r"|$"
    r")"
)
_PREREQ_CLAUSE_RE = re.compile(
    rf"(?:Pre-?requisites?|Preprequisite)\s*:\s*(.+?){_CLAUSE_STOP}",
    re.IGNORECASE | re.DOTALL,
)
_NOT_FOR_TAKEN_RE = re.compile(
    rf"Not for students who have taken\s+(.+?){_CLAUSE_STOP}",
    re.IGNORECASE | re.DOTALL,
)
_COREQ_PREFIX_RE = re.compile(
    r"Co-?requisite\s*:\s*Any course with .*(?:UGFH|UGFN).*prefix",
    re.IGNORECASE,
)
_NUMBERED_BOILERPLATE_RE = re.compile(
    r"^\s*\d+\.\s*(?:For .+ (?:College|students).+|Not for students who have taken .+\.?\s*)+$",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_numbered_lists(text: str) -> str:
    """Flatten ``1. Prerequisite: ... 2. Not for ...`` into plain clauses."""
    text = re.sub(r"^\s*\d+\.\s*", "", text)
    text = re.sub(r"\.\s*\d+\.\s*", ". ", text)
    return text


def strip_enrollment_noise(text: str) -> str:
    """Remove catalog footers, course-attribute tails, and collapse whitespace."""
    text = re.sub(r"\s+", " ", text.strip())
    text = _normalize_numbered_lists(text)
    text = _FOOTER_RE.sub("", text)
    text = _COURSE_ATTRS_RE.sub("", text)
    text = re.sub(r"\s+Additional Information.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+New Enrollment Requirement\(s\):.*$", "", text, flags=re.IGNORECASE)
    return text.strip()


def clean_prereq_clause(text: str) -> str:
    text = strip_enrollment_noise(text)
    text = _EQUIVALENT_SUFFIX_RE.sub("", text)
    text = _SENIOR_WAIVED_RE.sub("", text)
    text = _APPROVAL_RE.sub("", text)
    return text.strip().rstrip(".;")


def clean_exclusion_clause(text: str) -> str:
    text = strip_enrollment_noise(text)
    return text.strip().rstrip(".;")


def split_enrollment(text: str) -> tuple[str | None, str | None]:
    """Return ``(prerequisite_clause, exclusion_clause)`` from enrollment text."""
    text = strip_enrollment_noise(text)
    if not text:
        return None, None

    prereq: str | None = None
    exclusion: str | None = None

    if m := _ALREADY_TAKEN_ONLY_RE.search(text):
        prereq = m.group(1).upper()

    if m := _PREREQ_CLAUSE_RE.search(text):
        clause = clean_prereq_clause(m.group(1))
        if clause:
            prereq = clause if not prereq else f"{prereq} and {clause}"

    if m := _NOT_FOR_TAKEN_RE.search(text):
        clause = clean_exclusion_clause(m.group(1))
        if clause:
            exclusion = clause

    if not prereq and not exclusion and _COREQ_PREFIX_RE.search(text):
        return None, None

    if not prereq and not exclusion and _NUMBERED_BOILERPLATE_RE.match(text):
        return None, None

    return prereq, exclusion


def subject_prefix_for(course_code: CourseCode) -> str:
    i = 0
    while i < len(course_code) and course_code[i].isalpha():
        i += 1
    return course_code[:i]


def _sanitize_legacy_exclusion_raw(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.split(r"\bPre-?requisites?\s*:", cleaned, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = cleaned.split(";")[0].strip()
    return cleaned or None


def resolve_prerequisite_fields(
    *,
    course_code: CourseCode,
    enrollment: str | None = None,
    prereq_raw: str | None = None,
    exclusion_raw: str | None = None,
) -> tuple[str | None, PrereqExpr, str | None, list[CourseCode]]:
    """Build normalized prereq/exclusion fields for a catalog record."""
    if enrollment:
        prereq_clause, exclusion_clause = split_enrollment(enrollment)
    elif prereq_raw or exclusion_raw:
        legacy_excl = _sanitize_legacy_exclusion_raw(exclusion_raw)
        parts: list[str] = []
        if legacy_excl:
            parts.append(f"Not for students who have taken {legacy_excl}")
        if prereq_raw:
            parts.append(f"Pre-requisite: {prereq_raw}")
        prereq_clause, exclusion_clause = split_enrollment(". ".join(parts))
    else:
        return None, PrereqExpr.none(), None, []

    subject = subject_prefix_for(course_code)
    parsed = (
        parse_prerequisite(
            prereq_clause,
            subject_prefix=subject,
            course_code=course_code,
        )
        if prereq_clause
        else PrereqExpr.none()
    )

    exclusion_codes: list[CourseCode] = []
    if exclusion_clause:
        exclusion_codes = extract_codes(exclusion_clause)

    return prereq_clause, parsed, exclusion_clause, exclusion_codes
