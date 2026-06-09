"""``autocusis plan`` and ``autocusis course`` subcommands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..db import open_catalog
from ..ingest.availability_store import AvailabilityStore
from ..paths import default_requirements_path
from ..profile import Profile
from ..reports import plan_to_csv, plan_to_markdown, render_plan
from ..requirements.schema import Curriculum
from ..sections.db import SectionsDB
from ..sections.orchestrator import SectionPlan, attach_sections
from .service import build_scheduler_input
from .solver import solve

plan_app = typer.Typer(no_args_is_help=False, help="Generate optimal study plans.")
course_app = typer.Typer(no_args_is_help=False, help="Show a course's catalog details.")
schema_app = typer.Typer(no_args_is_help=False, help="Export JSON schemas for agents.")
console = Console()


@plan_app.callback(invoke_without_command=True)
def plan(
    ctx: typer.Context,
    curriculum_path: Optional[Path] = typer.Option(None, "--curriculum", "-c", help="Curriculum YAML (default: data/requirements/aist.yaml)."),
    count: int = typer.Option(1, "--count", "-n", min=1, help="Number of alternative plans to generate."),
    horizon: Optional[int] = typer.Option(None, "--horizon", help="Override planning horizon (years)."),
    time_limit: float = typer.Option(15.0, "--time-limit", help="Solver time limit per stage (seconds)."),
    assume_unavailable: bool = typer.Option(
        True,
        "--assume-unavailable/--assume-available",
        help="Treat courses with unknown availability as NOT offered (default: unavailable).",
    ),
    strict: bool = typer.Option(False, "--strict", help="Block courses with unparsed (raw) prerequisites."),
    spread: bool = typer.Option(
        False,
        "--spread",
        help="Spread courses across the full planning horizon (graduate as late as allowed).",
    ),
    with_sections: bool = typer.Option(False, "--with-sections", help="Attach section-level schedules where data exists."),
    section_aware: bool = typer.Option(
        True,
        "--section-aware/--no-section-aware",
        help="Plan around section timetable clashes (real-data clashes hard, extrapolated soft).",
    ),
    preference: Optional[str] = typer.Option(None, "--preference", help="Section preference mode (daysOff, etc.)."),
    stream: Optional[str] = typer.Option(None, "--stream", help="Elective specialization stream id to bias electives toward."),
    export_md: Optional[Path] = typer.Option(None, "--export-md", help="Write the first plan to a Markdown file."),
    export_csv: Optional[Path] = typer.Option(None, "--export-csv", help="Write the first plan to a CSV file."),
    export_json: Optional[Path] = typer.Option(None, "--export-json", help="Write agent-readable JSON plan."),
) -> None:
    """Generate one or more optimal study schedules for the remaining courses."""
    if ctx.invoked_subcommand is not None:
        return
    cpath = curriculum_path or default_requirements_path()
    if not Path(cpath).exists():
        console.print(f"[red]No curriculum at {cpath}. Fill in your requirements first.[/]")
        raise typer.Exit(1)
    curriculum = Curriculum.load(cpath)
    profile = Profile.load()
    if horizon is not None:
        profile.planning_horizon_years = horizon
    if spread:
        profile.planning_mode = "spread"
    if not section_aware:
        profile.section_aware = False
    if stream is not None:
        if curriculum.stream(stream) is None:
            valid = ", ".join(s.id for s in curriculum.elective_streams) or "(none defined)"
            console.print(f"[red]Unknown stream '{stream}'.[/] Valid streams: {valid}")
            raise typer.Exit(1)
        profile.elective_stream = stream

    with open_catalog() as db:
        availability = AvailabilityStore.load()
        sections_db = SectionsDB()
        inp, demand = build_scheduler_input(
            db, curriculum, profile, availability,
            assume_unknown_available=not assume_unavailable,
            strict_prereqs=strict,
            sections_db=sections_db,
        )
        if not demand.mandatory and not demand.electives:
            console.print("[bold green]No outstanding requirements - nothing to plan. Congrats![/]")
            return
        plans = solve(inp, max_plans=count, time_limit_s=time_limit)

        if inp.preferred_stream:
            active = curriculum.stream(inp.preferred_stream)
            if active is not None:
                console.print(
                    f"[cyan]Electives biased toward stream:[/] {active.name} "
                    f"[dim]({active.id})[/]"
                )

        first_plan = plans[0]
        section_plan: SectionPlan | None = None
        if with_sections or export_json:
            pref = preference or profile.schedule_preferences.mode
            section_plan = attach_sections(
                first_plan, profile, db, sections_db,
                preference=pref,  # type: ignore[arg-type]
                strict=strict,
            )

    for i, p in enumerate(plans):
        render_plan(console, p, index=i if len(plans) > 1 else None)
        if with_sections and section_plan and i == 0:
            for sem in section_plan.semesters:
                if sem.sections:
                    console.print(
                        f"  [cyan]{sem.academic_term}[/] sections "
                        f"({sem.section_status}): "
                        + ", ".join(s.course_code for s in sem.sections)
                    )
                elif sem.section_notes:
                    for note in sem.section_notes:
                        console.print(f"  [yellow]{sem.academic_term}:[/] {note}")

    first = plans[0]
    if export_md and first.feasible:
        Path(export_md).write_text(plan_to_markdown(first))
        console.print(f"[green]Wrote Markdown plan to {export_md}[/]")
    if export_csv and first.feasible:
        Path(export_csv).write_text(plan_to_csv(first))
        console.print(f"[green]Wrote CSV plan to {export_csv}[/]")
    if export_json and section_plan:
        Path(export_json).write_text(
            json.dumps(section_plan.model_dump(mode="json"), indent=2)
        )
        console.print(f"[green]Wrote JSON plan to {export_json}[/]")
    elif export_json and first.feasible:
        Path(export_json).write_text(
            json.dumps(
                {
                    "feasible": first.feasible,
                    "notes": first.notes,
                    "objective_terms_used": first.objective_terms_used,
                    "peak_term_credits": first.peak_term_credits,
                    "semesters": [
                        {
                            "label": sem.label,
                            "courses": [c.model_dump() for c in sem.courses],
                        }
                        for sem in first.semesters
                    ],
                },
                indent=2,
            )
        )
        console.print(f"[green]Wrote JSON plan to {export_json}[/]")

    if not first.feasible:
        raise typer.Exit(1)


@schema_app.callback(invoke_without_command=True)
def schema_plan(ctx: typer.Context) -> None:
    """Dump JSON Schema for SectionPlan (agent tool definitions)."""
    if ctx.invoked_subcommand is not None:
        return
    console.print_json(SectionPlan.model_json_schema())


@course_app.callback(invoke_without_command=True)
def course(
    ctx: typer.Context,
    code: Optional[str] = typer.Argument(None, help="Course code, e.g. AIST1110."),
) -> None:
    """Show a single course's parsed catalog details."""
    if ctx.invoked_subcommand is not None:
        return
    if not code:
        console.print("[yellow]Provide a course code, e.g. 'autocusis course AIST1110'.[/]")
        raise typer.Exit(1)
    code = code.upper().replace(" ", "")
    with open_catalog() as db:
        c = db.get_course(code)
        av = db.get_availability(code)
        sdb = SectionsDB()
    if not c:
        console.print(f"[red]{code} not in catalog.[/] Ingest it: 'autocusis ingest course {code}'.")
        raise typer.Exit(1)

    title = " / ".join(x for x in [c.title_en, c.title_zh] if x)
    console.print(Panel(f"[bold]{c.code}[/]  {title}   [cyan]{c.units:g} credits[/]", title="Course"))

    table = Table(show_header=False, box=None)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    if c.subject:
        table.add_row("Subject", c.subject)
    if c.academic_org:
        table.add_row("Academic org", c.academic_org)
    if c.grading_basis:
        table.add_row("Grading", c.grading_basis)
    table.add_row("Prerequisite", c.prerequisite.to_text() or "[dim]none[/]")
    if c.prerequisite.kind == "raw":
        table.add_row("", "[yellow](unparsed - review manually)[/]")
    table.add_row("Exclusions", ", ".join(c.exclusion_codes) or "[dim]none[/]")
    table.add_row("Components", ", ".join(c.components) or "[dim]n/a[/]")
    if av and av.terms:
        table.add_row("Offered", ", ".join(t.label for t in av.terms) + f"  [dim]({av.source})[/]")
    else:
        table.add_row("Offered", "[dim]unknown (run ingest update)[/]")
    n_terms = len(sdb.list_terms())
    if n_terms:
        table.add_row("Section data", f"{sdb.course_count()} course-term records across {n_terms} terms")
    table.add_row("Source", c.source_url or c.source)
    console.print(table)

    if c.description_en:
        console.print(Panel(c.description_en, title="Description", expand=True))
    if c.learning_outcomes:
        console.print("[bold]Learning outcomes:[/]")
        for i, o in enumerate(c.learning_outcomes, 1):
            console.print(f"  {i}. {o}")
