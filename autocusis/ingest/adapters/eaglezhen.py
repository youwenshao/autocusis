"""Adapter for EagleZhen another-cuhk-course-planner v2 JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..term_normalize import normalize_term, term_matches_filter
from .time_parse import parse_eaglezhen_time, parse_section_meta, resolve_parent_lecture
from .types import CanonicalCourseTerm, CanonicalMeeting, CanonicalSection


def _parse_num(val: str | int | float | None) -> int | None:
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except ValueError:
        return None


def _parse_float(val: str | int | float | None) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def iter_eaglezhen_file(
    path: Path,
    *,
    term_filter: str,
    subjects: set[str] | None = None,
) -> Iterator[CanonicalCourseTerm]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scraped_at = (data.get("metadata") or {}).get("scraped_at")
    subject_meta = (data.get("metadata") or {}).get("subject", path.stem)

    for course in data.get("courses") or []:
        subject = (course.get("subject") or subject_meta or "").upper()
        if subjects and subject not in subjects:
            continue
        code_part = str(course.get("course_code", "")).strip()
        if not code_part:
            continue
        course_code = f"{subject}{code_part}".replace(" ", "").upper()

        for term in course.get("terms") or []:
            term_name = term.get("term_name") or ""
            norm = normalize_term(term_name, term_code=term.get("term_code"))
            if not norm or not term_matches_filter(norm.term_label, term_filter):
                continue

            sections: list[CanonicalSection] = []
            for sec_raw in term.get("schedule") or []:
                section_label = sec_raw.get("section") or ""
                section_id, stype, class_number = parse_section_meta(section_label)
                meetings: list[CanonicalMeeting] = []
                seen: set[tuple[str, str, str]] = set()
                for mtg in sec_raw.get("meetings") or []:
                    parsed = parse_eaglezhen_time(mtg.get("time") or "")
                    if not parsed:
                        continue
                    day, start, end = parsed
                    key = (day, start, end)
                    if key in seen:
                        continue
                    seen.add(key)
                    meetings.append(
                        CanonicalMeeting(
                            day=day,
                            start_time=start,
                            end_time=end,
                            location=(mtg.get("location") or "").strip() or None,
                            instructor=(mtg.get("instructor") or "").strip() or None,
                        )
                    )
                if not meetings:
                    continue
                avail = sec_raw.get("availability") or {}
                sections.append(
                    CanonicalSection(
                        section_id=section_id,
                        section_type=stype,
                        class_number=class_number,
                        parent_lecture_id=resolve_parent_lecture(section_id, stype),
                        meetings=meetings,
                        quota=_parse_num(avail.get("capacity")),
                        enrolled=_parse_num(avail.get("enrolled")),
                        seats_remaining=_parse_num(avail.get("available_seats")),
                        language=(sec_raw.get("class_attributes") or "").strip() or None,
                    )
                )

            if not sections:
                continue

            yield CanonicalCourseTerm(
                course_code=course_code,
                title=(course.get("title") or "").strip() or None,
                credits=_parse_float(course.get("credits")),
                term_label=norm.term_label,
                year_label=norm.year_label,
                term_num=int(norm.term),
                enrollment_requirement=(course.get("enrollment_requirement") or "").strip() or None,
                scraped_at=scraped_at,
                source="eaglezhen",
                sections=sections,
            )
