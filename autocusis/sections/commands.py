"""``autocusis sections`` subcommands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from ..models import PreferenceMode
from ..profile import Profile
from .db import SectionsDB
from .solver import generate_schedules

sections_app = typer.Typer(no_args_is_help=True, help="Section-level timetable scheduling.")
console = Console()


@sections_app.command("generate")
def sections_generate(
    term_label: str = typer.Option(..., "--term-label", help='e.g. "2026-27 Term 1"'),
    courses: str = typer.Option(..., "--courses", help="Comma-separated course codes."),
    preference: str = typer.Option("daysOff", "--preference"),
    count: int = typer.Option(5, "--count", "-n"),
    export_json: Optional[Path] = typer.Option(None, "--export-json"),
) -> None:
    """Generate section schedules for a course list in one term."""
    if preference not in (
        "shortBreaks", "longBreaks", "consistentStart", "morning",
        "startLate", "endEarly", "daysOff",
    ):
        console.print("[red]Invalid preference mode[/]")
        raise typer.Exit(1)

    profile = Profile.load()
    db = SectionsDB()
    codes = [c.strip().upper() for c in courses.split(",") if c.strip()]
    options = {c: db.load_bundles(c, term_label) for c in codes}
    pins = profile.pins_for_term(term_label)

    schedules = generate_schedules(
        codes,
        options,
        preference=preference,  # type: ignore[arg-type]
        max_results=count,
        pins=pins or None,
    )
    if not schedules:
        console.print("[red]No feasible schedules found.[/]")
        raise typer.Exit(1)

    payload = []
    for i, sched in enumerate(schedules):
        payload.append(
            {
                "rank": i + 1,
                "score": sched.score,
                "courses": [
                    {
                        "code": b.course_code,
                        "bundle_id": b.bundle_id,
                        "sections": b.section_ids(),
                    }
                    for b in sched.bundles
                ],
            }
        )
        console.print(f"[bold]Schedule #{i + 1}[/] score={sched.score:.0f}")

    if export_json:
        Path(export_json).write_text(json.dumps(payload, indent=2))
        console.print(f"[green]Wrote {export_json}[/]")
