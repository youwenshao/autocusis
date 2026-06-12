"""Markdown course sheet export from SectionPlan."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import __version__
from ..sections.orchestrator import SectionPlan
from .context import ReportContext
from .timetable_grid import term_label_slug


def section_plan_to_markdown(
    section_plan: SectionPlan,
    context: ReportContext,
    *,
    svg_slugs: dict[str, str] | None = None,
) -> str:
    svg_slugs = svg_slugs or {}
    lines: list[str] = []

    program = context.profile.program
    cohort = context.profile.cohort
    title = f"# Study Plan — {program}"
    if cohort:
        title += f" (cohort {cohort})"
    lines.append(title)
    lines.append("")

    stream = context.stream_name()
    if stream:
        lines.append(f"**Elective stream:** {stream}")
        lines.append("")

    if not section_plan.feasible:
        lines.append("**No feasible plan found.**")
        lines.append("")
        lines.extend(f"- {n}" for n in section_plan.notes)
        return "\n".join(lines) + "\n"

    total_courses = sum(len(s.courses) for s in section_plan.semesters)
    total_credits = sum(s.total_credits for s in section_plan.semesters)

    lines.extend(
        [
            "## Summary",
            "",
            f"- Terms to completion: **{section_plan.objective_terms_used}**",
            f"- Courses planned: **{total_courses}**",
            f"- Credits planned: **{total_credits:g}**",
            f"- Peak load: **{section_plan.peak_term_credits:g} cr/term**",
            "",
        ]
    )

    if context.profile.completed:
        lines.extend(["## Completed courses", ""])
        lines.append("| Code | Cr | Title | Term |")
        lines.append("| --- | --- | --- | --- |")
        for cc in context.profile.completed:
            title = context.course_title(cc.code) or "—"
            cr = cc.credits if cc.credits is not None else "—"
            term = cc.term_taken or "—"
            lines.append(f"| {cc.code} | {cr} | {title} | {term} |")
        lines.append("")

    all_warnings = list(section_plan.warnings)
    if section_plan.notes:
        all_warnings.extend(section_plan.notes)
    if all_warnings:
        lines.extend(["## Warnings & notes", ""])
        lines.extend(f"- {w}" for w in all_warnings)
        lines.append("")

    for sem in section_plan.semesters:
        if not sem.courses:
            continue
        term = sem.academic_term or sem.label
        lines.append(f"## {term}")
        lines.append("")
        lines.append(
            f"**{sem.total_credits:g} credits** · section status: `{sem.section_status}`"
        )
        lines.append("")

        lines.append("### Courses")
        lines.append("")
        lines.append("| Code | Cr | Title | Flags |")
        lines.append("| --- | --- | --- | --- |")
        for c in sem.courses:
            flags = _course_flags(c)
            title = "Free elective" if c.is_filler else (c.title or context.course_title(c.code) or "—")
            lines.append(f"| {c.code} | {c.credits:g} | {title} | {flags} |")
        lines.append("")

        sections_by_code = {s.course_code: s for s in sem.sections}
        if sections_by_code:
            lines.append("### Sections")
            lines.append("")
            lines.append("| Code | Lec | Tut | Lab | Seats |")
            lines.append("| --- | --- | --- | --- | --- |")
            for code, sec in sorted(sections_by_code.items()):
                seats = sec.seats_remaining if sec.seats_remaining is not None else "—"
                lines.append(
                    f"| {code} | {sec.lecture or '—'} | {sec.tutorial or '—'} | "
                    f"{sec.lab or '—'} | {seats} |"
                )
            lines.append("")

            meeting_rows = []
            for sec in sem.sections:
                for entry in sec.schedule:
                    meeting_rows.append(
                        (
                            entry.get("day", "—"),
                            f"{entry.get('start', '?')}–{entry.get('end', '?')}",
                            entry.get("type", "—"),
                            entry.get("section_id", "—"),
                            entry.get("location") or "—",
                        )
                    )
            if meeting_rows:
                lines.append("### Meeting times")
                lines.append("")
                lines.append("| Day | Time | Type | Section | Location |")
                lines.append("| --- | --- | --- | --- | --- |")
                for day, time_rng, typ, sid, loc in meeting_rows:
                    lines.append(f"| {day} | {time_rng} | {typ} | {sid} | {loc} |")
                lines.append("")

        slug = svg_slugs.get(term) or term_label_slug(term)
        if sem.sections and any(s.schedule for s in sem.sections):
            lines.append("### Weekly timetable")
            lines.append("")
            lines.append(f"![{term}](timetables/{slug}.svg)")
            lines.append("")
        elif sem.sections:
            lines.append("*No meeting times available for graphical timetable.*")
            lines.append("")

        notes = list(sem.section_notes)
        prov = section_plan.data_provenance.get(term)
        if prov:
            notes.append(f"Section data source timestamp: {prov}")
        if notes:
            lines.append("> " + "\n> ".join(notes))
            lines.append("")

    lines.extend(
        [
            "---",
            "",
            f"*Generated by AutoCUSIS {__version__} on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.*",
            "",
            "*Section schedules may use extrapolated data from prior terms. "
            "ICS calendar files use approximate term dates for visualization only.*",
            "",
        ]
    )
    return "\n".join(lines)


def _course_flags(course) -> str:
    flags: list[str] = []
    if course.pinned:
        flags.append("pinned")
    if course.is_filler:
        flags.append("free elective")
    if course.section_trust:
        flags.append(course.section_trust)
    return ", ".join(flags) if flags else "—"
