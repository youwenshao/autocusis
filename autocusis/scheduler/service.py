"""Assemble a :class:`SchedulerInput` from the catalog DB, profile, curriculum."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..db import CatalogDB
from ..ingest.availability_store import AvailabilityStore
from ..models import Course
from ..profile import Profile
from ..requirements.engine import ScheduleDemand, build_demand, evaluate
from ..requirements.schema import Curriculum
from ..sections.db import SectionsDB
from ..services import make_credit_fn
from .solver import SchedulerInput


def build_scheduler_input(
    db: CatalogDB,
    curriculum: Curriculum,
    profile: Profile,
    availability: Optional[AvailabilityStore] = None,
    assume_unknown_available: bool = False,
    strict_prereqs: bool = False,
    sections_db: Optional[SectionsDB] = None,
) -> tuple[SchedulerInput, ScheduleDemand]:
    """Evaluate progress and gather catalog data needed by the solver."""
    availability = availability or AvailabilityStore.load()
    credit_fn = make_credit_fn(db, profile)
    report = evaluate(curriculum, profile, credit_fn)
    demand = build_demand(report)

    # Load catalog records for every course the solver might touch
    # (candidates + their prerequisites).
    catalog: dict[str, Course] = {}
    to_load = set(demand.candidate_courses())
    loaded: set[str] = set()
    while to_load:
        code = to_load.pop()
        if code in loaded:
            continue
        loaded.add(code)
        course = db.get_course(code)
        if course:
            catalog[code] = course
            for ref in course.prerequisite.referenced_codes():
                if ref not in loaded:
                    to_load.add(ref)

    if sections_db is None and profile.section_aware:
        sections_db = SectionsDB()

    course_stream = curriculum.course_to_stream()
    preferred_stream = profile.elective_stream
    if preferred_stream and curriculum.stream(preferred_stream) is None:
        preferred_stream = None

    inp = SchedulerInput(
        demand=demand,
        profile=profile,
        catalog=catalog,
        availability=availability,
        assume_unknown_available=assume_unknown_available,
        strict_prereqs=strict_prereqs,
        sections_db=sections_db,
        preferred_stream=preferred_stream,
        course_stream=course_stream,
    )
    return inp, demand
