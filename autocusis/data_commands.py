"""``autocusis data`` subcommands."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from .db import open_catalog
from .ingest.availability_store import AvailabilityStore
from .paths import sections_db_path
from .sections.db import SectionsDB

data_app = typer.Typer(no_args_is_help=True, help="Data coverage and sync status.")
console = Console()


@data_app.command("status")
def data_status() -> None:
    """Show catalog, availability, and section data coverage."""
    with open_catalog() as db:
        n_courses = db.count_courses()
    avail = AvailabilityStore.load()
    sdb = SectionsDB()

    table = Table(title="AutoCUSIS data status")
    table.add_column("Store")
    table.add_column("Detail", style="cyan")
    table.add_row("Catalog (catalog.sqlite)", f"{n_courses} courses")
    table.add_row("Availability (availability.yaml)", f"{len(avail)} records")
    table.add_row("Sections DB", str(sections_db_path()))
    table.add_row("Section courses (all terms)", str(sdb.course_count()))

    for term_info in sdb.list_terms():
        table.add_row(
            f"  Term {term_info['term_label']}",
            f"{term_info['courses']} courses, scraped_at={term_info.get('scraped_at') or 'unknown'}",
        )
    console.print(table)
