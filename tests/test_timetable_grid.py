"""Tests for shared timetable grid model."""

from autocusis.reports.timetable_grid import (
    build_term_timetable,
    meetings_from_sections,
    normalize_day,
    parse_time_minutes,
    term_label_slug,
)
from autocusis.sections.orchestrator import SelectedSection


def test_normalize_day():
    assert normalize_day("Mon") == "Monday"
    assert normalize_day("friday") == "Friday"


def test_parse_time_minutes():
    assert parse_time_minutes("10:30") == 630
    assert parse_time_minutes("16:30") == 990


def test_term_label_slug():
    assert term_label_slug("2026-27 Term 1") == "2026-27-term-1"


def test_meetings_from_sections_empty():
    sec = SelectedSection(course_code="CSCI2100", bundle_id="B", schedule=[])
    assert meetings_from_sections([sec]) == []


def test_build_term_timetable_bounds():
    sec = SelectedSection(
        course_code="CSCI2100",
        bundle_id="B",
        schedule=[
            {
                "day": "Tuesday",
                "start": "14:30",
                "end": "15:15",
                "section_id": "B",
                "type": "Lecture",
            }
        ],
    )
    grid = build_term_timetable("2026-27 Term 2", [sec], section_status="resolved")
    assert grid.has_meetings
    assert grid.day_start <= 14 * 60 + 30
    assert grid.day_end >= 15 * 60 + 15
    assert "Tuesday" in grid.days_present
