"""Orchestrate full report bundle export to a directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..sections.orchestrator import SectionPlan
from .context import ReportContext
from .html import render_html_report
from .ics import render_term_ics
from .markdown import section_plan_to_markdown
from .svg import render_term_svg
from .timetable_grid import build_term_timetable, term_label_slug


@dataclass
class ReportPaths:
    out_dir: Path
    markdown: Path
    html: Path
    timetables: list[Path]
    calendars: list[Path]


def export_report_bundle(
    section_plan: SectionPlan,
    out_dir: Path,
    context: ReportContext,
) -> ReportPaths:
    out_dir = Path(out_dir)
    tt_dir = out_dir / "timetables"
    cal_dir = out_dir / "calendars"
    tt_dir.mkdir(parents=True, exist_ok=True)
    cal_dir.mkdir(parents=True, exist_ok=True)

    timetables: list[Path] = []
    calendars: list[Path] = []
    svg_by_term: dict[str, str] = {}
    svg_slugs: dict[str, str] = {}
    ics_slugs: dict[str, str] = {}

    for sem in section_plan.semesters:
        if not sem.courses:
            continue
        term = sem.academic_term or sem.label
        slug = term_label_slug(term)
        svg_slugs[term] = slug

        grid = build_term_timetable(term, sem.sections, section_status=sem.section_status)
        svg = render_term_svg(grid)
        svg_path = tt_dir / f"{slug}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        timetables.append(svg_path)
        svg_by_term[term] = svg

        ics = render_term_ics(grid)
        ics_rel = f"calendars/{slug}.ics"
        ics_path = cal_dir / f"{slug}.ics"
        ics_path.write_text(ics, encoding="utf-8")
        calendars.append(ics_path)
        ics_slugs[term] = ics_rel

    md_path = out_dir / "course-sheet.md"
    md_path.write_text(
        section_plan_to_markdown(section_plan, context, svg_slugs=svg_slugs),
        encoding="utf-8",
    )

    html_path = out_dir / "index.html"
    html_path.write_text(
        render_html_report(
            section_plan,
            context,
            svg_by_term=svg_by_term,
            ics_slugs=ics_slugs,
        ),
        encoding="utf-8",
    )

    return ReportPaths(
        out_dir=out_dir,
        markdown=md_path,
        html=html_path,
        timetables=timetables,
        calendars=calendars,
    )
