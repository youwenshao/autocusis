"""SQLite persistence for the course catalog.

The catalog DB is the indexed, queryable store of structured course records
extracted from PDFs / the catalog browser. Personal profile and requirement
data live in editable YAML files instead (see ``profile.py`` and
``requirements/``).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .models import Course, CourseAvailability, PrereqExpr, Term
from .paths import catalog_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    code             TEXT PRIMARY KEY,
    course_id        TEXT,
    title_en         TEXT,
    title_zh         TEXT,
    units            REAL NOT NULL DEFAULT 3.0,
    description_en   TEXT,
    description_zh   TEXT,
    prerequisite_raw TEXT,
    prerequisite     TEXT,            -- JSON-encoded PrereqExpr
    exclusion_raw    TEXT,
    exclusion_codes  TEXT,            -- JSON list
    components       TEXT,            -- JSON list
    learning_outcomes TEXT,           -- JSON list
    academic_org     TEXT,
    subject          TEXT,
    grading_basis    TEXT,
    source           TEXT,
    source_url       TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_courses_subject ON courses(subject);

CREATE TABLE IF NOT EXISTS availability (
    code   TEXT PRIMARY KEY,
    terms  TEXT,                      -- JSON list of term ints
    note   TEXT,
    source TEXT,
    year   TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


class CatalogDB:
    """Thin wrapper around a SQLite connection holding the course catalog."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else catalog_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CatalogDB":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- courses ------------------------------------------------------------
    def upsert_course(self, course: Course) -> None:
        self.conn.execute(
            """
            INSERT INTO courses (
                code, course_id, title_en, title_zh, units, description_en,
                description_zh, prerequisite_raw, prerequisite, exclusion_raw,
                exclusion_codes, components, learning_outcomes, academic_org,
                subject, grading_basis, source, source_url, updated_at
            ) VALUES (
                :code, :course_id, :title_en, :title_zh, :units, :description_en,
                :description_zh, :prerequisite_raw, :prerequisite, :exclusion_raw,
                :exclusion_codes, :components, :learning_outcomes, :academic_org,
                :subject, :grading_basis, :source, :source_url, datetime('now')
            )
            ON CONFLICT(code) DO UPDATE SET
                course_id=excluded.course_id,
                title_en=excluded.title_en,
                title_zh=excluded.title_zh,
                units=excluded.units,
                description_en=excluded.description_en,
                description_zh=excluded.description_zh,
                prerequisite_raw=excluded.prerequisite_raw,
                prerequisite=excluded.prerequisite,
                exclusion_raw=excluded.exclusion_raw,
                exclusion_codes=excluded.exclusion_codes,
                components=excluded.components,
                learning_outcomes=excluded.learning_outcomes,
                academic_org=excluded.academic_org,
                subject=excluded.subject,
                grading_basis=excluded.grading_basis,
                source=excluded.source,
                source_url=excluded.source_url,
                updated_at=datetime('now')
            """,
            {
                "code": course.code,
                "course_id": course.course_id,
                "title_en": course.title_en,
                "title_zh": course.title_zh,
                "units": course.units,
                "description_en": course.description_en,
                "description_zh": course.description_zh,
                "prerequisite_raw": course.prerequisite_raw,
                "prerequisite": course.prerequisite.model_dump_json(),
                "exclusion_raw": course.exclusion_raw,
                "exclusion_codes": json.dumps(course.exclusion_codes),
                "components": json.dumps(course.components),
                "learning_outcomes": json.dumps(course.learning_outcomes),
                "academic_org": course.academic_org,
                "subject": course.subject,
                "grading_basis": course.grading_basis,
                "source": course.source,
                "source_url": course.source_url,
            },
        )
        self.conn.commit()

    @staticmethod
    def _row_to_course(row: sqlite3.Row) -> Course:
        return Course(
            code=row["code"],
            course_id=row["course_id"],
            title_en=row["title_en"],
            title_zh=row["title_zh"],
            units=row["units"],
            description_en=row["description_en"],
            description_zh=row["description_zh"],
            prerequisite_raw=row["prerequisite_raw"],
            prerequisite=PrereqExpr.model_validate_json(row["prerequisite"])
            if row["prerequisite"]
            else PrereqExpr.none(),
            exclusion_raw=row["exclusion_raw"],
            exclusion_codes=json.loads(row["exclusion_codes"] or "[]"),
            components=json.loads(row["components"] or "[]"),
            learning_outcomes=json.loads(row["learning_outcomes"] or "[]"),
            academic_org=row["academic_org"],
            subject=row["subject"],
            grading_basis=row["grading_basis"],
            source=row["source"] or "pdf",
            source_url=row["source_url"],
        )

    def get_course(self, code: str) -> Optional[Course]:
        row = self.conn.execute(
            "SELECT * FROM courses WHERE code = ?", (code.upper(),)
        ).fetchone()
        return self._row_to_course(row) if row else None

    def all_courses(self) -> list[Course]:
        rows = self.conn.execute("SELECT * FROM courses ORDER BY code").fetchall()
        return [self._row_to_course(r) for r in rows]

    def count_courses(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM courses").fetchone()[0]

    # -- availability -------------------------------------------------------
    def upsert_availability(self, av: CourseAvailability) -> None:
        self.conn.execute(
            """
            INSERT INTO availability (code, terms, note, source, year, updated_at)
            VALUES (:code, :terms, :note, :source, :year, datetime('now'))
            ON CONFLICT(code) DO UPDATE SET
                terms=excluded.terms,
                note=excluded.note,
                source=excluded.source,
                year=excluded.year,
                updated_at=datetime('now')
            """,
            {
                "code": av.code,
                "terms": json.dumps([int(t) for t in av.terms]),
                "note": av.note,
                "source": av.source,
                "year": av.year,
            },
        )
        self.conn.commit()

    def get_availability(self, code: str) -> Optional[CourseAvailability]:
        row = self.conn.execute(
            "SELECT * FROM availability WHERE code = ?", (code.upper(),)
        ).fetchone()
        if not row:
            return None
        return CourseAvailability(
            code=row["code"],
            terms=[Term(t) for t in json.loads(row["terms"] or "[]")],
            note=row["note"],
            source=row["source"] or "default",
            year=row["year"],
        )

    def all_availability(self) -> list[CourseAvailability]:
        rows = self.conn.execute("SELECT * FROM availability ORDER BY code").fetchall()
        return [
            CourseAvailability(
                code=r["code"],
                terms=[Term(t) for t in json.loads(r["terms"] or "[]")],
                note=r["note"],
                source=r["source"] or "default",
                year=r["year"],
            )
            for r in rows
        ]


@contextmanager
def open_catalog(path: Optional[Path] = None) -> Iterator[CatalogDB]:
    db = CatalogDB(path)
    try:
        yield db
    finally:
        db.close()
