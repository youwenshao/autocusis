"""Shared lookups bridging the catalog DB, profile, and the requirement engine."""

from __future__ import annotations

from typing import Callable, Optional

from .db import CatalogDB
from .models import Course, CourseCode
from .profile import Profile

DEFAULT_UNITS = 3.0


def make_credit_fn(
    db: CatalogDB, profile: Optional[Profile] = None, default: float = DEFAULT_UNITS
) -> Callable[[CourseCode], float]:
    """Return a function code -> credits.

    Resolution order: profile per-course override > catalog units > default.
    Cached for the lifetime of the returned closure.
    """
    overrides: dict[str, float] = {}
    if profile:
        for c in profile.completed:
            if c.credits is not None:
                overrides[c.code.upper()] = c.credits

    cache: dict[str, float] = {}

    def credit_fn(code: CourseCode) -> float:
        code = code.upper()
        if code in overrides:
            return overrides[code]
        if code in cache:
            return cache[code]
        course = db.get_course(code)
        val = course.units if course else default
        cache[code] = val
        return val

    return credit_fn


def course_title(db: CatalogDB, code: CourseCode) -> str:
    course: Optional[Course] = db.get_course(code)
    if not course:
        return ""
    return course.title_en or course.title_zh or ""
