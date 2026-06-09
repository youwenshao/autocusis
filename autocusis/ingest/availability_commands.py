"""``autocusis availability`` subcommands: set/sync/list term availability."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..db import open_catalog
from ..models import Term
from .availability_store import AvailabilityStore
from .timetable_scraper import combine_terms, parse_timetable_html

availability_app = typer.Typer(
    no_args_is_help=True, help="Manage per-course term availability (Sem 1/Sem 2)."
)
console = Console()


def _parse_terms(spec: str) -> list[Term]:
    terms: list[Term] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        terms.append(Term(int(part)))
    return terms


def _sync_db(store: AvailabilityStore) -> None:
    """Mirror the YAML store into the SQLite availability table for querying."""
    with open_catalog() as db:
        for av in store.records.values():
            db.upsert_availability(av)


@availability_app.command("set")
def set_availability(
    code: str = typer.Argument(..., help="Course code, e.g. AIST3010"),
    terms: str = typer.Option(..., "--terms", "-t", help="Comma list: 1=T1,2=T2,3=Summer (e.g. '1,2')"),
    note: Optional[str] = typer.Option(None, "--note"),
) -> None:
    """Manually set the terms a course is offered (highest precedence)."""
    store = AvailabilityStore.load()
    store.set_manual(code, _parse_terms(terms), note=note)
    store.save()
    _sync_db(store)
    console.print(
        f"[green]Set[/] {code.upper()} -> terms {[int(t) for t in _parse_terms(terms)]} (manual)"
    )


@availability_app.command("sync")
def sync_availability(
    from_html: Path = typer.Option(..., "--from-html", help="Saved Teaching Timetable results HTML."),
    term: int = typer.Option(..., "--term", help="Which term this page is for: 1, 2, or 3."),
    year: Optional[str] = typer.Option(None, "--year", help="Academic year, e.g. 2025-26."),
    subjects: Optional[str] = typer.Option(None, "--subjects", help="Comma list of subject prefixes to keep, e.g. AIST,CSCI,MATH."),
) -> None:
    """Import term availability from a saved Teaching Timetable page.

    Run once per term (e.g. one page for Term 1, one for Term 2). Scraped data
    will not overwrite your manual overrides.
    """
    html = Path(from_html).read_text(errors="ignore")
    restrict = subjects.split(",") if subjects else None
    scraped = parse_timetable_html(html, Term(term), year=year, restrict_subjects=restrict)
    store = AvailabilityStore.load()
    written = store.merge(scraped, respect_precedence=True)
    store.save()
    _sync_db(store)
    console.print(
        f"Found [cyan]{len(scraped)}[/] offered courses in term {term}; "
        f"wrote/updated [green]{written}[/] (manual overrides preserved)."
    )


@availability_app.command("sync-multi")
def sync_multi(
    term1_html: Optional[Path] = typer.Option(None, "--t1", help="Term 1 results HTML."),
    term2_html: Optional[Path] = typer.Option(None, "--t2", help="Term 2 results HTML."),
    year: Optional[str] = typer.Option(None, "--year"),
    subjects: Optional[str] = typer.Option(None, "--subjects"),
) -> None:
    """Import both terms at once and union them into recurring availability."""
    restrict = subjects.split(",") if subjects else None
    stores = []
    if term1_html:
        stores.append(parse_timetable_html(Path(term1_html).read_text(errors="ignore"), Term.TERM1, year, restrict))
    if term2_html:
        stores.append(parse_timetable_html(Path(term2_html).read_text(errors="ignore"), Term.TERM2, year, restrict))
    if not stores:
        console.print("[yellow]Provide at least one of --t1 / --t2.[/]")
        raise typer.Exit(1)
    combined = combine_terms(stores, year=year)
    store = AvailabilityStore.load()
    written = store.merge(combined, respect_precedence=True)
    store.save()
    _sync_db(store)
    console.print(f"Merged {len(combined)} courses; wrote/updated [green]{written}[/].")


@availability_app.command("list")
def list_availability(
    subject: Optional[str] = typer.Option(None, "--subject", "-s", help="Filter by subject prefix."),
) -> None:
    """List stored availability records."""
    store = AvailabilityStore.load()
    table = Table(title="Course availability")
    table.add_column("Code")
    table.add_column("Terms")
    table.add_column("Source")
    table.add_column("Year")
    table.add_column("Note")
    for code, av in sorted(store.records.items()):
        if subject and not code.startswith(subject.upper()):
            continue
        term_str = ", ".join(t.label for t in av.terms) or "[dim]unknown[/]"
        table.add_row(code, term_str, av.source, av.year or "", av.note or "")
    console.print(table)
    if len(store) == 0:
        console.print("[dim]No availability data yet. Use 'availability set' or 'sync'.[/]")
