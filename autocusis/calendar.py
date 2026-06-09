"""Map planning slots to academic year / term labels."""

from __future__ import annotations

import re

from .models import Term
from .profile import Profile


def _parse_year_label(label: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})-(\d{2})", label.strip())
    if not m:
        raise ValueError(f"Invalid start_year_label: {label!r}")
    return int(m.group(1)), int(m.group(2))


def slot_label(profile: Profile, planning_year: int, term: Term) -> str:
    """Map a plan slot to an academic term label like '2026-27 Term 1'.

    ``planning_year`` is the solver's relative index (1 = first planned term),
  aligned with ``profile.current_year`` for that first slot.
    """
    if not profile.start_year_label:
        return f"Y{planning_year} {term.label}"

    y1, _ = _parse_year_label(profile.start_year_label)
    y1_cohort = y1 - (profile.current_year - 1)
    abs_year = profile.current_year + (planning_year - 1)
    new_y1 = y1_cohort + (abs_year - 1)
    yy2 = str(new_y1 + 1)[-2:]
    return f"{new_y1}-{yy2} {term.label}"
