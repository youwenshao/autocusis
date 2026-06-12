"""End-to-end report bundle export tests."""

import json
from pathlib import Path

from autocusis.profile import Profile
from autocusis.reports.bundle import export_report_bundle
from autocusis.reports.context import ReportContext
from autocusis.sections.orchestrator import SectionPlan

FIXTURE = Path(__file__).parent / "fixtures" / "plan_section_snippet.json"


def test_export_report_bundle_layout(tmp_path):
    plan = SectionPlan.model_validate(json.loads(FIXTURE.read_text()))
    ctx = ReportContext(profile=Profile(program="AIST"))
    out = tmp_path / "report"
    paths = export_report_bundle(plan, out, ctx)

    assert paths.markdown.exists()
    assert paths.html.exists()
    assert len(paths.timetables) == 2
    assert len(paths.calendars) == 2
    assert (out / "timetables" / "2026-27-term-1.svg").exists()
    assert (out / "calendars" / "2026-27-term-1.ics").exists()
    md = paths.markdown.read_text()
    assert "2026-27 Term 1" in md
    html = paths.html.read_text()
    assert "<svg" in html
    assert "Study Plan" in html
