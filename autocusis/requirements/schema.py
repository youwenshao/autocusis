"""Graduation-requirement DSL.

A :class:`Curriculum` is a set of :class:`RequirementGroup` objects plus a
total-credit floor. Each group expresses one of four rules:

  * ``all_of``       - every listed course must be completed (core/required).
  * ``credits_from`` - at least ``min_credits`` credits from the pool.
  * ``count_from``   - at least ``min_count`` courses from the pool.
  * ``one_of``       - complete every course in exactly one ``tracks`` entry.

Curricula are stored as editable YAML under ``data/requirements/<program>.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from ..models import CourseCode

GroupKind = Literal["all_of", "credits_from", "count_from", "one_of"]


class RequirementGroup(BaseModel):
    id: str
    name: str
    kind: GroupKind
    courses: list[CourseCode] = Field(default_factory=list)
    tracks: list[list[CourseCode]] = Field(default_factory=list)
    equivalence_tracks: list[list[CourseCode]] = Field(default_factory=list)
    min_credits: Optional[float] = None
    max_credits: Optional[float] = None
    min_count: Optional[int] = None
    max_pool_count: Optional[int] = None
    note: Optional[str] = None

    def normalized_courses(self) -> list[CourseCode]:
        codes: list[CourseCode] = [c.upper() for c in self.courses]
        for track in self.tracks:
            codes.extend(c.upper() for c in track)
        return list(dict.fromkeys(codes))

    def normalized_tracks(self) -> list[list[CourseCode]]:
        return [[c.upper() for c in track] for track in self.tracks]

    def normalized_equivalence_tracks(self) -> list[list[CourseCode]]:
        return [[c.upper() for c in track] for track in self.equivalence_tracks]


class ElectiveStream(BaseModel):
    """A thematic specialization within the elective pool.

    Lists the elective courses that belong to a stream of interest. Used purely
    as a soft scheduling preference: when a stream is selected the scheduler
    biases elective picks toward its courses (without delaying graduation or
    violating any requirement).
    """

    id: str
    name: str
    courses: list[CourseCode] = Field(default_factory=list)
    note: Optional[str] = None

    def normalized_courses(self) -> list[CourseCode]:
        return list(dict.fromkeys(c.upper() for c in self.courses))


class ElectiveStream(BaseModel):
    """A named specialization that biases elective selection toward a theme.

    Streams are a soft preference: when a profile selects a stream, the scheduler
    prefers electives whose code appears in ``courses`` but still falls back to
    other pool courses when needed to satisfy credits / prerequisites /
    availability. Membership is curated per program (see the ``elective_streams``
    block in the curriculum YAML).
    """

    id: str
    name: str
    courses: list[CourseCode] = Field(default_factory=list)
    note: Optional[str] = None

    def normalized_courses(self) -> list[CourseCode]:
        return list(dict.fromkeys(c.upper() for c in self.courses))


class Curriculum(BaseModel):
    program: str
    cohort: Optional[str] = None
    total_credits_required: float = 0.0
    description: Optional[str] = None
    groups: list[RequirementGroup] = Field(default_factory=list)
    elective_streams: list[ElectiveStream] = Field(default_factory=list)
    elective_streams: list[ElectiveStream] = Field(default_factory=list)

    # -- persistence --------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Curriculum":
        data = yaml.safe_load(Path(path).read_text()) or {}
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            yaml.safe_dump(self.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True)
        )

    # -- queries ------------------------------------------------------------
    def all_referenced_courses(self) -> set[CourseCode]:
        codes: set[CourseCode] = set()
        for g in self.groups:
            codes |= set(g.normalized_courses())
        return codes

    def group(self, group_id: str) -> Optional[RequirementGroup]:
        return next((g for g in self.groups if g.id == group_id), None)

    def stream(self, stream_id: str) -> Optional[ElectiveStream]:
        return next((s for s in self.elective_streams if s.id == stream_id), None)

    def course_to_stream(self) -> dict[CourseCode, str]:
        """Map each curated course code to its stream id (first stream wins)."""
        out: dict[CourseCode, str] = {}
        for s in self.elective_streams:
            for code in s.normalized_courses():
                out.setdefault(code, s.id)
        return out

    def stream(self, stream_id: str) -> Optional[ElectiveStream]:
        return next((s for s in self.elective_streams if s.id == stream_id), None)

    def stream_ids(self) -> list[str]:
        return [s.id for s in self.elective_streams]

    def course_to_stream(self) -> dict[CourseCode, str]:
        """Map each elective course code to its stream id (first stream wins)."""
        mapping: dict[CourseCode, str] = {}
        for stream in self.elective_streams:
            for code in stream.normalized_courses():
                mapping.setdefault(code, stream.id)
        return mapping
