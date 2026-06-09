"""Canonical intermediate models for community data ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CanonicalMeeting:
    day: str
    start_time: str  # HH:MM 24h
    end_time: str
    location: str | None = None
    instructor: str | None = None


@dataclass
class CanonicalSection:
    section_id: str
    section_type: str  # Lecture, Tutorial, Lab, Seminar, Other
    class_number: int | None = None
    parent_lecture_id: str | None = None
    meetings: list[CanonicalMeeting] = field(default_factory=list)
    quota: int | None = None
    enrolled: int | None = None
    seats_remaining: int | None = None
    language: str | None = None


@dataclass
class CanonicalCourseTerm:
    course_code: str
    title: str | None
    credits: float | None
    term_label: str
    year_label: str
    term_num: int
    enrollment_requirement: str | None = None
    scraped_at: str | None = None
    source: str = "community"
    sections: list[CanonicalSection] = field(default_factory=list)
