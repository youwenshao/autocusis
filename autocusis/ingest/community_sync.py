"""Sync community course JSON into availability.yaml and sections.sqlite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Literal

from ..models import CourseAvailability, Term
from ..sections.bundle_builder import build_bundles
from ..sections.db import SectionsDB, SyncStats
from .adapters import (
    CanonicalCourseTerm,
    iter_cutopia_file,
    iter_eaglezhen_file,
    iter_queuesis_file,
)
from .availability_store import AvailabilityStore

SourceKind = Literal["eaglezhen", "cutopia", "queuesis"]


def _parse_subjects(raw: str | None) -> set[str] | None:
    if not raw:
        return None
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


def _iter_source_files(data_path: Path, source: SourceKind) -> Iterator[Path]:
    if data_path.is_file():
        yield data_path
        return
    pattern = "*.json"
    for p in sorted(data_path.glob(pattern)):
        if p.name.startswith("."):
            continue
        yield p


def _iter_records(
    source: SourceKind,
    data_path: Path,
    term_filter: str,
    subjects: set[str] | None,
) -> Iterator[CanonicalCourseTerm]:
    for path in _iter_source_files(data_path, source):
        if source == "eaglezhen":
            yield from iter_eaglezhen_file(path, term_filter=term_filter, subjects=subjects)
        elif source == "cutopia":
            yield from iter_cutopia_file(path, term_filter=term_filter, subjects=subjects)
        else:
            yield from iter_queuesis_file(path, term_filter=term_filter, subjects=subjects)


def _write_course_term(
    db: SectionsDB,
    conn,
    record: CanonicalCourseTerm,
    *,
    exclude_full: bool = True,
) -> int:
    bundles = build_bundles(
        record.course_code, record.sections, exclude_full=exclude_full
    )
    if not bundles:
        return 0

    conn.execute(
        """
        INSERT OR REPLACE INTO courses_meta
        (course_code, term_label, title, credits, enrollment_requirement, scraped_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.course_code,
            record.term_label,
            record.title,
            record.credits,
            record.enrollment_requirement,
            record.scraped_at,
            record.source,
        ),
    )
    conn.execute(
        "DELETE FROM section_groups WHERE course_code = ? AND term_label = ?",
        (record.course_code, record.term_label),
    )
    conn.execute(
        "DELETE FROM section_slots WHERE course_code = ? AND term_label = ?",
        (record.course_code, record.term_label),
    )

    n_bundles = 0
    for bundle in bundles:
        cur = conn.execute(
            """
            INSERT INTO section_groups
            (course_code, term_label, bundle_id, sections_json, min_seats_remaining)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.course_code,
                record.term_label,
                bundle.bundle_id,
                json.dumps(bundle.sections),
                bundle.min_seats_remaining,
            ),
        )
        bundle_row_id = cur.lastrowid
        for meeting in bundle.meetings:
            conn.execute(
                """
                INSERT INTO section_slots
                (bundle_row_id, course_code, term_label, section_id, section_type,
                 day, start_time, end_time, location, instructor,
                 quota, enrolled, seats_remaining)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle_row_id,
                    record.course_code,
                    record.term_label,
                    meeting.section_id,
                    meeting.section_type,
                    meeting.slot.day,
                    meeting.slot.start_time,
                    meeting.slot.end_time,
                    meeting.slot.location,
                    meeting.instructor,
                    None,
                    None,
                    meeting.seats_remaining,
                ),
            )
        n_bundles += 1
    return n_bundles


def sync_community(
    source: SourceKind,
    data_path: Path,
    term_filter: str,
    *,
    subjects: str | None = None,
    dry_run: bool = False,
    exclude_full: bool = True,
) -> SyncStats:
    stats = SyncStats()
    subject_set = _parse_subjects(subjects)
    availability = AvailabilityStore.load()
    db = SectionsDB()

    term_labels: set[str] = set()
    records = list(_iter_records(source, data_path, term_filter, subject_set))

    if dry_run:
        stats.courses_written = len(records)
        stats.bundles_written = sum(
            len(build_bundles(r.course_code, r.sections, exclude_full=exclude_full))
            for r in records
        )
        stats.availability_codes = len({r.course_code for r in records})
        return stats

    with db.connect() as conn:
        for record in records:
            term_labels.add(record.term_label)
        for tl in term_labels:
            db.clear_term(conn, tl)

        seen_codes: set[str] = set()
        for record in records:
            n = _write_course_term(
                db, conn, record, exclude_full=exclude_full
            )
            if n == 0:
                continue
            stats.courses_written += 1
            stats.bundles_written += n
            stats.slots_written += sum(len(b.meetings) for b in build_bundles(
                record.course_code, record.sections, exclude_full=exclude_full
            ))

            av = CourseAvailability(
                code=record.course_code,
                terms=[Term(record.term_num)],
                source="community",
                year=record.year_label,
                note=f"synced from {source} {record.term_label}",
            )
            if availability.upsert(av, respect_precedence=True):
                seen_codes.add(record.course_code)
            else:
                stats.skipped_manual += 1

        stats.availability_codes = len(seen_codes)

    availability.save()
    return stats
