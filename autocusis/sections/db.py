"""SQLite store for term-scoped section and bundle data."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .. import paths
from .models import SectionBundle, SectionMeeting, TimeSlot

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses_meta (
    course_code TEXT NOT NULL,
    term_label TEXT NOT NULL,
    title TEXT,
    credits REAL,
    enrollment_requirement TEXT,
    scraped_at TEXT,
    source TEXT NOT NULL,
    PRIMARY KEY (course_code, term_label)
);

CREATE TABLE IF NOT EXISTS section_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code TEXT NOT NULL,
    term_label TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    sections_json TEXT NOT NULL,
    min_seats_remaining INTEGER,
    UNIQUE(course_code, term_label, bundle_id)
);

CREATE TABLE IF NOT EXISTS section_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_row_id INTEGER NOT NULL,
    course_code TEXT NOT NULL,
    term_label TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_type TEXT NOT NULL,
    day TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    location TEXT,
    instructor TEXT,
    quota INTEGER,
    enrolled INTEGER,
    seats_remaining INTEGER,
    FOREIGN KEY (bundle_row_id) REFERENCES section_groups(id)
);

CREATE INDEX IF NOT EXISTS idx_slots_course_term
    ON section_slots(course_code, term_label);
CREATE INDEX IF NOT EXISTS idx_groups_course_term
    ON section_groups(course_code, term_label);
CREATE INDEX IF NOT EXISTS idx_meta_term ON courses_meta(term_label);
"""


@dataclass
class SyncStats:
    courses_written: int = 0
    bundles_written: int = 0
    slots_written: int = 0
    availability_codes: int = 0
    skipped_manual: int = 0


class SectionsDB:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or paths.sections_db_path())

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(_SCHEMA)
            yield conn
            conn.commit()
        finally:
            conn.close()

    def clear_term(self, conn: sqlite3.Connection, term_label: str) -> None:
        conn.execute(
            "DELETE FROM section_slots WHERE term_label = ?", (term_label,)
        )
        conn.execute(
            "DELETE FROM section_groups WHERE term_label = ?", (term_label,)
        )
        conn.execute(
            "DELETE FROM courses_meta WHERE term_label = ?", (term_label,)
        )

    def list_terms(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT term_label, COUNT(DISTINCT course_code) AS courses,
                       MAX(scraped_at) AS scraped_at
                FROM courses_meta
                GROUP BY term_label
                ORDER BY term_label
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def course_count(self, term_label: str | None = None) -> int:
        with self.connect() as conn:
            if term_label:
                row = conn.execute(
                    "SELECT COUNT(*) FROM courses_meta WHERE term_label = ?",
                    (term_label,),
                ).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) FROM courses_meta").fetchone()
        return int(row[0]) if row else 0

    def load_bundles(
        self, course_code: str, term_label: str
    ) -> list[SectionBundle]:
        code = course_code.upper()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT bundle_id, sections_json, min_seats_remaining
                FROM section_groups
                WHERE course_code = ? AND term_label = ?
                """,
                (code, term_label),
            ).fetchall()
        bundles: list[SectionBundle] = []
        for row in rows:
            sections_data = json.loads(row["sections_json"])
            meetings: list[SectionMeeting] = []
            for sec in sections_data:
                for m in sec.get("meetings", []):
                    meetings.append(
                        SectionMeeting(
                            course_code=code,
                            section_id=sec["section_id"],
                            section_type=sec["section_type"],
                            parent_lecture_id=sec.get("parent_lecture_id"),
                            slot=TimeSlot(
                                day=m["day"],
                                start_time=m["start_time"],
                                end_time=m["end_time"],
                                location=m.get("location"),
                            ),
                            instructor=m.get("instructor"),
                            seats_remaining=sec.get("seats_remaining"),
                        )
                    )
            bundles.append(
                SectionBundle(
                    bundle_id=row["bundle_id"],
                    course_code=code,
                    sections=sections_data,
                    meetings=meetings,
                    min_seats_remaining=row["min_seats_remaining"],
                )
            )
        return bundles

    def scraped_at(self, term_label: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(scraped_at) FROM courses_meta WHERE term_label = ?",
                (term_label,),
            ).fetchone()
        return row[0] if row and row[0] else None

    def bundle_source(self, course_code: str, term_label: str) -> str | None:
        """Return the ``courses_meta.source`` for a course-term, or None if absent.

        Used to distinguish authoritative (scraped/community) data from
        ``extrapolated`` data when classifying section trust.
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT source FROM courses_meta WHERE course_code = ? AND term_label = ?",
                (course_code.upper(), term_label),
            ).fetchone()
        return row[0] if row and row[0] else None

    def courses_for_term(self, term_label: str) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT course_code FROM courses_meta WHERE term_label = ?",
                (term_label,),
            ).fetchall()
        return {r[0] for r in rows}


@contextmanager
def open_sections_db(path: Path | None = None):
    db = SectionsDB(path)
    with db.connect() as conn:
        yield db, conn
