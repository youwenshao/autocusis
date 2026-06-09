"""Adapter for CUtopia / cuhk-course-data legacy JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..term_normalize import normalize_term, term_matches_filter
from .time_parse import cutopia_day, parse_section_meta, resolve_parent_lecture
from .types import CanonicalCourseTerm, CanonicalMeeting, CanonicalSection


def iter_cutopia_file(
    path: Path,
    *,
    term_filter: str,
    subjects: set[str] | None = None,
) -> Iterator[CanonicalCourseTerm]:
    subject = path.stem.upper()
    if subjects and subject not in subjects:
        return

    for course in json.loads(path.read_text(encoding="utf-8")):
        code_part = str(course.get("code", "")).strip()
        if not code_part:
            continue
        course_code = f"{subject}{code_part}".upper()

        for term_name, term_data in (course.get("terms") or {}).items():
            norm = normalize_term(term_name)
            if not norm or not term_matches_filter(norm.term_label, term_filter):
                continue

            sections: list[CanonicalSection] = []
            for section_label, sec in (term_data or {}).items():
                section_id, stype, class_number = parse_section_meta(section_label)
                days = sec.get("days") or []
                starts = sec.get("startTimes") or []
                ends = sec.get("endTimes") or []
                locations = sec.get("locations") or []
                instructors = sec.get("instructors") or []
                meetings: list[CanonicalMeeting] = []
                for i, day_num in enumerate(days):
                    if i >= len(starts) or i >= len(ends):
                        break
                    meetings.append(
                        CanonicalMeeting(
                            day=cutopia_day(int(day_num)),
                            start_time=str(starts[i])[:5],
                            end_time=str(ends[i])[:5],
                            location=locations[i] if i < len(locations) else None,
                            instructor=instructors[i] if i < len(instructors) else None,
                        )
                    )
                if not meetings:
                    continue
                sections.append(
                    CanonicalSection(
                        section_id=section_id,
                        section_type=stype,
                        class_number=class_number,
                        parent_lecture_id=resolve_parent_lecture(section_id, stype),
                        meetings=meetings,
                    )
                )

            if not sections:
                continue

            yield CanonicalCourseTerm(
                course_code=course_code,
                title=(course.get("title") or "").strip() or None,
                credits=float(course.get("units") or 0) or None,
                term_label=norm.term_label,
                year_label=norm.year_label,
                term_num=int(norm.term),
                enrollment_requirement=(course.get("requirements") or "").strip() or None,
                source="cutopia",
                sections=sections,
            )
