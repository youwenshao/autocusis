"""The personal academic profile: what you've done and your planning settings.

Stored as editable YAML (``data/profile.yaml``). The scheduler plans only the
*remaining* courses, starting from ``current_year``/``current_term``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from .models import CourseCode, PlanningMode, PreferenceMode, Term
from .paths import profile_path


class CompletedCourse(BaseModel):
    code: CourseCode
    grade: Optional[str] = None
    term_taken: Optional[str] = None  # free-form, e.g. "2024-25 T1"
    credits: Optional[float] = None  # override catalog units if needed


class PriorityPin(BaseModel):
    """Force a course into a specific planning slot (year offset + term)."""

    code: CourseCode
    year: int  # absolute academic-year index in the plan (1 = first planned year)
    term: Term


class PinnedSection(BaseModel):
    """Force specific section IDs for a course in a given academic term."""

    course: CourseCode
    term_label: str
    section_ids: list[str] = Field(default_factory=list)


class SchedulePreferences(BaseModel):
    mode: PreferenceMode = "daysOff"
    exclude_full_sections: bool = True
    pinned_sections: list[PinnedSection] = Field(default_factory=list)


class Profile(BaseModel):
    name: Optional[str] = None
    program: str = "AIST"
    cohort: Optional[str] = None

    # Where planning starts. ``current_year`` is the absolute year index that the
    # first *planned* term belongs to; completed work is everything in
    # ``completed`` regardless of these.
    start_year_label: Optional[str] = None  # e.g. "2023-24"
    current_year: int = 1
    current_term: Term = Term.TERM1

    max_credits_per_term: int = 18
    min_credits_per_term: int = 0
    max_credits_per_year: int = 39
    planning_horizon_years: int = 4
    allow_summer: bool = False
    # ``fast``: finish as early as possible; ``spread``: use the full horizon with
    # balanced per-term load and subject mix.
    planning_mode: PlanningMode = "fast"

    # Section-aware planning: the solver picks section bundles and avoids
    # timetable clashes. Real-data clashes are hard; extrapolated-data clashes are
    # soft-penalized (unless ``trust_extrapolated_hard``).
    section_aware: bool = True
    section_conflict_weight: int = 1
    trust_extrapolated_hard: bool = False

    # Elective specialization: when set, the scheduler softly biases elective
    # picks toward this stream id (see Curriculum.elective_streams). Falls back to
    # other pool courses when the stream cannot satisfy credits/prereqs/availability.
    elective_stream: Optional[str] = None

    completed: list[CompletedCourse] = Field(default_factory=list)
    # Courses treated as satisfied for prerequisite evaluation only (not degree credit).
    prereq_satisfied: list[CourseCode] = Field(default_factory=list)
    priority_pins: list[PriorityPin] = Field(default_factory=list)
    exclude_courses: list[CourseCode] = Field(default_factory=list)
    schedule_preferences: SchedulePreferences = Field(default_factory=SchedulePreferences)

    # -- persistence --------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Profile":
        path = Path(path or profile_path())
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls.model_validate(data)

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path or profile_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        # mode="json" converts Term IntEnums to plain ints so PyYAML can dump.
        path.write_text(
            yaml.safe_dump(
                self.model_dump(mode="json", exclude_none=True),
                sort_keys=False,
                allow_unicode=True,
            )
        )

    # -- queries ------------------------------------------------------------
    def completed_codes(self) -> set[CourseCode]:
        return {c.code.upper() for c in self.completed}

    def prereq_satisfied_codes(self) -> set[CourseCode]:
        return {c.upper() for c in self.prereq_satisfied}

    def effective_completed_codes(self) -> set[CourseCode]:
        """Transcript completed plus prereq-only waivers (for scheduling constraints)."""
        return self.completed_codes() | self.prereq_satisfied_codes()

    def excluded_codes(self) -> set[CourseCode]:
        return {c.upper() for c in self.exclude_courses}

    def pins_for_term(self, term_label: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for pin in self.schedule_preferences.pinned_sections:
            if pin.term_label == term_label:
                out[pin.course.upper()] = [s.upper() for s in pin.section_ids]
        return out
