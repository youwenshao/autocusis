"""Extrapolate section data from a scraped term to future academic years."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..sections.db import SectionsDB

_TERM_LABEL_RE = re.compile(r"^(\d{4})-(\d{2}) (Term \d|Summer)$")


@dataclass
class ExtrapolateStats:
    terms_written: int = 0
    courses_written: int = 0
    bundles_written: int = 0


def shift_term_label(term_label: str, year_delta: int) -> str:
    m = _TERM_LABEL_RE.match(term_label.strip())
    if not m:
        raise ValueError(f"Invalid term label: {term_label!r}")
    y1 = int(m.group(1)) + year_delta
    suffix = m.group(3)
    yy2 = str(y1 + 1)[-2:]
    return f"{y1}-{yy2} {suffix}"


def extrapolate_term(
    db: SectionsDB,
    source_label: str,
    target_label: str,
) -> ExtrapolateStats:
    """Copy all section rows from ``source_label`` to ``target_label``."""
    stats = ExtrapolateStats(terms_written=1)
    stamp = datetime.now(timezone.utc).isoformat()
    provenance = f"extrapolated from {source_label} at {stamp}"

    with db.connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM courses_meta WHERE term_label = ? LIMIT 1",
            (source_label,),
        ).fetchone():
            return stats

        db.clear_term(conn, target_label)

        meta_rows = conn.execute(
            """
            SELECT course_code, title, credits, enrollment_requirement, source
            FROM courses_meta
            WHERE term_label = ?
            """,
            (source_label,),
        ).fetchall()

        for row in meta_rows:
            conn.execute(
                """
                INSERT INTO courses_meta
                (course_code, term_label, title, credits, enrollment_requirement,
                 scraped_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["course_code"],
                    target_label,
                    row["title"],
                    row["credits"],
                    row["enrollment_requirement"],
                    provenance,
                    "extrapolated",
                ),
            )
            stats.courses_written += 1

        group_rows = conn.execute(
            """
            SELECT id, course_code, bundle_id, sections_json, min_seats_remaining
            FROM section_groups
            WHERE term_label = ?
            """,
            (source_label,),
        ).fetchall()

        for row in group_rows:
            cur = conn.execute(
                """
                INSERT INTO section_groups
                (course_code, term_label, bundle_id, sections_json, min_seats_remaining)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["course_code"],
                    target_label,
                    row["bundle_id"],
                    row["sections_json"],
                    row["min_seats_remaining"],
                ),
            )
            new_bundle_id = cur.lastrowid
            stats.bundles_written += 1
            slot_rows = conn.execute(
                """
                SELECT section_id, section_type, day, start_time, end_time,
                       location, instructor, quota, enrolled, seats_remaining
                FROM section_slots
                WHERE bundle_row_id = ?
                """,
                (row["id"],),
            ).fetchall()
            for slot in slot_rows:
                conn.execute(
                    """
                    INSERT INTO section_slots
                    (bundle_row_id, course_code, term_label, section_id, section_type,
                     day, start_time, end_time, location, instructor,
                     quota, enrolled, seats_remaining)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_bundle_id,
                        row["course_code"],
                        target_label,
                        slot["section_id"],
                        slot["section_type"],
                        slot["day"],
                        slot["start_time"],
                        slot["end_time"],
                        slot["location"],
                        slot["instructor"],
                        slot["quota"],
                        slot["enrolled"],
                        slot["seats_remaining"],
                    ),
                )

    return stats


def extrapolate_years(
    db: SectionsDB,
    source_label: str,
    year_deltas: list[int],
) -> ExtrapolateStats:
    total = ExtrapolateStats()
    for delta in year_deltas:
        target = shift_term_label(source_label, delta)
        part = extrapolate_term(db, source_label, target)
        total.terms_written += part.terms_written
        total.courses_written += part.courses_written
        total.bundles_written += part.bundles_written
    return total
