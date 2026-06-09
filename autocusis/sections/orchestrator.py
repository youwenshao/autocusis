"""Attach section-level schedules to multi-semester graduation plans."""

from __future__ import annotations

from itertools import combinations

from ..calendar import slot_label
from ..ingest.term_extrapolate import shift_term_label
from ..db import CatalogDB
from ..models import PreferenceMode
from ..profile import Profile
from ..scheduler.plan import Plan, PlannedCourse, Semester
from .conflict import find_hard_conflicts
from .db import SectionsDB
from .models import SectionBundle, SectionMeeting, SectionStatus, TimeSlot
from .solver import generate_schedules

from pydantic import BaseModel, Field


class SelectedSection(BaseModel):
    course_code: str
    bundle_id: str
    lecture: str | None = None
    tutorial: str | None = None
    lab: str | None = None
    schedule: list[dict] = Field(default_factory=list)
    seats_remaining: int | None = None


class SectionSemester(Semester):
    academic_term: str = ""
    sections: list[SelectedSection] = Field(default_factory=list)
    section_status: SectionStatus = "no_data"
    section_notes: list[str] = Field(default_factory=list)


class SectionPlan(BaseModel):
    feasible: bool
    semesters: list[SectionSemester] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    objective_terms_used: int = 0
    peak_term_credits: float = 0.0
    data_provenance: dict[str, str | None] = Field(default_factory=dict)

    @property
    def graduation(self) -> dict:
        return {
            "terms_to_completion": self.objective_terms_used,
            "peak_credits": self.peak_term_credits,
        }


def _collect_prereq_warnings(
    db: CatalogDB, courses: list[PlannedCourse]
) -> list[str]:
    warnings: list[str] = []
    for pc in courses:
        if pc.is_filler:
            continue
        course = db.get_course(pc.code)
        if course and course.prerequisite.kind == "raw" and course.prerequisite.text:
            warnings.append(
                f"{pc.code} prereq contains unparsed text: {course.prerequisite.text}"
            )
    return warnings


def _largest_feasible_subset_size(
    codes: list[str],
    options: dict[str, list[SectionBundle]],
) -> int:
    with_data = [c for c in codes if options.get(c)]
    for k in range(len(with_data), 0, -1):
        for subset in combinations(with_data, k):
            if generate_schedules(list(subset), options, max_results=1):
                return k
    return 0


def _load_bundle_by_id(
    sections_db: SectionsDB, code: str, term_label: str, bundle_id: str
) -> SectionBundle | None:
    """Find a specific bundle the solver chose, resolving year-back fallback."""
    code = code.upper()
    labels = [term_label]
    for back in (1, 2):
        try:
            labels.append(shift_term_label(term_label, -back))
        except ValueError:
            break
    for label in labels:
        for bundle in sections_db.load_bundles(code, label):
            if bundle.bundle_id == bundle_id:
                return bundle
    return None


def _bundle_to_selected(bundle: SectionBundle) -> SelectedSection:
    lecture = tutorial = lab = None
    for sec in bundle.sections:
        st = sec.get("section_type", "")
        sid = sec.get("section_id", "")
        if st == "Lecture":
            lecture = sid
        elif st == "Tutorial":
            tutorial = sid
        elif st == "Lab":
            lab = sid
    schedule = []
    for m in bundle.meetings:
        schedule.append(
            {
                "day": m.slot.day,
                "start": m.slot.start_time,
                "end": m.slot.end_time,
                "location": m.slot.location,
                "section_id": m.section_id,
                "type": m.section_type,
            }
        )
    return SelectedSection(
        course_code=bundle.course_code,
        bundle_id=bundle.bundle_id,
        lecture=lecture,
        tutorial=tutorial,
        lab=lab,
        schedule=schedule,
        seats_remaining=bundle.min_seats_remaining,
    )


def attach_sections(
    plan: Plan,
    profile: Profile,
    db: CatalogDB,
    sections_db: SectionsDB | None = None,
    *,
    preference: PreferenceMode | None = None,
    strict: bool = False,
) -> SectionPlan:
    sections_db = sections_db or SectionsDB()
    pref = preference or profile.schedule_preferences.mode
    exclude_full = profile.schedule_preferences.exclude_full_sections
    bias_start: float | None = None
    warnings: list[str] = []
    data_provenance: dict[str, str | None] = {}
    out_semesters: list[SectionSemester] = []

    for sem in plan.semesters:
        tl = slot_label(profile, sem.planning_year, sem.term)
        data_provenance[tl] = sections_db.scraped_at(tl)
        course_codes = [c.code for c in sem.courses if not c.is_filler]

        sec_sem = SectionSemester(
            planning_year=sem.planning_year,
            term=sem.term,
            courses=sem.courses,
            academic_term=tl,
            section_status=sem.section_status or "no_data",
            section_notes=list(sem.section_notes),
        )
        warnings.extend(_collect_prereq_warnings(db, sem.courses))

        if not course_codes:
            sec_sem.section_status = "resolved"
            out_semesters.append(sec_sem)
            continue

        # Trust path: the section-aware solver already chose conflict-free
        # bundles, so format those directly instead of re-optimizing.
        solver_chosen = {
            c.code: c.bundle_id for c in sem.courses if not c.is_filler and c.bundle_id
        }
        if profile.section_aware and solver_chosen:
            selected: list[SectionBundle] = []
            for pc in sem.courses:
                if pc.is_filler or not pc.bundle_id:
                    continue
                bundle = _load_bundle_by_id(sections_db, pc.code, tl, pc.bundle_id)
                if bundle is not None:
                    selected.append(bundle)
            sec_sem.sections = [_bundle_to_selected(b) for b in selected]
            for sel in sec_sem.sections:
                if sel.seats_remaining is not None and sel.seats_remaining < 5:
                    sec_sem.section_notes.append(
                        f"{sel.course_code}: only {sel.seats_remaining} seats remaining"
                    )
            out_semesters.append(sec_sem)
            continue

        options: dict[str, list[SectionBundle]] = {}
        section_exempt: list[str] = []
        for code in course_codes:
            bundles = sections_db.load_bundles(code, tl)
            if not bundles:
                for year_back in (1, 2):
                    try:
                        fallback_tl = shift_term_label(tl, -year_back)
                    except ValueError:
                        break
                    bundles = sections_db.load_bundles(code, fallback_tl)
                    if bundles:
                        sec_sem.section_notes.append(
                            f"{code}: using extrapolated section data from {fallback_tl}"
                        )
                        break
            if exclude_full:
                bundles = [
                    b for b in bundles
                    if b.min_seats_remaining is None or b.min_seats_remaining > 0
                ]
            if bundles:
                options[code] = bundles
            else:
                section_exempt.append(code)

        if section_exempt:
            sec_sem.section_notes.append(
                "Section-exempt (no timetable data): "
                + ", ".join(section_exempt)
            )

        schedulable = list(options.keys())
        if not schedulable:
            sec_sem.section_status = "resolved"
            out_semesters.append(sec_sem)
            continue

        if section_exempt:
            sec_sem.section_status = "partial"

        pins = profile.pins_for_term(tl)
        schedulable_pins = (
            {k: v for k, v in pins.items() if k in options} if pins else None
        )
        schedules = generate_schedules(
            schedulable,
            options,
            preference=pref,
            max_results=1,
            pins=schedulable_pins,
            bias_start=bias_start,
        )

        if not schedules:
            sec_sem.section_status = "infeasible"
            sec_sem.section_notes.append(
                "No conflict-free section schedule found for courses with timetable data"
            )
            hard = find_hard_conflicts(schedulable, options)
            for a, b in hard:
                sec_sem.section_notes.append(
                    f"Section conflict (all bundles): {a} × {b}"
                )
            max_subset = _largest_feasible_subset_size(schedulable, options)
            if max_subset < len(schedulable):
                sec_sem.section_notes.append(
                    f"Largest conflict-free subset: {max_subset} of {len(schedulable)} courses"
                )
            out_semesters.append(sec_sem)
            continue

        best = schedules[0]
        if hasattr(best.metrics, "avg_start_time"):
            bias_start = best.metrics.avg_start_time

        sec_sem.sections = [_bundle_to_selected(b) for b in best.bundles]
        if sec_sem.section_status != "partial":
            sec_sem.section_status = "resolved"

        for sel in sec_sem.sections:
            if sel.seats_remaining is not None and sel.seats_remaining < 5:
                sec_sem.section_notes.append(
                    f"{sel.course_code}: only {sel.seats_remaining} seats remaining"
                )

        out_semesters.append(sec_sem)

    return SectionPlan(
        feasible=plan.feasible,
        semesters=out_semesters,
        notes=plan.notes,
        warnings=warnings,
        objective_terms_used=plan.objective_terms_used,
        peak_term_credits=plan.peak_term_credits,
        data_provenance=data_provenance,
    )
