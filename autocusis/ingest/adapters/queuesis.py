"""Adapter for Queuesis flat course JSON snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from ..term_normalize import normalize_term, term_matches_filter
from .types import CanonicalCourseTerm, CanonicalMeeting, CanonicalSection


def iter_queuesis_file(
    path: Path,
    *,
    term_filter: str,
    subjects: set[str] | None = None,
) -> Iterator[CanonicalCourseTerm]:
    for course in json.loads(path.read_text(encoding="utf-8")):
        course_code = (course.get("courseCode") or "").replace(" ", "").upper()
        if not course_code:
            continue
        subject = course_code[:4].rstrip("0123456789")
        if len(subject) < 2:
            subject = (course.get("department") or "")[:4].upper()
        if subjects and not any(course_code.startswith(s) for s in subjects):
            continue

        term_raw = course.get("term") or ""
        norm = normalize_term(term_raw.replace("-T", " Term "))
        if not norm:
            norm = normalize_term(term_filter)
        if not norm or not term_matches_filter(norm.term_label, term_filter):
            continue

        sections: list[CanonicalSection] = []
        for sec in course.get("sections") or []:
            meetings: list[CanonicalMeeting] = []
            for slot in sec.get("timeSlots") or []:
                meetings.append(
                    CanonicalMeeting(
                        day=slot.get("day") or "Monday",
                        start_time=(slot.get("startTime") or "00:00")[:5],
                        end_time=(slot.get("endTime") or "00:00")[:5],
                        location=slot.get("location"),
                    )
                )
            if not meetings:
                continue
            stype = sec.get("sectionType") or "Other"
            section_id = str(sec.get("sectionId") or "")
            parent = sec.get("parentLecture")
            sections.append(
                CanonicalSection(
                    section_id=section_id,
                    section_type=stype,
                    class_number=sec.get("classNumber"),
                    parent_lecture_id=str(parent) if parent else None,
                    meetings=meetings,
                    quota=sec.get("quota"),
                    enrolled=sec.get("enrolled"),
                    seats_remaining=sec.get("seatsRemaining"),
                    language=sec.get("language"),
                )
            )

        if not sections:
            continue

        last_updated = course.get("lastUpdated")
        scraped_at = str(last_updated) if last_updated else None

        yield CanonicalCourseTerm(
            course_code=course_code,
            title=(course.get("courseName") or "").strip() or None,
            credits=float(course.get("credits") or 0) or None,
            term_label=norm.term_label,
            year_label=norm.year_label,
            term_num=int(norm.term),
            enrollment_requirement=(course.get("enrollmentRequirements") or "").strip() or None,
            scraped_at=scraped_at,
            source="queuesis",
            sections=sections,
        )
