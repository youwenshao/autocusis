"""Tests for the section-aware unified scheduler."""

import json

from autocusis.calendar import slot_label
from autocusis.ingest.availability_store import AvailabilityStore
from autocusis.models import Course, PrereqExpr, Term
from autocusis.profile import Profile
from autocusis.requirements.engine import ScheduleDemand
from autocusis.scheduler.section_data import dedup_bundles
from autocusis.scheduler.solver import SchedulerInput, solve
from autocusis.sections.db import SectionsDB
from autocusis.sections.models import SectionBundle, SectionMeeting, TimeSlot


def _course(code, prereq=None, units=3.0, excl=None):
    return Course(
        code=code,
        title_en=code,
        units=units,
        prerequisite=prereq or PrereqExpr.none(),
        exclusion_codes=excl or [],
    )


def _avail(codes_terms):
    av = AvailabilityStore()
    for code, terms in codes_terms.items():
        av.set_manual(code, terms)
    return av


def _meeting(day, start, end):
    return {"day": day, "start_time": start, "end_time": end, "location": None}


def _bundle(course, bundle_id, day, start, end, section_type="Lecture"):
    return SectionBundle(
        bundle_id=bundle_id,
        course_code=course,
        sections=[
            {
                "section_id": bundle_id,
                "section_type": section_type,
                "meetings": [_meeting(day, start, end)],
            }
        ],
        meetings=[
            SectionMeeting(
                course_code=course,
                section_id=bundle_id,
                section_type=section_type,
                slot=TimeSlot(day=day, start_time=start, end_time=end),
            )
        ],
    )


def _make_db(tmp_path, rows):
    """rows: list of (code, term_label, source, [section_dict, ...], min_seats)."""
    db = SectionsDB(tmp_path / "sections.db")
    with db.connect() as conn:
        for code, tl, source, sections, min_seats in rows:
            conn.execute(
                "INSERT INTO courses_meta (course_code, term_label, source) VALUES (?, ?, ?)",
                (code, tl, source),
            )
            for i, sec_list in enumerate(sections):
                conn.execute(
                    "INSERT INTO section_groups "
                    "(course_code, term_label, bundle_id, sections_json, min_seats_remaining) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (code, tl, sec_list["bundle_id"], json.dumps(sec_list["sections"]), min_seats),
                )
    return db


def _sec(bundle_id, day, start, end):
    return {
        "bundle_id": bundle_id,
        "sections": [
            {
                "section_id": bundle_id,
                "section_type": "Lecture",
                "meetings": [_meeting(day, start, end)],
            }
        ],
    }


def _labels(profile, years, terms):
    return [slot_label(profile, y, t) for y in years for t in terms]


# --------------------------------------------------------------------------


def test_dedup_bundles_collapses_identical_patterns():
    b1 = _bundle("A", "L1", "Monday", "10:00", "11:00")
    b2 = _bundle("A", "L2", "Monday", "10:00", "11:00")  # same pattern
    b3 = _bundle("A", "L3", "Tuesday", "10:00", "11:00")  # different
    reps = dedup_bundles([b1, b2, b3])
    assert len(reps) == 2


def test_real_conflict_forces_different_terms(tmp_path):
    profile = Profile(max_credits_per_term=12, planning_horizon_years=2, section_aware=True)
    rows = []
    for tl in _labels(profile, [1, 2], [Term.TERM1, Term.TERM2]):
        rows.append(("A", tl, "community", [_sec("A1", "Monday", "10:00", "11:00")], None))
        rows.append(("B", tl, "community", [_sec("B1", "Monday", "10:00", "11:00")], None))
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A"), "B": _course("B")}
    av = _avail({"A": [Term.TERM1, Term.TERM2], "B": [Term.TERM1, Term.TERM2]})
    demand = ScheduleDemand(mandatory=["A", "B"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible
    pos = {c.code: (c.planning_year, int(c.term)) for s in plan.semesters for c in s.courses}
    assert pos["A"] != pos["B"]  # hard real conflict -> never co-placed


def test_section_aware_disabled_allows_conflict(tmp_path):
    profile = Profile(max_credits_per_term=12, planning_horizon_years=1, section_aware=False)
    rows = []
    for tl in _labels(profile, [1], [Term.TERM1]):
        rows.append(("A", tl, "community", [_sec("A1", "Monday", "10:00", "11:00")], None))
        rows.append(("B", tl, "community", [_sec("B1", "Monday", "10:00", "11:00")], None))
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A"), "B": _course("B")}
    av = _avail({"A": [Term.TERM1], "B": [Term.TERM1]})
    demand = ScheduleDemand(mandatory=["A", "B"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible
    pos = {c.code: (c.planning_year, int(c.term)) for s in plan.semesters for c in s.courses}
    assert pos["A"] == pos["B"]  # ignored when disabled


def test_relief_valve_relaxes_unavoidable_real_conflict(tmp_path):
    profile = Profile(max_credits_per_term=12, planning_horizon_years=1, section_aware=True)
    tl = slot_label(profile, 1, Term.TERM1)
    rows = [
        ("A", tl, "community", [_sec("A1", "Monday", "10:00", "11:00")], None),
        ("B", tl, "community", [_sec("B1", "Monday", "10:00", "11:00")], None),
    ]
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A"), "B": _course("B")}
    av = _avail({"A": [Term.TERM1], "B": [Term.TERM1]})  # only one shared slot
    demand = ScheduleDemand(mandatory=["A", "B"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible  # relief valve keeps it solvable
    assert any("relaxed" in n.lower() for n in plan.notes)
    assert any(s.section_status == "relaxed" for s in plan.semesters)


def test_extrapolated_conflict_is_soft_and_avoided(tmp_path):
    # C is T2-only forcing two terms; A,B conflict but extrapolated -> soft.
    profile = Profile(max_credits_per_term=12, planning_horizon_years=1, section_aware=True)
    t1 = slot_label(profile, 1, Term.TERM1)
    t2 = slot_label(profile, 1, Term.TERM2)
    rows = [
        ("A", t1, "extrapolated", [_sec("A1", "Monday", "10:00", "11:00")], None),
        ("A", t2, "extrapolated", [_sec("A1", "Monday", "10:00", "11:00")], None),
        ("B", t1, "extrapolated", [_sec("B1", "Monday", "10:00", "11:00")], None),
        ("B", t2, "extrapolated", [_sec("B1", "Monday", "10:00", "11:00")], None),
        ("C", t2, "extrapolated", [_sec("C1", "Friday", "09:00", "10:00")], None),
    ]
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A"), "B": _course("B"), "C": _course("C")}
    av = _avail(
        {"A": [Term.TERM1, Term.TERM2], "B": [Term.TERM1, Term.TERM2], "C": [Term.TERM2]}
    )
    demand = ScheduleDemand(mandatory=["A", "B", "C"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible
    pos = {c.code: (c.planning_year, int(c.term)) for s in plan.semesters for c in s.courses}
    assert pos["A"] != pos["B"]  # soft penalty separates them when possible


def test_extrapolated_conflict_allowed_when_forced(tmp_path):
    # Single shared slot + extrapolated -> co-placement allowed (would be relaxed if real).
    profile = Profile(max_credits_per_term=12, planning_horizon_years=1, section_aware=True)
    tl = slot_label(profile, 1, Term.TERM1)
    rows = [
        ("A", tl, "extrapolated", [_sec("A1", "Monday", "10:00", "11:00")], None),
        ("B", tl, "extrapolated", [_sec("B1", "Monday", "10:00", "11:00")], None),
    ]
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A"), "B": _course("B")}
    av = _avail({"A": [Term.TERM1], "B": [Term.TERM1]})
    demand = ScheduleDemand(mandatory=["A", "B"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible
    # Not relaxed (it was soft from the start, not a relief-valve relaxation).
    assert not any(s.section_status == "relaxed" for s in plan.semesters)


def test_chosen_bundle_attached(tmp_path):
    profile = Profile(max_credits_per_term=12, planning_horizon_years=1, section_aware=True)
    tl = slot_label(profile, 1, Term.TERM1)
    rows = [("A", tl, "community", [_sec("A1", "Monday", "10:00", "11:00")], None)]
    db = _make_db(tmp_path, rows)

    catalog = {"A": _course("A")}
    av = _avail({"A": [Term.TERM1]})
    demand = ScheduleDemand(mandatory=["A"])
    plan = solve(
        SchedulerInput(
            demand=demand, profile=profile, catalog=catalog, availability=av, sections_db=db
        )
    )[0]
    assert plan.feasible
    a = next(c for s in plan.semesters for c in s.courses if c.code == "A")
    assert a.bundle_id == "A1"
    assert a.section_trust == "real"
