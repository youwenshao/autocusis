"""Rich rendering and file export for generated study plans."""

from __future__ import annotations

import csv
import io

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..scheduler.plan import Plan
from .bundle import ReportPaths, export_report_bundle
from .context import ReportContext
from .markdown import section_plan_to_markdown

__all__ = [
    "ReportContext",
    "ReportPaths",
    "export_report_bundle",
    "plan_to_csv",
    "plan_to_markdown",
    "render_plan",
    "section_plan_to_markdown",
]


def render_plan(console: Console, plan: Plan, index: int | None = None) -> None:
    title = "Study plan" if index is None else f"Study plan #{index + 1}"
    if not plan.feasible:
        console.print(Panel("[red]No feasible plan.[/]", title=title))
        for n in plan.notes:
            console.print(f"  [yellow]-[/] {n}")
        return

    header = (
        f"[bold]{plan.objective_terms_used}[/] terms  |  "
        f"{plan.num_courses} courses  |  "
        f"{plan.total_planned_credits:g} credits planned  |  "
        f"peak load {plan.peak_term_credits:g} cr/term"
    )
    console.print(Panel(header, title=title))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Semester", style="cyan", no_wrap=True)
    table.add_column("Cr", justify="right")
    table.add_column("Courses")
    for sem in plan.semesters:
        course_strs = []
        for c in sem.courses:
            tag = ""
            if c.pinned:
                tag = " [magenta](pinned)[/]"
            elif c.is_filler:
                tag = " [dim](free)[/]"
            label = c.code if c.is_filler else f"{c.code}"
            title_part = f" [dim]{c.title}[/]" if c.title and not c.is_filler else ""
            course_strs.append(f"{label}{title_part}{tag}")
        table.add_row(sem.label, f"{sem.total_credits:g}", "\n".join(course_strs))
    console.print(table)
    for sem in plan.semesters:
        if sem.section_status == "relaxed":
            for note in sem.section_notes:
                console.print(f"  [red]{sem.label}:[/] {note}")
        elif sem.section_notes:
            for note in sem.section_notes:
                console.print(f"  [dim]{sem.label}: {note}[/]")
    for n in plan.notes:
        console.print(f"  [yellow]note:[/] {n}")


def plan_to_markdown(plan: Plan) -> str:
    if not plan.feasible:
        lines = ["# Study plan", "", "**No feasible plan found.**", ""]
        lines += [f"- {n}" for n in plan.notes]
        return "\n".join(lines) + "\n"
    lines = [
        "# AutoCUSIS study plan",
        "",
        f"- Terms to completion: **{plan.objective_terms_used}**",
        f"- Courses planned: **{plan.num_courses}**",
        f"- Credits planned: **{plan.total_planned_credits:g}**",
        f"- Peak load: **{plan.peak_term_credits:g} cr/term**",
        "",
        "| Semester | Code | Cr | Title | Flags |",
        "| --- | --- | --- | --- | --- |",
    ]
    for sem in plan.semesters:
        for c in sem.courses:
            flags = []
            if c.pinned:
                flags.append("pinned")
            if c.is_filler:
                flags.append("free elective")
            lines.append(
                f"| {sem.label} | {c.code} | {c.credits:g} | "
                f"{(c.title or '') if not c.is_filler else 'Free elective'} | {', '.join(flags)} |"
            )
    return "\n".join(lines) + "\n"


def plan_to_csv(plan: Plan) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["planning_year", "term", "code", "credits", "title", "is_filler", "pinned"])
    for sem in plan.semesters:
        for c in sem.courses:
            w.writerow(
                [
                    c.planning_year,
                    c.term.label,
                    c.code,
                    f"{c.credits:g}",
                    (c.title or "") if not c.is_filler else "Free elective",
                    int(c.is_filler),
                    int(c.pinned),
                ]
            )
    return buf.getvalue()
