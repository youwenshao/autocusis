"""CP-SAT resolver when section pins are active."""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..models import PreferenceMode
from .conflict import bundles_conflict
from .models import SectionBundle
from .preferences import calculate_metrics, preference_score


@dataclass
class GeneratedSchedule:
    bundles: list[SectionBundle]
    score: float
    metrics: object


def _bundle_matches_pins(bundle: SectionBundle, pins: list[str]) -> bool:
    if not pins:
        return True
    ids = {s.upper() for s in bundle.section_ids()}
    return all(p.upper() in ids for p in pins)


def solve_with_pins(
    courses: list[str],
    options: dict[str, list[SectionBundle]],
    pins: dict[str, list[str]],
    *,
    preference: PreferenceMode | None = None,
    time_limit_s: float = 10.0,
) -> list[GeneratedSchedule]:
    courses = [c.upper() for c in courses]
    model = cp_model.CpModel()
    choice: dict[str, list[tuple[int, cp_model.IntVar]]] = {}

    for code in courses:
        vars_for_course: list[tuple[int, cp_model.IntVar]] = []
        for i, bundle in enumerate(options.get(code, [])):
            pin_list = pins.get(code, [])
            if pin_list and not _bundle_matches_pins(bundle, pin_list):
                continue
            v = model.NewBoolVar(f"x_{code}_{i}")
            vars_for_course.append((i, v))
        if not vars_for_course:
            return []
        choice[code] = vars_for_course
        model.Add(sum(v for _, v in vars_for_course) == 1)

    all_vars: list[tuple[cp_model.IntVar, SectionBundle]] = []
    for code in courses:
        for i, v in choice[code]:
            all_vars.append((v, options[code][i]))

    for a in range(len(all_vars)):
        for b in range(a + 1, len(all_vars)):
            va, ba = all_vars[a]
            vb, bb = all_vars[b]
            if bundles_conflict(ba, bb):
                model.Add(va + vb <= 1)

    terms = []
    for code in courses:
        for i, v in choice[code]:
            sc = int(preference_score(calculate_metrics([options[code][i]]), preference))
            terms.append(v * sc)
    if terms:
        model.Maximize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return []

    selected: list[SectionBundle] = []
    for code in courses:
        for i, v in choice[code]:
            if solver.Value(v) == 1:
                selected.append(options[code][i])
                break

    metrics = calculate_metrics(selected)
    return [
        GeneratedSchedule(
            bundles=selected,
            score=preference_score(metrics, preference),
            metrics=metrics,
        )
    ]
