"""Tests for SVG timetable renderer."""

import json
from pathlib import Path

from autocusis.reports.svg import render_term_svg, validate_svg
from autocusis.reports.timetable_grid import build_term_timetable
from autocusis.sections.orchestrator import SectionPlan

FIXTURE = Path(__file__).parent / "fixtures" / "plan_section_snippet.json"


def test_render_term_svg_contains_course_codes():
    plan = SectionPlan.model_validate(json.loads(FIXTURE.read_text()))
    sem = plan.semesters[0]
    grid = build_term_timetable(sem.academic_term, sem.sections, section_status=sem.section_status)
    svg = render_term_svg(grid)
    assert validate_svg(svg)
    assert "AIST3030" in svg
    assert "Mon" in svg or "Monday" in svg


def test_empty_svg_valid():
    grid = build_term_timetable("2026-27 Term 1", [], section_status="no_data")
    svg = render_term_svg(grid)
    assert validate_svg(svg)
    assert "no timetable data" in svg
