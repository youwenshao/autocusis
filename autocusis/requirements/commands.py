"""``autocusis status`` and ``autocusis profile`` subcommands."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..db import open_catalog
from ..models import Term
from ..paths import default_requirements_path, profile_path
from ..profile import CompletedCourse, Profile
from ..services import course_title, make_credit_fn
from .engine import build_demand, evaluate, one_of_gap_summary
from .schema import Curriculum

status_app = typer.Typer(no_args_is_help=False, help="Show graduation-requirement progress.")
profile_app = typer.Typer(no_args_is_help=True, help="Inspect and edit your profile.")
console = Console()


def _load_curriculum(path: Optional[Path]) -> Curriculum:
    path = path or default_requirements_path()
    if not Path(path).exists():
        console.print(
            f"[red]No curriculum file at {path}.[/] "
            "Provide your graduation requirements there (see data/requirements/aist.yaml template)."
        )
        raise typer.Exit(1)
    return Curriculum.load(path)


@status_app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    curriculum_path: Optional[Path] = typer.Option(None, "--curriculum", "-c", help="Curriculum YAML (default: data/requirements/aist.yaml)."),
    show_outstanding: bool = typer.Option(True, "--outstanding/--no-outstanding", help="List outstanding courses."),
) -> None:
    """Report progress toward graduation: per-group status, totals, and gaps."""
    if ctx.invoked_subcommand is not None:
        return
    curriculum = _load_curriculum(curriculum_path)
    profile = Profile.load()
    with open_catalog() as db:
        credit_fn = make_credit_fn(db, profile)
        report = evaluate(curriculum, profile, credit_fn)
        demand = build_demand(report)

        header = (
            f"[bold]{report.program}[/]"
            + (f"  cohort {report.cohort}" if report.cohort else "")
            + f"   credits: [cyan]{report.total_credits_done:g}[/]/"
            f"{report.total_credits_required:g}"
            f"  ([yellow]{report.total_credits_remaining:g} remaining[/])"
        )
        console.print(Panel(header, title="AutoCUSIS progress"))

        table = Table(title="Requirement groups")
        table.add_column("Group")
        table.add_column("Rule")
        table.add_column("Progress")
        table.add_column("Status")
        for g in report.groups:
            if g.kind == "all_of":
                prog = f"{g.count_done}/{len(g.completed_courses) + len(g.outstanding_required)} courses"
            elif g.kind == "credits_from":
                prog = f"{g.credits_done:g}/{g.credits_required:g} cr"
            elif g.kind == "one_of":
                if g.satisfied:
                    prog = "1 track complete"
                elif g.outstanding_required:
                    done = len(g.completed_courses)
                    prog = f"{done}/{done + len(g.outstanding_required)} courses (locked track)"
                else:
                    prog = f"pick 1 of {len(g.tracks_viable)} track(s)"
            else:
                prog = f"{g.count_done}/{g.count_required} courses"
            status_str = "[green]done[/]" if g.satisfied else "[red]open[/]"
            table.add_row(g.name, g.kind, prog, status_str)
        console.print(table)

        if show_outstanding:
            if demand.mandatory:
                mtable = Table(title="Outstanding required courses")
                mtable.add_column("Code")
                mtable.add_column("Cr")
                mtable.add_column("Title")
                for code in demand.mandatory:
                    mtable.add_row(code, f"{credit_fn(code):g}", course_title(db, code) or "[dim]not ingested[/]")
                console.print(mtable)
            groups_by_id = {g.id: g for g in report.groups}
            for e in demand.electives:
                if e.kind == "one_of":
                    gp = groups_by_id.get(e.group_id)
                    summary = one_of_gap_summary(gp) if gp else None
                    if summary:
                        console.print(
                            f"[yellow]Track choice[/] in [bold]{e.group_name}[/]: {summary}"
                        )
                    continue
                need = (
                    f"{e.need_credits:g} cr"
                    if e.kind == "credits_from"
                    else f"{e.need_count} course(s)"
                )
                console.print(
                    f"[yellow]Elective gap[/] in [bold]{e.group_name}[/]: choose {need} "
                    f"from {len(e.pool)} remaining option(s)."
                )
            if report.all_satisfied:
                console.print("[bold green]All requirements satisfied. Ready to graduate![/]")


# --------------------------------------------------------------------------
# profile subcommands
# --------------------------------------------------------------------------
@profile_app.command("show")
def profile_show() -> None:
    """Display the current profile."""
    profile = Profile.load()
    console.print_json(profile.model_dump_json(indent=2))


@profile_app.command("init")
def profile_init(
    program: str = typer.Option("AIST", "--program"),
    cohort: Optional[str] = typer.Option(None, "--cohort"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing profile."),
) -> None:
    """Create a starter profile.yaml."""
    path = profile_path()
    if path.exists() and not force:
        console.print(f"[yellow]{path} already exists. Use --force to overwrite.[/]")
        raise typer.Exit(1)
    profile = Profile(program=program, cohort=cohort)
    profile.save()
    console.print(f"[green]Wrote starter profile to {path}[/]")


@profile_app.command("add-completed")
def add_completed(
    codes: list[str] = typer.Argument(..., help="Completed course codes."),
    grade: Optional[str] = typer.Option(None, "--grade"),
    term: Optional[str] = typer.Option(None, "--term", help="Free-form, e.g. '2024-25 T1'."),
) -> None:
    """Mark one or more courses as completed."""
    profile = Profile.load()
    existing = profile.completed_codes()
    added = 0
    for raw in codes:
        code = raw.upper().replace(" ", "")
        if code in existing:
            continue
        profile.completed.append(CompletedCourse(code=code, grade=grade, term_taken=term))
        existing.add(code)
        added += 1
    profile.save()
    console.print(f"[green]Added {added}[/] completed course(s). Total: {len(profile.completed)}.")


@profile_app.command("add-exemption")
def add_exemption(
    codes: list[str] = typer.Argument(..., help="Course codes satisfied for prereqs only."),
) -> None:
    """Mark prerequisite waivers (e.g. foundation English exemption)."""
    profile = Profile.load()
    existing = profile.prereq_satisfied_codes()
    added = 0
    for raw in codes:
        code = raw.upper().replace(" ", "")
        if code in existing:
            continue
        profile.prereq_satisfied.append(code)
        existing.add(code)
        added += 1
    profile.save()
    console.print(
        f"[green]Added {added}[/] prereq exemption(s). Total: {len(profile.prereq_satisfied)}."
    )


@profile_app.command("set")
def profile_set(
    max_term: Optional[int] = typer.Option(None, "--max-term-credits"),
    min_term: Optional[int] = typer.Option(None, "--min-term-credits"),
    max_year: Optional[int] = typer.Option(None, "--max-year-credits"),
    horizon: Optional[int] = typer.Option(None, "--horizon-years"),
    current_year: Optional[int] = typer.Option(None, "--current-year"),
    current_term: Optional[int] = typer.Option(None, "--current-term", help="1, 2, or 3."),
    allow_summer: Optional[bool] = typer.Option(None, "--allow-summer/--no-summer"),
    planning_mode: Optional[str] = typer.Option(
        None, "--planning-mode", help="fast (default) or spread."
    ),
) -> None:
    """Update planning settings (credit caps, horizon, current position)."""
    profile = Profile.load()
    if max_term is not None:
        profile.max_credits_per_term = max_term
    if min_term is not None:
        profile.min_credits_per_term = min_term
    if max_year is not None:
        profile.max_credits_per_year = max_year
    if horizon is not None:
        profile.planning_horizon_years = horizon
    if current_year is not None:
        profile.current_year = current_year
    if current_term is not None:
        profile.current_term = Term(current_term)
    if allow_summer is not None:
        profile.allow_summer = allow_summer
    if planning_mode is not None:
        if planning_mode not in ("fast", "spread"):
            console.print("[red]--planning-mode must be 'fast' or 'spread'.[/]")
            raise typer.Exit(1)
        profile.planning_mode = planning_mode  # type: ignore[assignment]
    profile.save()
    console.print("[green]Profile updated.[/]")
    profile_show()


@profile_app.command("pin")
def profile_pin(
    code: str = typer.Argument(...),
    year: int = typer.Option(..., "--year", help="Planned year index (1=first planned year)."),
    term: int = typer.Option(..., "--term", help="1, 2, or 3."),
) -> None:
    """Pin a priority course to a specific planned year+term."""
    from ..profile import PriorityPin

    profile = Profile.load()
    profile.priority_pins = [p for p in profile.priority_pins if p.code.upper() != code.upper()]
    profile.priority_pins.append(PriorityPin(code=code.upper(), year=year, term=Term(term)))
    profile.save()
    console.print(f"[green]Pinned[/] {code.upper()} -> year {year}, term {term}.")
