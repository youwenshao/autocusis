"""``autocusis report`` — re-render study plan exports from JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ..db import open_catalog
from ..paths import default_requirements_path
from ..profile import Profile
from ..requirements.engine import evaluate
from ..requirements.schema import Curriculum
from ..sections.orchestrator import SectionPlan
from ..services import course_title, make_credit_fn
from .bundle import export_report_bundle
from .context import ReportContext

report_app = typer.Typer(no_args_is_help=False, help="Export formatted study plan reports.")
console = Console()


def build_report_context(
    profile: Profile,
    curriculum_path: Path | None = None,
) -> ReportContext:
    curriculum: Curriculum | None = None
    progress = None
    cpath = curriculum_path or default_requirements_path()
    if Path(cpath).exists():
        curriculum = Curriculum.load(cpath)
        with open_catalog() as db:
            credit_fn = make_credit_fn(db, profile)
            progress = evaluate(curriculum, profile, credit_fn)

    def title_fn(code: str) -> str | None:
        with open_catalog() as db:
            return course_title(db, code)

    return ReportContext(
        profile=profile,
        curriculum=curriculum,
        progress=progress,
        title_fn=title_fn,
    )


@report_app.callback(invoke_without_command=True)
def report(
    ctx: typer.Context,
    from_json: Path = typer.Option(..., "--from", help="SectionPlan JSON (from autocusis plan --export-json)."),
    out_dir: Path = typer.Option(..., "--out", help="Output directory for report bundle."),
    curriculum_path: Optional[Path] = typer.Option(None, "--curriculum", "-c", help="Curriculum YAML."),
) -> None:
    """Write Markdown, HTML, SVG timetables, and ICS calendars from a saved plan."""
    if ctx.invoked_subcommand is not None:
        return
    if not from_json.exists():
        console.print(f"[red]File not found:[/] {from_json}")
        raise typer.Exit(1)
    raw = json.loads(from_json.read_text(encoding="utf-8"))
    section_plan = SectionPlan.model_validate(raw)
    if not section_plan.feasible:
        console.print("[red]Plan is not feasible; nothing to export.[/]")
        raise typer.Exit(1)

    profile = Profile.load()
    context = build_report_context(profile, curriculum_path)
    paths = export_report_bundle(section_plan, out_dir, context)
    console.print(
        f"[green]Wrote report bundle to {paths.out_dir}[/] "
        f"(course-sheet.md, index.html, {len(paths.timetables)} timetables, "
        f"{len(paths.calendars)} calendars)"
    )
