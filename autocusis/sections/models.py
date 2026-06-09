"""Section-level timetable models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import PreferenceMode

SectionStatus = Literal["resolved", "partial", "no_data", "infeasible", "relaxed"]


class TimeSlot(BaseModel):
    day: str
    start_time: str
    end_time: str
    location: str | None = None

    def start_minutes(self) -> int:
        h, m = map(int, self.start_time.split(":"))
        return h * 60 + m

    def end_minutes(self) -> int:
        h, m = map(int, self.end_time.split(":"))
        return h * 60 + m


class SectionMeeting(BaseModel):
    course_code: str
    section_id: str
    section_type: str
    parent_lecture_id: str | None = None
    slot: TimeSlot
    instructor: str | None = None
    seats_remaining: int | None = None


class SectionBundle(BaseModel):
    bundle_id: str
    course_code: str
    sections: list[dict[str, Any]] = Field(default_factory=list)
    meetings: list[SectionMeeting] = Field(default_factory=list)
    min_seats_remaining: int | None = None

    def section_ids(self) -> list[str]:
        return [s.get("section_id", "") for s in self.sections]


class TermSchedule(BaseModel):
    term_label: str
    course_codes: list[str]
    bundles_by_course: dict[str, list[SectionBundle]] = Field(default_factory=dict)


class GeneratedScheduleResult(BaseModel):
    bundles: list[SectionBundle] = Field(default_factory=list)
    score: float = 0.0
