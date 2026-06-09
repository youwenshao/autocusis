"""``autocusis ingest`` subcommands: fetch & parse course PDFs into the catalog."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ..db import open_catalog
from ..paths import community_data_dir, html_cache_dir
from .pdf_fetcher import PdfFetchError, extract_text, fetch_pdf, pdf_url_for
from .pdf_parser import parse_course_text

ingest_app = typer.Typer(no_args_is_help=True, help="Fetch and parse course catalog PDFs.")
console = Console()

_CODE_RE = re.compile(r"^[A-Z]{2,5}\d{3,4}[A-Z]?$")


def _normalize(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def _ingest_from_html(code: str, html_path) -> tuple[bool, str]:
    from .catalog_scraper import parse_catalog_html

    html = html_path.read_text(encoding="utf-8", errors="replace")
    course = parse_catalog_html(code, html)
    with open_catalog() as db:
        db.upsert_course(course)
    detail = course.title_en or course.title_zh or "(no title)"
    return True, f"{course.units:g}cr  {detail} (catalog)"


def _catalog_html_hint(code: str) -> str:
    cache = html_cache_dir() / f"{code}.html"
    return (
        f"No public PDF for {code}. Save the Course Catalog detail page "
        f"(logged into CUSIS) to:\n  {cache}\n"
        f"then re-run 'autocusis ingest course {code}', or run:\n"
        f"  autocusis ingest html {code} PAGE.html"
    )


def _ingest_one(code: str, force: bool) -> tuple[bool, str]:
    code = _normalize(code)
    if not _CODE_RE.match(code):
        return False, f"invalid course code '{code}'"
    if pdf_url_for(code):
        try:
            path = fetch_pdf(code, force=force)
        except PdfFetchError as e:
            return False, str(e)
        text = extract_text(path)
        if not text.strip():
            return False, "empty PDF text extraction"
        course = parse_course_text(code, text, source_url=pdf_url_for(code))
        with open_catalog() as db:
            db.upsert_course(course)
        detail = course.title_en or course.title_zh or "(no title)"
        return True, f"{course.units:g}cr  {detail}"

    cached_html = html_cache_dir() / f"{code}.html"
    if cached_html.exists():
        try:
            return _ingest_from_html(code, cached_html)
        except Exception as e:
            return False, f"failed to parse cached catalog HTML: {e}"
    return False, _catalog_html_hint(code)


@ingest_app.command("course")
def ingest_course(
    codes: list[str] = typer.Argument(..., help="Course codes, e.g. AIST1110 CSCI2100"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-download cached PDFs."),
) -> None:
    """Ingest one or more courses by code."""
    table = Table(title="Ingestion results")
    table.add_column("Code")
    table.add_column("Status")
    table.add_column("Detail")
    ok_count = 0
    for raw in codes:
        ok, msg = _ingest_one(raw, force)
        ok_count += int(ok)
        table.add_row(
            _normalize(raw),
            "[green]OK[/]" if ok else "[red]FAIL[/]",
            msg,
        )
    console.print(table)
    console.print(f"Ingested [bold green]{ok_count}[/]/{len(codes)} courses.")


@ingest_app.command("file")
def ingest_file(
    path: typer.FileText = typer.Argument(..., help="Text file with one course code per line."),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """Ingest course codes listed in a file (one per line, '#' comments allowed)."""
    codes = []
    for line in path:
        line = line.split("#", 1)[0].strip()
        if line:
            codes.append(line)
    if not codes:
        console.print("[yellow]No course codes found in file.[/]")
        raise typer.Exit(1)
    ingest_course(codes=codes, force=force)


@ingest_app.command("html")
def ingest_html(
    code: str = typer.Argument(..., help="Course code, e.g. MATH1010"),
    path: typer.FileText = typer.Argument(..., help="Saved Course Catalog detail HTML page."),
) -> None:
    """Ingest a non-CSE course from a saved Catalog browser HTML page.

    Use this for subjects without a public CSE PDF (MATH, GE, language, ...).
    Save the catalog detail page from a logged-in CUSIS session, then pass it.
    """
    from .catalog_scraper import parse_catalog_html

    html = path.read()
    course = parse_catalog_html(_normalize(code), html)
    with open_catalog() as db:
        db.upsert_course(course)
    console.print(
        f"[green]Ingested[/] {course.code} (catalog): "
        f"{course.units:g}cr  {course.title_en or course.title_zh or '(no title)'}"
    )


@ingest_app.command("sync-community")
def ingest_sync_community(
    source: str = typer.Option(
        "eaglezhen",
        "--source",
        help="Data format: eaglezhen, cutopia, or queuesis.",
    ),
    data_path: Path = typer.Option(..., "--data-path", help="Directory or JSON file."),
    term: str = typer.Option(..., "--term", help='Academic term filter, e.g. "2025-26 Term 2".'),
    subjects: Optional[str] = typer.Option(None, "--subjects", help="Comma-separated subject prefixes."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print stats without writing."),
) -> None:
    """Import community course JSON into sections.sqlite and availability.yaml."""
    from .community_sync import sync_community

    if source not in ("eaglezhen", "cutopia", "queuesis"):
        console.print("[red]--source must be eaglezhen, cutopia, or queuesis[/]")
        raise typer.Exit(1)
    stats = sync_community(
        source,  # type: ignore[arg-type]
        data_path,
        term,
        subjects=subjects,
        dry_run=dry_run,
    )
    console.print(
        f"[green]Synced[/] {stats.courses_written} courses, "
        f"{stats.bundles_written} bundles, "
        f"{stats.availability_codes} availability records "
        f"({stats.skipped_manual} skipped manual overrides)"
    )


@ingest_app.command("community-catalog")
def ingest_community_catalog(
    data_path: Optional[Path] = typer.Option(
        None, "--data-path", help="Community JSON directory (default: data/community/)."
    ),
    missing_only: bool = typer.Option(
        True,
        "--missing-only/--all",
        help="Only add courses not already in catalog.sqlite (default: missing only).",
    ),
) -> None:
    """Ingest course metadata from EagleZhen community JSON into the catalog."""
    from .community_catalog import sync_community_catalog

    path = data_path or community_data_dir()
    with open_catalog() as db:
        stats = sync_community_catalog(db, path, missing_only=missing_only)
    console.print(
        f"[green]Community catalog:[/] scanned {stats.scanned}, "
        f"inserted {stats.inserted}, skipped existing {stats.skipped_existing}"
    )


@ingest_app.command("reparse-prereqs")
def ingest_reparse_prereqs(
    data_path: Optional[Path] = typer.Option(
        None, "--data-path", help="Community JSON directory (default: data/community/)."
    ),
) -> None:
    """Re-parse prerequisite ASTs for all catalog courses using improved rules."""
    from .community_catalog import reparse_catalog_prerequisites

    path = data_path or community_data_dir()
    with open_catalog() as db:
        stats = reparse_catalog_prerequisites(db, path)
    console.print(
        f"[green]Re-parsed prerequisites:[/] scanned {stats.scanned}, "
        f"updated {stats.updated}, structured {stats.structured}, raw {stats.raw}"
    )


@ingest_app.command("extrapolate-terms")
def ingest_extrapolate_terms(
    from_term: str = typer.Option(
        "2025-26 Term 1",
        "--from-term",
        help="Source scraped term label, e.g. '2025-26 Term 1'.",
    ),
    years: str = typer.Option(
        "1,2",
        "--years",
        help="Year offsets to project forward (default: 1,2 → next two academic years).",
    ),
) -> None:
    """Copy section data to future terms (assume identical offerings as prior years)."""
    from ..sections.db import SectionsDB
    from .term_extrapolate import extrapolate_years

    deltas = [int(x.strip()) for x in years.split(",") if x.strip()]
    sdb = SectionsDB()
    stats = extrapolate_years(sdb, from_term, deltas)
    console.print(
        f"[green]Extrapolated[/] {stats.courses_written} courses, "
        f"{stats.bundles_written} bundles into {stats.terms_written} term(s) from {from_term}"
    )


@ingest_app.command("update")
def ingest_update(
    source: str = typer.Option("github", "--source", help="github or local."),
    term: str = typer.Option(..., "--term", help='Academic term, e.g. "2025-26 Term 2".'),
    subjects: Optional[str] = typer.Option(None, "--subjects", help="Comma-separated subjects."),
    live_scrape: bool = typer.Option(False, "--live-scrape", help="Run external EagleZhen scraper."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Fetch community data (GitHub or local) and sync into AutoCUSIS."""
    from .data_update import update_data

    try:
        data_path, stats = update_data(
            source=source,
            term_filter=term,
            subjects=subjects,
            live_scrape=live_scrape,
            dry_run=dry_run,
        )
    except Exception as e:
        console.print(f"[red]Update failed:[/] {e}")
        raise typer.Exit(1) from e
    console.print(f"[cyan]Data path:[/] {data_path}")
    console.print(
        f"[green]Updated[/] {stats.courses_written} courses, "
        f"{stats.bundles_written} bundles"
    )


@ingest_app.command("show")
def ingest_show(code: str = typer.Argument(..., help="Course code to display from the catalog.")) -> None:
    """Show a stored course's parsed fields (debugging aid)."""
    with open_catalog() as db:
        course = db.get_course(_normalize(code))
    if not course:
        console.print(f"[red]{code} not found in catalog. Run 'ingest course {code}' first.[/]")
        raise typer.Exit(1)
    console.print_json(course.model_dump_json(indent=2))
