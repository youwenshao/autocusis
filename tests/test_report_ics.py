"""Tests for ICS calendar export."""

import json
from pathlib import Path

from autocusis.reports.ics import render_term_ics
from autocusis.reports.timetable_grid import build_term_timetable
from autocusis.sections.orchestrator import SectionPlan

FIXTURE = Path(__file__).parent / "fixtures" / "plan_section_snippet.json"


def test_render_term_ics_structure():
    plan = SectionPlan.model_validate(json.loads(FIXTURE.read_text()))
    sem = plan.semesters[0]
    grid = build_term_timetable(sem.academic_term, sem.sections, section_status=sem.section_status)
    ics = render_term_ics(grid)
    assert "BEGIN:VCALENDAR" in ics
    assert "BEGIN:VEVENT" in ics
    assert "RRULE:FREQ=WEEKLY" in ics
    assert "BYDAY=MO" in ics or "BYDAY=WE" in ics
    assert "Asia/Hong_Kong" in ics
    assert "AIST3030" in ics
