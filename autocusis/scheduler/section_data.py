"""Load and classify per-slot section bundles for the unified scheduler.

The course scheduler works in abstract planning *slots* ``(planning_year, term)``.
To reason about timetable feasibility it needs, for each candidate course and each
slot it could occupy, the set of section bundles offered in that academic term and
how much we trust that data:

* ``REAL`` - bundles scraped/synced for the exact academic term.
* ``EXTRAPOLATED`` - bundles only available by copying a prior year's data
  (either via the explicit ``extrapolated`` source tag or a year-back fallback).
* (absent) - no data; the course is section-exempt in that slot.

Real-data conflicts are enforced as hard constraints; extrapolated-data conflicts
are soft-penalized so uncertain future data never makes a plan infeasible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..calendar import slot_label
from ..ingest.term_extrapolate import shift_term_label
from ..models import CourseCode, Term
from ..profile import Profile
from ..sections.db import SectionsDB
from ..sections.models import SectionBundle


class Trust(str, Enum):
    REAL = "real"
    EXTRAPOLATED = "extrapolated"


@dataclass
class SlotSections:
    """Section bundles available to a course in one planning slot."""

    term_label: str
    trust: Trust
    bundles: list[SectionBundle]  # deduped representatives
    original_count: int = 0


@dataclass
class SectionData:
    """All per-slot section info the solver needs, keyed by (course, slot index)."""

    entries: dict[tuple[CourseCode, int], SlotSections] = field(default_factory=dict)

    def get(self, code: CourseCode, slot_index: int) -> Optional[SlotSections]:
        return self.entries.get((code.upper(), slot_index))

    def has_any(self) -> bool:
        return bool(self.entries)


def _pattern_key(bundle: SectionBundle) -> frozenset[tuple[str, str, str]]:
    """A hashable signature of a bundle's meeting times (ignores room/instructor)."""
    return frozenset(
        (m.slot.day, m.slot.start_time, m.slot.end_time) for m in bundle.meetings
    )


def dedup_bundles(bundles: list[SectionBundle]) -> list[SectionBundle]:
    """Collapse bundles whose meeting time-patterns are identical.

    Two bundles with the same days/times are interchangeable for conflict
    detection, so we keep a single representative to shrink the CP-SAT model.
    Bundles with no meetings (no timetable) are dropped - they cannot conflict
    and would otherwise collapse into one meaningless representative.
    """
    seen: set[frozenset[tuple[str, str, str]]] = set()
    out: list[SectionBundle] = []
    for b in bundles:
        key = _pattern_key(b)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def _resolve_slot_sections(
    db: SectionsDB,
    code: CourseCode,
    term_label: str,
    *,
    exclude_full: bool,
) -> Optional[SlotSections]:
    """Resolve bundles for one course-term, with year-back extrapolation fallback."""
    code = code.upper()

    def _prep(bundles: list[SectionBundle], trust: Trust, label: str) -> Optional[SlotSections]:
        if exclude_full:
            bundles = [
                b
                for b in bundles
                if b.min_seats_remaining is None or b.min_seats_remaining > 0
            ]
        reps = dedup_bundles(bundles)
        if not reps:
            return None
        return SlotSections(
            term_label=label, trust=trust, bundles=reps, original_count=len(bundles)
        )

    exact = db.load_bundles(code, term_label)
    if exact:
        source = db.bundle_source(code, term_label)
        trust = Trust.EXTRAPOLATED if source == "extrapolated" else Trust.REAL
        prepared = _prep(exact, trust, term_label)
        if prepared is not None:
            return prepared

    for year_back in (1, 2):
        try:
            fallback_label = shift_term_label(term_label, -year_back)
        except ValueError:
            break
        bundles = db.load_bundles(code, fallback_label)
        if bundles:
            prepared = _prep(bundles, Trust.EXTRAPOLATED, fallback_label)
            if prepared is not None:
                return prepared
    return None


def load_section_data(
    db: SectionsDB,
    profile: Profile,
    slots: list,  # list of _Slot (planning_year, term, index)
    allowed_slots: dict[CourseCode, list],
    *,
    exclude_full: bool = False,
) -> SectionData:
    """Build the (course, slot) -> SlotSections map for all candidate courses.

    ``slots`` and ``allowed_slots`` come from the solver after slot construction;
    each entry exposes ``index``, ``planning_year`` and ``term``.
    """
    data = SectionData()
    label_cache: dict[tuple[int, Term], str] = {}

    for code, course_slots in allowed_slots.items():
        for s in course_slots:
            key = (s.planning_year, s.term)
            term_label = label_cache.get(key)
            if term_label is None:
                term_label = slot_label(profile, s.planning_year, s.term)
                label_cache[key] = term_label
            resolved = _resolve_slot_sections(
                db, code, term_label, exclude_full=exclude_full
            )
            if resolved is not None:
                data.entries[(code.upper(), s.index)] = resolved
    return data
