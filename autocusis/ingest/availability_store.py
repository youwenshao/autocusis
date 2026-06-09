"""Load, merge, and persist per-course term availability.

``availability.yaml`` is the canonical, hand-editable store. Records carry a
``source`` so we can apply precedence when merging scraped data with manual
overrides:  manual > timetable > default.

The resolved availability is what the scheduler consumes: for each course it
answers "in which regular terms can this be taken?".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from ..models import CourseAvailability, Term
from .. import paths

_SOURCE_PRECEDENCE = {"default": 0, "timetable": 1, "community": 1, "manual": 2}


class AvailabilityStore:
    """In-memory collection of :class:`CourseAvailability`, keyed by code."""

    def __init__(self, records: Optional[dict[str, CourseAvailability]] = None):
        self.records: dict[str, CourseAvailability] = records or {}

    # -- persistence --------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "AvailabilityStore":
        path = path or paths.availability_path()
        if not Path(path).exists():
            return cls()
        data = yaml.safe_load(Path(path).read_text()) or {}
        records: dict[str, CourseAvailability] = {}
        for code, entry in (data.get("courses") or {}).items():
            entry = entry or {}
            records[code.upper()] = CourseAvailability(
                code=code.upper(),
                terms=[Term(int(t)) for t in (entry.get("terms") or [])],
                note=entry.get("note"),
                source=entry.get("source", "default"),
                year=entry.get("year"),
            )
        return cls(records)

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path or paths.availability_path())
        out = {
            "version": 1,
            "_legend": "terms: 1=Term 1, 2=Term 2, 3=Summer; empty list = unknown",
            "courses": {
                code: {
                    "terms": [int(t) for t in av.terms],
                    "source": av.source,
                    **({"year": av.year} if av.year else {}),
                    **({"note": av.note} if av.note else {}),
                }
                for code, av in sorted(self.records.items())
            },
        }
        path.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True))

    # -- mutation -----------------------------------------------------------
    def upsert(self, av: CourseAvailability, *, respect_precedence: bool = True) -> bool:
        """Insert/update a record. When ``respect_precedence`` is set, a lower
        priority source will not overwrite a higher priority one. Returns True
        if the record was written."""
        code = av.code.upper()
        existing = self.records.get(code)
        if existing and respect_precedence:
            if _SOURCE_PRECEDENCE.get(av.source, 0) < _SOURCE_PRECEDENCE.get(
                existing.source, 0
            ):
                return False
        self.records[code] = av
        return True

    def set_manual(self, code: str, terms: list[Term], note: Optional[str] = None) -> None:
        self.records[code.upper()] = CourseAvailability(
            code=code.upper(), terms=terms, source="manual", note=note
        )

    def merge(self, other: "AvailabilityStore", *, respect_precedence: bool = True) -> int:
        count = 0
        for av in other.records.values():
            if self.upsert(av, respect_precedence=respect_precedence):
                count += 1
        return count

    # -- resolution ---------------------------------------------------------
    def resolve(
        self,
        code: str,
        regular_terms: tuple[Term, ...] = Term.regular(),
        assume_unknown_available: bool = True,
    ) -> list[Term]:
        """Effective terms a course can be scheduled in.

        Unknown courses (no record or empty terms) default to all regular terms
        when ``assume_unknown_available`` is True, otherwise to none.
        """
        av = self.records.get(code.upper())
        if not av or not av.terms:
            return list(regular_terms) if assume_unknown_available else []
        return [t for t in av.terms if t in regular_terms]

    def __len__(self) -> int:
        return len(self.records)
