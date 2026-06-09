"""Core data models shared across AutoCUSIS.

Defines the course-catalog representation, the boolean prerequisite/exclusion
expression AST, and term/availability primitives used by the scheduler.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Literal, Optional

PreferenceMode = Literal[
    "shortBreaks",
    "longBreaks",
    "consistentStart",
    "morning",
    "startLate",
    "endEarly",
    "daysOff",
]

PlanningMode = Literal["fast", "spread"]

from pydantic import BaseModel, Field

# A normalized course code, e.g. "AIST1110". We keep these uppercase, no spaces.
CourseCode = str


class Term(IntEnum):
    """Academic terms within a CUHK academic year.

    The integer values are used by the scheduler when mapping an absolute
    slot index back to (year, term).
    """

    TERM1 = 1
    TERM2 = 2
    SUMMER = 3

    @property
    def label(self) -> str:
        return {Term.TERM1: "Term 1", Term.TERM2: "Term 2", Term.SUMMER: "Summer"}[self]

    @classmethod
    def regular(cls) -> tuple["Term", "Term"]:
        """The two regular terms (summer excluded by default)."""
        return (cls.TERM1, cls.TERM2)


# ---------------------------------------------------------------------------
# Prerequisite / exclusion boolean expression AST
# ---------------------------------------------------------------------------
PrereqKind = Literal["course", "and", "or", "raw", "none"]


class PrereqExpr(BaseModel):
    """A recursive boolean expression over course codes.

    Examples:
      "ENGG1110 or ESTR1002"  -> or(course(ENGG1110), course(ESTR1002))
      "(A and B) or C"        -> or(and(course(A), course(B)), course(C))

    ``raw`` holds text we could not confidently parse (e.g. "Consent of
    instructor"); such nodes are treated as already-satisfied by the
    scheduler but surfaced to the user for manual review.
    """

    kind: PrereqKind
    code: Optional[CourseCode] = None
    operands: list["PrereqExpr"] = Field(default_factory=list)
    text: Optional[str] = None

    # -- constructors -------------------------------------------------------
    @classmethod
    def none(cls) -> "PrereqExpr":
        return cls(kind="none")

    @classmethod
    def course(cls, code: CourseCode) -> "PrereqExpr":
        return cls(kind="course", code=code)

    @classmethod
    def all_of(cls, operands: list["PrereqExpr"]) -> "PrereqExpr":
        operands = [o for o in operands if o.kind != "none"]
        if not operands:
            return cls.none()
        if len(operands) == 1:
            return operands[0]
        return cls(kind="and", operands=operands)

    @classmethod
    def any_of(cls, operands: list["PrereqExpr"]) -> "PrereqExpr":
        operands = [o for o in operands if o.kind != "none"]
        if not operands:
            return cls.none()
        if len(operands) == 1:
            return operands[0]
        return cls(kind="or", operands=operands)

    @classmethod
    def raw(cls, text: str) -> "PrereqExpr":
        return cls(kind="raw", text=text)

    # -- queries ------------------------------------------------------------
    def referenced_codes(self) -> set[CourseCode]:
        """All concrete course codes mentioned anywhere in the expression."""
        if self.kind == "course" and self.code:
            return {self.code}
        codes: set[CourseCode] = set()
        for op in self.operands:
            codes |= op.referenced_codes()
        return codes

    def is_satisfied(self, completed: set[CourseCode]) -> bool:
        """Evaluate the expression against a set of completed course codes.

        ``raw`` and ``none`` nodes evaluate to True (cannot be machine-checked
        / no requirement), so the scheduler does not over-constrain on them.
        """
        if self.kind == "none" or self.kind == "raw":
            return True
        if self.kind == "course":
            return self.code in completed if self.code else True
        if self.kind == "and":
            return all(op.is_satisfied(completed) for op in self.operands)
        if self.kind == "or":
            return any(op.is_satisfied(completed) for op in self.operands)
        return True

    def to_text(self) -> str:
        if self.kind == "none":
            return ""
        if self.kind == "raw":
            return self.text or ""
        if self.kind == "course":
            return self.code or ""
        joiner = " and " if self.kind == "and" else " or "
        inner = joiner.join(
            op.to_text() if op.kind in ("course", "raw", "none") else f"({op.to_text()})"
            for op in self.operands
        )
        return inner


PrereqExpr.model_rebuild()


# ---------------------------------------------------------------------------
# Course catalog record
# ---------------------------------------------------------------------------
class Course(BaseModel):
    """A structured course-catalog record extracted from a PDF or the catalog
    browser."""

    code: CourseCode
    course_id: Optional[str] = None
    title_en: Optional[str] = None
    title_zh: Optional[str] = None
    units: float = 3.0
    description_en: Optional[str] = None
    description_zh: Optional[str] = None

    prerequisite_raw: Optional[str] = None
    prerequisite: PrereqExpr = Field(default_factory=PrereqExpr.none)
    exclusion_raw: Optional[str] = None
    exclusion_codes: list[CourseCode] = Field(default_factory=list)

    components: list[str] = Field(default_factory=list)
    learning_outcomes: list[str] = Field(default_factory=list)
    academic_org: Optional[str] = None
    subject: Optional[str] = None
    grading_basis: Optional[str] = None

    source: Literal["pdf", "catalog", "manual"] = "pdf"
    source_url: Optional[str] = None

    @property
    def subject_prefix(self) -> str:
        """Letter prefix of the course code, e.g. 'AIST' for 'AIST1110'."""
        i = 0
        while i < len(self.code) and self.code[i].isalpha():
            i += 1
        return self.code[:i]


# ---------------------------------------------------------------------------
# Availability (which terms a course is offered in)
# ---------------------------------------------------------------------------
class CourseAvailability(BaseModel):
    """Recurring term-availability pattern for a course.

    ``terms`` lists the regular terms in which the course is normally offered.
    An empty list means "unknown" -> the scheduler treats it as available in
    all regular terms unless ``assume_unknown_unavailable`` is set.
    """

    code: CourseCode
    terms: list[Term] = Field(default_factory=list)
    note: Optional[str] = None
    source: Literal["timetable", "community", "manual", "default"] = "default"
    year: Optional[str] = None  # e.g. "2025-26", the year the data came from
