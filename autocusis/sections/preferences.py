"""Schedule preference scoring for section-level timetables."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import PreferenceMode
from .models import SectionBundle, TimeSlot

_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class ScheduleMetrics:
    total_gap_minutes: int = 0
    gap_count: int = 0
    max_gap_minutes: int = 0
    days_used: int = 0
    free_days: int = 0
    avg_start_time: float = 0.0
    avg_end_time: float = 0.0
    start_variance: float = 0.0
    long_break_count: int = 0
    earliest_start: int = 0
    latest_end: int = 0


def _day_index(day: str) -> int:
    try:
        return _DAY_ORDER.index(day)
    except ValueError:
        return 0


def calculate_metrics(bundles: list[SectionBundle]) -> ScheduleMetrics:
    by_day: dict[str, list[tuple[int, int]]] = {d: [] for d in _DAY_ORDER}
    for bundle in bundles:
        for m in bundle.meetings:
            by_day.setdefault(m.slot.day, []).append(
                (m.slot.start_minutes(), m.slot.end_minutes())
            )

    total_gap = 0
    gap_count = 0
    max_gap = 0
    long_breaks = 0
    days_used = 0
    starts: list[int] = []
    ends: list[int] = []

    for day in _DAY_ORDER:
        intervals = sorted(by_day.get(day, []))
        if not intervals:
            continue
        days_used += 1
        for s, e in intervals:
            starts.append(s)
            ends.append(e)
        for i in range(len(intervals) - 1):
            gap = intervals[i + 1][0] - intervals[i][1]
            if gap > 0:
                gap_count += 1
                total_gap += gap
                max_gap = max(max_gap, gap)
                if gap >= 60:
                    long_breaks += 1

    free_days = sum(1 for d in _DAY_ORDER[:5] if not by_day.get(d))
    avg_start = sum(starts) / len(starts) if starts else 0
    avg_end = sum(ends) / len(ends) if ends else 0
    if starts:
        mean_s = avg_start
        start_var = sum((s - mean_s) ** 2 for s in starts) / len(starts)
    else:
        start_var = 0.0

    return ScheduleMetrics(
        total_gap_minutes=total_gap,
        gap_count=gap_count,
        max_gap_minutes=max_gap,
        days_used=days_used,
        free_days=free_days,
        avg_start_time=avg_start,
        avg_end_time=avg_end,
        start_variance=start_var,
        long_break_count=long_breaks,
        earliest_start=min(starts) if starts else 0,
        latest_end=max(ends) if ends else 0,
    )


def preference_score(metrics: ScheduleMetrics, mode: PreferenceMode | None) -> float:
    score = 0.0
    score += metrics.free_days * 5_000_000
    score -= metrics.days_used * 200_000
    score -= metrics.total_gap_minutes * 8_000
    score -= metrics.max_gap_minutes * 50_000
    score -= metrics.start_variance * 100

    if mode == "shortBreaks":
        score -= metrics.total_gap_minutes * 20_000
        score -= metrics.gap_count * 100_000
    elif mode == "longBreaks":
        score += metrics.long_break_count * 500_000
        score += metrics.max_gap_minutes * 30_000
    elif mode == "consistentStart":
        score -= metrics.start_variance * 5_000
    elif mode == "morning":
        score -= metrics.avg_start_time * 1_500
        score -= metrics.earliest_start * 800
    elif mode == "startLate":
        score += metrics.avg_start_time * 1_000
    elif mode == "endEarly":
        score -= metrics.avg_end_time * 1_000
        score -= metrics.latest_end * 500
    elif mode == "daysOff":
        score += metrics.free_days * 10_000_000
        score -= metrics.days_used * 500_000

    return score
