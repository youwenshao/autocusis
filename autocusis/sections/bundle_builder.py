"""Build valid LEC+TUT+LAB section bundles from canonical sections."""

from __future__ import annotations

import itertools
from typing import Any

from ..ingest.adapters.types import CanonicalSection
from .models import SectionBundle, SectionMeeting, TimeSlot


def _is_dependent(stype: str) -> bool:
    return stype != "Lecture"


def _resolve_parent(section: CanonicalSection, lectures: list[CanonicalSection]) -> str | None:
    if not _is_dependent(section.section_type):
        return None
    if section.parent_lecture_id:
        return section.parent_lecture_id
    sid = section.section_id
    if section.section_type == "Tutorial" and len(sid) >= 2 and sid[0].isalpha():
        return sid[0].upper()
    if lectures:
        return lectures[0].section_id
    return None


def _section_dict(sec: CanonicalSection) -> dict[str, Any]:
    return {
        "section_id": sec.section_id,
        "section_type": sec.section_type,
        "parent_lecture_id": sec.parent_lecture_id,
        "class_number": sec.class_number,
        "seats_remaining": sec.seats_remaining,
        "meetings": [
            {
                "day": m.day,
                "start_time": m.start_time,
                "end_time": m.end_time,
                "location": m.location,
                "instructor": m.instructor,
            }
            for m in sec.meetings
        ],
    }


def _to_meetings(course_code: str, sec: CanonicalSection) -> list[SectionMeeting]:
    out: list[SectionMeeting] = []
    for m in sec.meetings:
        out.append(
            SectionMeeting(
                course_code=course_code,
                section_id=sec.section_id,
                section_type=sec.section_type,
                parent_lecture_id=sec.parent_lecture_id,
                slot=TimeSlot(
                    day=m.day,
                    start_time=m.start_time,
                    end_time=m.end_time,
                    location=m.location,
                ),
                instructor=m.instructor,
                seats_remaining=sec.seats_remaining,
            )
        )
    return out


def build_bundles(
    course_code: str,
    sections: list[CanonicalSection],
    *,
    exclude_full: bool = True,
) -> list[SectionBundle]:
    """Return valid section bundles for one course."""
    if exclude_full:
        sections = [
            s
            for s in sections
            if s.seats_remaining is None or s.seats_remaining > 0
        ]
    if not sections:
        return []

    lectures = [s for s in sections if s.section_type == "Lecture"]
    dependents = [s for s in sections if _is_dependent(s.section_type)]

    if not lectures and not dependents:
        # seminar-only or single-component
        bundles: list[SectionBundle] = []
        for sec in sections:
            bid = f"{sec.section_id}"
            bundles.append(
                SectionBundle(
                    bundle_id=bid,
                    course_code=course_code,
                    sections=[_section_dict(sec)],
                    meetings=_to_meetings(course_code, sec),
                    min_seats_remaining=sec.seats_remaining,
                )
            )
        return bundles

    if not lectures:
        # dependents only - treat each as standalone
        bundles = []
        for sec in dependents:
            bundles.append(
                SectionBundle(
                    bundle_id=sec.section_id,
                    course_code=course_code,
                    sections=[_section_dict(sec)],
                    meetings=_to_meetings(course_code, sec),
                    min_seats_remaining=sec.seats_remaining,
                )
            )
        return bundles

    bundles = []
    for lec in lectures:
        linked_tuts = [
            d
            for d in dependents
            if d.section_type == "Tutorial"
            and _resolve_parent(d, [lec]) == lec.section_id
        ]
        linked_labs = [
            d
            for d in dependents
            if d.section_type == "Lab"
            and _resolve_parent(d, [lec]) == lec.section_id
        ]
        universal = [
            d for d in dependents if d.section_type not in ("Tutorial", "Lab")
        ]

        tut_options: list[list[CanonicalSection]] = [[]]
        if linked_tuts:
            tut_options = [[t] for t in linked_tuts]
        lab_options: list[list[CanonicalSection]] = [[]]
        if linked_labs:
            lab_options = [[l] for l in linked_labs]
        uni_options: list[list[CanonicalSection]] = [[]]
        if universal:
            uni_options = [[u] for u in universal]

        for tut_combo, lab_combo, uni_combo in itertools.product(
            tut_options, lab_options, uni_options
        ):
            combo = [lec, *tut_combo, *lab_combo, *uni_combo]
            secs_json = [_section_dict(s) for s in combo]
            meetings: list[SectionMeeting] = []
            for s in combo:
                meetings.extend(_to_meetings(course_code, s))
            seats = [s.seats_remaining for s in combo if s.seats_remaining is not None]
            min_seats = min(seats) if seats else None
            bid = "-".join(s.section_id for s in combo)
            bundles.append(
                SectionBundle(
                    bundle_id=bid,
                    course_code=course_code,
                    sections=secs_json,
                    meetings=meetings,
                    min_seats_remaining=min_seats,
                )
            )

    # dedupe
    seen: set[str] = set()
    unique: list[SectionBundle] = []
    for b in bundles:
        if b.bundle_id in seen:
            continue
        seen.add(b.bundle_id)
        unique.append(b)
    return unique
