"""Output data models for generated study plans."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..models import CourseCode, Term


class PlannedCourse(BaseModel):
    code: CourseCode
    title: Optional[str] = None
    credits: float = 3.0
    planning_year: int  # 1 = first planned year
    term: Term
    is_filler: bool = False  # generic free-elective placeholder
    pinned: bool = False
    # Section bundle chosen by the section-aware solver (if any).
    bundle_id: Optional[str] = None
    section_trust: Optional[str] = None  # "real" | "extrapolated"


class Semester(BaseModel):
    planning_year: int
    term: Term
    courses: list[PlannedCourse] = Field(default_factory=list)
    section_status: str = "no_data"  # resolved | partial | relaxed | no_data
    section_notes: list[str] = Field(default_factory=list)

    @property
    def total_credits(self) -> float:
        return sum(c.credits for c in self.courses)

    @property
    def label(self) -> str:
        return f"Y{self.planning_year} {self.term.label}"


class Plan(BaseModel):
    feasible: bool
    semesters: list[Semester] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    objective_terms_used: int = 0  # number of terms until completion
    peak_term_credits: float = 0.0

    @property
    def total_planned_credits(self) -> float:
        return sum(s.total_credits for s in self.semesters)

    @property
    def num_courses(self) -> int:
        return sum(len(s.courses) for s in self.semesters)
