"""Tests for section plan orchestration."""

from autocusis.models import Term
from autocusis.profile import Profile
from autocusis.scheduler.plan import Plan, PlannedCourse, Semester
from autocusis.sections.db import SectionsDB
from autocusis.sections.orchestrator import attach_sections
from autocusis.ingest.community_sync import sync_community
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "community"


class _FakeDB:
    def get_course(self, code):
        return None


def test_attach_sections_no_data(tmp_path, monkeypatch):
    """All courses section-exempt → resolved (not no_data/infeasible)."""
    monkeypatch.setattr(
        "autocusis.paths.sections_db_path",
        lambda: tmp_path / "sections.sqlite",
    )
    plan = Plan(
        feasible=True,
        semesters=[
            Semester(
                planning_year=3,
                term=Term.TERM1,
                courses=[
                    PlannedCourse(
                        code="CSCI1020", planning_year=3, term=Term.TERM1, credits=1
                    )
                ],
            )
        ],
        objective_terms_used=1,
    )
    profile = Profile(
        start_year_label="2026-27",
        current_year=3,
        current_term=Term.TERM1,
    )
    sp = attach_sections(plan, profile, _FakeDB(), SectionsDB(tmp_path / "sections.sqlite"))
    assert sp.semesters[0].section_status == "resolved"
    assert any("Section-exempt" in n for n in sp.semesters[0].section_notes)


def test_attach_sections_mixed_exempt(tmp_path, monkeypatch):
    """Schedulable courses resolve even when FYP/practicum courses have no data."""
    db_path = tmp_path / "sections.sqlite"
    avail_path = tmp_path / "availability.yaml"
    monkeypatch.setattr(
        "autocusis.paths.sections_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        "autocusis.paths.availability_path",
        lambda: avail_path,
    )
    sync_community(
        "eaglezhen",
        FIXTURES / "eaglezhen_csci_snippet.json",
        "2025-26 Term 2",
    )

    plan = Plan(
        feasible=True,
        semesters=[
            Semester(
                planning_year=1,
                term=Term.TERM2,
                courses=[
                    PlannedCourse(
                        code="CSCI1020", planning_year=1, term=Term.TERM2, credits=1
                    ),
                    PlannedCourse(
                        code="ESTR4999", planning_year=1, term=Term.TERM2, credits=3
                    ),
                ],
            )
        ],
        objective_terms_used=1,
    )
    profile = Profile(
        start_year_label="2025-26",
        current_year=3,
        current_term=Term.TERM2,
    )
    sp = attach_sections(plan, profile, _FakeDB(), SectionsDB(db_path))
    assert sp.semesters[0].section_status == "partial"
    assert sp.semesters[0].sections
    assert sp.semesters[0].sections[0].course_code == "CSCI1020"
    assert any("Section-exempt" in n and "ESTR4999" in n for n in sp.semesters[0].section_notes)


def test_attach_sections_resolved(tmp_path, monkeypatch):
    db_path = tmp_path / "sections.sqlite"
    avail_path = tmp_path / "availability.yaml"
    monkeypatch.setattr(
        "autocusis.paths.sections_db_path",
        lambda: db_path,
    )
    monkeypatch.setattr(
        "autocusis.paths.availability_path",
        lambda: avail_path,
    )
    sync_community(
        "eaglezhen",
        FIXTURES / "eaglezhen_csci_snippet.json",
        "2025-26 Term 2",
    )

    plan = Plan(
        feasible=True,
        semesters=[
            Semester(
                planning_year=1,
                term=Term.TERM2,
                courses=[
                    PlannedCourse(
                        code="CSCI1020", planning_year=1, term=Term.TERM2, credits=1
                    )
                ],
            )
        ],
        objective_terms_used=1,
    )
    profile = Profile(
        start_year_label="2025-26",
        current_year=3,
        current_term=Term.TERM2,
    )
    sp = attach_sections(plan, profile, _FakeDB(), SectionsDB(db_path))
    assert sp.semesters[0].section_status == "resolved"
    assert sp.semesters[0].sections


def test_attach_sections_infeasible_reports_conflicts(tmp_path, monkeypatch):
    """Infeasible terms name hard-conflicting course pairs."""
    import json

    from autocusis.sections.models import SectionBundle, SectionMeeting, TimeSlot

    db_path = tmp_path / "sections.sqlite"
    monkeypatch.setattr("autocusis.paths.sections_db_path", lambda: db_path)
    sdb = SectionsDB(db_path)

    slot = {"day": "Monday", "start_time": "10:00", "end_time": "12:00", "location": "LT1"}
    sections_json = json.dumps(
        [
            {
                "section_id": "L01",
                "section_type": "Lecture",
                "meetings": [slot],
            }
        ]
    )

    term = "2025-26 Term 1"
    with sdb.connect() as conn:
        for code in ("COURSEA", "COURSEB"):
            conn.execute(
                """
                INSERT INTO courses_meta (course_code, term_label, source)
                VALUES (?, ?, 'test')
                """,
                (code, term),
            )
            row = conn.execute(
                """
                INSERT INTO section_groups
                    (course_code, term_label, bundle_id, sections_json)
                VALUES (?, ?, 'L01', ?)
                """,
                (code, term, sections_json),
            )
            bundle_row_id = row.lastrowid
            conn.execute(
                """
                INSERT INTO section_slots
                    (bundle_row_id, course_code, term_label, section_id, section_type,
                     day, start_time, end_time, location)
                VALUES (?, ?, ?, 'L01', 'Lecture', 'Monday', '10:00', '12:00', 'LT1')
                """,
                (bundle_row_id, code, term),
            )

    plan = Plan(
        feasible=True,
        semesters=[
            Semester(
                planning_year=1,
                term=Term.TERM1,
                courses=[
                    PlannedCourse(code="COURSEA", planning_year=1, term=Term.TERM1, credits=3),
                    PlannedCourse(code="COURSEB", planning_year=1, term=Term.TERM1, credits=3),
                ],
            )
        ],
        objective_terms_used=1,
    )
    profile = Profile(start_year_label="2025-26", current_year=3, current_term=Term.TERM1)
    sp = attach_sections(plan, profile, _FakeDB(), sdb)
    assert sp.semesters[0].section_status == "infeasible"
    notes = " ".join(sp.semesters[0].section_notes)
    assert "Section conflict (all bundles): COURSEA × COURSEB" in notes
    assert "Largest conflict-free subset: 1 of 2 courses" in notes
