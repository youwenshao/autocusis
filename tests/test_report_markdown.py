"""Tests for Markdown course sheet export."""

import json
from pathlib import Path

from autocusis.profile import Profile
from autocusis.reports.markdown import section_plan_to_markdown
from autocusis.reports.context import ReportContext
from autocusis.sections.orchestrator import SectionPlan

FIXTURE = Path(__file__).parent / "fixtures" / "plan_section_snippet.json"


def test_section_plan_to_markdown_structure():
    plan = SectionPlan.model_validate(json.loads(FIXTURE.read_text()))
    ctx = ReportContext(profile=Profile(program="AIST", cohort="2024"))
    md = section_plan_to_markdown(
        plan,
        ctx,
        svg_slugs={"2026-27 Term 1": "2026-27-term-1"},
    )
    assert "## 2026-27 Term 1" in md
    assert "### Sections" in md
    assert "timetables/2026-27-term-1.svg" in md
    assert "AIST3030" in md
    assert "## Summary" in md
