"""Time conflict detection for section meetings."""

from __future__ import annotations

from .models import SectionBundle, SectionMeeting, TimeSlot

_DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


def slots_overlap(a: TimeSlot, b: TimeSlot) -> bool:
    if a.day != b.day:
        return False
    return a.start_minutes() < b.end_minutes() and a.end_minutes() > b.start_minutes()


def meetings_conflict(a: SectionMeeting, b: SectionMeeting) -> bool:
    return slots_overlap(a.slot, b.slot)


def bundles_conflict(a: SectionBundle, b: SectionBundle) -> bool:
    for ma in a.meetings:
        for mb in b.meetings:
            if meetings_conflict(ma, mb):
                return True
    return False


def schedule_conflicts(bundles: list[SectionBundle]) -> bool:
    for i, a in enumerate(bundles):
        for b in bundles[i + 1 :]:
            if bundles_conflict(a, b):
                return True
    return False


def find_hard_conflicts(
    codes: list[str],
    options: dict[str, list[SectionBundle]],
) -> list[tuple[str, str]]:
    """Return course pairs whose every bundle combination overlaps in time."""
    pairs: list[tuple[str, str]] = []
    for i, a in enumerate(codes):
        bundles_a = options.get(a) or []
        if not bundles_a:
            continue
        for b in codes[i + 1 :]:
            bundles_b = options.get(b) or []
            if not bundles_b:
                continue
            total = len(bundles_a) * len(bundles_b)
            if total == 0:
                continue
            conflicts = sum(
                1 for ba in bundles_a for bb in bundles_b if bundles_conflict(ba, bb)
            )
            if conflicts == total:
                pairs.append((a, b))
    return pairs
