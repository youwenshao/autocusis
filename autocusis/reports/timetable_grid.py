"""Shared weekly timetable grid model for SVG, HTML, and ICS renderers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..sections.orchestrator import SelectedSection

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

DAY_ABBREV = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun",
}

# Deterministic palette for course blocks (fill, stroke).
COURSE_COLORS = [
    ("#dbeafe", "#2563eb"),
    ("#dcfce7", "#16a34a"),
    ("#fef3c7", "#d97706"),
    ("#fce7f3", "#db2777"),
    ("#e0e7ff", "#4f46e5"),
    ("#ccfbf1", "#0d9488"),
    ("#fee2e2", "#dc2626"),
    ("#f3e8ff", "#9333ea"),
    ("#ffedd5", "#ea580c"),
    ("#ecfccb", "#65a30d"),
    ("#cffafe", "#0891b2"),
    ("#fae8ff", "#c026d3"),
]

_GRID_MIN = 8 * 60
_GRID_MAX = 20 * 60
_PAD_MINUTES = 30


@dataclass
class GridMeeting:
    course_code: str
    section_id: str
    section_type: str
    day: str
    start_minutes: int
    end_minutes: int
    location: str | None = None


@dataclass
class TermTimetable:
    term_label: str
    meetings: list[GridMeeting] = field(default_factory=list)
    days_present: list[str] = field(default_factory=list)
    day_start: int = _GRID_MIN
    day_end: int = _GRID_MAX
    section_status: str = "no_data"

    @property
    def has_meetings(self) -> bool:
        return bool(self.meetings)


def day_index(day: str) -> int:
    normalized = normalize_day(day)
    try:
        return DAY_ORDER.index(normalized)
    except ValueError:
        return 0


def normalize_day(day: str) -> str:
    d = day.strip()
    if d in DAY_ORDER:
        return d
    lower = d.lower()
    for full in DAY_ORDER:
        if full.lower().startswith(lower) or lower.startswith(full.lower()[:3]):
            return full
    return DAY_ORDER[0]


def parse_time_minutes(value: str) -> int:
    parts = value.strip().split(":")
    if len(parts) != 2:
        return 0
    h, m = int(parts[0]), int(parts[1])
    return h * 60 + m


def format_time_minutes(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h:02d}:{m:02d}"


def term_label_slug(term_label: str) -> str:
    slug = term_label.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def course_color(course_code: str) -> tuple[str, str]:
    idx = hash(course_code.upper()) % len(COURSE_COLORS)
    return COURSE_COLORS[idx]


def meetings_from_sections(sections: list[SelectedSection]) -> list[GridMeeting]:
    out: list[GridMeeting] = []
    for sec in sections:
        for entry in sec.schedule:
            day = normalize_day(str(entry.get("day", "")))
            start = parse_time_minutes(str(entry.get("start", "00:00")))
            end = parse_time_minutes(str(entry.get("end", "00:00")))
            if end <= start:
                continue
            out.append(
                GridMeeting(
                    course_code=sec.course_code,
                    section_id=str(entry.get("section_id", sec.lecture or "")),
                    section_type=str(entry.get("type", "Class")),
                    day=day,
                    start_minutes=start,
                    end_minutes=end,
                    location=entry.get("location"),
                )
            )
    out.sort(key=lambda m: (day_index(m.day), m.start_minutes, m.course_code))
    return out


def build_term_timetable(
    term_label: str,
    sections: list[SelectedSection],
    *,
    section_status: str = "no_data",
) -> TermTimetable:
    meetings = meetings_from_sections(sections)
    days_present = _days_with_meetings(meetings)
    day_start, day_end = _grid_bounds(meetings)
    return TermTimetable(
        term_label=term_label,
        meetings=meetings,
        days_present=days_present,
        day_start=day_start,
        day_end=day_end,
        section_status=section_status,
    )


def _days_with_meetings(meetings: list[GridMeeting]) -> list[str]:
    used = {m.day for m in meetings}
    return [d for d in DAY_ORDER if d in used]


def _grid_bounds(meetings: list[GridMeeting]) -> tuple[int, int]:
    if not meetings:
        return _GRID_MIN, _GRID_MAX
    lo = min(m.start_minutes for m in meetings) - _PAD_MINUTES
    hi = max(m.end_minutes for m in meetings) + _PAD_MINUTES
    lo = max(_GRID_MIN, (lo // 30) * 30)
    hi = min(_GRID_MAX, ((hi + 29) // 30) * 30)
    if hi <= lo:
        hi = lo + 120
    return lo, hi
