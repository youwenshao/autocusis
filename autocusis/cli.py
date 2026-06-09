"""AutoCUSIS command-line interface.

Built incrementally across phases. Subcommand groups:
  ingest        - fetch & parse course catalog PDFs into the SQLite catalog
  availability  - sync/set per-course term availability (scrape + manual)
  status        - show graduation-requirement progress and gaps
  profile       - inspect the personal profile
  plan          - generate optimal study schedules
  course        - show a single course's details
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .db import open_catalog

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="AutoCUSIS - CUHK academic progress planner & study-scheme optimizer.",
)
console = Console()


@app.command()
def version() -> None:
    """Print the AutoCUSIS version."""
    console.print(f"AutoCUSIS [bold cyan]{__version__}[/]")


@app.command("db-info")
def db_info() -> None:
    """Show catalog database location and record counts."""
    with open_catalog() as db:
        n_courses = db.count_courses()
        n_avail = len(db.all_availability())
        table = Table(title="AutoCUSIS catalog")
        table.add_column("Item")
        table.add_column("Value", style="cyan")
        table.add_row("Database", str(db.path))
        table.add_row("Courses", str(n_courses))
        table.add_row("Availability records", str(n_avail))
        console.print(table)


# --- subcommand groups (populated in later phases) -------------------------
from .ingest.commands import ingest_app  # noqa: E402
from .ingest.availability_commands import availability_app  # noqa: E402
from .requirements.commands import status_app, profile_app  # noqa: E402
from .scheduler.commands import plan_app, course_app, schema_app  # noqa: E402
from .sections.commands import sections_app  # noqa: E402
from .data_commands import data_app  # noqa: E402

app.add_typer(ingest_app, name="ingest")
app.add_typer(availability_app, name="availability")
app.add_typer(status_app, name="status")
app.add_typer(profile_app, name="profile")
app.add_typer(plan_app, name="plan")
app.add_typer(course_app, name="course")
app.add_typer(sections_app, name="sections")
app.add_typer(data_app, name="data")
app.add_typer(schema_app, name="schema")


if __name__ == "__main__":
    app()
