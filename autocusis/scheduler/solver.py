"""CP-SAT study-plan scheduler.

Builds a constraint model that assigns each outstanding course to a planning
slot (year + term) subject to:
  * prerequisite ordering (a course's prereq AST must be satisfied by courses
    scheduled in strictly earlier slots, or already completed),
  * mutual exclusions,
  * term availability (a course only in the terms it is offered),
  * per-term and per-year credit caps,
  * priority pins (a course fixed to a specific slot),
  * requirement satisfaction (mandatory courses taken; elective pools filled to
    their credit/count floor; free-elective credit gaps filled with generic
    placeholder courses).

Objective is lexicographic (multi-objective). In ``fast`` mode (default):
  1. minimize the finishing term (fastest graduation),
  2. minimize the peak per-term credit load (balanced load),
  3. minimize total earliness index (gentle tie-break toward earlier terms).

In ``spread`` mode the planner uses the full horizon instead of cramming:
  1. maximize the finishing term (graduate as late as allowed),
  2. maximize the number of active terms (spread across the timeline),
  3. minimize peak per-term credit load,
  4. minimize load spread (max − min credits among active terms),
  5. minimize total planned credits (avoid overshooting the degree total),
  6. minimize subject clustering (avoid many same-subject courses in one term).

The model is solved in stages so each objective is optimized exactly, then
locked, before optimizing the next. Alternate plans reuse the locked optimal
objectives with no-good cuts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Union

from ortools.sat.python import cp_model

from ..ingest.availability_store import AvailabilityStore
from ..models import Course, CourseCode, PrereqExpr, Term
from ..profile import Profile
from ..requirements.engine import ScheduleDemand
from ..sections.db import SectionsDB
from .plan import Plan, PlannedCourse, Semester
from .section_constraints import SectionConstraintBuilder, SectionModelResult
from .section_data import SectionData, Trust, load_section_data

CREDIT_SCALE = 10  # integerize credits (handles .5 units)
FILLER_PREFIX = "FREE-ELECTIVE"
FILLER_CREDITS = 3.0

# Mandatory I→II pairs that must occupy immediately adjacent planning slots.
MANDATORY_CONSECUTIVE_PAIRS: tuple[tuple[CourseCode, CourseCode], ...] = (
    ("GESH2011", "GESH2012"),
)

# Mandatory co-requisite pairs that must share the same planning slot.
MANDATORY_SAME_SEMESTER_PAIRS: tuple[tuple[CourseCode, CourseCode], ...] = (
    ("AIST2601", "AIST2602"),
)

# A compiled prerequisite term is either a CP-SAT literal or a constant 0/1.
Lit = Union[cp_model.IntVar, int]


def _required_prereq_codes(node: PrereqExpr, completed: set[CourseCode]) -> set[CourseCode]:
    """Concrete codes that must be scheduled before the parent (AND branches only).

    OR alternatives are omitted: the solver chooses a satisfied branch via
    prereq constraints, so expanding every OR arm into the candidate set would
    invite out-of-pool filler picks (e.g. MATH2221 as an alternative to CSCI1130).
    """
    if node.is_satisfied(completed):
        return set()
    if node.kind == "course" and node.code:
        return {node.code} if node.code not in completed else set()
    if node.kind == "and":
        out: set[CourseCode] = set()
        for op in node.operands:
            out |= _required_prereq_codes(op, completed)
        return out
    if node.kind == "or":
        return set()
    return set()


@dataclass
class SchedulerInput:
    demand: ScheduleDemand
    profile: Profile
    catalog: dict[CourseCode, Course]
    availability: AvailabilityStore
    assume_unknown_available: bool = False
    strict_prereqs: bool = False
    sections_db: Optional[SectionsDB] = None
    # Elective specialization bias (soft). ``preferred_stream`` is a stream id and
    # ``course_stream`` maps elective course codes to their stream id.
    preferred_stream: Optional[str] = None
    course_stream: dict[CourseCode, str] = field(default_factory=dict)


@dataclass
class _Slot:
    index: int
    planning_year: int
    term: Term


@dataclass
class _ModelCtx:
    """Per-model-build state: the CP-SAT model plus its objective variables."""

    model: cp_model.CpModel
    notes: list[str] = field(default_factory=list)
    setup_failed: bool = False
    last_slot: Optional[cp_model.IntVar] = None
    peak_load: Optional[cp_model.IntVar] = None
    sum_idx: Optional[cp_model.IntVar] = None
    active_count: Optional[cp_model.IntVar] = None
    load_spread: Optional[cp_model.IntVar] = None
    estr_penalty: Optional[cp_model.IntVar] = None
    subject_penalty: Optional[cp_model.IntVar] = None
    preference_term: Optional[cp_model.IntVar] = None
    stream_penalty: Optional[cp_model.IntVar] = None
    in_stream_count: Optional[cp_model.IntVar] = None
    elective_tiebreak: Optional[cp_model.IntVar] = None
    assignment_tiebreak: Optional[cp_model.IntVar] = None
    total_planned_credits: Optional[cp_model.IntVar] = None
    section: SectionModelResult = field(default_factory=SectionModelResult)
    relaxed: bool = False


class Scheduler:
    def __init__(self, inp: SchedulerInput, time_limit_s: float = 15.0):
        self.inp = inp
        self.time_limit_s = time_limit_s
        self.completed = inp.profile.completed_codes()
        self.effective_completed = inp.profile.effective_completed_codes()
        self.excluded = inp.profile.excluded_codes()
        self.slots: list[_Slot] = []
        self.candidates: list[CourseCode] = []
        self.filler_group: dict[str, list[CourseCode]] = {}  # group_id -> filler codes
        self.units: dict[CourseCode, float] = {}
        self.allowed_slots: dict[CourseCode, list[_Slot]] = {}
        self.x: dict[tuple[CourseCode, int], cp_model.IntVar] = {}
        self.taken: dict[CourseCode, cp_model.IntVar] = {}
        self._done_before_cache: dict[tuple[CourseCode, int], Lit] = {}
        self._slot_load: dict[int, cp_model.IntVar] = {}
        self._slot_active: dict[int, cp_model.IntVar] = {}
        self._subject_cluster_penalty: cp_model.IntVar | None = None
        self._estr_pick_penalty: cp_model.IntVar | None = None
        self._stream_penalty: cp_model.IntVar | None = None
        self._in_stream_count: cp_model.IntVar | None = None
        self._elective_tiebreak: cp_model.IntVar | None = None
        self._assignment_tiebreak: cp_model.IntVar | None = None
        self.section_data: SectionData = SectionData()
        self._assembled = False

    def _reset_model_state(self) -> None:
        """Clear per-model CP-SAT variable caches before (re)building the model."""
        self.x = {}
        self.taken = {}
        self._done_before_cache = {}
        self._slot_load = {}
        self._slot_active = {}
        self._subject_cluster_penalty = None
        self._estr_pick_penalty = None
        self._stream_penalty = None
        self._in_stream_count = None
        self._elective_tiebreak = None
        self._assignment_tiebreak = None

    # -- setup helpers ------------------------------------------------------
    def _regular_terms(self) -> list[Term]:
        terms = [Term.TERM1, Term.TERM2]
        if self.inp.profile.allow_summer:
            terms.append(Term.SUMMER)
        return terms

    def _build_slots(self) -> None:
        p = self.inp.profile
        remaining_years = max(1, p.planning_horizon_years - p.current_year + 1)
        idx = 0
        for py in range(1, remaining_years + 1):
            for term in self._regular_terms():
                # In the first planned year, skip terms before the current term.
                if py == 1 and int(term) < int(p.current_term):
                    continue
                self.slots.append(_Slot(index=idx, planning_year=py, term=term))
                idx += 1

    def _pool_codes(self) -> set[CourseCode]:
        return {c.upper() for c in self.inp.demand.candidate_courses()}

    def _prereq_closure(self, seed: set[CourseCode]) -> set[CourseCode]:
        """Expand the candidate set with transitive AND-required prerequisites."""
        result = set(seed)
        frontier = list(seed)
        completed = self.effective_completed
        while frontier:
            code = frontier.pop()
            course = self.inp.catalog.get(code)
            if not course:
                continue
            for ref in _required_prereq_codes(course.prerequisite, completed):
                if ref in self.excluded or ref in result:
                    continue
                result.add(ref)
                frontier.append(ref)
        return result

    def _out_of_pool_support(self) -> dict[CourseCode, list[CourseCode]]:
        """Map out-of-pool prereq codes to in-pool courses that AND-require them."""
        pool = self._pool_codes()
        support: dict[CourseCode, list[CourseCode]] = {}
        completed = self.effective_completed
        for code in pool:
            course = self.inp.catalog.get(code)
            if not course:
                continue
            for req in _required_prereq_codes(course.prerequisite, completed):
                if req not in pool:
                    support.setdefault(req, []).append(code)
        return support

    def _add_out_of_pool_constraints(self, model: cp_model.CpModel) -> None:
        """Forbid scheduling out-of-pool courses unless an in-pool dependent needs them."""
        pool = self._pool_codes()
        fillers = self.filler_codes()
        support = self._out_of_pool_support()
        for c in self.candidates:
            if c in pool or c in fillers or c in self.completed:
                continue
            deps = [d for d in support.get(c, []) if d in self.taken]
            if not deps:
                model.Add(self.taken[c] == 0)
            else:
                model.Add(self.taken[c] <= sum(self.taken[d] for d in deps))

    def _make_fillers(self) -> None:
        """Create generic placeholder courses for empty-pool credit demands."""
        for e in self.inp.demand.electives:
            if e.kind == "credits_from" and not e.pool and e.need_credits > 0:
                n = math.ceil(e.need_credits / FILLER_CREDITS)
                codes = [f"{FILLER_PREFIX}-{e.group_id}-{i+1}" for i in range(n)]
                self.filler_group[e.group_id] = codes
                for c in codes:
                    self.units[c] = FILLER_CREDITS

    def _units_of(self, code: CourseCode) -> float:
        if code in self.units:
            return self.units[code]
        course = self.inp.catalog.get(code)
        return course.units if course else 3.0

    def _scaled(self, credits: float) -> int:
        return int(round(credits * CREDIT_SCALE))

    def _resolve_terms(self, code: CourseCode) -> list[Term]:
        if code in self.filler_codes():
            return self._regular_terms()
        return self.inp.availability.resolve(
            code,
            tuple(self._regular_terms()),
            assume_unknown_available=self.inp.assume_unknown_available,
        )

    def filler_codes(self) -> set[CourseCode]:
        out: set[CourseCode] = set()
        for codes in self.filler_group.values():
            out |= set(codes)
        return out

    # -- model building -----------------------------------------------------
    def _assemble(self) -> None:
        """Build slots, fillers, candidates and section data (model-independent)."""
        if self._assembled:
            return
        self._build_slots()
        self._make_fillers()

        seed = set(self.inp.demand.candidate_courses()) - self.completed - self.excluded
        seed |= self.filler_codes()
        all_candidates = self._prereq_closure(seed) | self.filler_codes()
        self.candidates = sorted(all_candidates)

        for c in self.candidates:
            self.units[c] = self._units_of(c)
            self.allowed_slots[c] = [
                s for s in self.slots if s.term in self._resolve_terms(c)
            ]

        self.section_data = self._load_section_data()
        self._assembled = True

    def _load_section_data(self) -> SectionData:
        if not self.inp.profile.section_aware or self.inp.sections_db is None:
            return SectionData()
        return load_section_data(
            self.inp.sections_db,
            self.inp.profile,
            self.slots,
            self.allowed_slots,
            exclude_full=False,  # planning is forward-looking; seats are volatile
        )

    def _build_model(self, relax_real: bool) -> _ModelCtx:
        """Assemble a fresh CP-SAT model. ``relax_real`` softens hard section
        conflicts (the relief valve when real-data clashes are unavoidable)."""
        self._reset_model_state()
        model = cp_model.CpModel()
        ctx = _ModelCtx(model=model, relaxed=relax_real)

        # Decision vars
        for c in self.candidates:
            for s in self.allowed_slots[c]:
                self.x[(c, s.index)] = model.NewBoolVar(f"x_{c}_{s.index}")
            tk = model.NewBoolVar(f"taken_{c}")
            self.taken[c] = tk
            slot_vars = [self.x[(c, s.index)] for s in self.allowed_slots[c]]
            if slot_vars:
                model.Add(sum(slot_vars) == tk)
            else:
                model.Add(tk == 0)

        self._precheck(ctx.notes)

        # Mandatory courses must be taken.
        for c in self.inp.demand.mandatory:
            c = c.upper()
            if c in self.completed:
                continue
            if c not in self.taken or not self.allowed_slots.get(c):
                ctx.notes.append(
                    f"Mandatory course {c} has no available slot in the horizon "
                    f"(check availability / horizon)."
                )
                ctx.setup_failed = True
                return ctx
            model.Add(self.taken[c] == 1)

        self._add_prereq_constraints(model)
        self._add_out_of_pool_constraints(model)
        self._add_exclusion_constraints(model, ctx.notes)
        self._add_credit_caps(model)
        self._add_elective_demands(model)
        self._add_total_credit_floor(model, ctx)
        self._add_sequential_track_timing(model)
        self._add_consecutive_mandatory_timing(model)
        self._add_same_semester_mandatory_timing(model)
        self._add_equivalence_constraints(model)

        if not self._add_pins(model, ctx.notes):
            ctx.setup_failed = True
            return ctx

        # Section (timetable) constraints.
        if self.inp.profile.section_aware and self.section_data.has_any():
            builder = SectionConstraintBuilder(
                self.section_data,
                weight=self.inp.profile.section_conflict_weight,
                relax_real=relax_real,
                trust_extrapolated_hard=self.inp.profile.trust_extrapolated_hard,
            )
            ctx.section = builder.build(
                model, self.candidates, self.allowed_slots, self.x
            )

        # Objective variables.
        last_slot, peak_load, sum_idx = self._objective_vars(model)
        ctx.last_slot = last_slot
        ctx.peak_load = peak_load
        ctx.sum_idx = sum_idx
        if self.inp.profile.planning_mode == "spread":
            ctx.active_count = self._active_term_count_var(model)
            ctx.load_spread = self._load_spread_var(model, peak_load)
            self._add_subject_balance_penalty(model)
            self._add_estr_preference_penalty(model)
            ctx.estr_penalty = self._estr_pick_penalty
            ctx.subject_penalty = self._subject_cluster_penalty
        self._add_stream_preference_penalty(model)
        ctx.stream_penalty = self._stream_penalty
        ctx.in_stream_count = self._in_stream_count
        self._add_elective_tiebreak_penalty(model)
        ctx.elective_tiebreak = self._elective_tiebreak
        self._add_assignment_tiebreak_penalty(model)
        ctx.assignment_tiebreak = self._assignment_tiebreak
        return ctx

    def build_and_solve(self, max_plans: int = 1) -> list[Plan]:
        self._assemble()

        last_ctx: _ModelCtx | None = None
        # First attempt enforces real-data section conflicts as hard. If that is
        # infeasible (an unavoidable real clash), relax them to a heavy soft
        # penalty so we still return a plan, with the relaxed pairs reported.
        for relax_real in (False, True):
            ctx = self._build_model(relax_real)
            last_ctx = ctx
            if ctx.setup_failed:
                return [Plan(feasible=False, notes=ctx.notes)]

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = self.time_limit_s
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 1

            st = self._solve_staged(ctx, solver)
            if st in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                notes = list(ctx.notes)
                if relax_real:
                    notes.extend(self._relaxed_conflict_notes(solver, ctx))
                plans = [self._extract_plan(solver, ctx, notes)]
                for _ in range(max_plans - 1):
                    self._add_nogood(ctx.model, solver)
                    st = solver.Solve(ctx.model)
                    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                        break
                    plans.append(self._extract_plan(solver, ctx, []))
                return plans

            # Only the relief valve (relaxing hard section conflicts) can help; if
            # there were no hard section pairs, retrying is pointless.
            if relax_real or ctx.section.hard_pairs == 0:
                break

        notes = list(last_ctx.notes) if last_ctx else []
        notes.append(self._infeasible_hint(cp_model.INFEASIBLE))
        return [Plan(feasible=False, notes=notes)]

    def _objective_stages(self, ctx: _ModelCtx) -> list[tuple[cp_model.IntVar, bool]]:
        """Ordered (variable, maximize) lexicographic objective stages."""
        stages: list[tuple[cp_model.IntVar, bool]] = []
        section = ctx.section.penalty
        if self.inp.profile.planning_mode == "spread":
            stages.append((ctx.last_slot, True))
            stages.append((ctx.active_count, True))
            stages.append((ctx.peak_load, False))
            stages.append((ctx.load_spread, False))
            if ctx.total_planned_credits is not None:
                stages.append((ctx.total_planned_credits, False))
            if section is not None:
                stages.append((section, False))
            if ctx.estr_penalty is not None:
                stages.append((ctx.estr_penalty, False))
            if ctx.subject_penalty is not None:
                stages.append((ctx.subject_penalty, False))
            if ctx.in_stream_count is not None:
                stages.append((ctx.in_stream_count, True))
            if ctx.stream_penalty is not None:
                stages.append((ctx.stream_penalty, False))
            if ctx.sum_idx is not None:
                stages.append((ctx.sum_idx, False))
        else:
            stages.append((ctx.last_slot, False))
            if section is not None:
                stages.append((section, False))
            stages.append((ctx.peak_load, False))
            if ctx.total_planned_credits is not None:
                stages.append((ctx.total_planned_credits, False))
            if ctx.in_stream_count is not None:
                stages.append((ctx.in_stream_count, True))
            if ctx.stream_penalty is not None:
                stages.append((ctx.stream_penalty, False))
            stages.append((ctx.sum_idx, False))
        if ctx.preference_term is not None:
            stages.append((ctx.preference_term, False))
        if ctx.elective_tiebreak is not None:
            stages.append((ctx.elective_tiebreak, False))
        if ctx.assignment_tiebreak is not None:
            stages.append((ctx.assignment_tiebreak, False))
        return stages

    def _solve_staged(self, ctx: _ModelCtx, solver: cp_model.CpSolver) -> int:
        st = cp_model.FEASIBLE
        for var, maximize in self._objective_stages(ctx):
            if var is None:
                continue
            st = self._optimize_stage(ctx.model, solver, var, maximize=maximize)
            if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                return st
        return st

    def _relaxed_conflict_notes(
        self, solver: cp_model.CpSolver, ctx: _ModelCtx
    ) -> list[str]:
        notes: list[str] = []
        for code_a, code_b, term_label, was_hard in ctx.section.violated_conflicts(solver):
            if was_hard:
                notes.append(
                    f"Unavoidable section conflict relaxed: {code_a} x {code_b} "
                    f"in {term_label} (no conflict-free placement exists)."
                )
        return notes

    @staticmethod
    def _optimize_stage(
        model: cp_model.CpModel,
        solver: cp_model.CpSolver,
        var: cp_model.IntVar,
        *,
        maximize: bool,
    ) -> int:
        if maximize:
            model.Maximize(var)
        else:
            model.Minimize(var)
        st = solver.Solve(model)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return st
        bound = int(solver.Value(var))
        if maximize:
            model.Add(var >= bound)
        else:
            model.Add(var <= bound)
        return st

    def _precheck(self, notes: list[str]) -> None:
        """Flag elective pools that cannot possibly meet their requirement.

        Catches the common "placeholder pool too small" case and reports it
        precisely instead of a generic infeasibility message.
        """
        floor = self.inp.demand.min_total_planned_credits
        if floor > 0:
            supply = sum(
                self._units_of(c)
                for c in self.candidates
                if c not in self.completed and self.allowed_slots.get(c)
            )
            if supply < floor:
                notes.append(
                    f"Degree credit floor: schedulable pool supplies only {supply:g} cr "
                    f"but {floor:g} cr are required to reach the graduation total."
                )

        for e in self.inp.demand.electives:
            if e.kind == "credits_from" and e.pool:
                supply = sum(
                    self._units_of(c.upper())
                    for c in e.pool
                    if c.upper() not in self.completed and self.allowed_slots.get(c.upper())
                )
                if supply < e.need_credits:
                    notes.append(
                        f"Elective group '{e.group_name}': remaining pool supplies only "
                        f"{supply:g} cr but {e.need_credits:g} cr are required. "
                        f"Add more courses to the pool, mark prerequisites/availability, "
                        f"or reduce min_credits."
                    )
            elif e.kind == "count_from":
                supply = sum(
                    1
                    for c in e.pool
                    if c.upper() not in self.completed and self.allowed_slots.get(c.upper())
                )
                if supply < e.need_count:
                    notes.append(
                        f"Elective group '{e.group_name}': only {supply} schedulable course(s) "
                        f"remain but {e.need_count} are required."
                    )
            elif e.kind == "one_of":
                schedulable_tracks = 0
                for track in e.tracks:
                    if all(
                        c.upper() in self.completed or self.allowed_slots.get(c.upper())
                        for c in track
                    ):
                        schedulable_tracks += 1
                if schedulable_tracks == 0:
                    notes.append(
                        f"Elective group '{e.group_name}': no track can be fully scheduled "
                        f"within the planning horizon."
                    )

    # -- constraint pieces --------------------------------------------------
    def _done_before(self, model: cp_model.CpModel, code: CourseCode, s_index: int) -> Lit:
        code = code.upper()
        key = (code, s_index)
        if key in self._done_before_cache:
            return self._done_before_cache[key]
        if code in self.effective_completed:
            self._done_before_cache[key] = 1
            return 1
        if code not in self.taken:
            self._done_before_cache[key] = 0
            return 0
        earlier = [
            self.x[(code, s.index)]
            for s in self.allowed_slots[code]
            if s.index < s_index
        ]
        if not earlier:
            self._done_before_cache[key] = 0
            return 0
        db = model.NewBoolVar(f"db_{code}_{s_index}")
        model.Add(db == sum(earlier))
        self._done_before_cache[key] = db
        return db

    @staticmethod
    def _is_const(lit: Lit, value: int) -> bool:
        return isinstance(lit, int) and lit == value

    def _lit_and(self, model: cp_model.CpModel, lits: list[Lit]) -> Lit:
        if any(self._is_const(l, 0) for l in lits):
            return 0
        real = [l for l in lits if not self._is_const(l, 1)]
        if not real:
            return 1
        if len(real) == 1:
            return real[0]
        y = model.NewBoolVar("and")
        model.AddBoolAnd(real).OnlyEnforceIf(y)
        model.AddBoolOr([l.Not() for l in real]).OnlyEnforceIf(y.Not())
        return y

    def _lit_or(self, model: cp_model.CpModel, lits: list[Lit]) -> Lit:
        if any(self._is_const(l, 1) for l in lits):
            return 1
        real = [l for l in lits if not self._is_const(l, 0)]
        if not real:
            return 0
        if len(real) == 1:
            return real[0]
        y = model.NewBoolVar("or")
        model.AddBoolOr(real).OnlyEnforceIf(y)
        model.AddBoolAnd([l.Not() for l in real]).OnlyEnforceIf(y.Not())
        return y

    def _compile_prereq(self, model: cp_model.CpModel, node: PrereqExpr, s_index: int) -> Lit:
        if node.kind == "raw" and self.inp.strict_prereqs:
            return 0
        if node.kind in ("none", "raw"):
            return 1
        if node.kind == "course":
            return self._done_before(model, node.code, s_index) if node.code else 1
        child = [self._compile_prereq(model, o, s_index) for o in node.operands]
        if node.kind == "and":
            return self._lit_and(model, child)
        return self._lit_or(model, child)

    def _add_prereq_constraints(self, model: cp_model.CpModel) -> None:
        for c in self.candidates:
            course = self.inp.catalog.get(c)
            if not course or course.prerequisite.kind == "none":
                continue
            if course.prerequisite.kind == "raw" and not self.inp.strict_prereqs:
                continue
            for s in self.allowed_slots[c]:
                lit = self._compile_prereq(model, course.prerequisite, s.index)
                xvar = self.x[(c, s.index)]
                if self._is_const(lit, 1):
                    continue
                if self._is_const(lit, 0):
                    model.Add(xvar == 0)
                else:
                    model.AddImplication(xvar, lit)

    def _add_exclusion_constraints(self, model: cp_model.CpModel, notes: list[str]) -> None:
        cand_set = set(self.candidates)
        pairs: set[tuple[str, str]] = set()
        for c in self.candidates:
            course = self.inp.catalog.get(c)
            if not course:
                continue
            for other in course.exclusion_codes:
                other = other.upper()
                # Exclusions prevent taking both for credit in the same plan, not
                # blocking a course because an exclusion was taken earlier (e.g.
                # CSCI1130 as an alternative prereq path while AIST1110 is still
                # required).
                if other in cand_set:
                    pairs.add(tuple(sorted((c, other))))
        for a, b in pairs:
            model.Add(self.taken[a] + self.taken[b] <= 1)

    def _add_credit_caps(self, model: cp_model.CpModel) -> None:
        p = self.inp.profile
        term_cap = self._scaled(p.max_credits_per_term)
        term_floor = self._scaled(p.min_credits_per_term)
        year_cap = self._scaled(p.max_credits_per_year)

        self._ensure_slot_vars(model)

        # Per-term cap and optional minimum (active slots only).
        for s in self.slots:
            load = self._slot_load.get(s.index)
            if load is None:
                continue
            model.Add(load <= term_cap)
            if term_floor > 0:
                active = self._slot_active[s.index]
                model.Add(load >= term_floor).OnlyEnforceIf(active)

        # Per-year cap
        years = {s.planning_year for s in self.slots}
        for py in years:
            year_slots = [s.index for s in self.slots if s.planning_year == py]
            load = [
                self._scaled(self.units[c]) * self.x[(c, si)]
                for c in self.candidates
                for si in year_slots
                if (c, si) in self.x
            ]
            if load:
                model.Add(sum(load) <= year_cap)

    def _ensure_slot_vars(self, model: cp_model.CpModel) -> None:
        if self._slot_load:
            return
        term_cap = self._scaled(self.inp.profile.max_credits_per_term)
        for s in self.slots:
            load_terms = [
                self._scaled(self.units[c]) * self.x[(c, s.index)]
                for c in self.candidates
                if (c, s.index) in self.x
            ]
            if not load_terms:
                continue
            load = model.NewIntVar(0, term_cap, f"load_{s.index}")
            model.Add(load == sum(load_terms))
            self._slot_load[s.index] = load
            slot_vars = [
                self.x[(c, s.index)] for c in self.candidates if (c, s.index) in self.x
            ]
            active = model.NewBoolVar(f"active_{s.index}")
            model.AddMaxEquality(active, slot_vars)
            self._slot_active[s.index] = active

    def _active_term_count_var(self, model: cp_model.CpModel) -> cp_model.IntVar:
        self._ensure_slot_vars(model)
        n = len(self._slot_active)
        count = model.NewIntVar(0, n, "active_term_count")
        if self._slot_active:
            model.Add(count == sum(self._slot_active.values()))
        else:
            model.Add(count == 0)
        return count

    def _load_spread_var(
        self, model: cp_model.CpModel, peak_load: cp_model.IntVar
    ) -> cp_model.IntVar:
        self._ensure_slot_vars(model)
        term_cap = self._scaled(self.inp.profile.max_credits_per_term)
        min_load = model.NewIntVar(0, term_cap, "min_active_load")
        for s in self.slots:
            load = self._slot_load.get(s.index)
            active = self._slot_active.get(s.index)
            if load is None or active is None:
                continue
            model.Add(load >= min_load).OnlyEnforceIf(active)
        spread = model.NewIntVar(0, term_cap, "load_spread")
        model.Add(spread == peak_load - min_load)
        return spread

    def _course_prefix(self, code: CourseCode) -> str:
        course = self.inp.catalog.get(code)
        if course:
            return course.subject_prefix
        i = 0
        while i < len(code) and code[i].isalpha():
            i += 1
        return code[:i] or "OTHER"

    def _add_subject_balance_penalty(self, model: cp_model.CpModel) -> None:
        """Penalize stacking many courses from the same subject prefix in one term."""
        prefixes = sorted({self._course_prefix(c) for c in self.candidates})
        if not prefixes:
            return
        penalty_terms: list[cp_model.IntVar] = []
        for s in self.slots:
            slot_vars = [
                self.x[(c, s.index)] for c in self.candidates if (c, s.index) in self.x
            ]
            if not slot_vars:
                continue
            max_in_slot = len(slot_vars)
            for prefix in prefixes:
                group = [
                    self.x[(c, s.index)]
                    for c in self.candidates
                    if self._course_prefix(c) == prefix and (c, s.index) in self.x
                ]
                if len(group) < 2:
                    continue
                count = model.NewIntVar(0, len(group), f"subj_{prefix}_{s.index}")
                model.Add(count == sum(group))
                excess = model.NewIntVar(0, max_in_slot, f"subj_excess_{prefix}_{s.index}")
                model.Add(excess >= count - 1)
                penalty_terms.append(excess)
        if not penalty_terms:
            return
        total = model.NewIntVar(0, len(penalty_terms) * max(1, len(self.slots)), "subject_cluster_penalty")
        model.Add(total == sum(penalty_terms))
        self._subject_cluster_penalty = total

    def _add_estr_preference_penalty(self, model: cp_model.CpModel) -> None:
        """Minimize ESTR picks from declared equivalence tracks (prefer CSCI/AIST)."""
        estr_codes: list[CourseCode] = []
        seen: set[CourseCode] = set()
        for e in self.inp.demand.electives:
            for track in e.equivalence_tracks:
                for code in track:
                    code = code.upper()
                    if code.startswith("ESTR") and code in self.taken and code not in seen:
                        seen.add(code)
                        estr_codes.append(code)
        if not estr_codes:
            return
        total = model.NewIntVar(0, len(estr_codes), "estr_pick_count")
        model.Add(total == sum(self.taken[c] for c in estr_codes))
        self._estr_pick_penalty = total

    def _optional_electives(self) -> list[CourseCode]:
        """Schedulable, non-mandatory, non-filler elective candidates (sorted)."""
        mandatory = {c.upper() for c in self.inp.demand.mandatory}
        fillers = self.filler_codes()
        return sorted(
            c
            for c in self.candidates
            if c not in self.completed
            and c not in fillers
            and c not in mandatory
            and c in self.taken
        )

    def _add_stream_preference_penalty(self, model: cp_model.CpModel) -> None:
        """Softly bias elective picks toward the profile's chosen stream.

        Two lexicographic signals (after graduation/load objectives):
          1. maximize in-stream mapped electives,
          2. minimize electives mapped to a *different* stream.

        Neutral unmapped courses (e.g. ENGG2720) are neither rewarded nor
        penalized so the objective targets cross-stream drift without fighting
        intentional generic fallbacks.
        """
        stream = self.inp.preferred_stream
        if not stream:
            return
        course_stream = self.inp.course_stream
        optional = self._optional_electives()
        in_stream = [
            self.taken[code] for code in optional if course_stream.get(code) == stream
        ]
        if in_stream:
            total_in = model.NewIntVar(0, len(in_stream), "in_stream_count")
            model.Add(total_in == sum(in_stream))
            self._in_stream_count = total_in
        out_of_stream = [
            self.taken[code]
            for code in optional
            if (mapped := course_stream.get(code)) is not None and mapped != stream
        ]
        if out_of_stream:
            total_out = model.NewIntVar(0, len(out_of_stream), "stream_penalty")
            model.Add(total_out == sum(out_of_stream))
            self._stream_penalty = total_out

    def _add_elective_tiebreak_penalty(self, model: cp_model.CpModel) -> None:
        """Prefer lexicographically earlier courses when objectives tie."""
        optional = self._optional_electives()
        terms: list[cp_model.LinearExpr] = []
        for rank, code in enumerate(optional):
            terms.append(rank * self.taken[code])
        if not terms:
            return
        max_penalty = sum(range(len(terms)))
        total = model.NewIntVar(0, max_penalty, "elective_tiebreak")
        model.Add(total == sum(terms))
        self._elective_tiebreak = total

    def _add_assignment_tiebreak_penalty(self, model: cp_model.CpModel) -> None:
        """Break ties on which slot each course occupies (lexicographic by code)."""
        if not self.x:
            return
        n_slots = max(1, len(self.slots))
        ranking = {code: rank for rank, code in enumerate(sorted({c for c, _ in self.x}))}
        terms: list[cp_model.LinearExpr] = []
        for (code, si), xv in self.x.items():
            if code in self.completed:
                continue
            weight = ranking[code] * n_slots + si
            terms.append(weight * xv)
        max_penalty = sum(
            ranking[code] * n_slots + si
            for (code, si) in self.x
            if code not in self.completed
        )
        total = model.NewIntVar(0, max_penalty, "assignment_tiebreak")
        model.Add(total == sum(terms))
        self._assignment_tiebreak = total

    def _add_equivalence_constraints(self, model: cp_model.CpModel) -> None:
        for e in self.inp.demand.electives:
            for track in e.equivalence_tracks:
                lits = [self.taken[c.upper()] for c in track if c.upper() in self.taken]
                if len(lits) > 1:
                    model.Add(sum(lits) <= 1)

    def _track_engaged(self, model: cp_model.CpModel, track: list[CourseCode]) -> Lit:
        for c in track:
            code = c.upper()
            if code in self.completed:
                return 1
        lits = [self.taken[code] for c in track if (code := c.upper()) in self.taken]
        return self._lit_or(model, lits) if lits else 0

    def _track_complete(self, model: cp_model.CpModel, track: list[CourseCode]) -> Lit:
        lits: list[Lit] = []
        for c in track:
            code = c.upper()
            if code in self.completed:
                continue
            if code in self.taken:
                lits.append(self.taken[code])
            else:
                return 0
        return self._lit_and(model, lits) if lits else 1

    def _is_sequential_pair_track(self, track: list[CourseCode]) -> bool:
        """True when a two-course track is a strict I→II sequence (e.g. FYP/thesis)."""
        if len(track) != 2:
            return False
        c0, c1 = track[0].upper(), track[1].upper()
        course = self.inp.catalog.get(c1)
        if not course:
            return False
        return c0 in course.prerequisite.referenced_codes()

    @staticmethod
    def _is_thesis_sequence(c0: CourseCode, c1: CourseCode) -> bool:
        """True for FYP / graduation-thesis I→II pairs (e.g. AIST4998→AIST4999)."""
        c0, c1 = c0.upper(), c1.upper()
        return c0.endswith("4998") and c1 == f"{c0[:-1]}9"

    def _sequential_pairs(self) -> list[tuple[CourseCode, CourseCode]]:
        """Collect I→II course pairs from one_of tracks and locked mandatory part II."""
        pairs: list[tuple[CourseCode, CourseCode]] = []
        seen: set[tuple[str, str]] = set()

        def add(c0: CourseCode, c1: CourseCode) -> None:
            key = (c0.upper(), c1.upper())
            if key not in seen:
                seen.add(key)
                pairs.append(key)

        for e in self.inp.demand.electives:
            if e.kind != "one_of":
                continue
            for track in e.tracks:
                if self._is_sequential_pair_track(track):
                    add(track[0], track[1])

        for c1 in self.inp.demand.mandatory:
            course = self.inp.catalog.get(c1.upper())
            if not course:
                continue
            for c0 in course.prerequisite.referenced_codes():
                c0 = c0.upper()
                if c0 in self.completed and self._is_thesis_sequence(c0, c1):
                    add(c0, c1.upper())
        return pairs

    def _add_sequential_track_timing(self, model: cp_model.CpModel) -> None:
        """Pin two-part sequential tracks to the last two horizon slots.

        Final-year project / thesis parts I and II must occupy the penultimate
        and final planning slots (consecutive terms), with part II immediately
        after part I. When only part II remains, it is fixed to the final slot.
        """
        if len(self.slots) < 2:
            return
        penultimate = self.slots[-2]
        final = self.slots[-1]
        for c0, c1 in self._sequential_pairs():
            if c0 not in self.completed and c0 in self.taken:
                if (c0, penultimate.index) in self.x:
                    model.AddImplication(self.taken[c0], self.x[(c0, penultimate.index)])
                else:
                    model.Add(self.taken[c0] == 0)

            if c1 not in self.completed and c1 in self.taken:
                if (c1, final.index) in self.x:
                    model.AddImplication(self.taken[c1], self.x[(c1, final.index)])
                else:
                    model.Add(self.taken[c1] == 0)

    def _add_consecutive_mandatory_timing(self, model: cp_model.CpModel) -> None:
        """Force mandatory course pairs into immediately adjacent planning slots.

        Used for SHHO service-learning (GESH2011 then GESH2012 in the next term).
        Skipped when part I is already completed (only part II remains).
        """
        if len(self.slots) < 2:
            return
        for c0, c1 in MANDATORY_CONSECUTIVE_PAIRS:
            c0, c1 = c0.upper(), c1.upper()
            if c0 in self.completed or c1 in self.completed:
                continue
            if c0 not in self.taken or c1 not in self.taken:
                continue

            for i in range(len(self.slots) - 1):
                s0, s1 = self.slots[i], self.slots[i + 1]
                k0 = (c0, s0.index)
                k1 = (c1, s1.index)
                if k0 in self.x and k1 in self.x:
                    model.AddImplication(self.x[k0], self.x[k1])
                elif k0 in self.x:
                    model.Add(self.x[k0] == 0)

            for i in range(1, len(self.slots)):
                s_prev, s_cur = self.slots[i - 1], self.slots[i]
                k0 = (c0, s_prev.index)
                k1 = (c1, s_cur.index)
                if k0 in self.x and k1 in self.x:
                    model.AddImplication(self.x[k1], self.x[k0])
                elif k1 in self.x:
                    model.Add(self.x[k1] == 0)

            final = self.slots[-1]
            if (c0, final.index) in self.x:
                model.Add(self.x[(c0, final.index)] == 0)

    def _add_same_semester_mandatory_timing(self, model: cp_model.CpModel) -> None:
        """Force mandatory course pairs into the same planning slot.

        Used for AIST practicum (AIST2601 + AIST2602 co-requisite in Term 2).
        Skipped when either course is already completed.
        """
        for c0, c1 in MANDATORY_SAME_SEMESTER_PAIRS:
            c0, c1 = c0.upper(), c1.upper()
            if c0 in self.completed or c1 in self.completed:
                continue
            if c0 not in self.taken or c1 not in self.taken:
                continue

            for s in self.slots:
                k0 = (c0, s.index)
                k1 = (c1, s.index)
                if k0 in self.x and k1 in self.x:
                    model.Add(self.x[k0] == self.x[k1])

    def _add_one_of_demand(self, model: cp_model.CpModel, e: ElectiveDemand) -> None:
        complete_tracks: list[Lit] = []
        engaged_tracks: list[Lit] = []
        for track in e.tracks:
            complete_tracks.append(self._track_complete(model, track))
            engaged_tracks.append(self._track_engaged(model, track))

        model.Add(self._lit_or(model, complete_tracks) == 1)

        active = [lit for lit in engaged_tracks if not self._is_const(lit, 0)]
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                model.Add(active[i] + active[j] <= 1)

    def _add_total_credit_floor(self, model: cp_model.CpModel, ctx: _ModelCtx) -> None:
        """Require planned credits to reach the degree total (not just group mins).

        Per-group floors can be satisfied while still falling short of
        ``total_credits_required`` when completed or planned courses sit outside
        every requirement group (e.g. MATH1020, PHYS1003, CSCI1130).
        """
        floor = self.inp.demand.min_total_planned_credits
        if floor <= 0:
            return
        terms = [
            self._scaled(self.units[c]) * self.taken[c]
            for c in self.candidates
            if c not in self.completed and c in self.taken
        ]
        if not terms:
            ctx.notes.append(
                f"Degree credit floor: {floor:g} cr required but no schedulable candidates."
            )
            ctx.setup_failed = True
            return
        floor_scaled = self._scaled(floor)
        max_supply = sum(
            self._scaled(self.units[c])
            for c in self.candidates
            if c not in self.completed and c in self.taken
        )
        total = model.NewIntVar(0, max_supply, "total_planned_credits")
        model.Add(total == sum(terms))
        model.Add(total >= floor_scaled)
        model.Add(total <= floor_scaled)
        ctx.total_planned_credits = total

    def _add_elective_demands(self, model: cp_model.CpModel) -> None:
        for e in self.inp.demand.electives:
            if e.kind == "credits_from":
                if e.pool:
                    pool = [p.upper() for p in e.pool if p.upper() in self.taken]
                    terms = [self._scaled(self.units[c]) * self.taken[c] for c in pool]
                    if terms:
                        model.Add(sum(terms) >= self._scaled(e.need_credits))
                        if e.max_credits is not None:
                            model.Add(sum(terms) <= self._scaled(e.max_credits))
                        if e.max_pool_count is not None:
                            model.Add(sum(self.taken[c] for c in pool) <= e.max_pool_count)
                else:
                    fillers = self.filler_group.get(e.group_id, [])
                    terms = [self._scaled(self.units[c]) * self.taken[c] for c in fillers]
                    if terms:
                        model.Add(sum(terms) >= self._scaled(e.need_credits))
            elif e.kind == "count_from":
                pool = [p.upper() for p in e.pool if p.upper() in self.taken]
                if pool:
                    model.Add(sum(self.taken[c] for c in pool) >= e.need_count)
            elif e.kind == "one_of":
                self._add_one_of_demand(model, e)

    def _add_pins(self, model: cp_model.CpModel, notes: list[str]) -> bool:
        for pin in self.inp.profile.priority_pins:
            code = pin.code.upper()
            if code in self.completed:
                continue
            target = next(
                (s for s in self.slots if s.planning_year == pin.year and s.term == pin.term),
                None,
            )
            if target is None:
                notes.append(f"Pin {code}: slot Y{pin.year} {pin.term.label} is outside the horizon.")
                return False
            if (code, target.index) not in self.x:
                notes.append(
                    f"Pin {code}: not offered in {pin.term.label} (per availability) "
                    f"or not a candidate course."
                )
                return False
            model.Add(self.x[(code, target.index)] == 1)
        return True

    def _objective_vars(self, model: cp_model.CpModel):
        self._ensure_slot_vars(model)
        n = max(1, len(self.slots))
        last_slot = model.NewIntVar(0, n - 1, "last_slot")
        for (c, si), xv in self.x.items():
            model.Add(last_slot >= si).OnlyEnforceIf(xv)

        max_term_scaled = self._scaled(self.inp.profile.max_credits_per_term)
        peak_load = model.NewIntVar(0, max_term_scaled, "peak_load")
        for load in self._slot_load.values():
            model.Add(peak_load >= load)

        n_x = max(1, len(self.x))
        sum_idx = model.NewIntVar(0, n_x * n, "sum_idx")
        model.Add(sum_idx == sum(si * xv for (c, si), xv in self.x.items()))
        return last_slot, peak_load, sum_idx

    def _add_nogood(self, model: cp_model.CpModel, solver: cp_model.CpSolver) -> None:
        clause = []
        for key, xv in self.x.items():
            if solver.Value(xv) == 1:
                clause.append(xv.Not())
            else:
                clause.append(xv)
        if clause:
            model.AddBoolOr(clause)

    # -- extraction ---------------------------------------------------------
    def _extract_plan(self, solver: cp_model.CpSolver, ctx: _ModelCtx, notes: list[str]) -> Plan:
        pinned_codes = {p.code.upper() for p in self.inp.profile.priority_pins}
        fillers = self.filler_codes()
        section = ctx.section
        by_slot: dict[int, list[PlannedCourse]] = {}
        for (c, si), xv in self.x.items():
            if solver.Value(xv) != 1:
                continue
            slot = self.slots[si]
            course = self.inp.catalog.get(c)
            is_filler = c in fillers
            bundle_id = None
            trust = None
            entry = self.section_data.get(c, si)
            if entry is not None:
                k = section.chosen_bundle_index(solver, c, si)
                if k is not None and k < len(entry.bundles):
                    bundle_id = entry.bundles[k].bundle_id
                    trust = entry.trust.value
            by_slot.setdefault(si, []).append(
                PlannedCourse(
                    code=c,
                    title=(course.title_en or course.title_zh) if course else ("Free elective" if is_filler else None),
                    credits=self.units[c],
                    planning_year=slot.planning_year,
                    term=slot.term,
                    is_filler=is_filler,
                    pinned=c in pinned_codes,
                    bundle_id=bundle_id,
                    section_trust=trust,
                )
            )

        # Per-term relaxed-conflict map (only populated on the relief-valve pass).
        relaxed_terms: dict[int, list[str]] = {}
        if ctx.relaxed:
            for code_a, code_b, term_label, was_hard in section.violated_conflicts(solver):
                if not was_hard:
                    continue
                for s in self.slots:
                    entry = self.section_data.get(code_a, s.index)
                    if entry is not None and entry.term_label == term_label:
                        relaxed_terms.setdefault(s.index, []).append(
                            f"Section clash relaxed: {code_a} x {code_b}"
                        )
                        break

        semesters: list[Semester] = []
        for s in self.slots:
            if s.index not in by_slot:
                continue
            courses = sorted(by_slot[s.index], key=lambda pc: pc.code)
            status, sec_notes = self._semester_section_status(s.index, courses)
            sec_notes.extend(relaxed_terms.get(s.index, []))
            if relaxed_terms.get(s.index):
                status = "relaxed"
            semesters.append(
                Semester(
                    planning_year=s.planning_year,
                    term=s.term,
                    courses=courses,
                    section_status=status,
                    section_notes=sec_notes,
                )
            )
        return Plan(
            feasible=True,
            semesters=semesters,
            notes=notes,
            objective_terms_used=len(semesters),
            peak_term_credits=int(solver.Value(ctx.peak_load)) / CREDIT_SCALE,
        )

    def _semester_section_status(
        self, slot_index: int, courses: list[PlannedCourse]
    ) -> tuple[str, list[str]]:
        real_courses = [c for c in courses if not c.is_filler]
        if not real_courses:
            return "no_data", []
        with_data = 0
        extrapolated = []
        for pc in real_courses:
            entry = self.section_data.get(pc.code, slot_index)
            if entry is None:
                continue
            with_data += 1
            if entry.trust == Trust.EXTRAPOLATED:
                extrapolated.append(pc.code)
        notes: list[str] = []
        if extrapolated:
            notes.append(
                "Using extrapolated section data: " + ", ".join(sorted(extrapolated))
            )
        if with_data == 0:
            return "no_data", notes
        if with_data < len(real_courses):
            return "partial", notes
        return "resolved", notes

    def _infeasible_hint(self, status: int) -> str:
        return (
            "No feasible plan found. Likely causes: planning horizon too short, "
            "credit caps too low for the remaining load, prerequisite cycles, or "
            "conflicting availability/pins. Try increasing --horizon or credit caps."
        )


def solve(inp: SchedulerInput, max_plans: int = 1, time_limit_s: float = 15.0) -> list[Plan]:
    return Scheduler(inp, time_limit_s=time_limit_s).build_and_solve(max_plans=max_plans)
