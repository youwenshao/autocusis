"""Progress engine: evaluate a profile against a curriculum.

Produces two things:
  * a human-facing :class:`ProgressReport` (per-group status + totals), and
  * the machine-facing scheduling demand (which courses are mandatory and how
    many credits/courses each elective pool still needs) that the CP-SAT
    scheduler consumes.
"""

from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, Field

from ..models import CourseCode
from ..profile import Profile
from .schema import Curriculum, RequirementGroup

# A callable that returns the credit value for a course code.
CreditFn = Callable[[CourseCode], float]


class GroupProgress(BaseModel):
    id: str
    name: str
    kind: str
    satisfied: bool
    completed_courses: list[CourseCode] = Field(default_factory=list)
    credits_done: float = 0.0
    credits_required: Optional[float] = None
    count_done: int = 0
    count_required: Optional[int] = None
    outstanding_required: list[CourseCode] = Field(default_factory=list)  # all_of gaps
    remaining_pool: list[CourseCode] = Field(default_factory=list)  # electives still choosable
    tracks_viable: list[list[CourseCode]] = Field(default_factory=list)  # one_of tracks still choosable
    credits_remaining: float = 0.0
    count_remaining: int = 0
    max_credits: Optional[float] = None
    max_pool_count: Optional[int] = None
    equivalence_tracks: list[list[CourseCode]] = Field(default_factory=list)
    note: Optional[str] = None


class ProgressReport(BaseModel):
    program: str
    cohort: Optional[str] = None
    total_credits_required: float
    total_credits_done: float
    groups: list[GroupProgress]

    @property
    def total_credits_remaining(self) -> float:
        return max(0.0, self.total_credits_required - self.total_credits_done)

    @property
    def all_satisfied(self) -> bool:
        return all(g.satisfied for g in self.groups) and self.total_credits_remaining <= 0


class ElectiveDemand(BaseModel):
    """An unmet elective requirement the scheduler must fill from a pool."""

    group_id: str
    group_name: str
    kind: str  # credits_from | count_from | one_of
    pool: list[CourseCode] = Field(default_factory=list)  # not-yet-completed, schedulable candidates
    tracks: list[list[CourseCode]] = Field(default_factory=list)  # one_of alternatives
    need_credits: float = 0.0
    max_credits: Optional[float] = None
    need_count: int = 0
    max_pool_count: Optional[int] = None
    equivalence_tracks: list[list[CourseCode]] = Field(default_factory=list)


class ScheduleDemand(BaseModel):
    """Everything the scheduler needs to know about *what* must still be taken."""

    mandatory: list[CourseCode] = Field(default_factory=list)
    electives: list[ElectiveDemand] = Field(default_factory=list)
    # Minimum credits the plan must schedule (degree total minus completed).
    min_total_planned_credits: float = 0.0

    def candidate_courses(self) -> set[CourseCode]:
        codes = set(self.mandatory)
        for e in self.electives:
            codes |= set(e.pool)
            for track in e.tracks:
                codes |= set(track)
        return codes


def _group_credits(group: RequirementGroup, codes: list[CourseCode], credit_fn: CreditFn) -> float:
    return sum(credit_fn(c) for c in codes)


def _track_credits(track: list[CourseCode], credit_fn: CreditFn) -> float:
    return sum(credit_fn(c) for c in track)


def _one_of_state(
    tracks: list[list[CourseCode]],
    done: set[CourseCode],
) -> tuple[bool, list[CourseCode], list[list[CourseCode]]]:
    """Return (satisfied, outstanding_required, viable_tracks) for a one_of group."""
    all_courses = {c for track in tracks for c in track}
    done_in_group = {c for c in done if c in all_courses}

    for track in tracks:
        if all(c in done for c in track):
            return True, [], tracks

    viable: list[list[CourseCode]] = []
    for track in tracks:
        track_set = set(track)
        if not any(c in done_in_group and c not in track_set for c in done_in_group):
            viable.append(track)

    if len(viable) == 1:
        track = viable[0]
        return False, [c for c in track if c not in done], viable

    return False, [], viable


def evaluate(
    curriculum: Curriculum,
    profile: Profile,
    credit_fn: CreditFn,
) -> ProgressReport:
    """Compute requirement progress for the given profile."""
    done = profile.completed_codes()
    groups: list[GroupProgress] = []

    for g in curriculum.groups:
        pool = g.normalized_courses()
        completed_in_group = [c for c in pool if c in done]
        credits_done = _group_credits(g, completed_in_group, credit_fn)
        gp = GroupProgress(
            id=g.id,
            name=g.name,
            kind=g.kind,
            satisfied=False,
            completed_courses=completed_in_group,
            credits_done=credits_done,
            count_done=len(completed_in_group),
            note=g.note,
        )

        if g.kind == "all_of":
            outstanding = [c for c in pool if c not in done]
            gp.outstanding_required = outstanding
            gp.satisfied = not outstanding
            gp.credits_required = _group_credits(g, pool, credit_fn)
        elif g.kind == "credits_from":
            need = g.min_credits or 0.0
            gp.credits_required = need
            gp.credits_remaining = max(0.0, need - credits_done)
            gp.remaining_pool = [c for c in pool if c not in done]
            gp.satisfied = credits_done >= need
            gp.max_credits = g.max_credits
            gp.max_pool_count = g.max_pool_count
            gp.equivalence_tracks = g.normalized_equivalence_tracks()
        elif g.kind == "count_from":
            need = g.min_count or 0
            gp.count_required = need
            gp.count_remaining = max(0, need - len(completed_in_group))
            gp.remaining_pool = [c for c in pool if c not in done]
            gp.satisfied = len(completed_in_group) >= need
        elif g.kind == "one_of":
            tracks = g.normalized_tracks()
            all_courses = [c for track in tracks for c in track]
            completed_in_group = [c for c in all_courses if c in done]
            gp.completed_courses = completed_in_group
            gp.credits_done = _group_credits(g, completed_in_group, credit_fn)
            if tracks:
                gp.credits_required = _track_credits(tracks[0], credit_fn)
            satisfied, outstanding, viable = _one_of_state(tracks, done)
            gp.satisfied = satisfied
            gp.outstanding_required = outstanding
            gp.tracks_viable = viable
            if not satisfied and not viable:
                done_in_group = {c for c in done if c in set(all_courses)}
                if done_in_group:
                    mixed = ", ".join(sorted(done_in_group))
                    extra = f" Mixed tracks ({mixed}); complete one full track."
                    gp.note = f"{g.note}{extra}" if g.note else extra.strip()

        groups.append(gp)

    total_done = sum(credit_fn(c) for c in done)
    return ProgressReport(
        program=curriculum.program,
        cohort=curriculum.cohort,
        total_credits_required=curriculum.total_credits_required,
        total_credits_done=total_done,
        groups=groups,
    )


def one_of_gap_summary(gp: GroupProgress) -> Optional[str]:
    """Human-readable elective-gap line for an open ``one_of`` group."""
    if gp.kind != "one_of" or gp.satisfied or not gp.tracks_viable:
        return None
    if len(gp.tracks_viable) > 1:
        opts = "; ".join(" + ".join(track) for track in gp.tracks_viable)
        return f"pick one of {len(gp.tracks_viable)} tracks: {opts}"
    track = gp.tracks_viable[0]
    return f"complete track: {' + '.join(track)}"


def build_demand(report: ProgressReport) -> ScheduleDemand:
    """Translate an unmet :class:`ProgressReport` into scheduling demand."""
    mandatory: list[CourseCode] = []
    electives: list[ElectiveDemand] = []
    for g in report.groups:
        if g.kind == "all_of":
            mandatory.extend(g.outstanding_required)
        elif g.kind == "credits_from" and g.credits_remaining > 0:
            electives.append(
                ElectiveDemand(
                    group_id=g.id,
                    group_name=g.name,
                    kind="credits_from",
                    pool=g.remaining_pool,
                    need_credits=g.credits_remaining,
                    max_credits=g.max_credits,
                    max_pool_count=g.max_pool_count,
                    equivalence_tracks=g.equivalence_tracks,
                )
            )
        elif g.kind == "count_from" and g.count_remaining > 0:
            electives.append(
                ElectiveDemand(
                    group_id=g.id,
                    group_name=g.name,
                    kind="count_from",
                    pool=g.remaining_pool,
                    need_count=g.count_remaining,
                )
            )
        elif g.kind == "one_of" and not g.satisfied:
            if g.outstanding_required:
                mandatory.extend(g.outstanding_required)
            elif g.tracks_viable:
                # Lock to the first viable track (YAML order) so the solver does
                # not flip between equivalent research paths across runs.
                tracks = g.tracks_viable if len(g.tracks_viable) == 1 else [g.tracks_viable[0]]
                electives.append(
                    ElectiveDemand(
                        group_id=g.id,
                        group_name=g.name,
                        kind="one_of",
                        tracks=tracks,
                    )
                )
    # De-duplicate mandatory while preserving order.
    seen: set[str] = set()
    mandatory = [c for c in mandatory if not (c in seen or seen.add(c))]
    return ScheduleDemand(
        mandatory=mandatory,
        electives=electives,
        min_total_planned_credits=report.total_credits_remaining,
    )
