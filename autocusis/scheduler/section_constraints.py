"""Timetable (section bundle) constraints for the unified scheduler.

Given per-slot section data, this builds the bundle-selection decision variables
``b[(course, slot, k)]`` and the within-term non-overlap constraints:

* both courses backed by REAL data  -> hard ``b_a + b_b <= 1``
* otherwise (EXTRAPOLATED involved)  -> soft penalty term

The aggregated soft penalty is exposed as a single CP-SAT variable that the
solver minimizes as one lexicographic objective stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ortools.sat.python import cp_model

from ..models import CourseCode
from ..sections.conflict import bundles_conflict
from .section_data import SectionData, SlotSections, Trust

# Relative weights within the single section-conflict penalty objective.
_W_REAL_RELAXED = 1000  # a real-vs-real conflict only tolerated via the relief valve
_W_MIXED = 10  # real-vs-extrapolated
_W_EXTRAPOLATED = 1  # extrapolated-vs-extrapolated


@dataclass
class _ConflictRecord:
    slot_index: int
    term_label: str
    code_a: CourseCode
    k_a: int
    code_b: CourseCode
    k_b: int
    hard: bool
    weight: int


@dataclass
class SectionModelResult:
    b: dict[tuple[CourseCode, int, int], cp_model.IntVar] = field(default_factory=dict)
    penalty: Optional[cp_model.IntVar] = None
    hard_pairs: int = 0
    soft_pairs: int = 0
    relaxed_real_pairs: int = 0
    _conflicts: list[_ConflictRecord] = field(default_factory=list)

    def chosen_bundle_index(
        self, solver: cp_model.CpSolver, code: CourseCode, slot_index: int
    ) -> Optional[int]:
        code = code.upper()
        for (c, si, k), var in self.b.items():
            if c == code and si == slot_index and solver.Value(var) == 1:
                return k
        return None

    def violated_conflicts(
        self, solver: cp_model.CpSolver
    ) -> list[tuple[CourseCode, CourseCode, str, bool]]:
        """Return (code_a, code_b, term_label, was_hard) pairs co-placed despite conflict."""
        out: list[tuple[CourseCode, CourseCode, str, bool]] = []
        seen: set[tuple[CourseCode, CourseCode, int]] = set()
        for rec in self._conflicts:
            va = self.b.get((rec.code_a, rec.slot_index, rec.k_a))
            vb = self.b.get((rec.code_b, rec.slot_index, rec.k_b))
            if va is None or vb is None:
                continue
            if solver.Value(va) == 1 and solver.Value(vb) == 1:
                key = (rec.code_a, rec.code_b, rec.slot_index)
                if key in seen:
                    continue
                seen.add(key)
                out.append((rec.code_a, rec.code_b, rec.term_label, rec.hard))
        return out


class SectionConstraintBuilder:
    def __init__(
        self,
        section_data: SectionData,
        *,
        weight: int = 1,
        relax_real: bool = False,
        trust_extrapolated_hard: bool = False,
    ) -> None:
        self.data = section_data
        self.weight = max(1, weight)
        self.relax_real = relax_real
        self.trust_extrapolated_hard = trust_extrapolated_hard
        self._conflict_cache: dict[tuple[int, int], bool] = {}

    def _pair_is_hard(self, ta: Trust, tb: Trust) -> bool:
        """A conflict is hard when both sides are trusted as authoritative."""
        if self.relax_real:
            return False
        if self.trust_extrapolated_hard:
            return True
        return ta == Trust.REAL and tb == Trust.REAL

    def _pair_weight(self, ta: Trust, tb: Trust) -> int:
        if ta == Trust.REAL and tb == Trust.REAL:
            return _W_REAL_RELAXED  # only reachable when relax_real is set
        if Trust.REAL in (ta, tb):
            return _W_MIXED
        return _W_EXTRAPOLATED

    def build(
        self,
        model: cp_model.CpModel,
        candidates: list[CourseCode],
        allowed_slots: dict[CourseCode, list],
        x: dict[tuple[CourseCode, int], cp_model.IntVar],
    ) -> SectionModelResult:
        result = SectionModelResult()
        if not self.data.has_any():
            return result

        # 1) Bundle-selection vars, linked to placement.
        slot_courses: dict[int, list[tuple[CourseCode, SlotSections]]] = {}
        for code in candidates:
            for s in allowed_slots.get(code, []):
                entry = self.data.get(code, s.index)
                if entry is None or (code, s.index) not in x:
                    continue
                bvars = []
                for k in range(len(entry.bundles)):
                    bv = model.NewBoolVar(f"b_{code}_{s.index}_{k}")
                    result.b[(code, s.index, k)] = bv
                    bvars.append(bv)
                model.Add(sum(bvars) == x[(code, s.index)])
                slot_courses.setdefault(s.index, []).append((code, entry))

        # 2) Within-term non-overlap (hard for real data, soft otherwise).
        penalty_terms: list[cp_model.IntVar] = []
        penalty_weights: list[int] = []
        for slot_index, items in slot_courses.items():
            for i in range(len(items)):
                code_a, entry_a = items[i]
                for j in range(i + 1, len(items)):
                    code_b, entry_b = items[j]
                    hard = self._pair_is_hard(entry_a.trust, entry_b.trust)
                    weight = self._pair_weight(entry_a.trust, entry_b.trust) * self.weight
                    for ka, ba in enumerate(entry_a.bundles):
                        for kb, bb in enumerate(entry_b.bundles):
                            if not self._conflict(ba, bb):
                                continue
                            va = result.b[(code_a, slot_index, ka)]
                            vb = result.b[(code_b, slot_index, kb)]
                            rec = _ConflictRecord(
                                slot_index=slot_index,
                                term_label=entry_a.term_label,
                                code_a=code_a,
                                k_a=ka,
                                code_b=code_b,
                                k_b=kb,
                                hard=(entry_a.trust == Trust.REAL and entry_b.trust == Trust.REAL),
                                weight=weight,
                            )
                            result._conflicts.append(rec)
                            if hard:
                                model.Add(va + vb <= 1)
                                result.hard_pairs += 1
                            else:
                                viol = model.NewBoolVar(
                                    f"sconf_{code_a}_{code_b}_{slot_index}_{ka}_{kb}"
                                )
                                model.Add(viol >= va + vb - 1)
                                penalty_terms.append(viol)
                                penalty_weights.append(weight)
                                result.soft_pairs += 1
                                if rec.hard:
                                    result.relaxed_real_pairs += 1

        if penalty_terms:
            ub = sum(penalty_weights)
            penalty = model.NewIntVar(0, ub, "section_conflict_penalty")
            model.Add(
                penalty == sum(w * v for w, v in zip(penalty_weights, penalty_terms))
            )
            result.penalty = penalty
        return result

    def _conflict(self, ba, bb) -> bool:
        key = (id(ba), id(bb))
        cached = self._conflict_cache.get(key)
        if cached is None:
            cached = bundles_conflict(ba, bb)
            self._conflict_cache[key] = cached
        return cached
