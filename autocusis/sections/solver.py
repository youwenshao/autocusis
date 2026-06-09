"""Hybrid section scheduler: backtracking with preference scoring."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import PreferenceMode
from .conflict import bundles_conflict
from .models import SectionBundle
from .pin_solver import solve_with_pins
from .preferences import calculate_metrics, preference_score


@dataclass
class GeneratedSchedule:
    bundles: list[SectionBundle]
    score: float
    metrics: object


def _backtrack(
    courses: list[str],
    options: dict[str, list[SectionBundle]],
    course_index: int,
    current: list[SectionBundle],
    results: list[list[SectionBundle]],
    max_results: int,
) -> None:
    if len(results) >= max_results:
        return
    if course_index >= len(courses):
        results.append(list(current))
        return

    code = courses[course_index]
    for bundle in options.get(code, []):
        if any(bundles_conflict(bundle, b) for b in current):
            continue
        current.append(bundle)
        _backtrack(courses, options, course_index + 1, current, results, max_results)
        current.pop()


def _schedulable_courses(
    courses: list[str],
    options: dict[str, list[SectionBundle]],
) -> tuple[list[str], dict[str, list[SectionBundle]]]:
    """Keep only courses that have at least one section bundle."""
    by_code = {k.upper(): v for k, v in options.items()}
    schedulable = [c.upper() for c in courses if by_code.get(c.upper())]
    return schedulable, {c: by_code[c] for c in schedulable}


def generate_schedules(
    courses: list[str],
    options: dict[str, list[SectionBundle]],
    *,
    preference: PreferenceMode | None = "daysOff",
    max_results: int = 5,
    pins: dict[str, list[str]] | None = None,
    bias_start: float | None = None,
) -> list[GeneratedSchedule]:
    """Generate ranked conflict-free section schedules for one term."""
    courses, options = _schedulable_courses(courses, options)
    if not courses:
        return []

    if pins:
        schedulable_pins = {
            k.upper(): v for k, v in pins.items() if k.upper() in set(courses)
        }
        pinned: list[GeneratedSchedule] = [
            GeneratedSchedule(bundles=p.bundles, score=p.score, metrics=p.metrics)
            for p in solve_with_pins(
                courses, options, schedulable_pins, preference=preference
            )
        ]
        if pinned:
            return pinned
    raw: list[list[SectionBundle]] = []
    target = max(max_results * 40, 200)
    _backtrack(courses, options, 0, [], raw, target)

    scored: list[GeneratedSchedule] = []
    for bundles in raw:
        metrics = calculate_metrics(bundles)
        score = preference_score(metrics, preference)
        if bias_start is not None:
            score -= abs(metrics.avg_start_time - bias_start) * 500
        scored.append(GeneratedSchedule(bundles=bundles, score=score, metrics=metrics))

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:max_results]
