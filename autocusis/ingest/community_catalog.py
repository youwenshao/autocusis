"""Build catalog.sqlite entries from EagleZhen community JSON course metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..db import CatalogDB
from ..models import Course
from ..paths import community_data_dir
from .enrollment import resolve_prerequisite_fields


@dataclass
class CommunityCatalogStats:
    scanned: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    skipped_no_file: int = 0


def _course_code(subject: str, course_code: str) -> str:
    return f"{subject.upper()}{str(course_code).strip()}".replace(" ", "")


def course_from_community(subject: str, entry: dict) -> Course | None:
    num = str(entry.get("course_code", "")).strip()
    if not num:
        return None
    code = _course_code(subject, num)
    try:
        units = float(str(entry.get("credits") or "3").strip())
    except ValueError:
        units = 3.0

    enrollment = (entry.get("enrollment_requirement") or "").strip()
    prereq_raw, prereq, exclusion_raw, exclusion_codes = resolve_prerequisite_fields(
        course_code=code,
        enrollment=enrollment or None,
    )

    components = [
        ln.strip()
        for ln in (entry.get("component") or "").splitlines()
        if ln.strip()
    ]
    outcomes_raw = (entry.get("learning_outcomes") or "").strip()
    learning_outcomes = (
        [ln.strip() for ln in outcomes_raw.splitlines() if ln.strip()]
        if outcomes_raw
        else []
    )

    return Course(
        code=code,
        title_en=(entry.get("title") or "").strip() or None,
        units=units,
        description_en=(entry.get("description") or "").strip() or None,
        prerequisite_raw=prereq_raw,
        prerequisite=prereq,
        exclusion_raw=exclusion_raw,
        exclusion_codes=exclusion_codes,
        components=components,
        learning_outcomes=learning_outcomes,
        academic_org=(entry.get("academic_org") or entry.get("academic_group") or "").strip()
        or None,
        subject=subject.upper(),
        grading_basis=(entry.get("grading_basis") or "").strip() or None,
        source="catalog",
        source_url="community:eaglezhen",
    )


def _iter_community_files(data_path: Path) -> list[Path]:
    if data_path.is_file():
        return [data_path]
    return sorted(data_path.glob("*.json"))


def sync_community_catalog(
    db: CatalogDB,
    data_path: Path | None = None,
    *,
    codes: set[str] | None = None,
    missing_only: bool = True,
) -> CommunityCatalogStats:
    stats = CommunityCatalogStats()
    root = data_path or community_data_dir()
    want = {c.upper() for c in codes} if codes else None

    for path in _iter_community_files(root):
        if path.name.startswith("."):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        subject = (data.get("metadata") or {}).get("subject") or path.stem
        for entry in data.get("courses") or []:
            course = course_from_community(str(subject), entry)
            if not course:
                continue
            stats.scanned += 1
            if want and course.code not in want:
                continue
            if missing_only and db.get_course(course.code):
                stats.skipped_existing += 1
                continue
            db.upsert_course(course)
            stats.inserted += 1

    if want:
        found = stats.inserted + stats.skipped_existing
        stats.skipped_no_file = len(want) - found

    return stats


def load_community_enrollments(data_path: Path | None = None) -> dict[str, str]:
    """Map course code -> enrollment_requirement text from community JSON."""
    root = data_path or community_data_dir()
    enrollments: dict[str, str] = {}
    paths = [root] if root.is_file() else sorted(root.glob("*.json"))
    for path in paths:
        if path.name.startswith("."):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        subject = (data.get("metadata") or {}).get("subject") or path.stem
        for entry in data.get("courses") or []:
            course = course_from_community(str(subject), entry)
            if not course:
                continue
            enrollment = (entry.get("enrollment_requirement") or "").strip()
            if enrollment:
                enrollments[course.code] = enrollment
    return enrollments


@dataclass
class ReparseStats:
    scanned: int = 0
    updated: int = 0
    structured: int = 0
    raw: int = 0


def reparse_catalog_prerequisites(
    db: CatalogDB,
    data_path: Path | None = None,
) -> ReparseStats:
    """Re-derive prerequisite ASTs for every course already in the catalog."""
    enrollments = load_community_enrollments(data_path)
    stats = ReparseStats()
    for course in db.all_courses():
        stats.scanned += 1
        enrollment = enrollments.get(course.code)
        prereq_raw, prereq, exclusion_raw, exclusion_codes = resolve_prerequisite_fields(
            course_code=course.code,
            enrollment=enrollment,
            prereq_raw=course.prerequisite_raw if not enrollment else None,
            exclusion_raw=course.exclusion_raw if not enrollment else None,
        )
        changed = (
            prereq_raw != course.prerequisite_raw
            or prereq != course.prerequisite
            or exclusion_raw != course.exclusion_raw
            or exclusion_codes != course.exclusion_codes
        )
        if changed:
            course.prerequisite_raw = prereq_raw
            course.prerequisite = prereq
            course.exclusion_raw = exclusion_raw
            course.exclusion_codes = exclusion_codes
            db.upsert_course(course)
            stats.updated += 1
        if prereq.kind == "raw":
            stats.raw += 1
        elif prereq.kind != "none":
            stats.structured += 1
    return stats
